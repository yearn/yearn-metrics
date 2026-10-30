"""The maintained worker runs real accounting/preparation against offline sources."""
from contextlib import closing
from decimal import Decimal
import json
from types import SimpleNamespace

from hexbytes import HexBytes
import pytest

from yearn_data import analytics,pricing,refresh,tvl_discovery,tvl_sources
from yearn_data.config import CHAINS
from yearn_data.history_sources import HistoricalSource
from yearn_data.indexing import normalize_strategy_report
from yearn_data.refresh_scope import RefreshOptions
from yearn_data.storage import connect,init_db
from yearn_data.hosted_publications import Registry

VAULT='0x'+'11'*20
ASSET='0x'+'22'*20
STRATEGY='0x'+'33'*20
CUTOFF=172800
HASH='0x'+'ab'*32


class Source(HistoricalSource):
    def __init__(self,chain):
        super().__init__(chain,SimpleNamespace(eth=SimpleNamespace(chain_id=CHAINS[chain].chain_id,block_number=1000,
            get_block=lambda n:dict(number=1000 if n=='finalized' else n,hash=HexBytes(HASH),
                                    timestamp=(1000 if n=='finalized' else n)*1000))))
    def inventory(self,*args):return []
    def reports(self,vault,first,last,source):
        if not first<=1<=last:return []
        return [normalize_strategy_report(self.chain_id,'v3',vault['address'],vault['asset'],6,
            dict(transactionHash='0x'+vault['address'][2:]+'00'*12,logIndex=0,blockNumber=1),1000,
            dict(strategy=STRATEGY,gain=1000000,loss=0,current_debt=0,
                 protocol_fees=20000,total_fees=100000,total_refunds=0))]


class Reader:
    def __init__(self,chain):pass
    def block_at(self,timestamp):return timestamp//1000
    def exists(self,address,block):return True
    def uint(self,address,signature,block,arg=None):return 100000000
    def debt(self,*args):return 0


@pytest.fixture
def workflow(postgres_database,tmp_path,monkeypatch):
    with closing(connect('staging')) as conn:
        init_db(conn)
        with conn:
            conn.execute('''INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,api_version,
                management,active,updated_at) VALUES(1,'v3',?,?,6,'3.0.2','yearn',0,0)''',(VAULT,ASSET))
            conn.execute('''INSERT INTO tvl_vaults(chain_id,version,address,asset,asset_decimals,api_version,
                name,active,updated_at) VALUES(1,'v3',?,?,6,'3.0.2','Offline fixture',0,0)''',(VAULT,ASSET))
    monkeypatch.setattr(refresh,'chain_source',Source)
    monkeypatch.setattr(refresh.tvl_batch,'reader',lambda conn, chain, **kwargs:Reader(chain))
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'data':{'vaults':[]}})
    monkeypatch.setattr(tvl_sources.requests,'post',lambda *a,**k:response)
    def empty(source):return dict(source=source,status='complete',discovered=0,enriched=0,failures=[])
    monkeypatch.setattr(tvl_discovery,'discover_v1',lambda *a,**k:empty('v1'))
    monkeypatch.setattr(tvl_discovery,'discover_curation',lambda *a,**k:empty('curation'))
    monkeypatch.setattr(tvl_discovery,'discover_adapter_relations',lambda *a,**k:empty('relations'))
    def quote(chain,asset,timestamp):return 2,'ok',{'provider':'yearn-prices','normalized_timestamp':timestamp}
    monkeypatch.setattr(pricing,'fetch_yearn_prices_batch',lambda batch:{t:quote(*t) for t in batch})
    monkeypatch.setattr(pricing,'fetch_yearn_price',quote)
    monkeypatch.setattr(refresh.fee_valuation.pricing,'fetch_yearn_prices_batch',lambda batch:{t:quote(*t) for t in batch})
    monkeypatch.setattr(refresh.fee_valuation.pricing,'fetch_yearn_price',quote)
    monkeypatch.setattr(tvl_sources,'fetch_yearn_price',quote)
    acquired=[]
    def inputs(database,progress):
        acquired.append('analytics')
        with closing(connect(database)) as conn:
            conn.executescript(analytics.SCHEMA)
            scope=[(r[0],r[1]) for r in conn.execute('SELECT chain_id,lower(address) FROM tvl_vaults')]
            reference=analytics.put_input(conn,'defillama',{'snapshots':[],'failedProtocols':['offline-fixture']},'partial',1)
            member=analytics.put_input(conn,'membership',{'vaults':[{'chainId':c,'address':a,'included':True} for c,a in scope]},'available',1)
            fee=analytics.put_input(conn,'fee-config',{'vaults':[{'chainId':c,'address':a,'performanceFee':1000,
                'managementFee':0,'fixedRateSupported':True,'availability':'available'} for c,a in scope]},'available',1)
        return dict(referenceId=reference,membershipId=member,configurationId=fee,expectedVaults=len(scope))
    monkeypatch.setattr(refresh.analytics_collect,'collect',inputs)
    options=RefreshOptions('staging',('eth',),CUTOFF,tmp_path/'publication',from_blocks={'eth':0},
                           first_close=86399,source='rpc')
    return options,acquired


