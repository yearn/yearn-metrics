"""Coverage, value preservation and rollback for canonical-history consolidation."""
from contextlib import closing
import json

import pytest

from test_hosted_api import published as published
from test_pairing import snapshots
from test_tvl_api import run, DAY, PARENT, CHILD, BRIDGE
from yearn_data import canonical_history
from yearn_data.canonical_history import consolidate
from yearn_data.storage import connect, init_db
from yearn_data.pairing import select_pairing
from yearn_data.tvl_api import TvlStore, TvlDataset
from yearn_data.tvl_history_cache import prepare
from yearn_data.hosted_api import HostedAPI
from yearn_data.hosted_publications import Registry, publish
from yearn_data.analytics import SCHEMA as ANALYTICS_SCHEMA


def publish_without_consolidating(database, financial, history, monkeypatch):
    """Arrange a legacy multi-version database for the migration tests."""
    fees = json.loads((financial/'datasets'/(
        json.loads((financial/'current.json').read_text())['datasetId']+'.json')).read_text())
    tvl = TvlStore(database, history).get()
    prepare(tvl)
    identity = ('b' if len(tvl.dates()) > 1 else 'a')*64
    meta = {'publicationId':identity,'feesDatasetId':fees['datasetId'],'tvlDatasetId':tvl.id}
    with closing(connect(database)) as conn:
        conn.executescript(ANALYTICS_SCHEMA)
        conn.execute('INSERT INTO analytics_publications VALUES (?,1,?) ON CONFLICT(id) DO UPDATE SET body_json=excluded.body_json',
                     (identity,json.dumps({'meta':meta,'stack':{},'comparison':{},'comparable':{},'profitability':{}})))
        conn.commit()
    with monkeypatch.context() as patch:
        patch.setattr(canonical_history, 'consolidate', lambda *a, **k: None)
        publish(database, financial, history, identity)
    return tvl


def add_repairs(database):
    with closing(connect(database)) as conn:
        run(conn,3,DAY,[(PARENT,'300'),(CHILD,'0')],[(CHILD,'0')])
        run(conn,4,DAY,[(PARENT,None)],[(CHILD,None)],status='incomplete')
        # An unavailable retry must not win over a successfully observed zero.
        row=json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=4').fetchone()[0])
        row['status']='unavailable'
        conn.execute('UPDATE tvl_positions SET data_json=? WHERE run_id=4',(json.dumps(row),))
        run(conn,5,DAY+14*86400,[(BRIDGE,None)],status='incomplete')
        # Preserve an unpublished failed-only key, without publishing its value.
        run(conn,6,DAY+21*86400,[(BRIDGE,None)],status='failed')
        # Even a valued observation in a failed run cannot replace published data.
        run(conn,7,DAY+7*86400,[(PARENT,'999')],status='failed')
        return snapshots(conn)


