import copy
import json
from contextlib import closing
import shutil
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import HTTPError

import pytest
from test_pairing import snapshots
from test_tvl_api import run, DAY, PARENT
from yearn_data.storage import connect, init_db
from yearn_data.pairing import select_pairing, PairingStore, pairing_response
from yearn_data.tvl_api import TvlStore, tvl_response
from yearn_data.tvl_history_cache import prepare
from yearn_data.analytics import SCHEMA as ANALYTICS_SCHEMA
from yearn_data.hosted_publications import publish, Registry, save_release
from yearn_data.hosted_api import HostedAPI, Handler, cache_headers


@pytest.fixture
def published(tmp_path):
    database = 'staging'
    financial = tmp_path/'fees'
    history = tmp_path/'tvl'
    with closing(connect(database)) as conn:
        init_db(conn)
        runs = snapshots(conn)
        run(conn, 1, DAY, [(PARENT, '100')])
        run(conn, 2, DAY+7*86400, [(PARENT, '200')])
    fees = select_pairing(database, *runs, financial)
    tvl = TvlStore(database, history).get()
    prepare(tvl)
    analytics_id = 'a'*64
    meta = {'publicationId': analytics_id, 'feesDatasetId': fees['datasetId'], 'tvlDatasetId': tvl.id}
    with closing(connect(database)) as conn:
        conn.executescript(ANALYTICS_SCHEMA)
        conn.execute('INSERT INTO analytics_publications VALUES (?,1,?)',
                     (analytics_id, json.dumps({'meta':meta, 'stack':{'chains':[]}, 'comparison':{},
                                               'comparable':{}, 'profitability':{'None':{'vaults':[]}}})))
        conn.execute("INSERT INTO analytics_selection VALUES ('powerglove',?)", (analytics_id,))
        conn.commit()
    release = publish(database, financial, history)
    return database, financial, history, release


def test_fileless_routes_match_existing_contracts(published):
    database, financial, history, _ = published
    expected_fees = pairing_response(PairingStore(financial), '/api/fees/history?interval=weekly')
    expected_tvl = tvl_response(TvlStore(database, history, auto_publish=False), '/api/tvl')
    # Remove every local publication file before initializing the hosted API.
    shutil.rmtree(financial); shutil.rmtree(history)
    api = HostedAPI(database)
    assert api.response('/api/fees/history?interval=weekly') == expected_fees
    assert api.response('/api/tvl') == expected_tvl
    assert api.response('/api/fees/stack')[1]['chains'] == []
    assert api.response('/api/analytics/publication')[1]['feesDatasetId'] == expected_fees[1]['datasetId']
    assert api.response('/api/fees?chainId=bad')[0] == 400
    assert api.response('/api/fees?datasetId='+'f'*64)[0] == 410
    assert api.response('/api/fees/stack?publicationId=bad')[0] == 400
    assert api.response('/api/publication?unknown=yes')[0] == 400
    assert api.response('/unknown')[0] == 404


def test_atomic_selection_and_immutable_old_manifests(published):
    database, _, _, release = published
    registry = Registry(database)
    old = registry.selection()
    fees = registry.manifest('fees', old['feesDatasetId'])
    tvl = registry.manifest('tvl', old['tvlDatasetId'])
    with closing(connect(database)) as conn:
        assert save_release(conn, fees, tvl, old['analyticsPublicationId']) == release
        changed = copy.deepcopy(tvl);changed['currentBridgePolicy'] = 'changed'
        with pytest.raises(ValueError, match='immutable'):
            save_release(conn, fees, changed, 'b'*64)
    assert registry.selection() == old
    with closing(connect(database)) as conn:
        new = save_release(conn, fees, tvl, 'b'*64)
    assert registry.selection()['releaseId'] == new  # Same reader, no restart.
    assert registry.manifest('tvl', old['tvlDatasetId']) == tvl


