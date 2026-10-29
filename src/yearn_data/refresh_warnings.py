"""Operator-visible source and availability warnings; never financial substitutes."""
import html
import json


def findings(stages, result, bounds=None):
    warnings = []
    for chain, bound in (bounds or {}).items():
        if not bound['cutoffReached']:
            warnings.append(dict(code='chain-freshness',stage='reports:'+chain,
                message='The finalized chain head is older than the requested cutoff; only the verified pinned interval was acquired.',
                details=dict(finalizedTimestamp=bound['finalizedTimestamp'],cutoff=result['cutoff'])))
    for stage in stages:
        if stage['status'] != 'incomplete':
            continue
        detail = stage['result']
        warnings.append(dict(code='incomplete-source', stage=stage['name'],
            message='Source acquisition was incomplete; retained data and explicit gaps are preserved.',
            details={key: detail[key] for key in ('sources', 'skipped_vaults', 'attempt') if key in detail}))
    for family in ('canonicalReports', 'reportPrices', 'feePrices'):
        gaps = {status: count for status, count in result['gaps'].get(family, {}).items()
                if status != 'ok' and count}
        if gaps:
            warnings.append(dict(code='processing-gaps', stage=family,
                message='Some records have unresolved evidence or prices.', details=gaps))
    for kind in ('unpriceableFeeEvents', 'unresolvedTvlChainDates', 'deferredTvlRepairs'):
        gaps = result['gaps'].get(kind)
        if gaps:
            warnings.append(dict(code='availability-gaps', stage=kind,
                message='Some observations remain unavailable or need a separate repair.', details=gaps))
    for kind, source in result.get('analyticsSources', {}).items():
        if source['availability'] != 'available' or source.get('retainedRows'):
            warnings.append(dict(code='analytics-availability', stage=kind,
                message='Analytics uses partial, unavailable or retained source evidence.', details=source))
    history = result.get('history', {}).get('unavailableHistoryDates', [])
    if history:
        warnings.append(dict(code='history-availability', stage='history',
            message='Historical rows retain explicit unavailable values.',
            details=dict(dates=len(history), firstTimestamp=min(r['timestamp'] for r in history),
                         lastTimestamp=max(r['timestamp'] for r in history),
                         unavailableRows=sum(r['unavailableRows'] for r in history))))
    return warnings


def write_report(root, job_id, result):
    """Save durable JSON and escaped standalone HTML alongside prepared datasets."""
    report = dict(jobId=job_id, cutoff=result['cutoff'], warnings=result['warnings'],
                  warningCount=len(result['warnings']), publishReady=result.get('publishReady'),
                  publicationBlockers=result.get('publicationBlockers', []))
    def esc(value):return html.escape(str(value))
    page = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Refresh warnings</title>
<style>body{font:17px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:24px;color:#171717}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}article{border-top:1px solid #ccc;padding:16px 0}</style>
<h1>Refresh warnings</h1>'''
    page += '<p>Job '+esc(job_id)+' · Cutoff '+esc(result['cutoff'])+'</p>'
    page += '<p>'+str(len(result['warnings']))+' warnings. Source and availability findings do not block publication. Missing values remain unavailable.</p>'
    if report['publicationBlockers']:
        page += '<h2>Publication blockers</h2><pre>'+esc(json.dumps(report['publicationBlockers'], indent=2))+'</pre>'
    for warning in result['warnings']:
        page += '<article><h2>'+esc(warning['stage'])+'</h2><p>'+esc(warning['message'])+'</p><pre>'+esc(json.dumps(warning['details'], indent=2))+'</pre></article>'
    page += '</html>'
    for name, content in [('warnings.json', json.dumps(report, indent=2, allow_nan=False)), ('warnings.html', page)]:
        target = root/name
        temporary = target.with_suffix(target.suffix+'.tmp')
        temporary.write_text(content)
        temporary.replace(target)
