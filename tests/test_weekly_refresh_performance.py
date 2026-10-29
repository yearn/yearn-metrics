"""Recent windows and bounded parallel I/O must preserve coverage and money."""
from contextlib import closing
from dataclasses import replace
import json
from threading import Event, Lock, get_ident
from types import SimpleNamespace

import pytest

from yearn_data import catchup, refresh
from yearn_data.coverage import Scope, commit_range
from yearn_data.history_sources import report_scope
from yearn_data.refresh_scope import RefreshOptions, resolve_bounds, since_receipt, tvl_plan
from yearn_data.storage import connect
from test_refresh_scope import db as db, Source as BoundSource
from test_catchup import db as catchup_db, Source as ReportSource, VAULT
from test_refresh_workflow import workflow as workflow

__all__ = ["catchup_db"]


def test_weekly_window_does_not_repair_old_holes_or_use_global_maximum(db, tmp_path):
    cutoff=20*86400
    commit_range(db,Scope(1,'a','reports'),10,20,source='rpc',end_hash='old',run_id=None,write=lambda c:0)
    commit_range(db,Scope(1,'b','reports'),1500,1550,source='rpc',end_hash='recent',run_id=None,write=lambda c:0)
    options=RefreshOptions('staging',('eth',),cutoff,tmp_path,source='rpc')
    bounds=resolve_bounds(db,options,'eth',BoundSource(head=2000))
    assert bounds['fromBlock']==1124  # First block at/after day 13, not global MAX+1=1551.
    assert bounds['toBlock']==1727
    assert bounds['startReason']=='weekly-window'
    db.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES(1,'v3',?,1)",(VAULT,));db.commit()
    plan=tvl_plan(db,options)
    assert plan['targets']==[(1,day*86400-1) for day in range(14,21)]
    assert len(tvl_plan(db,replace(options,first_close=86399))['targets'])==20
    assert resolve_bounds(db,replace(options,repair_history=True),'eth',BoundSource(head=2000))['fromBlock']==10


def test_skipped_week_warns_per_vault_without_expanding_window_or_blocking_publication(db,tmp_path):
    from yearn_data.refresh_warnings import findings
    from yearn_data.github_warning_report import render
    addresses=[VAULT,'0x'+'55'*20]
    scopes=[]
    for address in addresses:
        db.execute("""INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,updated_at)
            VALUES(1,'v3',?,?,6,0)""",(address,'0x'+'66'*20))
        scopes.append(report_scope(dict(db.execute('SELECT * FROM vaults WHERE address=?',(address,)).fetchone())))
    db.commit()
    commit_range(db,scopes[0],10,20,source='rpc',end_hash='old',run_id=None,write=lambda c:0)
    commit_range(db,scopes[1],10,1500,source='rpc',end_hash='recent',run_id=None,write=lambda c:0)
    # Old metadata policies must not produce false gaps for a currently covered vault.
    commit_range(db,Scope(1,addresses[1],'reports','obsolete'),10,20,source='rpc',end_hash='old',run_id=None,write=lambda c:0)
    options=RefreshOptions('staging',('eth',),20*86400,tmp_path,source='rpc')
    bounds=resolve_bounds(db,options,'eth',BoundSource(head=2000))
    assert (bounds['fromBlock'],bounds['toBlock'])==(1124,1727)
    assert bounds['deferredReportRanges']==[dict(target=VAULT,policy=scopes[0].policy,fromBlock=21,toBlock=1123)]
    result=dict(cutoff=options.cutoff,gaps={},publishReady=True,publicationBlockers=[])
    warnings=findings([],result,{'eth':bounds})
    assert warnings==[dict(code='report-coverage-gap',stage='reports:eth',
        message='Retained report coverage has unscanned block ranges before the acquisition window.',
        details=bounds['deferredReportRanges'])]
    receipt=dict(jobId='skipped-week',createdAt=1,status='prepared',context=dict(database='staging',
        cutoff=options.cutoff,chains=['eth']),result=result|dict(warnings=warnings))
    rendered=render(receipt)
    assert VAULT in rendered and '21–1123' in rendered and '1 vault report ranges' in rendered
    assert result['publishReady'] and not result['publicationBlockers']
    assert resolve_bounds(db,replace(options,repair_history=True),'eth',BoundSource(head=2000))['deferredReportRanges']==[]
    # Newer weekly coverage cannot hide the old hole in a later run's warning.
    commit_range(db,scopes[0],1124,1727,source='rpc',end_hash='weekly',run_id=None,write=lambda c:0)
    later=resolve_bounds(db,replace(options,cutoff=21*86400),'eth',BoundSource(head=2000))
    assert later['fromBlock']==1210
    assert later['deferredReportRanges']==bounds['deferredReportRanges']
    # A separate catch-up clears the warning without widening the next weekly run.
    commit_range(db,scopes[0],21,1123,source='rpc',end_hash='repaired',run_id=None,write=lambda c:0)
    repaired=resolve_bounds(db,options,'eth',BoundSource(head=2000))
    assert repaired['fromBlock']==1124 and repaired['deferredReportRanges']==[]


