"""Reuse must agree with a full rebuild, including corrections outside a backfill."""
from contextlib import closing
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from yearn_data.storage import connect, init_db
from yearn_data.tvl_api import TvlStore, TvlDataset
from yearn_data.tvl_history_cache import prepare, ready, totals_ready
from yearn_data.tvl_incremental import context_identity
from test_tvl_api import run, DAY, PARENT, CHILD, BRIDGE


def captured(dataset):
    with closing(connect('staging', readonly=True)) as conn:
        points = [tuple(r) for r in conn.execute('''SELECT timestamp,chain_id,vault,name,category,version,
            asset_units,tvl_usd,external_tvl_usd FROM tvl_chart_rows WHERE dataset_id=?
            ORDER BY timestamp,chain_id,vault''', (dataset.id,))]
        totals = [tuple(r) for r in conn.execute('''SELECT timestamp,group_by,mode,series,value
            FROM tvl_chart_totals WHERE dataset_id=? ORDER BY timestamp,group_by,mode,series''', (dataset.id,))]
    return points, totals


def full_copy(dataset, identity):
    manifest = deepcopy(dataset.manifest)
    manifest['datasetId'] = identity*64
    full = TvlDataset(manifest)
    result = prepare(full, full_rebuild=True)
    assert result['fullRebuildReason'] == 'explicit-full-rebuild'
    assert not result['reusedDates']
    assert captured(full) == captured(dataset)
    return full


def test_refresh_and_targeted_backfill_match_full_rows_totals_and_analytics(postgres_database, tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn)
        run(conn, 1, DAY, [(PARENT,'100'),(CHILD,'100')], [(CHILD,'50')])
        run(conn, 2, DAY+86400, [(PARENT,'200'),(CHILD,None)])
    old = TvlStore('staging', tmp_path/'pub').get()
    prepare(old)
    original = captured(old)
    with closing(connect('staging')) as conn:
        run(conn, 3, DAY+2*86400, [(PARENT,'0'),(CHILD,'0')])
    daily = TvlStore('staging', tmp_path/'pub').get()
    result = prepare(daily)
    assert result['reusedDates'] == [DAY,DAY+86400]
    assert result['recomputedDates'] == [DAY+2*86400]
    assert result['copiedRows'] == 4 and result['recomputedRowsWritten'] == 2
    assert result['totalsRowsRead'] == 2
    assert result['totalsRowsCopied'] > 0
    assert captured(old) == original
    assert daily.history(interval='daily')['chart'] == [
        {'timestamp':DAY,'Ethereum':150.0},
        {'timestamp':DAY+86400,'Ethereum':200.0},
        {'timestamp':DAY+2*86400,'Ethereum':0.0}]
    full_copy(daily, 'f')
    with closing(connect('staging')) as conn:
        # Both a captured price repair and changed ownership affect the whole
        # network frame. The repair is outside the explicitly forced backfill.
        run(conn, 4, DAY, [(CHILD,'120')], [(CHILD,'60')])
    repaired = TvlStore('staging', tmp_path/'pub').get()
    result = prepare(repaired, backfill=(DAY+86400,DAY+86400))
    assert result['recomputedDates'] == [DAY,DAY+86400]
    assert result['reusedDates'] == [DAY+2*86400]
    assert repaired.history(interval='daily')['chart'][0]['Ethereum'] == 160
    full = full_copy(repaired, 'e')
    points, _ = captured(repaired)
    # Unknown child valuation on day two stays None; final genuine zero stays 0.
    assert next(r for r in points if r[0]==DAY+86400 and r[2]==CHILD)[-2:] == (None,None)
    assert next(r for r in points if r[0]==DAY+2*86400 and r[2]==CHILD)[-2:] == ('0','0')
    from yearn_data.analytics import prepare as analytics, AnalyticsStore
    financial = SimpleNamespace(id='a'*64, cutoff=DAY+3*86400+1, fees=[], earnings=[], vault_names={})
    bodies = []
    for candidate in (repaired,full):
        identity = analytics('staging',financial,candidate,select=False)
        body = AnalyticsStore('staging').load(identity)
        body.pop('meta')
        bodies.append(json.dumps(body,sort_keys=True).replace(candidate.id,'TVL_DATASET'))
    assert bodies[0] == bodies[1]


