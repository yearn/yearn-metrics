"""Maintained multi-chain acquisition, preparation and explicit release selection."""
from bisect import bisect_right
from contextlib import closing
import json
import time
import requests

from . import analytics,analytics_collect,canonical_history,catchup,fee_valuation,fees,pricing
from . import tokenized_fees,tvl_api,tvl_discovery,tvl_history_cache,tvl_sources
from .analysis import run_lifetime_yield
from .chains import web3_for
from .config import CHAINS
from .fee_products import PRODUCTS
from .history_sources import HistoricalSource
from .hosted_publications import publish,PublicationCleanupError,encoded as release_encoded
from .pairing import PairingDataset,select_pairing
from .refresh_scope import context,resolve_bounds,verify_bounds,tvl_plan,DAY
from .refresh_state import RefreshState,WorkBudget,WorkDeferred,digest,sql_digest,drain_pages
from .storage import connect,init_db,table_exists


def chain_source(chain):
    return HistoricalSource(chain,web3_for(chain))


def selection(database):
    with closing(connect(database,readonly=True)) as conn:
        if not table_exists(conn,'api_selection'):return None
        row=conn.execute("SELECT release_id FROM api_selection WHERE name='powerglove'").fetchone()
        return row[0] if row else None


def observed_publication(job):
    """Recover a selected pointer even if the process died before saving success."""
    receipt=job.receipt()
    stage=next((s for s in receipt['stages'] if s['name']=='publication'),None)
    expected=stage['progress'].get('expectedReleaseId') if stage else None
    if expected and selection(job.database)==expected:
        saved=receipt['result']
        if saved.get('publication')=='selected' and saved.get('releaseId')==expected:return saved
        if stage['result'].get('publication')=='selected':return stage['result']
        return stage['result']|{'releaseId':expected,'publication':'selected','consolidation':'unknown'}
    return None


def _page(items,key):
    ordered=sorted(items,key=key)
    keys=[key(item) for item in ordered]
    def select(after,limit):
        offset=bisect_right(keys,tuple(after)) if after is not None else 0
        return ordered[offset:offset+limit]
    return select


def _files(root,identity=None):
    paths=[root/'current.json']
    if identity:paths.append(root/'datasets'/f'{identity}.json')
    return digest([p.read_bytes().hex() if p.exists() else None for p in paths])


class ProgressSource:
    """Report bounded fetches without claiming that fetched rows were committed."""
    def __init__(self,source,emit):self.source=source;self.emit=emit
    def __getattr__(self,name):return getattr(self.source,name)
    def fetch(self,family,target,first,last,call):
        details=dict(family=family,target=target,fromBlock=first,toBlock=last)
        self.emit('range-fetch-started',**details)
        try:rows=call()
        except BaseException:
            self.emit('range-fetch-failed',**details)
            raise
        self.emit('range-fetched',**details,observedRows=len(rows))
        return rows
    def reports(self,vault,first,last,source):
        return self.fetch('reports',vault['address'],first,last,
                          lambda:self.source.reports(vault,first,last,source))
    def inventory(self,target,first,last,source):
        return self.fetch(target.scope.family,target.scope.target,first,last,
                          lambda:self.source.inventory(target,first,last,source))


