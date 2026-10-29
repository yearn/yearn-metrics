"""Prevent serial acquisition and historical retries returning to weekly refresh."""
from contextlib import closing
import json
from types import SimpleNamespace
from unittest.mock import Mock


from yearn_data import refresh, tvl_batch, tvl_sources
from yearn_data.refresh_scope import RefreshOptions
from yearn_data.storage import connect, init_db
from yearn_data.tvl import export_tvl


def address(n):
    return '0x' + f'{n:040x}'


def test_coordinator_batches_daily_reads_and_reuses_cached_prices(postgres_database, monkeypatch, tmp_path):
    parent, child, asset = address(1), address(2), address(3)
    with closing(connect('staging')) as conn:
        init_db(conn)
        with conn:
            for vault in (parent, child):
                conn.execute('INSERT INTO tvl_vaults(chain_id,version,address,asset,asset_decimals,updated_at) '
                             "VALUES(1,'v3',?,?,6,1)", (vault, asset))
            conn.execute('INSERT INTO tvl_strategies VALUES(1,?,?,?)', (parent, child, 'fixture'))
    calls = []
    readers = []
    def get_block(n):
        return dict(number=n, timestamp=n*43200, hash=b'\x01'*32)
    def post(url, *, json, timeout):
        calls.append(json)
        rows = []
        for request in json:
            if request['method'] == 'eth_getCode':
                result = '0x01'
            else:
                target = request['params'][0]
                data = target['data']
                if data == tvl_batch.call_data('totalSupply()'):
                    words = [100_000_000]
                elif data == tvl_batch.call_data('totalAssets()'):
                    words = [100_000_000 if target['to'].lower() == parent else 50_000_000]
                else:
                    assert data == tvl_batch.call_data('strategies(address)', child)
                    words = [1, 2, 25_000_000, 100_000_000]
                result = '0x' + b''.join(w.to_bytes(32, 'big') for w in words).hex()
            rows.append(dict(id=request['id'], result=result))
        return SimpleNamespace(raise_for_status=lambda:None, json=lambda:list(reversed(rows)))
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=get_block,
        get_code=lambda *a, **k:b'', contract=lambda **k:None),
        provider=SimpleNamespace(endpoint_uri='https://offline.invalid'))
    def initialize(reader, chain):
        readers.append(chain)
        reader.w3=w3; reader.head=get_block(4); reader.cache={}; reader.blocks={}
    monkeypatch.setattr(tvl_sources.ArchiveReader, '__init__', initialize)
    monkeypatch.setattr(tvl_batch.requests, 'post', post)
    quotes = Mock(side_effect=lambda targets, **k:{t:(2, 'ok', {'provider':'yearn-prices'}) for t in targets})
    monkeypatch.setattr(tvl_batch, 'fetch_yearn_prices_batch', quotes)
    monkeypatch.setattr(tvl_batch, 'fetch_yearn_price', Mock(side_effect=AssertionError('unexpected exact lookup')))
    monkeypatch.setattr(refresh, 'chain_source', lambda _:None)
    options=RefreshOptions('staging', ('eth',), 172800, tmp_path, first_close=86399)
    worker=refresh.RefreshWorker(options, SimpleNamespace(id='offline', emit=lambda *a, **k:None,
                                                         checkpoint=lambda *a:None))
    assert worker.collect_tvl({})['processed'] == 2
    assert readers == [1]  # One reader spans both daily durable checkpoints.
    assert len(calls) == 4  # Two code batches and two contract-call batches, not per-vault HTTP calls.
    assert quotes.call_count == 2
    with closing(connect('staging')) as conn:
        runs=[r[0] for r in conn.execute('SELECT id FROM tvl_runs ORDER BY id')]
        for run in runs:
            out=tmp_path/str(run); export_tvl(conn,out,run)
            history=json.loads((out/'tvl.json').read_text())['history']
            # $200 parent + $100 child - $50 internal strategy allocation.
            assert history[0]['external_tvl_usd'] == '250'
        prices=tvl_batch.Prices(conn)
        prices.prime([(1,asset,86399),(1,asset,172799)])
        assert quotes.call_count == 2  # Persistent cache survives reader recreation.
        assert prices.get(1,asset,86399)[:2] == (2, 'ok')


def test_saved_missing_tvl_prices_need_explicit_repair(postgres_database, monkeypatch):
    with closing(connect('staging')) as conn:
        init_db(conn)
        exact=Mock(return_value=(None,'missing',{'provider':'yearn-prices'}))
        monkeypatch.setattr(tvl_batch,'fetch_yearn_prices_batch',lambda *a, **k:{})
        monkeypatch.setattr(tvl_batch,'fetch_yearn_price',exact)
        key=(1,address(1),86399)
        assert tvl_batch.Prices(conn).get(*key)[:2] == (None,'missing')
        assert tvl_batch.Prices(conn).get(*key)[:2] == (None,'missing')
        assert exact.call_count == 1
        assert tvl_batch.Prices(conn,retry_missing=True).get(*key)[:2] == (None,'missing')
        assert exact.call_count == 2
        monkeypatch.setenv('YEARN_PRICE_PROD_BASE_URL','https://different.invalid')
        tvl_batch.Prices(conn).get(*key)
        assert exact.call_count == 3  # Endpoint changes cannot silently reuse unrelated evidence.


def test_weekly_pricing_does_not_retry_historical_missing_quotes(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh,'chain_source',lambda _:None)
    conn=Mock();monkeypatch.setattr(refresh,'connect',lambda *a, **k:conn)
    seen=[]
    def reports(conn, **kwargs):
        seen.append(kwargs)
        return [dict(chain_id=1,asset=address(1),block_timestamp=100)]
    monkeypatch.setattr(refresh.pricing,'report_pricing_targets',reports)
    report_apply=Mock();monkeypatch.setattr(refresh.pricing,'price_unpriced_reports',report_apply)
    fee_select=Mock(return_value=SimpleNamespace(targets=[(1,address(1),86399)]))
    fee_apply=Mock();monkeypatch.setattr(refresh.fee_valuation,'fee_pricing_work',fee_select)
    monkeypatch.setattr(refresh.fee_valuation,'price_fees',fee_apply)
    worker=refresh.RefreshWorker(RefreshOptions('staging',('eth',),172800,tmp_path),SimpleNamespace(id='offline'))
    def drain(saved,select,apply,key,**kwargs):
        apply(select(None,500));return {}
    monkeypatch.setattr(worker,'drain',drain)
    worker.price_reports({});worker.price_fees({})
    assert seen[0]['retry_missing'] is False
    assert report_apply.call_args.kwargs['retry_missing'] is False
    assert fee_select.call_args.kwargs['retry_missing'] is False
    assert fee_apply.call_args.kwargs['retry_missing'] is False


def test_invalid_code_batch_cannot_mark_vault_undeployed(monkeypatch):
    reader=object.__new__(tvl_batch.BatchedReader)
    reader.w3=SimpleNamespace(provider=SimpleNamespace(endpoint_uri='https://offline.invalid'))
    reader.cache={}
    response=SimpleNamespace(raise_for_status=lambda:None,
        json=lambda:[dict(id=True,result='0x')])
    monkeypatch.setattr(tvl_batch.requests,'post',lambda *a, **k:response)
    reader.prefetch_code([address(1)],123)
    assert reader.cache == {}  # Missing/malformed evidence requires a direct code probe.
