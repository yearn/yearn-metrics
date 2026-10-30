"""Update one developer issue after refresh completion; never gate publication."""
from contextlib import closing
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from string import Template
import subprocess
import sys
import stat
import tempfile

from .storage import connect
from .refresh_state import encoded

_root=Path(__file__).resolve().parents[2]
TEMPLATE_PATH=(_root/'.github'/'weekly-sync-warning-report.md'
               if (_root/'pyproject.toml').is_file() else
               Path(sys.prefix)/'share'/'yearn-data'/'.github'/'weekly-sync-warning-report.md')
PREFIX='<!-- yearn-weekly-warning-report:v1 '
TITLE='Weekly sync warnings — latest run'


def template_digest():
    return hashlib.sha256(TEMPLATE_PATH.read_bytes()).hexdigest()


def safe(value):
    """Provider-controlled evidence stays text, with no private URLs or mentions."""
    text=str(value)
    text=re.sub(r'https?://\S+','[URL omitted]',text)
    text=re.sub(r'(?i)(?:[A-Z0-9-]+\.)*tail[A-Z0-9-]*\.ts\.net\S*','[private host omitted]',text)
    return text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('@','&#64;').replace('|','&#124;').replace('`','&#96;').replace('\r',' ').replace('\n',' ')


def date(timestamp):
    return datetime.fromtimestamp(timestamp,timezone.utc).strftime('%Y-%m-%d')


def area(stage):
    from .config import CHAINS
    labels={'canonicalReports':'Canonical fee evidence','reportPrices':'Report prices',
            'feePrices':'Fee prices','deferredTvlRepairs':'TVL price / ownership coverage',
            'unresolvedTvlChainDates':'TVL balance coverage','unpriceableFeeEvents':'Fee asset metadata',
            'comparison-evidence':'Analytics comparison evidence','fee-config':'Analytics fee configuration',
            'membership':'Analytics membership','history':'Retained TVL history',
            'analytics-sources':'Analytics source refresh','defillama':'DefiLlama'}
    if ':' in stage:
        family,chain=stage.split(':',1)
        name=CHAINS[chain].name if chain in CHAINS else chain
        return name+' '+{'catalog':'catalog','reports':'reports'}.get(family,family)
    return labels.get(stage,stage)