class RefreshWorker:
    def __init__(self,options,job):
        self.options=options;self.job=job
        self.sources={chain:chain_source(chain) for chain in options.chains}
        self.targets=WorkBudget(options.max_targets);self.dates=WorkBudget(options.max_dates)
        self.root=options.publication_root/'refresh'/job.id
        self.root.mkdir(parents=True,exist_ok=True)
        self.ids=options.chain_ids
        self.marks=','.join('?' for _ in self.ids)
        self.active='bounds'

    def signature(self,queries):
        with closing(connect(self.options.database,readonly=True)) as conn,conn:
            return digest([sql_digest(conn,query,params) if table_exists(conn,table) else 'missing:'+table
                           for table,query,params in queries])

    def reports(self,*,global_scope=False,chain=None):
        ids=(CHAINS[chain].chain_id,) if chain else self.ids
        marks=','.join('?' for _ in ids)
        clause='' if global_scope else f' AND chain_id IN ({marks})'
        params=(self.options.cutoff,) if global_scope else (self.options.cutoff,*ids)
        return self.signature([
            ('strategy_reports','SELECT * FROM strategy_reports WHERE block_timestamp<?'+clause,params),
            ('vaults','SELECT * FROM vaults'+('' if global_scope else f' WHERE chain_id IN ({marks})'),
             () if global_scope else ids)])

    def canonical(self,partial=False,*,global_scope=False):
        columns='f.chain_id,f.tx_hash,f.report_log_index,f.status' if partial else 'f.*'
        clause='' if global_scope else f' AND r.chain_id IN ({self.marks})'
        params=(self.options.cutoff,) if global_scope else (self.options.cutoff,*self.ids)
        return self.signature([('canonical_fee_reports',f'''SELECT {columns} FROM canonical_fee_reports f
            JOIN strategy_reports r ON r.chain_id=f.chain_id AND r.tx_hash=f.tx_hash AND r.log_index=f.report_log_index
            WHERE r.block_timestamp<?{clause}''',params)])

    def prices(self,partial=False,*,global_scope=False):
        columns='chain_id,token_address,timestamp,source,status' if partial else '*'
        clause='' if global_scope else f' AND chain_id IN ({self.marks})'
        params=(self.options.cutoff,) if global_scope else (self.options.cutoff,*self.ids)
        return self.signature([('prices',f"SELECT {columns} FROM prices WHERE source='yearn-prices' AND timestamp<?"+clause,params)])

    def fee_prices(self,partial=False):
        columns='chain_id,asset,eod_timestamp,policy,endpoint_id,status' if partial else '*'
        return self.signature([('fee_daily_prices',f'''SELECT {columns} FROM fee_daily_prices WHERE eod_timestamp<?
            AND policy=? AND endpoint_id=?''',(self.options.cutoff,fee_valuation.POLICY,fee_valuation.endpoint_identity()))])

    def tokenized(self,partial=False,chain=None):
        columns='chain_id,tx_hash,log_index' if partial else '*'
        clause=' AND chain_id=?' if chain else ''
        params=(self.options.cutoff,CHAINS[chain].chain_id) if chain else (self.options.cutoff,)
        return self.signature([
            ('tokenized_fee_events',f'''SELECT {columns} FROM tokenized_fee_events WHERE
                CAST(json_extract(event_json,'$.block_timestamp') AS BIGINT)<?{clause}''',params),
            ('tokenized_fee_ranges','SELECT * FROM tokenized_fee_ranges WHERE before_timestamp<=?'+clause,params)])

    def catalog(self,global_scope=False,chain=None):
        ids=(CHAINS[chain].chain_id,) if chain else self.ids
        marks=','.join('?' for _ in ids)
        clause='' if global_scope else f' WHERE v.chain_id IN ({marks})'
        params=() if global_scope else ids
        return self.signature([('tvl_vaults',"SELECT to_jsonb(v)-'updated_at' AS evidence FROM tvl_vaults v"+clause,params),
            ('tvl_catalog_evidence',"SELECT to_jsonb(v)-'updated_at' AS evidence FROM tvl_catalog_evidence v"+clause,params)])

    def tvl(self,partial=False,*,global_scope=False):
        clause='' if global_scope else f' AND s.chain_id IN ({self.marks})'
        params=(self.options.cutoff,) if global_scope else (self.options.cutoff,*self.ids)
        points='s.run_id,s.chain_id,s.vault,s.timestamp' if partial else 's.*'
        edges='s.run_id,s.chain_id,s.parent,s.strategy,s.child,s.timestamp' if partial else 's.*'
        queries=[]
        for table,columns in [('tvl_snapshots',points),('tvl_positions',edges)]:
            queries.append((table,f'''SELECT {columns} FROM {table} s JOIN tvl_runs r ON r.id=s.run_id
                WHERE r.completed_at IS NOT NULL AND r.status IN ('complete','incomplete') AND s.timestamp<?{clause}''',params))
        if global_scope:
            queries.append(('tvl_runs',"SELECT * FROM tvl_runs WHERE completed_at IS NOT NULL AND status IN ('complete','incomplete')",()))
        return self.signature(queries)

    def analytics_inputs(self):
        return self.signature([('analytics_inputs',"""SELECT DISTINCT ON(kind) * FROM analytics_inputs
            WHERE kind IN ('defillama','membership','fee-config','comparison-evidence') AND status IN ('available','partial')
            ORDER BY kind,observed_at DESC,id DESC""",())])

    def analysis_output(self,result):
        identity=result.get('runId')
        if identity is None:return digest(None)
        return self.signature([('analysis_runs','SELECT * FROM analysis_runs WHERE id=?',(identity,)),
                               ('analysis_outputs','SELECT * FROM analysis_outputs WHERE run_id=?',(identity,))])

    def history_output(self,result):
        identity=result.get('datasetId')
        if not identity:return digest(None)
        return self.signature([(t,f'SELECT * FROM {t} WHERE dataset_id=?',(identity,)) for t in
            ('tvl_chart_publications','tvl_chart_rows','tvl_chart_totals')])

    def stage(self,name,inputs,output,action):
        self.active=name
        return self.job.stage(name,inputs,output,action)

    def drain(self,saved,select,apply,key,*,dates=False):
        def checkpoint(state,result):self.job.checkpoint(state,result|{'phase':'partial'})
        return drain_pages(saved,
            select=select,apply=apply,key=key,budget=self.dates if dates else self.targets,
            checkpoint=checkpoint,batch_size=1 if dates else 500)

    def establish_bounds(self):
        if not self.job.bounds:
            with closing(connect(self.options.database)) as conn:
                self.job.set_bounds({c:resolve_bounds(conn,self.options,c,self.sources[c]) for c in self.options.chains})
        for chain,bounds in self.job.bounds.items():verify_bounds(self.options,chain,bounds,self.sources[chain])

    def acquire_reports(self,chain):
        bounds=self.job.bounds[chain]
        with closing(connect(self.options.database)) as conn:
            result=catchup.catch_up(conn,chain,bounds['fromBlock'],bounds['toBlock'],source=self.options.source,
                confirmations=self.options.confirmations.get(chain),chunk_size=self.options.chunk_size,
                discover=True,client=ProgressSource(self.sources[chain],self.job.emit))
        return result|{'checkpointStatus':'incomplete' if result['skipped_vaults'] else 'complete'}

    def acquire_catalog(self,chain):
        bounds=self.job.bounds[chain]
        with closing(connect(self.options.database)) as conn:
            summary=tvl_discovery.discover_catalog(conn,chain_ids=[CHAINS[chain].chain_id],
                from_block=bounds['fromBlock'],to_block=bounds['toBlock'],chunk_size=self.options.chunk_size)
        return dict(status=summary['status'],runId=summary['run_id'],catalogVaults=summary['catalog_vaults'],
                    sources=[{'source':s['source'],'status':s['status'],'failures':len(s.get('failures',[]))} for s in summary['sources']],
                    checkpointStatus='complete' if summary['status']=='complete' else 'incomplete')

    def acquire_tokenized(self,chain,saved):
        members={(p['chainId'],a.lower()) for p in PRODUCTS for a in p['memberAddresses'] if a!=p['earningsAddress']}
        inventory=[v for v in tokenized_fees.load_inventory() if (v['chain_id'],v['address']) in members
                   and v['chain_id']==CHAINS[chain].chain_id]
        bounds=self.job.bounds[chain]
        with closing(connect(self.options.database)) as conn:
            work=tokenized_fees.pending_tokenized_ranges(conn,inventory,chain,0,bounds['toBlock'],self.options.cutoff,
                                                       confirmations=self.options.confirmations.get(chain),chunk_size=self.options.chunk_size)
            def apply(rows):
                client=self.sources[chain].w3
                window=tokenized_fees.TokenizedWindow(client,chain,bounds['toBlock'],
                    self.options.cutoff,self.options.confirmations.get(chain))
                for row in rows:
                    tokenized_fees.index_tokenized_fees(conn,[row['vault']],chain,row['fromBlock'],row['toBlock'],
                        self.options.cutoff,chunk_size=row['toBlock']-row['fromBlock']+1,max_vaults=1,
                        confirmations=self.options.confirmations.get(chain),client=client,window=window)
            key=lambda r:(r['vault']['address'],r['fromBlock'])
            result=self.drain(saved,_page(work,key),apply,key)
        return result|{'maintainedVaults':len(inventory),'rangePolicy':'maintained-deployment-through-pin'}

    def project_fees(self,saved):
        with closing(connect(self.options.database)) as conn:
            def select(after,limit):return fees.canonical_fee_targets(conn,self.options.chains,limit,True,
                before_timestamp=self.options.cutoff,after=after)
            def apply(rows):fees.index_canonical_fees(conn,self.options.chains,retry_unresolved=True,
                before_timestamp=self.options.cutoff,report_keys=[(r['chain_id'],r['tx_hash'],r['log_index']) for r in rows])
            return self.drain(saved,select,apply,lambda r:(r['chain_id'],r['block_number'],r['tx_hash'],r['log_index']))

    def price_reports(self,saved):
        with closing(connect(self.options.database)) as conn:
            def select(after,limit):return pricing.report_pricing_targets(conn,source='yearn-prices',retry_missing=True,
                retry_statuses=('missing','pending','retryable'),chain_ids=set(self.ids),before_timestamp=self.options.cutoff,
                after=after,limit=limit)
            def apply(rows):pricing.price_unpriced_reports(conn,source='yearn-prices',fallback=None,onchain_fallbacks=False,
                retry_missing=True,retry_statuses=('missing','pending','retryable'),before_timestamp=self.options.cutoff,
                targets=[(r['chain_id'],r['asset'],r['block_timestamp']) for r in rows])
            return self.drain(saved,select,apply,lambda r:(r['block_timestamp'],r['chain_id'],r['asset']))

    def price_fees(self,saved):
        with closing(connect(self.options.database)) as conn:
            targets=fee_valuation.fee_pricing_work(conn,before_timestamp=self.options.cutoff,retry_missing=True,
                retry_statuses=('missing','pending','retryable'),chain_ids=set(self.ids)).targets
            def apply(rows):fee_valuation.price_fees(conn,before_timestamp=self.options.cutoff,
                limit=len(rows),retry_missing=True,targets=set(rows),
                retry_statuses=('missing','pending','retryable'),chain_ids=set(self.ids))
            return self.drain(saved,_page(targets,lambda r:r),apply,lambda r:r)

    def collect_tvl(self,saved):
        with closing(connect(self.options.database)) as conn:
            plan=tvl_plan(conn,self.options)
            work=[dict(chain=chain,day=day) for chain,day in plan['targets']]
            key=lambda r:(r['chain'],r['day'])
            def apply(rows):
                for row in rows:
                    self.job.emit('tvl-collection-started',chainId=row['chain'],timestamp=row['day'])
                    tvl_sources.collect_tvl(conn,row['day'],row['day'],chain_ids=[row['chain']],
                        price_source='yearn-prices',reader_factory=tvl_sources.ArchiveReader)
            result=self.drain(saved,_page(work,key),apply,key,dates=True)
            remaining=tvl_plan(conn,self.options)
        return result|{'unresolvedChainDates':remaining['targets'],'deferredRepairs':remaining['deferredRepairs'],
                       'expectedVaults':remaining['expectedVaults']}

    def source_ages(self):
        summaries={}
        with closing(connect(self.options.database,readonly=True)) as conn:
            available=table_exists(conn,'analytics_inputs')
            for kind in ('defillama','membership','fee-config','comparison-evidence'):
                selected=(analytics.selected_input(conn,kind) if available else
                          dict(id=None,status='unavailable',observed_at=None,body={}))
                retained=[]
                for row in selected['body'].get('vaults',selected['body'].get('snapshots',[])):
                    if not row.get('retainedAfterUnavailableRetry'):continue
                    observed=row.get('retainedObservedAt')
                    previous=row.get('retainedFromInputId')
                    if observed is None and previous:
                        saved=conn.execute('SELECT observed_at FROM analytics_inputs WHERE id=?',(previous,)).fetchone()
                        observed=saved[0] if saved else None
                    retained.append(observed)
                observed=selected['observed_at']
                known=[v for v in retained if v is not None]
                summaries[kind]=dict(inputId=selected['id'],availability=selected['status'],observedAt=observed,
                    ageSeconds=max(0,int(time.time())-observed) if observed is not None else None,
                    retainedRows=len(retained),oldestRetainedObservedAt=min(known) if known else None,
                    retainedAgeUnknown=bool(retained and len(known)!=len(retained)),
                    refreshPolicy='retained-manual-evidence' if kind=='comparison-evidence' else 'owned-collector')
        return summaries

    def refresh_analytics_sources(self):
        try:
            attempt=analytics_collect.collect(self.options.database,progress=lambda _:None)
        except (requests.RequestException,ValueError,KeyError,IndexError) as exc:
            return {'attempt':{'status':'unavailable','failureClass':type(exc).__name__},
                    'sources':self.source_ages(),'checkpointStatus':'incomplete'}
        return {'attempt':attempt,'sources':self.source_ages()}

    def run_analysis(self,kind):
        with closing(connect(self.options.database)) as conn:
            identity=(run_lifetime_yield(conn,price_source='yearn-prices',fallback_price_source=None,
                                        before_timestamp=self.options.cutoff) if kind=='earnings' else
                      fee_valuation.run_fee_usd(conn,before_timestamp=self.options.cutoff,
                                               deferred_manifest=self.options.deferred_manifest))
        return {'runId':identity}

    def manifest(self,result):
        identity=result['datasetId']
        return json.loads((self.root/'tvl'/'datasets'/f'{identity}.json').read_text())

    def make_tvl_manifest(self):
        manifest=tvl_api.publish_tvl(self.options.database,self.root/'tvl',
            current_bridge_policy='retired-registry',before_timestamp=self.options.cutoff,
            observation_revision=self.tvl(global_scope=True))
        return {'datasetId':manifest['datasetId'],'asOfTimestamp':manifest['asOfTimestamp']}

    def prepare_history(self,tvl):
        dataset=tvl_api.TvlDataset(self.manifest(tvl))
        receipt=tvl_history_cache.prepare(dataset,progress=lambda message:self.job.emit('history-progress',detail=message))
        with closing(connect(self.options.database,readonly=True)) as conn:
            gaps=[dict(timestamp=r[0],unavailableRows=r[1]) for r in conn.execute('''SELECT timestamp,count(*)
                FROM tvl_chart_rows WHERE dataset_id=? AND (tvl_usd IS NULL OR external_tvl_usd IS NULL)
                GROUP BY timestamp ORDER BY timestamp''',(dataset.id,))]
        return {'datasetId':dataset.id,'preparation':receipt,'unavailableHistoryDates':gaps}

    def make_pairing(self,earnings,fee_usd):
        pair=select_pairing(self.options.database,earnings['runId'],fee_usd['runId'],self.root/'fees')
        return {'datasetId':pair['datasetId'],'earningsRunId':earnings['runId'],'feesRunId':fee_usd['runId']}

    def prepare_analytics(self,pair,tvl):
        financial=PairingDataset(self.options.database,pair['earningsRunId'],pair['feesRunId'])
        dataset=tvl_api.TvlDataset(self.manifest(tvl))
        identity=analytics.prepare(self.options.database,financial,dataset,select=False)
        meta=analytics.AnalyticsStore(self.options.database).load(identity)['meta']
        return dict(publicationId=identity,feesDatasetId=meta['feesDatasetId'],tvlDatasetId=meta['tvlDatasetId'],
                    cutoffTimestamp=meta['cutoffTimestamp'],tvlAsOfTimestamp=meta['tvlAsOfTimestamp'])

    def analytics_output(self,result):
        if 'publicationId' not in result:return digest(None)
        return self.signature([('analytics_publications','SELECT * FROM analytics_publications WHERE id=?',(result['publicationId'],))])

    def processing_gaps(self):
        with closing(connect(self.options.database,readonly=True)) as conn:
            report_counts={r[0]:r[1] for r in conn.execute(f'''SELECT coalesce(f.status,'not-indexed'),count(*)
                FROM strategy_reports r LEFT JOIN canonical_fee_reports f ON f.chain_id=r.chain_id
                AND f.tx_hash=r.tx_hash AND f.report_log_index=r.log_index WHERE r.block_timestamp<?
                AND r.chain_id IN ({self.marks}) GROUP BY coalesce(f.status,'not-indexed')''',(self.options.cutoff,*self.ids))}
            price_counts={r[0]:r[1] for r in conn.execute(f'''SELECT status,count(*) FROM (
                SELECT DISTINCT r.chain_id,r.asset,r.block_timestamp,coalesce(p.status,'not-priced') AS status
                FROM strategy_reports r LEFT JOIN prices p ON p.chain_id=r.chain_id AND p.token_address=r.asset
                AND p.timestamp=r.block_timestamp AND p.source='yearn-prices'
                WHERE r.block_timestamp<? AND r.chain_id IN ({self.marks})) targets GROUP BY status''',
                (self.options.cutoff,*self.ids))}
            fee_work=fee_valuation.fee_pricing_work(conn,before_timestamp=self.options.cutoff,refresh=True,chain_ids=set(self.ids))
            cache=fee_valuation.cached_prices(conn)
            fee_counts={}
            for target in fee_work.targets:
                status=cache.get(target,{}).get('status','not-priced')
                fee_counts[status]=fee_counts.get(status,0)+1
            tvl_gaps=tvl_plan(conn,self.options)
        return dict(canonicalReports=report_counts,reportPrices=price_counts,feePrices=fee_counts,
                    unpriceableFeeEvents=fee_work.metadata_gaps,
                    unresolvedTvlChainDates=tvl_gaps['targets'],deferredTvlRepairs=tvl_gaps['deferredRepairs'])

    def pipeline(self,mode):
        self.job.begin_pass()
        for chain in self.options.chains:
            self.stage('reports:'+chain,lambda c=chain:self.job.bounds[c],lambda _,c=chain:self.reports(chain=c),
                       lambda _,c=chain:self.acquire_reports(c))
            self.stage('catalog:'+chain,lambda c=chain:dict(bounds=self.job.bounds[c],freshnessBucket=int(time.time())//DAY),
                       lambda _,c=chain:self.catalog(chain=c),lambda _,c=chain:self.acquire_catalog(c))
            self.stage('tokenized:'+chain,lambda c=chain:self.job.bounds[c],
                       lambda r,c=chain:self.tokenized(r.get('phase')=='partial',chain=c),lambda saved,c=chain:self.acquire_tokenized(c,saved))
        self.stage('canonical-fees',lambda:self.reports(),lambda r:self.canonical(r.get('phase')=='partial'),self.project_fees)
        self.stage('report-prices',lambda:self.reports(),lambda r:self.prices(r.get('phase')=='partial'),self.price_reports)
        self.stage('fee-prices',lambda:[self.canonical(),self.tokenized(),self.reports()],
                   lambda r:self.fee_prices(r.get('phase')=='partial'),self.price_fees)
        self.stage('tvl-collection',lambda:self.catalog(),lambda r:self.tvl(r.get('phase')=='partial'),self.collect_tvl)
        self.stage('analytics-sources',lambda:[self.catalog(global_scope=True),int(time.time())//DAY],
                           lambda _:self.analytics_inputs(),lambda _:self.refresh_analytics_sources())
        gaps=self.processing_gaps()
        result=dict(cutoff=self.options.cutoff,refreshedChains=list(self.options.chains),
                    analysisScope='retained-database-through-cutoff',gaps=gaps,analyticsSources=self.source_ages())
        if mode=='acquire':return result
        earnings=self.stage('earnings',lambda:[self.reports(global_scope=True),self.prices(global_scope=True)],
                            self.analysis_output,lambda _:self.run_analysis('earnings'))
        fee_usd=self.stage('fee-usd',lambda:[self.reports(global_scope=True),self.canonical(global_scope=True),
                                           self.tokenized(),self.fee_prices()],self.analysis_output,lambda _:self.run_analysis('fees'))
        pair=self.stage('pairing',lambda:[earnings,fee_usd],lambda r:_files(self.root/'fees',r.get('datasetId')),
                        lambda _:self.make_pairing(earnings,fee_usd))
        tvl=self.stage('tvl-manifest',lambda:[self.tvl(global_scope=True),self.catalog(global_scope=True)],
                       lambda r:_files(self.root/'tvl',r.get('datasetId')),lambda _:self.make_tvl_manifest())
        history=self.stage('history',lambda:[tvl,self.tvl(global_scope=True)],self.history_output,lambda _:self.prepare_history(tvl))
        owned=self.stage('analytics',lambda:[pair,tvl,history,self.analytics_inputs()],
                         self.analytics_output,lambda _:self.prepare_analytics(pair,tvl))
        incomplete=[r['name'] for r in self.job.conn.execute(
            "SELECT name FROM refresh_stages WHERE job_id=? AND name<>'publication' AND status<>'complete'",(self.job.id,))]
        ready=(not incomplete and all(b['cutoffReached'] for b in self.job.bounds.values())
               and owned['cutoffTimestamp']==self.options.cutoff and owned['tvlAsOfTimestamp']==self.options.cutoff-1
               and owned['feesDatasetId']==pair['datasetId'] and owned['tvlDatasetId']==tvl['datasetId'])
        return result|dict(earningsRunId=earnings['runId'],feesRunId=fee_usd['runId'],
            feeDatasetId=pair['datasetId'],tvlDatasetId=tvl['datasetId'],analyticsPublicationId=owned['publicationId'],
            tvlAsOfTimestamp=tvl['asOfTimestamp'],history=history,publishReady=ready,incompleteStages=incomplete)

    def publish_prepared(self,result):
        if not result.get('publishReady'):raise ValueError('prepared datasets are incomplete or do not match the requested cutoff')
        for chain,bounds in self.job.bounds.items():verify_bounds(self.options,chain,bounds,self.sources[chain])
        import hashlib
        expected=hashlib.sha256(release_encoded(dict(feesDatasetId=result['feeDatasetId'],tvlDatasetId=result['tvlDatasetId'],
            analyticsPublicationId=result['analyticsPublicationId'])).encode()).hexdigest()
        self.job.checkpoint({'expectedReleaseId':expected},result|{'expectedReleaseId':expected})
        try:
            release=publish(self.options.database,self.root/'fees',self.root/'tvl',result['analyticsPublicationId'])
            return result|{'releaseId':release,'publication':'selected','consolidation':'complete'}
        except PublicationCleanupError as exc:
            return result|{'releaseId':exc.release_id,'publication':'selected','consolidation':'failed'}
        except Exception:
            # Selection may have committed before a connection failure was
            # observed. Inspect authoritative state before calling it unselected.
            if selection(self.options.database)==expected:
                return result|{'releaseId':expected,'publication':'selected','consolidation':'unknown'}
            raise

    def recover_selection(self,result):
        if selection(self.options.database)!=result['releaseId']:
            raise ValueError('the serving release changed after this refresh; inspect the current selection before recovery')
        if result['consolidation']!='complete':
            try:canonical_history.consolidate(self.options.database,apply=True)
            except Exception:
                self.job.finish('selected-cleanup-failed',result|{'consolidation':'failed'})
                return self.job.receipt()
        self.job.finish('selected',result|{'consolidation':'complete'})
        return self.job.receipt()


def run_refresh(options,*,mode='prepare',resume=None,progress=None):
    if mode not in ('acquire','prepare','publish'):raise ValueError('select acquire, prepare or publish')
    with RefreshState(options.database,context(options),None if resume else {},resume=resume,progress=progress,
                      initial_release=lambda:selection(options.database)) as job:
        worker=None
        result={}
        try:
            with closing(connect(options.database)) as conn:init_db(conn)
            previous=observed_publication(job) or job.receipt()['result']
            if previous.get('publication')=='selected':
                result=previous
                worker=RefreshWorker(options,job)
                if previous.get('consolidation')!='complete':job.finish('selected-cleanup-failed',previous)
                return worker.recover_selection(previous) if mode=='publish' else job.receipt()
            worker=RefreshWorker(options,job)
            worker.establish_bounds()
            for _ in range(3):
                if context(options)!=job.context:raise ValueError('refresh code or configuration changed; start a new job')
                result=worker.pipeline(mode)
                changed=job.changed_stages()
                if changed:
                    job.emit('upstream-outputs-changed',stages=changed)
                    continue
                if mode=='publish':
                    worker.active='publication'
                    if context(options)!=job.context:raise ValueError('refresh code or configuration changed; start a new job')
                    # The publication action is checkpointed separately so a
                    # post-selection cleanup error can never look unselected.
                    prepared=result
                    result=job.stage('publication',lambda:prepared,lambda _:digest(selection(options.database)),
                                     lambda _:worker.publish_prepared(prepared))
                    job.finish('selected' if result['consolidation']=='complete' else 'selected-cleanup-failed',result)
                else:job.finish('acquired' if mode=='acquire' else 'prepared',result)
                return job.receipt()
            raise ValueError('upstream refresh outputs did not stabilize; retry after the other writer finishes')
        except WorkDeferred:
            return job.receipt()
        except BaseException as exc:
            observed=observed_publication(job)
            if observed:
                job.finish('selected-cleanup-failed',observed)
            else:
                job.finish('failed',result|{'failedStage':worker.active if worker else 'initialization',
                    'failureClass':type(exc).__name__,'servingReleaseId':selection(options.database)})
            job.emit('refresh-failed',stage=worker.active if worker else 'initialization',failureClass=type(exc).__name__)
            if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
            return job.receipt()