def test_prepare_is_inspectable_then_publish_resumes_the_same_verified_job(workflow):
    options,acquired=workflow
    events=[]
    prepared=refresh.run_refresh(options,progress=events.append)
    assert prepared['status']=='prepared',prepared
    assert prepared['result']['publishReady'] is True,prepared
    assert refresh.selection('staging') is None
    assert acquired==['analytics']
    assert prepared['result']['gaps']['canonicalReports']=={'ok':1}
    assert prepared['result']['gaps']['reportPrices']=={'ok':1}
    assert [e['timestamp'] for e in events if e['event']=='tvl-collection-started']==[86399,172799]
    result=prepared['result']
    with closing(connect('staging',readonly=True)) as conn:
        earned=json.loads(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'",
            (result['earningsRunId'],)).fetchone()[0])
        fee=json.loads(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='fee_usd_events'",
            (result['feesRunId'],)).fetchone()[0])
        assert Decimal(earned['net_yield_usd'])==Decimal('2')
        assert Decimal(fee['total_fees_paid_usd'])==Decimal('0.2')
    published=refresh.run_refresh(options,mode='publish',resume=prepared['jobId'])
    assert published['status']=='selected',published
    assert acquired==['analytics']
    assert Registry('staging').selection()['releaseId']==published['result']['releaseId']
    assert published['result']['consolidation']=='complete'


def test_budget_resume_drains_all_canonical_report_and_fee_price_pages(workflow):
    from dataclasses import replace
    options,_=workflow
    options=replace(options,max_targets=500)
    with closing(connect('staging')) as conn,conn:
        for i in range(500):
            vault=f'0x{i+10000:040x}';asset=f'0x{i+20000:040x}'
            conn.execute('''INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,api_version,
                management,active,updated_at) VALUES(1,'v3',?,?,6,'3.0.2','yearn',0,0)''',(vault,asset))
            conn.execute('''INSERT INTO tvl_vaults(chain_id,version,address,asset,asset_decimals,api_version,
                active,updated_at) VALUES(1,'v3',?,?,6,'3.0.2',0,0)''',(vault,asset))
    result=refresh.run_refresh(options,mode='acquire')
    phases=[result['status']]
    identity=result['jobId']
    for _ in range(3):
        result=refresh.run_refresh(options,mode='acquire',resume=identity)
        phases.append(result['status'])
    assert phases==['work-deferred','work-deferred','work-deferred','acquired'],result
    stages={r['name']:r for r in result['stages']}
    for name in ('canonical-fees','report-prices','fee-prices'):
        assert stages[name]['result']=={'processed':501,'batches':2}
    assert result['result']['gaps']['canonicalReports']=={'ok':501}
    assert result['result']['gaps']['reportPrices']=={'ok':501}
    assert result['result']['gaps']['feePrices']=={'ok':501}
    with closing(connect('staging',readonly=True)) as conn:
        assert conn.execute('SELECT count(*) FROM tvl_snapshots').fetchone()[0]==1002
    assert refresh.selection('staging') is None


