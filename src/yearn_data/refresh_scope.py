"""Explicit refresh scope, safe chain bounds and missing daily-close planning."""
from dataclasses import dataclass,field,replace
import hashlib
import os
from pathlib import Path
import platform

from .config import CHAINS,normalize_chain_key,TVL_EXCLUDED_CHAIN_IDS
from .analysis import closed_cutoff
from .coverage import uncovered_ranges
from .history_sources import route,report_scope
from .refresh_state import digest
from .github_warning_report import TEMPLATE_PATH

DAY=86400


@dataclass(frozen=True)
class RefreshOptions:
    database:str
    chains:tuple[str,...]
    cutoff:int
    publication_root:Path
    from_blocks:dict=field(default_factory=dict)
    confirmations:dict=field(default_factory=dict)
    first_close:int|None=None
    source:str='auto'
    chunk_size:int|None=None
    chain_chunk_sizes:dict=field(default_factory=dict)
    report_workers:int=4
    window_days:int=7
    baseline_job:str|None=None
    max_targets:int=10_000
    max_dates:int=30
    repair_history:bool=False
    deferred_manifest:Path|None=None

    def __post_init__(self):
        if self.database not in ('neon','staging'):
            raise ValueError('select neon or staging explicitly')
        chains=tuple(sorted({normalize_chain_key(c) for c in self.chains}))
        if not chains:raise ValueError('select at least one chain')
        object.__setattr__(self,'chains',chains)
        object.__setattr__(self,'publication_root',Path(self.publication_root).resolve())
        for name in ('from_blocks','confirmations','chain_chunk_sizes'):
            values={normalize_chain_key(c):v for c,v in getattr(self,name).items()}
            if set(values)-set(chains):raise ValueError('bounds/confirmation chain is outside refresh scope')
            if any(isinstance(v,bool) or not isinstance(v,int) or v<(0 if name=='from_blocks' else 1) for v in values.values()):
                raise ValueError('invalid explicit bounds or confirmations')
            object.__setattr__(self,name,values)
        closed_cutoff(self.cutoff)
        if self.source not in ('auto','rpc','envio'):raise ValueError('invalid source routing')
        if any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in (self.max_targets,self.max_dates,self.report_workers,self.window_days)):
            raise ValueError('refresh work limits must be positive integers')
        if self.report_workers > 32:
            raise ValueError('report workers must be between 1 and 32')
        if self.chunk_size is not None and (type(self.chunk_size) is not int or self.chunk_size < 1):
            raise ValueError('chunk size must be a positive integer')
        if self.first_close is not None and (self.first_close<0 or self.first_close%DAY!=DAY-1 or self.first_close>=self.cutoff):
            raise ValueError('first close must be a closed UTC day end before cutoff')

    def report_chunk_size(self, chain):
        return self.chain_chunk_sizes.get(chain, self.chunk_size or (1_000_000 if route(CHAINS[chain].chain_id,'reports',self.source)=='envio' else 50_000))

    @property
    def window_start(self):
        return max(0, self.cutoff - self.window_days * DAY)

    @property
    def chain_ids(self):return tuple(CHAINS[c].chain_id for c in self.chains)


def code_identity():
    root=Path(__file__).parent
    result=hashlib.sha256()
    for path in sorted([*root.glob('*.py'),*(root/'inventories').glob('*.json')]):
        result.update(str(path.relative_to(root)).encode());result.update(path.read_bytes())
    result.update(b'.github/weekly-sync-warning-report.md')
    try:result.update(TEMPLATE_PATH.read_bytes())
    except OSError:result.update(b'unavailable')
    return result.hexdigest()


def context(options):
    # Keep effective financial configuration identity, never its values/URLs.
    environment={k:v for k,v in os.environ.items() if k.startswith(('YEARN_','ARCHIVE_RPC_','RPC_URI_FOR_'))
                 or k.endswith('_RPC_URL') or k in ('NEON_DB_URL','STAGING_DB_URL')}
    configuration=dict(environment=environment,root=str(options.publication_root),
                       deferrals=options.deferred_manifest.read_text() if options.deferred_manifest else None)
    return dict(database=options.database,chains=list(options.chains),cutoff=options.cutoff,
                codeId=code_identity(),configurationId=digest(configuration),python=platform.python_version(),
                source=options.source,fromBlocks=options.from_blocks,confirmations=options.confirmations,
                firstClose=options.first_close,maxTargets=options.max_targets,maxDates=options.max_dates,
                chunkSize=options.chunk_size,chainChunkSizes=options.chain_chunk_sizes,
                reportWorkers=options.report_workers,windowDays=options.window_days,
                baselineJob=options.baseline_job,repairHistory=options.repair_history)