def test_publish_rejects_mismatched_analytics(published):
    database, financial, history, _ = published
    old = Registry(database).selection()
    with closing(connect(database)) as conn:
        conn.execute('INSERT INTO analytics_publications VALUES (?,1,?)',
                     ('b'*64, json.dumps({'meta':{'feesDatasetId':'c'*64,'tvlDatasetId':old['tvlDatasetId']}})))
        conn.commit()
    with pytest.raises(ValueError, match='analytics must reference'):
        publish(database, financial, history, 'b'*64)
    assert Registry(database).selection() == old


def test_request_connections_are_readonly(published, monkeypatch):
    from yearn_data import hosted_publications, pairing, analytics, tvl_api, tvl_history_cache
    original = connect
    calls = []
    def reader(*args, **kwargs):
        assert kwargs.get('readonly') is True
        calls.append(args)
        return original(*args, **kwargs)
    for module in (hosted_publications, pairing, analytics, tvl_api, tvl_history_cache):
        monkeypatch.setattr(module, 'connect', reader)
    api = HostedAPI(published[0])
    for route in ['/api/fees', '/api/tvl', '/api/tvl/graph', '/api/fees/stack', '/api/publication']:
        assert api.response(route)[0] == 200
    assert calls


def test_hosted_graph_uses_selected_tvl_release_and_rejects_retired_pin(published):
    database,_,history,release=published
    api=HostedAPI(database)
    expected=tvl_response(TvlStore(database,history,auto_publish=False),'/api/tvl/graph')
    assert api.response('/api/tvl/graph')==expected
    pin=expected[1]['datasetId']
    assert api.response('/api/tvl/graph?datasetId='+pin)==expected
    assert api.response('/api/tvl/graph?datasetId='+'f'*64)[0]==410


@pytest.mark.parametrize('url,status,expected', [
    ('/api/fees',200,'public, s-maxage=30'),
    ('/api/fees?datasetId='+'a'*64,200,'public, s-maxage=3600'),
    ('/api/profitability?datasetId='+'a'*64,200,'public, s-maxage=30'),
    ('/api/profitability?publicationId='+'a'*64,200,'public, s-maxage=3600'),
    ('/api/comparison',200,'public, s-maxage=30'),
    ('/api/comparison?datasetId='+'a'*64,200,'public, s-maxage=30'),
    ('/api/comparison?publicationId='+'a'*64,200,'public, s-maxage=3600'),
    ('/api/comparison?datasetId='+'a'*64+'&publicationId='+'b'*64,200,'public, s-maxage=3600'),
    ('/api/fees?datasetId='+'a'*64,400,None),
    ('/api/publication',503,None),
])
def test_cdn_policy(url,status,expected):
    headers = cache_headers(url,status)
    assert headers.get('Vercel-CDN-Cache-Control') == expected
    if status != 200:assert headers['Cache-Control']=='no-store'


def test_comparison_dataset_pin_does_not_freeze_analytics_republication(published):
    database,_,_,_=published
    api=HostedAPI(database)
    selected=api.registry.selection()
    dataset_url='/api/comparison?datasetId='+selected['tvlDatasetId']
    status,before=api.response(dataset_url)
    assert status==200
    new_id='b'*64
    with closing(connect(database)) as conn:
        body=json.loads(conn.execute('SELECT body_json FROM analytics_publications WHERE id=?',
                                     (selected['analyticsPublicationId'],)).fetchone()[0])
        body['meta']['publicationId']=new_id
        body['comparison']['republished']=True
        conn.execute('INSERT INTO analytics_publications VALUES (?,2,?)',(new_id,json.dumps(body)))
        save_release(conn,api.registry.manifest('fees',selected['feesDatasetId']),
                     api.registry.manifest('tvl',selected['tvlDatasetId']),new_id)
    status,after=api.response(dataset_url)
    assert status==200 and after!=before and after['republished'] is True
    assert cache_headers(dataset_url,status)['Vercel-CDN-Cache-Control']=='public, s-maxage=30'
    old_url=dataset_url+'&publicationId='+selected['analyticsPublicationId']
    assert api.response(old_url)[0]==410
    assert cache_headers(old_url,410)=={'Cache-Control':'no-store'}
    for url in ['/api/comparison?publicationId='+new_id,dataset_url+'&publicationId='+new_id]:
        assert api.response(url)==(200,after)
        assert cache_headers(url,200)['Vercel-CDN-Cache-Control']=='public, s-maxage=3600'