def test_global_policy_change_rebuilds_and_catalog_touch_does_not(postgres_database, tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn)
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES(1,'v3',?,1)", (PARENT,))
        conn.commit()
        run(conn,1,DAY,[(PARENT,'100')])
    old = TvlStore('staging',tmp_path/'pub').get(); prepare(old)
    with closing(connect('staging')) as conn:
        conn.execute('UPDATE tvl_vaults SET updated_at=2');conn.commit()
    touched = TvlStore('staging',tmp_path/'pub').get()
    assert prepare(touched)['reusedDates'] == [DAY]
    manifest = deepcopy(touched.manifest)
    manifest['datasetId'] = 'd'*64
    manifest['newAccountingPolicy'] = 'new-global-rule'
    changed = TvlDataset(manifest)
    result = prepare(changed)
    assert result['reusedDates'] == [] and result['recomputedDates'] == [DAY]
    assert result['fullRebuildReason'] == 'accounting-or-catalog-context-changed'
    with pytest.raises(ValueError, match='immutable'):
        prepare(changed,full_rebuild=True)


def test_advance_current_close_recalculates_previous_bridge_close(postgres_database, tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn)
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,active,updated_at) VALUES(1,'v3',?,0,1)", (BRIDGE,))
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES(10,'v3',?,1)", (CHILD,))
        conn.commit()
        run(conn,1,DAY,[(BRIDGE,'100')])
    old = TvlStore('staging',tmp_path/'pub',current_bridge_policy='retired-registry').get(); prepare(old)
    assert old.history(interval='daily')['chart'][0]['Ethereum'] == 0
    with closing(connect('staging')) as conn:
        run(conn,2,DAY+86400,[(BRIDGE,'100')])
    daily = TvlStore('staging',tmp_path/'pub',current_bridge_policy='retired-registry').get()
    result = prepare(daily)
    assert result['recomputedDates'] == [DAY,DAY+86400]
    assert daily.history(interval='daily')['chart'] == [
        {'timestamp':DAY,'Ethereum':100.0},{'timestamp':DAY+86400,'Ethereum':0.0}]
    assert old.history(interval='daily')['chart'][0]['Ethereum'] == 0
    full_copy(daily,'c')


def test_interruption_rolls_back_reuse_index_and_reports_daily_gaps(postgres_database, tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn);run(conn,1,DAY,[(PARENT,'100')])
    old = TvlStore('staging',tmp_path/'pub').get();prepare(old)
    before = captured(old)
    with closing(connect('staging')) as conn:
        run(conn,2,DAY+2*86400,[(PARENT,None)])
    candidate = TvlStore('staging',tmp_path/'pub').get()
    def interrupt(message):
        if 'History staged' in message:
            raise RuntimeError('injected interruption after copying')
    with pytest.raises(RuntimeError,match='interruption'):
        prepare(candidate,progress=interrupt)
    assert not ready(candidate) and captured(candidate) == ([],[])
    with closing(connect('staging',readonly=True)) as conn:
        assert conn.execute('SELECT count(*) FROM tvl_chart_inputs WHERE dataset_id=?',(candidate.id,)).fetchone()[0] == 0
    result = prepare(candidate)
    assert result['pendingChainDates'] == [{'timestamp':DAY+86400,'chainIds':[1]}]
    assert totals_ready(candidate) and captured(old) == before
    assert candidate.history(interval='daily')['chart'][-1].get('Ethereum') is None
    with closing(connect('staging')) as conn:
        run(conn,3,DAY+86400,[(PARENT,'0')])
    repaired=TvlStore('staging',tmp_path/'pub').get()
    repaired_receipt=prepare(repaired)
    assert repaired_receipt['recomputedDates']==[DAY+86400]
    assert repaired_receipt['pendingChainDates']==[]
    assert repaired.history(interval='daily')['chart'][1]['Ethereum']==0