def test_source_failure_publishes_retained_evidence_with_visible_warnings(workflow,monkeypatch):
    import requests
    options,_=workflow
    first=refresh.run_refresh(options,mode='publish')
    assert first['status']=='selected',first
    previous=first['result']['releaseId']
    def unavailable(*a,**k):raise requests.ConnectionError('https://private-provider.invalid/secret-key')
    monkeypatch.setattr(refresh.analytics_collect,'collect',unavailable)
    failed=refresh.run_refresh(options,mode='publish')
    assert failed['status']=='selected',failed
    assert failed['result']['publishReady'] is True
    assert failed['result']['publicationBlockers']==[]
    assert failed['servingReleaseIdBefore']==previous
    assert refresh.selection('staging')==failed['result']['releaseId']
    assert any(w['stage']=='analytics-sources' for w in failed['result']['warnings'])
    report=options.publication_root/'refresh'/failed['jobId']/'warnings.json'
    assert json.loads(report.read_text())['warnings']==failed['result']['warnings']
    assert report.with_suffix('.html').exists()
    source=next(s for s in failed['stages'] if s['name']=='analytics-sources')
    assert source['status']=='incomplete'
    assert source['result']['sources']['membership']['observedAt']==1
    assert source['result']['sources']['membership']['ageSeconds']>0
    assert 'private-provider' not in json.dumps(failed) and 'secret-key' not in json.dumps(failed)


def test_selected_release_cleanup_failure_resumes_cleanup_without_republishing(workflow,monkeypatch):
    options,acquired=workflow
    original=refresh.canonical_history.consolidate
    def fail(*a,**k):raise RuntimeError('private cleanup details')
    monkeypatch.setattr(refresh.canonical_history,'consolidate',fail)
    failed=refresh.run_refresh(options,mode='publish')
    assert failed['status']=='selected-cleanup-failed',failed
    assert failed['result']['publication']=='selected'
    selected=failed['result']['releaseId']
    assert refresh.selection('staging')==selected
    assert 'private cleanup' not in json.dumps(failed)
    monkeypatch.setattr(refresh.canonical_history,'consolidate',original)
    monkeypatch.setattr(refresh,'publish',lambda *a,**k:pytest.fail('cleanup recovery republished'))
    recovered=refresh.run_refresh(options,mode='publish',resume=failed['jobId'])
    assert recovered['status']=='selected' and recovered['result']['consolidation']=='complete'
    assert recovered['result']['releaseId']==selected and acquired==['analytics']


@pytest.mark.parametrize('field,value,code', [
    ('feesDatasetId','f'*64,'prepared-dataset-mismatch'),
    ('cutoffTimestamp',CUTOFF-86400,'prepared-cutoff-mismatch'),
])
def test_publication_integrity_gate_rejects_wrong_analytics_identity_or_cutoff(workflow,monkeypatch,field,value,code):
    options,_=workflow
    original=refresh.RefreshWorker.prepare_analytics
    def wrong(self,*args):
        result=original(self,*args)
        return result|{field:value}
    monkeypatch.setattr(refresh.RefreshWorker,'prepare_analytics',wrong)
    result=refresh.run_refresh(options,mode='publish')
    assert result['status']=='failed' and result['result']['failedStage']=='publication'
    assert result['result']['publicationBlockers']==[{'code':code}]
    assert refresh.selection('staging') is None


def test_explicit_tvl_cutoff_retains_future_observations_through_publication_cleanup(workflow):
    options,_=workflow
    with closing(connect('staging')) as conn:
        conn.execute("INSERT INTO tvl_runs VALUES(90,1,1,'complete',?)",(json.dumps({'chain_ids':[10]}),))
        future=dict(chain_id=10,vault=VAULT,timestamp=259199,block_number=259,version='v3',
                    asset_units='10',tvl_usd='300',status='ok')
        conn.execute('INSERT INTO tvl_snapshots VALUES(90,10,?,?,259,?)',(VAULT,259199,json.dumps(future)))
        conn.commit()
    result=refresh.run_refresh(options,mode='publish')
    assert result['status']=='selected',result
    registry=Registry('staging')
    manifest=registry.manifest('tvl',result['result']['tvlDatasetId'])
    assert manifest['reportingCutoff']==CUTOFF and manifest['asOfTimestamp']==172799
    with closing(connect('staging',readonly=True)) as conn:
        assert json.loads(conn.execute('SELECT data_json FROM tvl_snapshots WHERE chain_id=10 AND timestamp=259199').fetchone()[0])['tvl_usd']=='300'
    assert registry.tvl(result['result']['tvlDatasetId']).dates()==(86399,172799)
    # Cleanup retains winning provenance while retiring superseded observations.
    # The earlier reporting period must remain preparable afterward.
    again=refresh.run_refresh(options,mode='prepare')
    assert again['status']=='prepared' and again['result']['publishReady'] is True,again