def test_http_failure_does_not_expose_credentials(monkeypatch):
    from yearn_data import hosted_api
    def fail():raise ValueError('postgres://secret-password')
    monkeypatch.setattr(hosted_api, 'application', fail)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(f'http://127.0.0.1:{server.server_port}/api/fees')
        assert caught.value.code == 503
        assert caught.value.headers['Cache-Control']=='no-store'
        assert 'secret' not in caught.value.read().decode()
    finally:
        server.shutdown();server.server_close();thread.join()

# Reuse the existing fixture, which creates and drops a dedicated test schema.


def test_postgres_registry_transaction_and_readonly_selection(pg):
    conn, _ = pg
    fees = {'datasetId':'1'*64,'database':'neon'}
    tvl = {'datasetId':'2'*64,'database':'neon'}
    release = save_release(conn, fees, tvl, '3'*64)
    registry = Registry('neon')
    assert registry.selection()['releaseId'] == release
    with pytest.raises(ValueError, match='immutable'):
        save_release(conn, fees, dict(tvl,extra='changed'), '4'*64)
    assert registry.selection()['releaseId'] == release
    assert registry.manifest('tvl', tvl['datasetId']) == tvl


def test_publish_rejects_tampered_tvl_manifest(published):
    database, financial, history, _ = published
    selected = Registry(database).selection()
    path = history/'datasets'/(selected['tvlDatasetId']+'.json')
    body = json.loads(path.read_text());body['currentBridgePolicy']='changed'
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match='content does not match'):
        publish(database, financial, history)
    assert Registry(database).selection() == selected


def test_prepared_history_survives_removal_of_old_ingestion_rows(published):
    database, _, history, _ = published
    route = '/api/tvl/history/runs/latest?interval=weekly'
    expected = tvl_response(TvlStore(database, history, auto_publish=False), route)
    with closing(connect(database)) as conn:
        conn.execute('DELETE FROM tvl_snapshots WHERE timestamp=?', (DAY,))
        conn.commit()
    api = HostedAPI(database)
    assert api.response(route) == expected
    assert len(expected[1]['chart']) == 2

@pytest.mark.parametrize('route', [
    '/api/fees', '/api/fees/vaults', '/api/fees/history?interval=weekly',
    '/api/fees/history?chainId=1,10&interval=monthly',
    '/api/fees?chainId=10&since=1704153600&until=1704240000',
    '/api/fees?chainId=1&vaultAddress=0x'+'22'*20,
    '/api/fees/history?chainId=1&vaultAddress=0x'+'33'*20+'&interval=weekly',
    '/api/fees?chainId=999', '/api/fees?since=9999999999',
])
def test_compact_financial_rows_match_without_analysis_outputs(published, route):
    database, financial, _, _ = published
    expected = pairing_response(PairingStore(financial), route)
    with closing(connect(database)) as conn:
        conn.execute('DELETE FROM analysis_outputs')
        conn.commit()
    assert HostedAPI(database).response(route) == expected


@pytest.mark.parametrize('encoding,compressed', [('gzip',True), ('gzip;q=0',False), ('identity',False)])
def test_http_compression(monkeypatch, encoding, compressed):
    import gzip
    from urllib.request import Request
    from yearn_data import hosted_api
    payload = {'data':'a'*3000}
    class App:
        def response(self, url):return 200, payload
    monkeypatch.setattr(hosted_api, 'application', lambda:App())
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        request = Request(f'http://127.0.0.1:{server.server_port}/api/fees',
                          headers={'Accept-Encoding':encoding})
        with urlopen(request) as response:
            assert (response.headers.get('Content-Encoding')=='gzip') == compressed
            assert response.headers['Vary'] == 'Accept-Encoding'
            body=response.read()
            assert len(body)==int(response.headers['Content-Length'])
            assert json.loads(gzip.decompress(body) if compressed else body)==payload
    finally:
        server.shutdown();server.server_close();thread.join()