def test_global_identity_keeps_future_catalog_and_accounting_fields():
    manifest = dict(runs=[],catalog=[dict(updated_at=1,name='a')],database='staging',allocatorKeys=[])
    original = context_identity(SimpleNamespace(manifest=manifest))
    def identity(**changes):
        return context_identity(SimpleNamespace(manifest=manifest|changes))
    assert identity(catalog=[dict(updated_at=2,name='a')]) == original
    assert identity(catalog=[dict(updated_at=1,name='b')]) != original
    assert identity(catalog=[dict(updated_at=1,name='a',futureField='new')]) != original
    assert identity(futurePolicy='new') != original
    assert identity(reportingCutoff=DAY+1) == identity(reportingCutoff=DAY+86401) == original
    from yearn_data.tvl_incremental import build
    with pytest.raises(ValueError,match='accounting revision differs'):
        build(SimpleNamespace(manifest=manifest|{'accountingRevision':'old-code'}))
    with pytest.raises(ValueError,match='ordered inclusive'):
        build(SimpleNamespace(manifest=manifest),backfill=(2,1))


def test_competing_builder_cannot_write_partial_rows(postgres_database,tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn);run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore('staging',tmp_path/'pub').get()
    with closing(connect('staging')) as lock,lock:
        lock.execute('SELECT pg_advisory_xact_lock(743219889)')
        with pytest.raises(ValueError,match='already running'):
            prepare(dataset)
        assert not ready(dataset)
    result=prepare(dataset)
    assert result['status']=='prepared' and totals_ready(dataset)
    assert prepare(dataset)['recomputedDates']==[DAY]


def test_cli_backfill_mode_and_invalid_modes_fail_before_storage(monkeypatch,tmp_path,capsys):
    from yearn_data import cli, tvl_api, tvl_history_cache
    candidate=object()
    monkeypatch.setattr(tvl_api,'TvlStore',lambda *a,**k:SimpleNamespace(get=lambda:candidate))
    called={}
    def prepare_candidate(dataset,**kwargs):
        assert dataset is candidate
        called.update(kwargs)
        return {'status':'prepared'}
    monkeypatch.setattr(tvl_history_cache,'prepare',prepare_candidate)
    args=['--db','staging','tvl','prepare-history','--publication',str(tmp_path)]
    assert cli.main(args+['--backfill-from','1','--backfill-to','2'])==0
    assert called['backfill']==(1,2) and called['full_rebuild'] is False
    assert json.loads(capsys.readouterr().out)['status']=='prepared'
    assert cli.main(args+['--full-rebuild'])==0
    assert called['full_rebuild'] is True and called['backfill'] is None
    with pytest.raises(ValueError,match='both'):
        cli.main(args+['--backfill-from','1'])
    with pytest.raises(ValueError,match='choose price-only'):
        cli.main(args+['--previous-dataset-id','a'*64,'--full-rebuild'])