def database_rows(database):
    with closing(connect(database,readonly=True)) as conn:
        return {table:[tuple(r) for r in conn.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
                for table in ('tvl_snapshots','tvl_positions','analysis_outputs','analysis_runs',
                              'api_selection','tvl_chart_rows','analytics_publications')}


@pytest.fixture
def legacy(published,monkeypatch):
    database,financial,history,_=published
    selected=select_pairing(database,*add_repairs(database),financial)
    tvl=publish_without_consolidating(database,financial,history,monkeypatch)
    return database,financial,history,tvl,selected


def assert_consolidated(database,tvl):
    dates=tvl.dates()
    with closing(connect(database,readonly=True)) as conn, conn:
        for table, fields in (('tvl_snapshots', ('timestamp','chain_id','vault')),
                              ('tvl_positions', ('timestamp','chain_id','parent','strategy','child'))):
            identities=[tuple(row[field] for field in fields)
                        for row in tvl.selected_rows(conn,table,dates)]
            assert identities==sorted(identities)
    expected_frames=TvlDataset(tvl.manifest).frames(dates)
    expected_api={route:HostedAPI(database).response(route) for route in (
        '/api/fees','/api/fees/history?interval=weekly','/api/fees/vaults',
        '/api/tvl','/api/tvl/history/runs/latest?interval=daily',
        '/api/tvl/history/runs/latest/constant-price?interval=daily')}
    original=database_rows(database)
    plan=consolidate(database)
    assert database_rows(database)==original  # A dry run makes no persistent writes.
    result=consolidate(database,apply=True)
    assert result['observations']['tvl_snapshots']['before']>result['observations']['tvl_snapshots']['after']
    assert result['observations']['tvl_snapshots']['canonical_keys']==plan['observations']['tvl_snapshots']['canonical_keys']
    assert TvlDataset(tvl.manifest).frames(dates)==expected_frames
    for route,response in expected_api.items():assert HostedAPI(database).response(route)==response
    with closing(connect(database,readonly=True)) as conn:
        # Old date repair, known zero, later original date, unavailable-only date,
        # and failed-only date all survive. Source run IDs remain provenance.
        rows={(r['vault'],r['timestamp']):r for r in conn.execute('SELECT * FROM tvl_snapshots')}
        assert rows[PARENT,DAY]['run_id']==3
        assert rows[PARENT,DAY+7*86400]['run_id']==2
        assert rows[CHILD,DAY]['run_id']==3
        assert json.loads(rows[CHILD,DAY]['data_json'])['tvl_usd']=='0'
        assert rows[BRIDGE,DAY+14*86400]['run_id']==5
        assert rows[BRIDGE,DAY+21*86400]['run_id']==6
        assert conn.execute('SELECT run_id FROM tvl_positions').fetchone()[0]==3
        assert conn.execute('SELECT COUNT(*) FROM tvl_chart_publications').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM api_releases').fetchone()[0]==1
        assert conn.execute("SELECT COUNT(*) FROM analysis_runs WHERE status='superseded'").fetchone()[0]==2
    again=consolidate(database,apply=True)
    assert all(v['before']==v['after'] for v in again['observations'].values())
    assert again['analysis_outputs']['before']==again['analysis_outputs']['retained_rows']


def test_every_historical_key_and_selected_value_survives(legacy):
    assert_consolidated(legacy[0],legacy[3])


@pytest.mark.parametrize('table', ['tvl_snapshots','tvl_positions','analysis_outputs','tvl_chart_rows'])
def test_value_corruption_rolls_back_the_whole_consolidation(legacy,monkeypatch,table):
    database=legacy[0];before=database_rows(database)
    original=canonical_history._delete_obsolete
    def corrupt(conn,selection,keep):
        original(conn,selection,keep)
        if table=='tvl_chart_rows':conn.execute("UPDATE tvl_chart_rows SET tvl_usd='999'")
        else:
            field='row_json' if table=='analysis_outputs' else 'data_json'
            conn.execute(f"UPDATE {table} SET {field}='{{}}'")
    monkeypatch.setattr(canonical_history,'_delete_obsolete',corrupt)
    with pytest.raises(ValueError,match='verification failed'):consolidate(database,apply=True)
    assert database_rows(database)==before


@pytest.mark.parametrize('initial,changed',[(None,None),(100,100),(100,101),(None,100),(100,None)])
def test_snapshot_block_verification_is_null_safe_and_rolls_back(legacy,monkeypatch,initial,changed):
    database=legacy[0]
    with closing(connect(database)) as conn:
        conn.execute('UPDATE tvl_snapshots SET block_number=?',(initial,));conn.commit()
    before=database_rows(database)
    original=canonical_history._delete_obsolete
    def change_block(conn,selection,keep):
        original(conn,selection,keep)
        conn.execute('UPDATE tvl_snapshots SET block_number=?',(changed,))
    monkeypatch.setattr(canonical_history,'_delete_obsolete',change_block)
    if initial!=changed:
        with pytest.raises(ValueError,match='canonical key/value verification failed'):
            consolidate(database,apply=True)
        assert database_rows(database)==before
    else:
        result=consolidate(database,apply=True)
        assert result['observations']['tvl_snapshots']['missing_or_changed_keys']==0
        with closing(connect(database)) as conn:
            assert {r[0] for r in conn.execute('SELECT block_number FROM tvl_snapshots')}=={initial}


def test_shorter_analysis_cannot_erase_earlier_event_keys(legacy):
    database=legacy[0]
    with closing(connect(database)) as conn:
        latest=legacy[4]['feesRunId']
        row=conn.execute("SELECT rowid FROM analysis_outputs WHERE run_id=? AND name='fee_usd_events' LIMIT 1",(latest,)).fetchone()
        conn.execute('DELETE FROM analysis_outputs WHERE rowid=?',(row[0],));conn.commit()
    before=database_rows(database)
    with pytest.raises(ValueError,match='historical event identities'):consolidate(database,apply=True)
    assert database_rows(database)==before


@pytest.mark.parametrize('status', ['running','complete'])
def test_unpublished_update_is_not_discarded(legacy,status):
    database=legacy[0]
    with closing(connect(database)) as conn:run(conn,8,DAY+28*86400,[(PARENT,'500')],status=status)
    before=database_rows(database)
    with pytest.raises(ValueError,match='running jobs|outside the selected'):consolidate(database,apply=True)
    assert database_rows(database)==before


def test_publish_automatically_retires_obsolete_results_and_rejects_stale_revision(legacy):
    database,financial,history,_,_=legacy
    selection=Registry(database).selection()
    with closing(connect(database,readonly=True)) as conn:
        old=conn.execute('SELECT dataset_id FROM tvl_chart_publications WHERE dataset_id<>?',(selection['tvlDatasetId'],)).fetchone()[0]
    assert HostedAPI(database).response('/api/tvl?datasetId='+old)[0]==410
    publish(database,financial,history,selection['analyticsPublicationId'])
    with closing(connect(database,readonly=True)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM tvl_chart_publications').fetchone()[0]==1
    assert HostedAPI(database).response('/api/tvl?datasetId='+selection['tvlDatasetId'])[0]==200


def test_revision_change_during_request_cannot_return_mixed_success(published,monkeypatch):
    api=HostedAPI(published[0]);selection=api.registry.selection()
    reads=iter([selection,selection|{'releaseId':'e'*64}])
    monkeypatch.setattr(api.registry,'selection',lambda:next(reads))
    status,body=api.response('/api/fees')
    assert status==410 and body['releaseId']=='e'*64
    assert 'totalFeesPaidUsd' not in body


def test_postgres_canonical_history_preserves_dates_values_and_api(pg,tmp_path,monkeypatch):
    conn,_=pg
    init_db(conn)
    runs=snapshots(conn)
    run(conn,1,DAY,[(PARENT,'100')]);run(conn,2,DAY+7*86400,[(PARENT,'200')])
    financial=tmp_path/'fees';history=tmp_path/'tvl'
    select_pairing('neon',*runs,financial)
    publish_without_consolidating('neon',financial,history,monkeypatch)
    select_pairing('neon',*add_repairs('neon'),financial)
    tvl=publish_without_consolidating('neon',financial,history,monkeypatch)
    assert_consolidated('neon',tvl)


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('route', ['/api/tvl', '/api/tvl/history/runs/latest',
                                  '/api/tvl/history/runs/latest/constant-price'])
def test_file_pin_fails_explicitly_after_observations_are_retired(legacy, cached, route):
    from yearn_data.tvl_api import tvl_response
    database, _, history, current, _ = legacy
    old = next(path.stem for path in (history/'datasets').glob('*.json')
               if path.stem != current.id)
    store = TvlStore(database, history, auto_publish=False)
    url = route+'?datasetId='+old
    assert tvl_response(store, url)[0] == 200
    consolidate(database, apply=True)
    if not cached:
        store = TvlStore(database, history, auto_publish=False)
    status, body = tvl_response(store, url)
    assert status == 410
    assert 'retired' in body['error']
    assert tvl_response(store, route+'?datasetId='+current.id)[0] == 200


def test_file_pin_rechecks_consolidation_before_returning_success(legacy, monkeypatch):
    from yearn_data.tvl_api import tvl_response
    database, _, history, current, _ = legacy
    old = next(path.stem for path in (history/'datasets').glob('*.json')
               if path.stem != current.id)
    store = TvlStore(database, history, auto_publish=False)
    dataset = store.get(old)
    original = dataset.summary
    def consolidate_during_response(*args):
        consolidate(database, apply=True)
        return original(*args)
    monkeypatch.setattr(dataset, 'summary', consolidate_during_response)
    assert tvl_response(store, '/api/tvl?datasetId='+old)[0] == 410


@pytest.mark.parametrize('keep_manifest', [False, True])
def test_file_pin_handles_legacy_consolidation_receipts(legacy, keep_manifest):
    from yearn_data.tvl_api import tvl_response
    database, _, history, current, _ = legacy
    old = next(path.stem for path in (history/'datasets').glob('*.json')
               if path.stem != current.id)
    consolidate(database, apply=True)
    with closing(connect(database)) as conn:
        row = conn.execute('SELECT id,receipt_json FROM canonical_history_receipts ORDER BY id DESC LIMIT 1').fetchone()
        receipt = json.loads(row['receipt_json']); receipt.pop('tvlRunIds')
        conn.execute('UPDATE canonical_history_receipts SET receipt_json=? WHERE id=?', (json.dumps(receipt), row['id']))
        if not keep_manifest:
            conn.execute("DELETE FROM api_manifests WHERE kind='tvl'")
        conn.commit()
    store = TvlStore(database, history, auto_publish=False)
    assert tvl_response(store, '/api/tvl?datasetId='+old)[0] == 410
    assert tvl_response(store, '/api/tvl?datasetId='+current.id)[0] == 200


@pytest.mark.parametrize('hosted', [False, True])
def test_receipt_is_checked_once_only_for_file_backed_responses(published, monkeypatch, hosted):
    from yearn_data.tvl_api import tvl_response
    database, _, history, _ = published
    calls=[]; original=TvlDataset.assert_available
    def check(dataset):
        calls.append(dataset.id)
        return original(dataset)
    monkeypatch.setattr(TvlDataset, 'assert_available', check)
    if hosted:
        response = HostedAPI(database).response('/api/tvl/history/runs/latest')
    else:
        response = tvl_response(TvlStore(database, history, auto_publish=False), '/api/tvl/history/runs/latest')
    assert response[0] == 200
    assert len(calls) == (0 if hosted else 1)