def resolve_bounds(conn,options,chain,client):
    confirmations=options.confirmations.get(chain)
    safe=client.pin(confirmations=confirmations,use_envio=False)
    first=options.from_blocks.get(chain)
    reason='explicit' if first is not None else 'retained-coverage'
    if first is None:
        retained=conn.execute('SELECT min(from_block) FROM history_coverage WHERE chain_id=?',
                           (CHAINS[chain].chain_id,)).fetchone()[0]
        if retained is not None and options.repair_history:
            first=retained
        elif retained is not None:
            reason='weekly-window'
            lo,hi=0,int(safe['number'])
            while lo<hi:
                mid=(lo+hi)//2
                if int(client.block(mid)['timestamp']) < options.window_start:lo=mid+1
                else:hi=mid
            first=max(retained,lo)
    if first is None:
        raise ValueError(f'initial report scope is unavailable for {chain}; provide an explicit from-block')
    lo,hi=0,int(safe['number'])
    while lo<hi:
        mid=(lo+hi+1)//2
        if int(client.block(mid)['timestamp'])<options.cutoff:lo=mid
        else:hi=mid-1
    if int(client.block(lo)['timestamp'])>=options.cutoff or first>lo:
        raise ValueError('no ordered finalized block interval exists before cutoff')
    selected=route(CHAINS[chain].chain_id,'reports',options.source)
    pin=client.pin(end=lo,confirmations=confirmations,use_envio=selected=='envio')
    return dict(fromBlock=first,startReason=reason,toBlock=lo,pin=pin,source=selected,
                finalizedTimestamp=safe['timestamp'],cutoffReached=safe['timestamp']>=options.cutoff,
                deferredReportRanges=deferred_report_ranges(conn,CHAINS[chain].chain_id,first))


def deferred_report_ranges(conn,chain_id,first):
    """Expose old holes and skipped tails without expanding acquisition."""
    scopes={report_scope(dict(row)).key for row in conn.execute('''SELECT * FROM vaults
        WHERE chain_id=? AND version IN ('v2','v3') AND asset IS NOT NULL
        AND asset_decimals IS NOT NULL''',(chain_id,))}
    spans={}
    for target,policy,lo,hi in conn.execute('''SELECT target,policy,from_block,to_block
        FROM history_coverage WHERE chain_id=? AND family='reports' AND from_block<?
        ORDER BY target,policy,from_block,to_block''',(chain_id,first)):
        if (chain_id,target,'reports',policy) in scopes:
            spans.setdefault((target,policy),[]).append((lo,hi))
    return [dict(target=target,policy=policy,fromBlock=lo,toBlock=hi)
            for (target,policy),ranges in spans.items()
            for lo,hi in uncovered_ranges(ranges,ranges[0][0],first-1,first)]


def verify_bounds(options,chain,bounds,client):
    confirmations=options.confirmations.get(chain)
    # A new source watermark or head may advance. The retained pinned interval
    # must still be safe and canonical; advancing heads alone do not stale it.
    client.pin(end=bounds['toBlock'],confirmations=confirmations,use_envio=bounds['source']=='envio')
    client.verify_pin(bounds['pin'])
    if int(client.block(bounds['toBlock'])['timestamp'])>=options.cutoff:
        raise ValueError('saved bound is outside the closed cutoff')