def test_interrupted_selection_recovers_durable_intent_then_remains_terminal(workflow,monkeypatch):
    options,acquired=workflow
    publish=refresh.publish
    def interrupted(*args,**kwargs):
        publish(*args,**kwargs)
        raise SystemExit('simulated process exit after selection')
    monkeypatch.setattr(refresh,'publish',interrupted)
    with pytest.raises(SystemExit):refresh.run_refresh(options,mode='publish')
    with closing(connect('staging',readonly=True)) as conn:
        identity=conn.execute('SELECT id FROM refresh_jobs').fetchone()[0]
        selected=refresh.selection('staging')
        # Simulate process death before the outer exception handler saved its
        # summary. The committed publication-stage intent remains authoritative.
    with closing(connect('staging')) as conn,conn:
        conn.execute("UPDATE refresh_jobs SET result_json='{}',status='running' WHERE id=?",(identity,))
    monkeypatch.setattr(refresh,'publish',lambda *a,**k:pytest.fail('selected release republished'))
    recovered=refresh.run_refresh(options,mode='publish',resume=identity)
    assert recovered['status']=='selected' and recovered['result']['releaseId']==selected
    assert recovered['result']['consolidation']=='complete' and acquired==['analytics']
    monkeypatch.setattr(refresh.canonical_history,'consolidate',lambda *a,**k:pytest.fail('completed cleanup repeated'))
    terminal=refresh.run_refresh(options,mode='publish',resume=identity)
    assert terminal['status']=='selected' and terminal['result']==recovered['result']


def test_tvl_date_budget_resumes_without_reacquiring_completed_closes(workflow):
    from dataclasses import replace
    options,acquired=workflow
    options=replace(options,max_dates=1)
    first_events=[]
    first=refresh.run_refresh(options,progress=first_events.append)
    assert first['status']=='work-deferred'
    assert [e['timestamp'] for e in first_events if e['event']=='tvl-collection-started']==[86399]
    resumed_events=[]
    resumed=refresh.run_refresh(options,resume=first['jobId'],progress=resumed_events.append)
    assert resumed['status']=='prepared' and resumed['result']['publishReady'] is True,resumed
    assert [e['timestamp'] for e in resumed_events if e['event']=='tvl-collection-started']==[172799]
    assert 'reports:eth' in [e['stage'] for e in resumed_events if e['event']=='stage-reused']
    assert acquired==['analytics'] and refresh.selection('staging') is None


def test_multichain_completed_producers_reuse_their_own_scope(workflow):
    from dataclasses import replace
    options,_=workflow
    options=replace(options,chains=('eth','op'),from_blocks={'eth':0,'op':0})
    with closing(connect('staging')) as conn,conn:
        for table in ('vaults','tvl_vaults'):
            conn.execute(f'''INSERT INTO {table}(chain_id,version,address,asset,asset_decimals,api_version,active,updated_at)
                VALUES(10,'v3',?,?,6,'3.0.2',0,0)''',(VAULT,ASSET))
    first=refresh.run_refresh(options,mode='acquire')
    assert first['status']=='acquired',first
    assert set(first['bounds'])=={'eth','op'}
    assert first['result']['gaps']['canonicalReports']=={'ok':2}
    assert first['result']['gaps']['reportPrices']=={'ok':2}
    events=[]
    resumed=refresh.run_refresh(options,mode='acquire',resume=first['jobId'],progress=events.append)
    assert resumed['status']=='acquired',resumed
    reused={e['stage'] for e in events if e['event']=='stage-reused'}
    assert {'reports:eth','reports:op','catalog:eth','catalog:op','tokenized:eth','tokenized:op'}<=reused
    assert not [e for e in events if e['event']=='range-fetch-started']
    with closing(connect('staging',readonly=True)) as conn:
        assert conn.execute('SELECT count(*) FROM tvl_snapshots').fetchone()[0]==4