def render(receipt):
    """Use the framework for evidence-based explanations, not terse counters."""
    from .config import CHAINS
    names={c.chain_id:c.name for c in CHAINS.values()}
    result=receipt['result'];context=receipt['context'];warnings=result.get('warnings')
    rows=[];sections=[];target_count=0;omitted=0
    for w in warnings or []:
        code=w['code'];stage=w['stage'];d=w['details'];label=area(stage)
        description=w['message'];meaning='The available evidence does not establish the cause.'
        if code=='incomplete-source':
            all_sources=d.get('sources',[])
            if isinstance(all_sources,dict):
                all_sources=[dict(value,source=kind,
                    status='complete' if value['availability']=='available' else 'incomplete')
                    for kind,value in all_sources.items()]
            sources=[source for source in all_sources if source['status']!='complete']
            descriptions=[];context_notes=[];targets=[]
            for source in sources:
                failures=source.get('findings',[])
                if 'availability' in source:
                    descriptions.append(f"{area(source['source'])} evidence is {source['availability']}.")
                    if source.get('observedAt') is not None:
                        context_notes.append(f"{area(source['source'])} selected evidence was observed on {date(source['observedAt'])} UTC.")
                    if source.get('retainedRows'):
                        context_notes.append(f"{source['retainedRows']:,} rows use retained evidence.")
                elif failures and all(f.get('reason')=='missing_asset_metadata' for f in failures):
                    descriptions.append(f"{source['source']} returned incomplete underlying-token metadata for {source['failures']:,} vaults.")
                    retained=sum(bool(f.get('retained_asset')) and f.get('retained_asset_decimals') is not None for f in failures)
                    if retained:
                        context_notes.append(f'Retained token metadata is available for {retained:,} of these {len(failures):,} vaults, so the upstream gap does not by itself establish missing balances.')
                    else:context_notes.append('This finding does not establish whether retained token metadata is sufficient for collection.')
                elif failures and all(f.get('stage')=='morpho-api' for f in failures):
                    roles=', '.join(dict.fromkeys(str(f.get('family','Morpho'))+' '+str(f.get('role','query')) for f in failures))
                    descriptions.append(f"{source['failures']:,} Morpho discovery queries failed: {roles}.")
                    reasons=', '.join(sorted({str(f.get('reason','unknown')) for f in failures}))
                    context_notes.append(f'Only {reasons} was recorded. Source applicability or unsupported-chain behavior has not been confirmed as the cause.')
                else:descriptions.append(f"{source['source']} reported {source['failures']:,} discovery failures.")
                for f in failures:
                    if not f.get('address'):continue
                    if target_count>=25:omitted+=1;continue
                    target_count+=1
                    text='- **'+safe(f.get('vault_name') or 'Vault')+'** — `'+safe(f['address'])+'`'
                    if f.get('retained_asset') and f.get('retained_asset_decimals') is not None:
                        text+='; retained asset: '+safe(f.get('retained_asset_symbol') or f['retained_asset'])+', '+str(f['retained_asset_decimals'])+' decimals'
                    text+='; finding: '+safe(f.get('reason','unavailable metadata'))+'.'
                    targets.append(text)
            for source in all_sources:
                if source['status']=='complete' and source.get('source')=='kong' and source.get('discovered') is not None:
                    context_notes.append(f"Kong refreshed {source['discovered']:,} tracked vaults successfully.")
            if d.get('attempt'):
                descriptions.append('The source refresh failed with '+str(d['attempt'].get('failureClass','an unavailable response'))+'.')
                context_notes.append('The selected retained inputs remain available; their coverage and age are recorded separately.')
            for skipped in d.get('skipped_vaults',[]):
                descriptions.append('A vault was skipped: '+str(skipped.get('address','unknown'))+' ('+str(skipped.get('reason','metadata unavailable'))+').')
            description=' '.join(descriptions) or 'Source acquisition was incomplete.'
            meaning=' '.join(context_notes) or 'Some source evidence could not be refreshed; the receipt retains the resulting availability gaps.'
            if targets:sections.append('### '+safe(label)+' — affected vaults\n\n'+'\n'.join(targets))
        elif code=='processing-gaps':
            counts='; '.join(f'{count:,} {status}' for status,count in sorted(d.items()))
            description=counts+'.'
            meaning={'canonicalReports':'Canonical fee evidence remains unresolved for these retained reports.',
                     'reportPrices':'These retained report-price targets have no accepted price evidence.',
                     'feePrices':'These retained daily fee-price targets have no accepted price evidence; they are separate from report-price targets.'}.get(stage,'Some retained evidence remains unresolved.')
        elif code=='availability-gaps':
            if isinstance(d,list):
                # Balance gaps are (chain, timestamp) pairs; price/ownership
                # gaps carry dictionaries. Both shapes survive receipt JSON.
                entries=[dict(chainId=x[0],timestamp=x[1]) if isinstance(x,(list,tuple)) else x for x in d]
                chains=sorted({x.get('chainId',x.get('chain')) for x in entries if x.get('chainId',x.get('chain')) is not None})
                by_chain={chain:sum(x.get('chainId',x.get('chain'))==chain for x in entries) for chain in chains}
                description=f'{len(d):,} chain/day findings: '+', '.join(f'{names.get(c,str(c))} ({n} days)' for c,n in by_chain.items())+'.'
                meaning=('Asset units are known, but some price or ownership evidence remains unavailable. Each chain/day finding can cover multiple vaults.'
                         if stage=='deferredTvlRepairs' else 'Some expected daily asset-unit observations are unavailable.')
            else:
                description=f'{d:,} fee events could not be priced.';meaning='Required asset metadata is missing.'
        elif code=='analytics-availability':
            description='Selected evidence is '+d['availability']+'.'
            if d.get('retainedRows'):description+=f" {d['retainedRows']:,} rows use retained evidence."
            meaning={'comparison-evidence':'No usable manual comparison-evidence input is selected.' if d['availability']=='unavailable' else 'Comparison evidence covers only part of the requested scope.',
                     'fee-config':'Fee configuration evidence does not cover the full requested scope.',
                     'membership':'Vault membership evidence does not cover the full requested scope.'}.get(stage,'Analytics uses incomplete or retained source evidence.')
        elif code=='history-availability':
            description=f"{d['unavailableRows']:,} rows have unavailable USD fields across {d['dates']:,} dates ({date(d['firstTimestamp'])}–{date(d['lastTimestamp'])} UTC)."
            meaning='These are gaps in retained history, not a count of new failures in this weekly window. The report does not yet distinguish expected historical limitations from repairable omissions.'
        elif code=='chain-freshness':
            description='The finalized head is dated '+date(d['finalizedTimestamp'])+' UTC; the requested cutoff is '+date(d['cutoff'])+' UTC.'
            meaning='Acquisition covers the verified pinned interval. This finding alone does not establish whether the chain stopped or its provider is stale.'
        elif code=='report-coverage-gap':
            description=f'{len(d):,} vault report ranges before the acquisition window were not scanned.'
            meaning='Reports, earnings and fees may be incomplete for these blocks. This run kept its bounded scope; the missing intervals need a separate catch-up.'
            targets=[]
            for gap in d:
                if target_count>=25:omitted+=1;continue
                target_count+=1
                targets.append('- Vault `'+safe(gap['target'])+'`: blocks '+str(gap['fromBlock'])+'–'+str(gap['toBlock'])+'.')
            if targets:sections.append('### '+safe(label)+' — unscanned report ranges\n\n'+'\n'.join(targets))
        rows.append('| '+safe(label)+' | '+safe(description)+' | '+safe(meaning)+' |')
    if warnings is None:summary='A complete warning summary was not produced. Warning counts are unavailable.'
    elif rows:summary='| Area | What was observed | Context / significance |\n| --- | --- | --- |\n'+'\n'.join(rows)
    else:summary='No warnings were reported for this run.'
    if omitted:sections.append(f'{omitted} additional affected targets are available in the saved run receipt.')
    cutoff=context['cutoff']
    first=result.get('acquisitionWindow',{}).get('firstClose') or context.get('firstClose') or cutoff-context.get('windowDays',7)*86400+86399
    outcome={'prepared':'completed through financial preparation','selected':'completed and selected a financial release',
             'acquired':'completed acquisition','failed':'failed','selected-cleanup-failed':'selected a financial release; cleanup remains incomplete'}.get(receipt['status'],receipt['status'])
    run_summary=f"The {context['database']} sync for **{date(first)}–{date(cutoff-1)} UTC** {outcome}."
    run_summary+=' A financial release was selected.' if result.get('publication')=='selected' and receipt['status']!='selected' else (' No financial release was selected.' if result.get('publication')!='selected' else '')
    if warnings is not None:run_summary+=f' The run reported **{len(warnings)} warning groups**.'
    run_summary+='\n\nAcquisition scope: '+', '.join(CHAINS[c].name if c in CHAINS else c for c in context['chains'])+'. Counts below cover retained inputs through the cutoff unless a narrower window is stated; they are not all new failures from this week.'
    blockers=result.get('publicationBlockers')
    if blockers:integrity='\n'.join('- '+safe(b['code']) for b in blockers)
    elif result.get('publishReady'):integrity='The recorded publication integrity checks passed.'
    elif result.get('publishReady') is False:
        integrity='The recorded run was not cleared for publication.'
        if result.get('incompleteStages'):integrity+=' Its receipt lists incomplete stages: '+', '.join(safe(area(stage)) for stage in result['incompleteStages'])+'.'
    else:integrity='Publication integrity was not completed or evaluated.'
    if result.get('failedStage'):integrity+='\n\nStopped at: '+safe(area(result['failedStage']))+'.'
    marker=json.dumps(dict(jobId=receipt['jobId'],cutoff=cutoff,createdAt=receipt['createdAt']),sort_keys=True,separators=(',',':'))
    return Template(TEMPLATE_PATH.read_text()).substitute(marker=marker,run_summary=run_summary,
        summary=summary,details='\n\n'.join(sections),integrity=integrity,
        job_id=safe(receipt['jobId']),database=safe(context['database']))