def test_totals_resume_after_copy_source_is_retired(postgres_database,tmp_path,monkeypatch):
    from yearn_data import tvl_history_cache as cache
    with closing(connect('staging')) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')])
        run(conn,2,DAY+86400,[(PARENT,'200')])
    old=TvlStore('staging',tmp_path/'pub').get();prepare(old)
    with closing(connect('staging')) as conn:
        run(conn,3,DAY+2*86400,[(PARENT,'300')])
    target=TvlStore('staging',tmp_path/'pub').get()
    def interrupt(_dataset):
        raise RuntimeError('totals stage interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(cache,'prepare_totals',interrupt)
        with pytest.raises(RuntimeError,match='totals stage'):
            prepare(target)
    assert ready(target) and not totals_ready(target)
    with closing(connect('staging')) as conn,conn:
        # Another completed publication may consolidate an older copy source
        # before this interrupted candidate resumes its totals stage.
        for table in ('tvl_chart_rows','tvl_chart_totals','tvl_chart_publications',
                      'tvl_chart_inputs','tvl_chart_preparations'):
            conn.execute(f'DELETE FROM {table} WHERE dataset_id=?',(old.id,))
    result=prepare(target)
    assert result['status']=='prepared'
    assert prepare(target)['status']=='already-prepared'
    assert result['totalsRowsCopied']==0 and result['totalsRowsRead']==3
    assert target.history(interval='daily')['chart']==[
        {'timestamp':DAY,'Ethereum':100.0},{'timestamp':DAY+86400,'Ethereum':200.0},
        {'timestamp':DAY+2*86400,'Ethereum':300.0}]


def test_bounded_history_does_not_report_future_only_chains_as_historical_gaps(postgres_database,tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')])
        conn.execute('UPDATE tvl_runs SET params_json=? WHERE id=1',(json.dumps({'chain_ids':[1]}),))
        conn.commit()
    manifest=deepcopy(TvlStore('staging',tmp_path/'pub').get().manifest)
    future=DAY+3*86400
    with closing(connect('staging')) as conn,conn:
        conn.execute('INSERT INTO tvl_runs VALUES(99,99,99,\'complete\',?)',(json.dumps({'chain_ids':[10]}),))
        conn.execute('INSERT INTO tvl_snapshots VALUES(99,10,?,?,1,?)',
            (CHILD,future,json.dumps(dict(chain_id=10,vault=CHILD,timestamp=future,asset_units='1',tvl_usd='1'))))
        manifest['runs'].append(dict(conn.execute('SELECT * FROM tvl_runs WHERE id=99').fetchone()))
    manifest.update(datasetId='9'*64,reportingCutoff=DAY+2*86400+1,asOfTimestamp=DAY+2*86400)
    result=prepare(TvlDataset(manifest))
    assert result['pendingChainDates']==[
        {'timestamp':DAY+86400,'chainIds':[1]},
        {'timestamp':DAY+2*86400,'chainIds':[1]}]


def test_new_chain_gaps_start_at_its_first_retained_observation(postgres_database,tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn)
        for number, day in enumerate([DAY, DAY+86400, DAY+2*86400, DAY+3*86400], 1):
            run(conn,number,day,[(PARENT,'100')])
        # Chain 10 first enters retained history on day 2, then misses day 3.
        for number, day in [(2,DAY+86400),(4,DAY+3*86400)]:
            row=dict(chain_id=10,vault=CHILD,timestamp=day,version='v3',
                     category='v3',name='New chain vault',asset_units='1',tvl_usd='1')
            conn.execute('INSERT INTO tvl_snapshots VALUES(?,10,?,?,1,?)',
                         (number,CHILD,day,json.dumps(row)))
        conn.commit()
    result=prepare(TvlStore('staging',tmp_path/'pub').get())
    assert result['pendingChainDates']==[{'timestamp':DAY+2*86400,'chainIds':[10]}]


def test_targeted_backfill_records_matched_dates_and_rejects_unmatched_range(postgres_database,tmp_path):
    with closing(connect('staging')) as conn:
        init_db(conn);run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore('staging',tmp_path/'pub').get()
    with pytest.raises(ValueError,match='matches no captured dates'):
        prepare(dataset,backfill=(DAY-10,DAY-1))
    assert not ready(dataset)
    result=prepare(dataset,backfill=(DAY-1,DAY+1))
    assert result['requestedBackfill']==dict(fromTimestamp=DAY-1,toTimestamp=DAY+1,matchedDates=[DAY])