def test_since_receipt_covers_a_missed_week_and_rejects_incomplete_baseline(workflow,monkeypatch):
    options,_=workflow
    first=refresh.run_refresh(options)
    assert first['status']=='prepared' and first['result']['publishReady']
    next_options=replace(options,from_blocks={},first_close=None,cutoff=options.cutoff+14*86400,
                         baseline_job=first['jobId'])
    scoped=since_receipt(next_options)
    assert scoped.from_blocks=={'eth':first['bounds']['eth']['toBlock']+1}
    assert scoped.first_close==options.cutoff+86399
    with closing(connect('staging')) as conn:
        assert len(tvl_plan(conn,scoped)['targets'])==14
    continued_options=replace(next_options,cutoff=options.cutoff+86400)
    events=[]
    continued=refresh.run_refresh(continued_options,progress=events.append)
    assert continued['status']=='prepared' and continued['result']['publishReady']
    assert [e['timestamp'] for e in events if e['event']=='tvl-collection-started']==[options.cutoff+86399]
    resumed=refresh.run_refresh(continued_options,resume=continued['jobId'])
    assert resumed['result']['tvlDatasetId']==continued['result']['tvlDatasetId']
    changed=BoundSource();changed.changed=True
    monkeypatch.setattr(refresh,'chain_source',lambda _:changed)
    worker=refresh.RefreshWorker(scoped,SimpleNamespace(id='new',bounds={}))
    with pytest.raises(ValueError,match='hash changed'):worker.establish_bounds()
    with closing(connect('staging')) as conn:
        assert len(tvl_plan(conn,scoped)['targets'])==13
        with conn:
            conn.execute("UPDATE refresh_jobs SET result_json=? WHERE id=?",(json.dumps({'publishReady':False}),first['jobId']))
    with pytest.raises(ValueError,match='publish-ready'):since_receipt(next_options)
    with pytest.raises(ValueError,match='combined'):since_receipt(replace(next_options,from_blocks={'eth':0}))


def test_parallel_ranges_overlap_but_all_database_work_stays_on_caller(catchup_db,monkeypatch):
    entered=Event();both=Event();third=Event();guard=Lock();threads=set();main=get_ident()
    original=catchup_db.execute
    def execute(*args,**kwargs):
        assert get_ident()==main
        return original(*args,**kwargs)
    monkeypatch.setattr(catchup_db,'execute',execute)
    source=ReportSource();native=source.reports
    def reports(vault,lo,hi,selected):
        with guard:
            threads.add(get_ident())
            if entered.is_set():both.set()
            entered.set()
        assert both.wait(3),'report fetches did not overlap'
        if lo==10:assert third.wait(3),'slow first request prevented refilling a free worker'
        if lo==30:third.set()
        return native(vault,lo,hi,selected)
    monkeypatch.setattr(source,'reports',reports)
    result=catchup.catch_up(catchup_db,'op',10,39,chunk_size=10,workers=2,client=source)
    assert len(threads)==2
    assert result['completed_ranges']==3 and result['inserted_rows']==1
    assert catchup_db.execute('SELECT gain_raw FROM strategy_reports').fetchone()[0]=='12'
    source.calls.clear()
    assert catchup.catch_up(catchup_db,'op',10,39,chunk_size=10,workers=2,client=source)['completed_ranges']==0
    assert source.calls==[]