def tvl_plan(conn,options):
    marks=','.join('?' for _ in options.chain_ids)
    catalog={}
    for row in conn.execute(f'SELECT chain_id,lower(address) FROM tvl_vaults WHERE chain_id IN ({marks})',options.chain_ids):
        if row[0] not in TVL_EXCLUDED_CHAIN_IDS:catalog.setdefault(row[0],set()).add(row[1])
    if set(options.chain_ids)-TVL_EXCLUDED_CHAIN_IDS-set(catalog):
        raise ValueError('TVL inventory is unavailable for part of the selected chain scope')
    lower = options.first_close
    if lower is None and not options.repair_history:
        lower = options.window_start + DAY - 1
    lower = 0 if lower is None else lower
    observations={};repairs={}
    for row in conn.execute(f'''SELECT s.chain_id,lower(s.vault),s.timestamp,
        max(CASE WHEN json_extract(s.data_json,'$.asset_units') IS NOT NULL THEN 1 ELSE 0 END),
        max(CASE WHEN json_extract(s.data_json,'$.tvl_usd') IS NOT NULL THEN 1 ELSE 0 END)
        FROM tvl_snapshots s JOIN tvl_runs r ON r.id=s.run_id
        WHERE s.chain_id IN ({marks}) AND s.timestamp>=? AND s.timestamp<? AND r.completed_at IS NOT NULL
        AND r.status IN ('complete','incomplete') GROUP BY s.chain_id,lower(s.vault),s.timestamp''',
        (*options.chain_ids,lower,options.cutoff)):
        chain,vault,day,units,usd=row
        if units:observations.setdefault((chain,day),set()).add(vault)
        repairs.setdefault((chain,day),{})[vault]=bool(usd)
    bad_edges={(r[0],r[1]) for r in conn.execute(f'''SELECT p.chain_id,p.timestamp FROM tvl_positions p
        JOIN tvl_runs r ON r.id=p.run_id WHERE p.chain_id IN ({marks}) AND p.timestamp>=? AND p.timestamp<?
        AND r.completed_at IS NOT NULL AND r.status IN ('complete','incomplete')
        GROUP BY p.chain_id,p.timestamp,p.parent,p.strategy,p.child
        HAVING max(CASE WHEN json_extract(p.data_json,'$.status')='ok' AND
        json_extract(p.data_json,'$.debt_usd') IS NOT NULL AND
        (json_extract(p.data_json,'$.owned_usd') IS NOT NULL OR
         CAST(json_extract(p.data_json,'$.debt_usd') AS NUMERIC)=0) THEN 1 ELSE 0 END)=0''',
        (*options.chain_ids,lower,options.cutoff))}
    targets=[];missing=[];deferred=[]
    for chain,expected in sorted(catalog.items()):
        retained=[day for cid,day in repairs if cid==chain]
        first=(lower if not options.repair_history or options.first_close is not None else
               (min(retained)//DAY)*DAY+DAY-1 if retained else None)
        if first is None:raise ValueError(f'initial TVL close scope is unavailable for chain {chain}; provide first-close')
        for day in range(first,options.cutoff,DAY):
            absent=expected-observations.get((chain,day),set())
            repair=(any(not repairs.get((chain,day),{}).get(v,False) for v in expected) or (chain,day) in bad_edges)
            if absent or options.repair_history:targets.append((chain,day))
            if absent:missing.append((chain,day))
            elif repair:deferred.append(dict(chainId=chain,timestamp=day,reason='price-or-ownership-unavailable'))
    return dict(targets=targets,unresolvedTargets=missing,deferredRepairs=deferred,
                expectedVaults={str(c):len(v) for c,v in catalog.items()})


def since_receipt(options):
    """Freeze acquisition bounds from an explicitly accepted financial baseline."""
    if options.baseline_job is None:
        return options
    if options.from_blocks or options.first_close is not None or options.repair_history:
        raise ValueError('--since cannot be combined with explicit acquisition bounds or repair-history')
    from .refresh_state import read_receipt
    previous = read_receipt(options.database, options.baseline_job)
    if previous['status'] not in ('prepared','selected','selected-cleanup-failed') or not previous['result'].get('publishReady'):
        raise ValueError('baseline must be a publish-ready prepared or selected refresh')
    prior = previous['context']
    if prior['database'] != options.database or not set(options.chains) <= set(prior['chains']):
        raise ValueError('baseline does not cover the requested database/chains')
    if prior['cutoff'] >= options.cutoff:
        raise ValueError('baseline cutoff must precede the requested cutoff')
    return replace(options, first_close=prior['cutoff']+DAY-1,
                   from_blocks={c:previous['bounds'][c]['toBlock']+1 for c in options.chains})