def test_postgres_compact_rows_preserve_filters_and_nulls(pg):
    from yearn_data.hosted_fees import prepare as prepare_fees, PublishedFees
    from yearn_data.pairing import PairingDataset
    conn, _ = pg
    init_db(conn)
    runs = snapshots(conn)
    source = PairingDataset('neon', *runs, vault_names={})
    prepare_fees(conn, source)
    prepare_fees(conn, source)  # Same immutable publication is safe to retry.
    reader = PublishedFees('neon', source.id)
    for view in ('summary', 'history', 'vaults'):
        assert reader.view(view, chains=(10,), interval='weekly') == source.view(view, chains=(10,), interval='weekly')


def test_release_rollback_rejects_unknown_and_restores_selection(published):
    from yearn_data.hosted_publications import select_release
    database, _, _, original = published
    registry = Registry(database)
    previous = registry.selection()
    with closing(connect(database)) as conn:
        other = save_release(conn, registry.manifest('fees', previous['feesDatasetId']),
                             registry.manifest('tvl', previous['tvlDatasetId']), 'b'*64)
    assert registry.selection()['releaseId'] == other
    assert select_release(database, original) == original
    with pytest.raises(ValueError, match='unknown releaseId'):
        select_release(database, 'f'*64)
    assert registry.selection() == previous


def new_analytics_revision(database):
    with closing(connect(database)) as conn, conn:
        body = json.loads(conn.execute('SELECT body_json FROM analytics_publications').fetchone()[0])
        body['meta']['publicationId'] = 'b'*64
        conn.execute('INSERT INTO analytics_publications VALUES (?,1,?)', ('b'*64, json.dumps(body)))
    return 'b'*64


def test_prepare_then_publish_aligns_legacy_selection_and_retires_old_release(published, monkeypatch):
    from yearn_data import analytics
    from yearn_data.hosted_publications import select_release
    database, financial, history, old_release = published
    old = Registry(database).selection()
    def prepare_owned(db, fees, tvl, *, select):
        assert select is False
        assert fees.id == old['feesDatasetId'] and tvl.id == old['tvlDatasetId']
        new_id = new_analytics_revision(database)
        with closing(connect(database)) as conn:
            assert conn.execute('SELECT publication_id FROM analytics_selection').fetchone()[0] == old['analyticsPublicationId']
        return new_id
    monkeypatch.setattr(analytics, 'prepare', prepare_owned)
    new_release = publish(database, financial, history, prepare_analytics=True)
    assert new_release != old_release
    assert Registry(database).selection()['analyticsPublicationId'] == 'b'*64
    with closing(connect(database)) as conn:
        assert conn.execute('SELECT publication_id FROM analytics_selection').fetchone()[0] == 'b'*64
        assert [r[0] for r in conn.execute('SELECT id FROM api_releases')] == [new_release]
    with pytest.raises(ValueError, match='unknown releaseId'):
        select_release(database, old_release)


def test_publish_cli_reports_selected_release_when_cleanup_fails(published, monkeypatch, capsys):
    from yearn_data import canonical_history
    from yearn_data.cli import main
    from yearn_data.hosted_publications import select_release
    database, financial, history, old_release = published
    new_id = new_analytics_revision(database)
    def fail(*args, **kwargs):
        raise RuntimeError('postgres://secret-password')
    monkeypatch.setattr(canonical_history, 'consolidate', fail)
    result = main(['--db', str(database), 'publish-hosted', '--publication', str(financial),
                   '--tvl-publication', str(history), '--analytics-publication-id', new_id])
    assert result == 1
    output = capsys.readouterr()
    body = json.loads(output.out)
    current = Registry(database).selection()['releaseId']
    assert current != old_release
    assert body['releaseId'] == current
    assert body['published'] is True and body['consolidated'] is False
    assert 'secret-password' not in output.out + output.err
    # The failed cleanup leaves the prior coherent release available for recovery.
    assert select_release(database, old_release) == old_release
    assert Registry(database).selection()['releaseId'] == old_release