def test_corrected_upstream_quote_invalidates_prepared_financial_outputs(workflow):
    options,_=workflow
    first=refresh.run_refresh(options)
    assert first['status']=='prepared',first
    with closing(connect('staging')) as conn,conn:
        conn.execute("UPDATE prices SET price_usd='3' WHERE source='yearn-prices'")
        conn.execute("UPDATE fee_daily_prices SET price_usd='3'")
    events=[]
    refreshed=refresh.run_refresh(options,resume=first['jobId'],progress=events.append)
    assert refreshed['status']=='prepared' and refreshed['result']['publishReady'] is True,json.dumps(refreshed)
    assert refreshed['result']['earningsRunId']!=first['result']['earningsRunId']
    assert 'earnings' in [e['stage'] for e in events if e['event']=='stage-invalidated']
    with closing(connect('staging',readonly=True)) as conn:
        earned=json.loads(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'",
            (refreshed['result']['earningsRunId'],)).fetchone()[0])
        assert Decimal(earned['net_yield_usd'])==Decimal('3')
    assert refresh.selection('staging') is None


def test_advancing_cutoff_collects_only_the_new_close_and_preserves_prepared_history(workflow):
    from dataclasses import replace
    options,_=workflow
    first=refresh.run_refresh(options)
    assert first['status']=='prepared',first
    events=[]
    advanced=refresh.run_refresh(replace(options,cutoff=CUTOFF+86400),progress=events.append)
    assert advanced['status']=='prepared' and advanced['result']['publishReady'] is True,advanced
    assert [e['timestamp'] for e in events if e['event']=='tvl-collection-started']==[259199]
    with closing(connect('staging',readonly=True)) as conn:
        for result,expected in ((first,[86399,172799]),(advanced,[86399,172799,259199])):
            rows=conn.execute('SELECT timestamp,tvl_usd FROM tvl_chart_rows WHERE dataset_id=? ORDER BY timestamp',
                (result['result']['tvlDatasetId'],)).fetchall()
            assert [r[0] for r in rows]==expected
            assert {Decimal(r[1]) for r in rows}=={Decimal('200')}
    assert refresh.selection('staging') is None


def test_failed_publication_can_prepare_and_publish_on_resume(workflow,monkeypatch):
    options,_=workflow
    publish=refresh.publish
    def unavailable(*a,**k):raise RuntimeError('temporary storage failure')
    monkeypatch.setattr(refresh,'publish',unavailable)
    failed=refresh.run_refresh(options,mode='publish')
    assert failed['status']=='failed' and failed['result']['failedStage']=='publication'
    assert refresh.selection('staging') is None
    monkeypatch.setattr(refresh,'publish',publish)
    prepared=refresh.run_refresh(options,resume=failed['jobId'])
    assert prepared['status']=='prepared' and prepared['result']['publishReady'] is True,prepared
    assert prepared['result']['incompleteStages']==[]
    selected=refresh.run_refresh(options,mode='publish',resume=failed['jobId'])
    assert selected['status']=='selected',selected
    assert selected['result']['releaseId']==refresh.selection('staging')


def test_manifest_reuse_and_corrected_tvl_inputs_reprepare_history(workflow):
    options,_=workflow
    first=refresh.run_refresh(options)
    assert first['status']=='prepared',first
    root=options.publication_root/'refresh'/first['jobId']/'tvl'
    old=json.loads((root/'datasets'/f"{first['result']['tvlDatasetId']}.json").read_text())
    # The existing-file path must return the full persisted manifest.
    again=refresh.tvl_api.publish_tvl('staging',root,current_bridge_policy='retired-registry',
        before_timestamp=CUTOFF,observation_revision=old['observationRevision'])
    assert again['datasetId']==old['datasetId'] and again['asOfTimestamp']==CUTOFF-1
    with closing(connect('staging')) as conn,conn:
        conn.execute("UPDATE tvl_snapshots SET data_json=jsonb_set(data_json::jsonb,'{tvl_usd}','\"300\"')::text")
    events=[]
    corrected=refresh.run_refresh(options,resume=first['jobId'],progress=events.append)
    assert corrected['status']=='prepared' and corrected['result']['publishReady'] is True,corrected
    assert corrected['result']['tvlDatasetId']!=first['result']['tvlDatasetId']
    assert 'tvl-manifest' in [e['stage'] for e in events if e['event']=='stage-invalidated']
    with closing(connect('staging',readonly=True)) as conn:
        for result,expected in [(first,Decimal('200')),(corrected,Decimal('300'))]:
            values={Decimal(r[0]) for r in conn.execute('SELECT tvl_usd FROM tvl_chart_rows WHERE dataset_id=?',
                    (result['result']['tvlDatasetId'],))}
            assert values=={expected}
    assert refresh.selection('staging') is None