def test_provider_range_limit_splits_but_failures_never_certify_empty_coverage(catchup_db,monkeypatch):
    source=ReportSource();native=source.reports;failed=True;calls=[]
    def reports(vault,lo,hi,selected):
        calls.append((lo,hi))
        if hi-lo>4:raise ValueError('block range too wide')
        if failed and lo<=25<=hi:raise RuntimeError('provider unavailable')
        return native(vault,lo,hi,selected)
    monkeypatch.setattr(source,'reports',reports)
    with pytest.raises(RuntimeError,match='provider unavailable'):
        catchup.catch_up(catchup_db,'op',10,30,chunk_size=21,workers=2,client=source)
    assert catchup_db.execute('SELECT count(*) FROM history_coverage').fetchone()[0]==0
    failed=False
    result=catchup.catch_up(catchup_db,'op',10,30,chunk_size=21,workers=2,client=source)
    assert result['completed_ranges']==1 and result['inserted_rows']==1
    assert len(calls)>2
    assert catchup_db.execute('SELECT gain_raw FROM strategy_reports').fetchone()[0]=='12'


def test_financial_analyses_use_real_pipeline_and_keep_known_outputs(workflow,monkeypatch):
    options,_=workflow
    prepared=refresh.run_refresh(options)
    assert prepared['status']=='prepared'
    # Native full workflow now exercises both calculations in real libpq pipeline mode.
    with closing(connect('staging',readonly=True)) as conn:
        earnings=conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'",
                              (prepared['result']['earningsRunId'],)).fetchone()[0]
        from decimal import Decimal
        assert Decimal(json.loads(earnings)['net_yield_usd'])==2
    entered=[]
    def analysis(conn,**kwargs):
        from psycopg.pq import PipelineStatus
        assert conn.raw.pgconn.pipeline_status != PipelineStatus.OFF
        entered.append(True)
        raise RuntimeError('interrupted analysis')
    monkeypatch.setattr(refresh,'run_lifetime_yield',analysis)
    monkeypatch.setattr(refresh,'chain_source',lambda _:None)
    worker=refresh.RefreshWorker(options,SimpleNamespace(id='failure'))
    with pytest.raises(RuntimeError,match='interrupted'):worker.run_analysis('earnings')
    assert entered==[True]


def test_parallel_failure_keeps_completed_ranges_and_retries_only_the_failure(catchup_db,monkeypatch):
    source=ReportSource();native=source.reports;release=Event()
    execute=catchup_db.execute
    def commit_signal(statement,params=()):
        result=execute(statement,params)
        if statement.startswith('INSERT INTO history_coverage') and params[4]==30:release.set()
        return result
    monkeypatch.setattr(catchup_db,'execute',commit_signal)
    def reports(vault,lo,hi,selected):
        if lo==20:
            assert release.wait(3)
            raise RuntimeError('provider unavailable')
        return native(vault,lo,hi,selected)
    monkeypatch.setattr(source,'reports',reports)
    with pytest.raises(RuntimeError,match='provider unavailable'):
        catchup.catch_up(catchup_db,'op',10,39,chunk_size=10,workers=3,client=source)
    assert {tuple(r) for r in catchup_db.execute('SELECT from_block,to_block FROM history_coverage')}=={(10,19),(30,39)}
    monkeypatch.setattr(source,'reports',native)
    assert catchup.catch_up(catchup_db,'op',10,39,chunk_size=10,workers=3,client=source)['completed_ranges']==1
    assert catchup_db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0]==1


def test_cli_exposes_weekly_window_and_provider_tuning_without_starting_a_run(monkeypatch,tmp_path,capsys):
    from yearn_data import cli
    monkeypatch.setattr(cli,'load_environment',lambda _:None)
    captured=[]
    def run(options,**kwargs):
        captured.append(options)
        return {'status':'prepared'}
    monkeypatch.setattr(refresh,'run_refresh',run)
    assert cli.main(['--db','staging','refresh','--chains','eth','op','--cutoff','172800',
        '--publication-root',str(tmp_path),'--since','baseline','--window-days','14',
        '--report-workers','2','--chain-chunk-size','op:250000'])==0
    options=captured[0]
    assert options.baseline_job=='baseline' and options.window_days==14 and options.report_workers==2
    assert options.report_chunk_size('op')==250000 and options.report_chunk_size('eth')==1000000
    assert replace(options,chain_chunk_sizes={}).report_chunk_size('op')==50000
    assert replace(options,chunk_size=100,chain_chunk_sizes={}).report_chunk_size('op')==100