def test_hosted_date_index_does_not_scan_vault_rows(published):
    database, _, _, release = published
    registry = Registry(database)
    identity = registry.selection()['tvlDatasetId']
    with closing(connect(database)) as conn:
        conn.execute('DROP TABLE tvl_chart_rows')
        conn.commit()
    assert registry.tvl(identity).dates() == (DAY, DAY+7*86400)


@pytest.mark.parametrize('pin', ['datasetId', 'publicationId'])
def test_comparison_superseded_pin_identifies_current_release(published, pin):
    database, _, _, release = published
    status, body = HostedAPI(database).response('/api/comparison?' + pin + '=' + 'f'*64)
    assert status == 410
    assert body['releaseId'] == release


@pytest.mark.parametrize('query', [
    'datasetId=bad', 'datasetId=', 'datasetId=' + 'F'*64,
    'datasetId=' + 'f'*64 + '&unknown=yes',
    'datasetId=' + 'f'*64 + '&datasetId=' + 'e'*64,
    'datasetId=' + 'f'*64 + '&publicationId=bad',
    'publicationId=' + 'f'*64 + '&datasetId=bad',
])
def test_comparison_rejects_invalid_filters_before_pin_expiry(published, query):
    assert HostedAPI(published[0]).response('/api/comparison?' + query)[0] == 400


def test_republish_regenerated_tvl_manifest_preserves_first_metadata(published, monkeypatch):
    from yearn_data import tvl_api
    database, financial, history, release = published
    registry = Registry(database)
    selection = registry.selection()
    original = registry.manifest('tvl', selection['tvlDatasetId'])
    expected = HostedAPI(database).response('/api/tvl/history/runs/latest')
    shutil.rmtree(history)
    monkeypatch.setattr(tvl_api.time, 'time', lambda: original['publishedAt'] + 60)
    regenerated = TvlStore(database, history).get()
    assert regenerated.id == original['datasetId']
    assert regenerated.manifest['publishedAt'] != original['publishedAt']
    assert publish(database, financial, history) == release
    assert Registry(database).manifest('tvl', regenerated.id) == original
    assert HostedAPI(database).response('/api/tvl/history/runs/latest') == expected


@pytest.mark.parametrize('field,value', [('publishedAt', 1), ('diagnostics', {'changed': True})])
def test_existing_tvl_manifest_ignores_only_identity_excluded_metadata(published, field, value):
    database, _, _, release = published
    registry = Registry(database); selection = registry.selection()
    fees = registry.manifest('fees', selection['feesDatasetId'])
    original = registry.manifest('tvl', selection['tvlDatasetId'])
    changed = dict(original, **{field: value})
    with closing(connect(database)) as conn:
        assert save_release(conn, fees, changed, selection['analyticsPublicationId']) == release
        changed['currentBridgePolicy'] = 'changed'
        with pytest.raises(ValueError, match='immutable'):
            save_release(conn, fees, changed, selection['analyticsPublicationId'])
    assert Registry(database).manifest('tvl', original['datasetId']) == original


@pytest.mark.parametrize('change', ['close', 'dates', 'empty'])
def test_republish_rejects_changed_serving_bounds_atomically(published, change):
    database, financial, history, release = published
    registry = Registry(database)
    selection = registry.selection()
    original = registry.manifest('tvl', selection['tvlDatasetId'])
    with closing(connect(database)) as conn:
        old_dates = conn.execute('SELECT dates_json FROM api_tvl_dates').fetchone()[0]
        if change == 'dates':
            conn.execute('UPDATE tvl_chart_rows SET timestamp=timestamp+1 WHERE timestamp=?', (DAY,))
        elif change == 'empty':
            conn.execute('DELETE FROM tvl_chart_rows')
        conn.commit()
    if change == 'close':
        path = history/'datasets'/f"{original['datasetId']}.json"
        path.write_text(json.dumps(dict(original, asOfTimestamp=original['asOfTimestamp']+86400)))
    expected = {'close':'reporting close changed', 'dates':'prepared dates changed', 'empty':'dates are unavailable'}[change]
    with pytest.raises(ValueError, match=expected):
        publish(database, financial, history)
    assert Registry(database).selection() == selection
    assert Registry(database).manifest('tvl', original['datasetId']) == original
    with closing(connect(database)) as conn:
        assert conn.execute('SELECT dates_json FROM api_tvl_dates').fetchone()[0] == old_dates