def gh(method,endpoint,payload=None):
    args=['gh','api','--hostname','github.com','--method',method,endpoint]
    if payload is not None:args+=['--input','-']
    response=subprocess.run(args,input=json.dumps(payload) if payload is not None else None,
                            text=True,capture_output=True,timeout=15,check=False)
    if response.returncode:raise RuntimeError('GitHub warning report request failed')
    return json.loads(response.stdout)


def lock_directory():
    # The shared temporary directory may contain foreign entries. Only this
    # account may create files inside the verified private subdirectory.
    path=Path(tempfile.gettempdir())/f'yearn-warning-reporter-{os.getuid()}'
    path.mkdir(mode=0o700,exist_ok=True)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise PermissionError('unsafe warning report lock directory')
    return path


def lock_file(path):
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
        os.close(fd)
        raise PermissionError('unsafe warning report lock file')
    return os.fdopen(fd,'r+')


def contention(path,job_id=None):
    # A separate, short-lived lock serializes diagnostics only, never network
    # work. Retrying one job does not masquerade as multiple completed runs.
    with lock_file(path.with_suffix('.contention')) as note:
        fcntl.flock(note,fcntl.LOCK_EX)
        previous=json.loads(note.read() or '{}')
        count=previous.get('consecutiveDeferredRuns',0)+int(previous.get('jobId')!=job_id) if job_id else 0
        note.seek(0);note.truncate()
        note.write(json.dumps(dict(jobId=job_id,consecutiveDeferredRuns=count)))
        return count