def test_discovered_inventory_resume_keeps_financial_ids_and_ignores_bookkeeping(workflow,monkeypatch):
    from yearn_data.history_sources import inventory_targets
    options,_=workflow
    target=next(t for t in inventory_targets('eth') if t.version=='v3')
    def inventory(self,selected,first,last,source):
        if selected!=target or not first<=1<=last:return []
        return [dict(chain_id=1,version='v3',vault_address=VAULT,source_kind=target.kind,
            action='added',source_address=target.address,asset=ASSET,tx_hash='0x'+'44'*32,
            log_index=0,block_number=1,block_timestamp=1000,
            decoded_json=json.dumps(dict(vault=VAULT,api_version='3.0.2'))) ]
    monkeypatch.setattr(Source,'inventory',inventory)
    first=refresh.run_refresh(options)
    assert first['status']=='prepared',first
    with closing(connect('staging',readonly=True)) as conn:
        assert conn.execute('SELECT count(*) FROM vault_inventory_events').fetchone()[0]==1
    events=[]
    resumed=refresh.run_refresh(options,resume=first['jobId'],progress=events.append)
    assert resumed['status']=='prepared',resumed
    assert 'reports:eth' in [e['stage'] for e in events if e['event']=='stage-reused']
    fields=('earningsRunId','feesRunId','feeDatasetId','analyticsPublicationId')
    assert all(resumed['result'][k]==first['result'][k] for k in fields)
    worker=refresh.RefreshWorker(options,SimpleNamespace(id=first['jobId']))
    before=worker.reports()
    def mutate(sql,params=()):
        with closing(connect('staging')) as conn,conn:conn.execute(sql,params)
    mutate('UPDATE vaults SET updated_at=updated_at+60')
    assert worker.reports()==before
    mutate('UPDATE vaults SET asset_decimals=7')
    assert worker.reports()!=before
    mutate('UPDATE vaults SET asset_decimals=6')
    mutate("UPDATE strategy_reports SET gain_raw='2000000'")
    assert worker.reports()!=before
    mutate("UPDATE strategy_reports SET gain_raw='1000000'")
    events=[]
    published=refresh.run_refresh(options,mode='publish',resume=first['jobId'],progress=events.append)
    assert published['status']=='selected',published
    assert 'reports:eth' in [e['stage'] for e in events if e['event']=='stage-reused']
    assert all(published['result'][k]==first['result'][k] for k in fields)


def test_forced_tvl_repairs_report_actual_gaps_after_collection(workflow,monkeypatch):
    from dataclasses import replace
    options,_=workflow
    quote=tvl_sources.fetch_yearn_price
    def partly_unavailable(chain,asset,timestamp):
        if timestamp==86399:return None,'missing',{'reason':'offline-price-gap'}
        return quote(chain,asset,timestamp)
    monkeypatch.setattr(tvl_sources,'fetch_yearn_price',partly_unavailable)
    initial=refresh.run_refresh(options,mode='acquire')
    assert initial['status']=='acquired',initial
    expected=[dict(chainId=1,timestamp=86399,reason='price-or-ownership-unavailable')]
    assert initial['result']['gaps']['unresolvedTvlChainDates']==[]
    assert initial['result']['gaps']['deferredTvlRepairs']==expected
    events=[]
    repaired=refresh.run_refresh(replace(options,repair_history=True),mode='acquire',progress=events.append)
    assert repaired['status']=='acquired',repaired
    assert [e['timestamp'] for e in events if e['event']=='tvl-collection-started']==[86399,172799]
    assert repaired['result']['gaps']['unresolvedTvlChainDates']==[]
    assert repaired['result']['gaps']['deferredTvlRepairs']==expected
    stage=next(s for s in repaired['stages'] if s['name']=='tvl-collection')
    assert stage['result']['unresolvedChainDates']==[]
    assert stage['result']['deferredRepairs']==expected