def test_new_history_uses_new_dataset_and_date_index(published):
    from yearn_data.analytics import prepare as prepare_analytics
    database, financial, history, release = published
    api = HostedAPI(database)
    old = Registry(database).selection()
    assert api.response('/api/tvl/history/runs/latest')[0] == 200
    with closing(connect(database)) as conn:
        run(conn, 3, DAY+8*86400, [(PARENT, '300')])
    tvl = TvlStore(database, history).get()
    assert tvl.id != old['tvlDatasetId']
    assert prepare(tvl)['status'] == 'prepared'
    analytics_id = prepare_analytics(database, PairingStore(financial).get(), tvl, select=False)
    assert publish(database, financial, history, analytics_id) != release
    assert Registry(database).tvl(tvl.id).dates() == (DAY, DAY+7*86400, DAY+8*86400)
    status, body = api.response('/api/tvl')
    assert status == 200 and body['datasetId'] == tvl.id and body['totalTvl'] == 300


@pytest.mark.parametrize('missing', ['vaults', 'asOfTimestamp', 'coverage', 'sources'])
def test_publish_with_incomplete_comparison_evidence(published, missing):
    from yearn_data.analytics import put_input
    database, financial, history, _ = published
    tvl_id = Registry(database).selection()['tvlDatasetId']
    body = {'tvlDatasetId': tvl_id, 'asOfTimestamp': DAY, 'vaults': [], 'coverage': {}, 'sources': {}}
    del body[missing]
    with closing(connect(database)) as conn:
        put_input(conn, 'comparison-evidence', body, 'partial')
    release = publish(database, financial, history, prepare_analytics=True)
    api = HostedAPI(database)
    assert Registry(database).selection()['releaseId'] == release
    status, payload = api.response('/api/comparison/defillama-comparable')
    assert status == 200
    assert payload['reconciliation']['state'] == 'unavailable'


@pytest.mark.parametrize('status', ['missed', 'handled'])
@pytest.mark.parametrize('deduction', [None, 'not-a-number', 'NaN', 'Infinity'])
def test_publish_with_unknown_overlap_deduction(published, status, deduction):
    from yearn_data.analytics import put_input
    database, financial, history, _ = published
    tvl_id = Registry(database).selection()['tvlDatasetId']
    body = {'tvlDatasetId': tvl_id, 'asOfTimestamp': DAY, 'vaults': [], 'coverage': {}, 'sources': {},
            'overlapChecks': [{'protocol': 'yearn-finance', 'status': status, 'ourDeductionUsd': deduction}]}
    with closing(connect(database)) as conn:
        put_input(conn, 'comparison-evidence', body, 'partial')
    release = publish(database, financial, history, prepare_analytics=True)
    api = HostedAPI(database)
    assert Registry(database).selection()['releaseId'] == release
    code, payload = api.response('/api/comparison/defillama-comparable')
    assert code == 200
    assert payload['reconciliation']['state'] == 'unavailable'


@pytest.mark.parametrize('field', ['units', 'replayTvlUsd'])
def test_publish_with_malformed_vault_valuation(published, field):
    from yearn_data.analytics import put_input
    database, financial, history, _ = published
    tvl_id = Registry(database).selection()['tvlDatasetId']
    vault = {'protocol': 'yearn-finance', 'chainId': 1, 'address': 'absent',
             'units': '10', 'replayTvlUsd': '20', field: 'n/a'}
    body = {'tvlDatasetId': tvl_id, 'asOfTimestamp': DAY, 'vaults': [vault], 'coverage': {}, 'sources': {}}
    with closing(connect(database)) as conn:
        put_input(conn, 'comparison-evidence', body, 'partial')
    publish(database, financial, history, prepare_analytics=True)
    code, payload = HostedAPI(database).response('/api/comparison/defillama-comparable')
    assert code == 200 and payload['reconciliation']['state'] == 'unavailable'