def update(receipt):
    """No search or create: one configured repository/issue is the durable target."""
    if os.environ.get('YEARN_REFRESH_GITHUB_REPORTING','on')=='off':return {'status':'disabled'}
    if os.environ.get('YEARN_REFRESH_GITHUB_TEMPLATE_SHA256')!=template_digest():
        return {'status':'awaiting-template-approval','templateSha256':template_digest()}
    repository=os.environ.get('YEARN_REFRESH_GITHUB_REPOSITORY','yearn/yearn-metrics')
    issue=os.environ.get('YEARN_REFRESH_GITHUB_ISSUE','92')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',repository) or not re.fullmatch(r'[1-9][0-9]*',issue):
        raise ValueError('invalid GitHub warning issue target')
    identity=hashlib.sha256(f'{repository}/{issue}'.encode()).hexdigest()
    # Coordinate staging/production reporters on this host as well as the
    # database's refresh writer lock. Never queue a notification indefinitely.
    path=lock_directory()/(identity+'.lock')
    with lock_file(path) as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            count=contention(path,receipt['jobId'])
            return {'status':'deferred','reason':'issue-update-in-progress',
                    'consecutiveDeferredRuns':count,'persistentContention':count>1}
        contention(path)
        return update_target(receipt,repository,issue)


def update_target(receipt,repository,issue):
    endpoint=f'repos/{repository}/issues/{issue}'
    current=gh('GET',endpoint)
    if current.get('pull_request'):raise ValueError('warning target is a pull request')
    body=render(receipt)
    previous=current.get('body') or ''
    if previous.startswith(PREFIX):
        previous_marker=json.loads(previous.split(' -->',1)[0][len(PREFIX):])
        if (previous_marker['cutoff'],previous_marker['createdAt'])>(receipt['context']['cutoff'],receipt['createdAt']):
            return {'status':'skipped-older-run','issueUrl':current['html_url']}
    reopen=bool(receipt['result'].get('warnings')) and current['state']=='closed'
    if previous==body and current['title']==TITLE and not reopen:
        return {'status':'unchanged','issueUrl':current['html_url']}
    payload={'title':TITLE,'body':body}
    if reopen:payload['state']='open'
    gh('PATCH',endpoint,payload)
    return {'status':'updated','issueUrl':current['html_url']}


def completed(job):
    """The financial outcome is committed before notification, under writer lock.

    Every reporting error is isolated, including persistence/progress failures.
    Resuming a terminal job retries reporting without rerunning publication.
    """
    receipt=job.receipt()
    if receipt['status'] not in ('acquired','prepared','selected','selected-cleanup-failed','failed'):
        return receipt
    try:
        outcome=update(receipt)
    except Exception as exc:
        outcome={'status':'failed','failureClass':type(exc).__name__}
    receipt['result']=receipt['result']|{'githubWarningReport':outcome}
    try:
        # A reporting write cannot poison the financial worker's connection.
        with closing(connect(job.database)) as conn,conn:
            conn.execute('UPDATE refresh_jobs SET result_json=? WHERE id=? AND status=?',
                         (encoded(receipt['result']),job.id,receipt['status']))
    except Exception as exc:
        outcome['receiptPersistenceFailureClass']=type(exc).__name__
    try:job.emit('github-warning-report',**outcome)
    except Exception:pass
    return receipt