def test_changed_code_rejects_saved_manifest_resume_before_stage_reuse(workflow,monkeypatch):
    from pathlib import Path
    from yearn_data import tvl_api
    from yearn_data.refresh_state import CheckpointRejected,read_receipt
    options,_=workflow
    def interrupted(*args):raise RuntimeError('interrupted after manifest')
    with monkeypatch.context() as patch:
        patch.setattr(refresh.RefreshWorker,'prepare_history',interrupted)
        failed=refresh.run_refresh(options)
    assert failed['status']=='failed',failed
    manifest_stage=next(s for s in failed['stages'] if s['name']=='tvl-manifest')
    assert manifest_stage['status']=='complete'
    identity=manifest_stage['result']['datasetId']
    path=options.publication_root/'refresh'/failed['jobId']/'tvl'/'datasets'/f'{identity}.json'
    saved=json.loads(path.read_text())
    source_path=Path(tvl_api.__file__)
    read_bytes=Path.read_bytes
    changed=read_bytes(source_path).replace(b'excluded = set()',b'excluded = set(())')
    original=read_receipt('staging',failed['jobId'])
    with monkeypatch.context() as patch:
        patch.setattr(Path,'read_bytes',lambda self:changed if self==source_path else read_bytes(self))
        assert saved['accountingRevision']!=tvl_api.accounting_revision()
        with pytest.raises(CheckpointRejected,match='code or configuration changed; start a new job'):
            refresh.run_refresh(options,resume=failed['jobId'])
        assert read_receipt('staging',failed['jobId'])==original
        events=[]
        fresh=refresh.run_refresh(options,progress=events.append)
        assert fresh['status']=='prepared',fresh
        assert fresh['result']['tvlDatasetId']!=identity
        assert not [e for e in events if e['event'] in ('range-fetch-started','tvl-collection-started')]


def test_incomplete_catalog_publishes_and_keeps_affected_vault_in_warning_report(workflow,monkeypatch):
    options,_=workflow
    native=refresh.tvl_discovery.discover_catalog
    def incomplete(*args,**kwargs):
        summary=native(*args,**kwargs)
        source=next(s for s in summary['sources'] if s['source']=='kong')
        source.update(status='incomplete',failures=[dict(chain_id=1,address=VAULT,
            stage='metadata',reason='missing_asset_metadata')])
        return summary|{'status':'incomplete'}
    monkeypatch.setattr(refresh.tvl_discovery,'discover_catalog',incomplete)
    events=[]
    selected=refresh.run_refresh(options,mode='publish',progress=events.append)
    assert selected['status']=='selected',selected
    result=selected['result']
    assert result['publishReady'] and result['publicationBlockers']==[]
    assert result['incompleteStages']==['catalog:eth']
    warning=next(w for w in result['warnings'] if w['stage']=='catalog:eth')
    finding=warning['details']['sources'][0]['findings'][0]
    assert finding['address']==VAULT and finding['reason']=='missing_asset_metadata'
    assert finding['vault_name']=='Offline fixture'
    assert finding['retained_asset']==ASSET and finding['retained_asset_decimals']==6
    assert any(e['event']=='refresh-findings' and e['warningCount'] for e in events)
    report=options.publication_root/'refresh'/selected['jobId']/'warnings.json'
    assert json.loads(report.read_text())['warnings']==result['warnings']
    assert VAULT in report.with_suffix('.html').read_text()
    assert Registry('staging').selection()['releaseId']==result['releaseId']


def test_aging_chain_head_is_visible_without_blocking_coherent_publication(workflow,monkeypatch):
    options,_=workflow
    class AgingSource(Source):
        def pin(self,*,end=None,confirmations=None,use_envio=True):
            return dict(number=100 if end is None else end,hash=HASH,
                        timestamp=(100 if end is None else end)*1000,finality='finalized')
    class AgingReader(Reader):
        def block_at(self,timestamp):return min(100,timestamp//1000)
    monkeypatch.setattr(refresh,'chain_source',AgingSource)
    monkeypatch.setattr(refresh.tvl_batch,'reader',lambda *args,**kwargs:AgingReader(1))
    selected=refresh.run_refresh(options,mode='publish')
    assert selected['status']=='selected',selected
    assert selected['bounds']['eth']['cutoffReached'] is False
    assert selected['result']['publicationBlockers']==[]
    warning=next(w for w in selected['result']['warnings'] if w['code']=='chain-freshness')
    assert warning['details']==dict(finalizedTimestamp=100000,cutoff=CUTOFF)
    assert selected['result']['cutoff']==CUTOFF
    assert selected['result']['tvlAsOfTimestamp']==CUTOFF-1
