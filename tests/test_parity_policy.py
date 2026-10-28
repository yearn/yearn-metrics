"""Known local parity guesses cannot become accepted historical USD evidence."""
import json
import csv
from decimal import Decimal

import pytest

from yearn_data import pricing_adapters as adapters, pricing_clients as clients, pricing
from yearn_data.analysis import run_lifetime_yield, run_vault_volume, run_vault_fees
from yearn_data.storage import connect, init_db, from_json
from yearn_data.pricing_policy import quote_classification
from yearn_data.exports import export_analysis

USDC = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'
WETH_POLYGON = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'


@pytest.mark.parametrize(('evidence', 'expected'), [
    ({'source': 'canonical_btc_equivalent_fallback'}, 'estimated'),
    ({'source': 'ycrv_crv_par_fallback'}, 'estimated'),
    ({'source': 'curve_lp_balance_fallback', 'reserves': [{'source': 'canonical_ethereum_stable_fallback'}]}, 'estimated'),
    ({'provider': 'defillama'}, 'provider-quote'),
    ({'provider': 'yearn-prices', 'upstream_source': 'provider-estimate'}, 'provider-quote'),
    ({'source': 'erc4626_wrapper_fallback', 'underlying_payload': {'provider': 'defillama'}}, 'derived'),
    ({'provider': 'defillama', 'retired_estimate': {'evidence_json': '{"source":"canonical_ethereum_stable_fallback"}'}}, 'provider-quote'),
    ({}, 'legacy-unclassified'), ('not JSON', 'legacy-unclassified'),
])
def test_parity_classification_preserves_observations_derivations_and_opaque_audit(evidence, expected):
    assert quote_classification(evidence) == expected


def test_missing_provider_observations_stay_missing_in_batch_and_single_paths(monkeypatch):
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self): return {'coins': {}}
    monkeypatch.setattr(clients.requests, 'get', lambda *a, **kw: Response())
    targets = [(1, USDC, 100), (137, WETH_POLYGON, 100)]
    batch = adapters.fetch_defillama_prices_batch(targets)
    for target in targets:
        assert batch[target][:2] == (None, 'missing')
        assert adapters.fetch_defillama_price(*target)[:2] == (None, 'missing')


def seed_valuation_cases(conn):
    cases = [
        ('observed', 2, {'provider': 'defillama', 'matched': {'timestamp': 100}}, 'observed', '14'),
        ('stable', 1, {'source': 'canonical_ethereum_stable_fallback'}, 'estimated', None),
        ('alias', 3000, {'source': 'canonical_polygon_weth_fallback'}, 'estimated', None),
        ('nested', 1.25, {'source': 'curve_lp_balance_fallback', 'reserves': [
            {'underlying': {'source': 'canonical_ethereum_stable_fallback'}}]}, 'estimated', None),
        ('derived', 3, {'provider': 'local-adapter', 'source': 'erc4626_wrapper_fallback'}, 'derived', '21'),
        ('zero', 1, {'source': 'canonical_ethereum_stable_fallback'}, 'estimated', '0'),
    ]
    for asset, price, evidence, _, _ in cases:
        amount = '0' if asset == 'zero' else '7000000'
        conn.execute('''INSERT INTO strategy_reports
            (chain_id,version,vault_address,strategy_address,tx_hash,log_index,block_number,
             block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw)
            VALUES(1,'v3','vault','strategy',?,0,10,100,?,6,?,'0',?)''', (asset, asset, amount, amount))
        conn.execute('''INSERT INTO vault_flows
            (chain_id,version,vault_address,direction,tx_hash,log_index,block_number,
             block_timestamp,asset,asset_decimals,assets_raw,shares_raw)
            VALUES(1,'v3','vault','deposit',?,0,10,100,?,6,?,?)''', (asset, asset, amount, amount))
        conn.execute('''INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status,raw_json)
            VALUES(1,?,100,'defillama',?,'ok',?)''', (asset, price, json.dumps(evidence)))
    conn.commit()
    return cases


def test_earnings_and_volume_expose_retired_estimates_without_inventing_zero(postgres_database, tmp_path):
    conn = connect('staging')
    init_db(conn)
    cases = seed_valuation_cases(conn)
    for run_id, name, field in [
        (run_lifetime_yield(conn, price_source='defillama', fallback_price_source=None), 'reports', 'net_yield_usd'),
        (run_vault_volume(conn), 'vault_flows', 'amount_usd'),
    ]:
        rows = {row['asset']: row for row in [from_json(r[0]) for r in conn.execute(
            'SELECT row_json FROM analysis_outputs WHERE run_id=? AND name=?', (run_id, name))]}
        for asset, _, _, classification, expected in cases:
            row = rows[asset]
            assert row['price_classification'] == classification
            value = row[field]
            assert (None if value is None else Decimal(value)) == (None if expected is None else Decimal(expected))
            if classification == 'estimated':
                assert row['price_usd'] is None
        assert sum(Decimal(row[field]) for row in rows.values() if row[field] is not None) == 35
        job = 'lifetime-yield' if name == 'reports' else 'vault-volume'
        export_analysis(conn, job, tmp_path/name, run_id=run_id)
        with (tmp_path/name/(name+'.csv')).open() as exported:
            csv_rows = {row['asset']: row for row in csv.DictReader(exported)}
        assert csv_rows['stable']['price_classification'] == 'estimated'
        assert csv_rows['stable']['price_usd'] == '' and csv_rows['stable'][field] == ''
        summary_name = 'total_yield_summary' if name == 'reports' else 'volume_summary'
        summary = from_json(conn.execute('SELECT row_json FROM analysis_outputs WHERE run_id=? AND name=?',
                                        (run_id, summary_name)).fetchone()[0])
        priced = 'priced_reports' if name == 'reports' else 'priced_events'
        unpriced = 'unpriced_reports' if name == 'reports' else 'unpriced_events'
        assert summary[priced] == 3 and summary[unpriced] == 3


@pytest.mark.parametrize('original', [
    {'source': 'canonical_ethereum_stable_fallback'},
    {'source': 'curve_lp_balance_fallback', 'reserve': {'source': 'canonical_ethereum_stable_fallback'}},
])
def test_rejected_cached_estimate_is_reacquired_and_audited_but_observations_survive_failure(postgres_database, monkeypatch, original):
    conn = connect('staging'); init_db(conn)
    conn.execute('''INSERT INTO strategy_reports
        (chain_id,version,vault_address,strategy_address,tx_hash,log_index,block_number,
         block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw)
        VALUES(1,'v3','vault','strategy','report',0,10,100,?,6,'7','0','7')''', (USDC,))
    conn.execute('''INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status,raw_json)
        VALUES(1,?,100,'defillama',1,'ok',?)''', (USDC, json.dumps(original)))
    conn.commit()
    monkeypatch.setattr(adapters, 'fetch_defillama_prices_batch', lambda targets: {
        target: (None, 'retryable', {'provider': 'defillama', 'failure_class': 'transport'}) for target in targets})
    assert pricing.price_unpriced_reports(conn, source='defillama', fallback=None, onchain_fallbacks=False) == 1
    row = conn.execute('SELECT * FROM prices').fetchone()
    assert row['price_usd'] is None and row['status'] == 'retryable'
    audit = json.loads(row['raw_json'])['retired_estimate']
    assert audit['price_usd'] == 1 and json.loads(audit['evidence_json']) == original
    # Audit metadata must not repeatedly select an already rejected estimate.
    assert pricing.price_unpriced_reports(conn, source='defillama', fallback=None, onchain_fallbacks=False) == 0
    with conn:
        conn.execute("UPDATE prices SET price_usd=2,status='ok',raw_json=?", (json.dumps({'provider': 'defillama'}),))
    pricing._write_defillama_price_row(conn, dict(chain_id=1,asset=USDC,block_timestamp=100,block_number=10),
        {(1, USDC, 100): (None, 'retryable', {})}, None, onchain_fallbacks=False)
    assert conn.execute('SELECT price_usd FROM prices').fetchone()[0] == 2


def test_legacy_fee_refunds_and_mints_keep_unknown_usd_separate_from_zero(postgres_database):
    conn = connect('staging'); init_db(conn)
    conn.execute('''INSERT INTO strategy_reports
        (chain_id,version,vault_address,strategy_address,tx_hash,log_index,block_number,
         block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw,total_fees_raw,total_refunds_raw)
        VALUES(1,'v3','vault','strategy','v3',0,10,100,'asset',6,'0','0','0','0','7000000')''')
    conn.execute('''INSERT INTO vault_fee_events
        (chain_id,version,source,vault_address,tx_hash,log_index,block_number,block_timestamp,
         asset,asset_decimals,fee_raw) VALUES(1,'v2','v2_harvest_fee_mint','vault','v2',0,10,100,'asset',6,'7000000')''')
    conn.execute('''INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status,raw_json)
        VALUES(1,'asset',100,'defillama',1,'ok',?)''', (json.dumps({'source': 'canonical_ethereum_stable_fallback'}),))
    conn.commit()
    run_id = run_vault_fees(conn)
    rows = {row['version']: row for row in [from_json(r[0]) for r in conn.execute(
        "SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='fee_events'", (run_id,))]}
    assert rows['v2']['v2_fee_mint_usd'] is None
    assert rows['v3']['v3_total_refunds_usd'] is None
    assert Decimal(rows['v3']['v3_total_fees_usd']) == 0
    assert all(row['price_classification'] == 'estimated' and not row['price_accepted'] for row in rows.values())


def test_valid_secondary_quote_wins_over_retired_primary_estimate(postgres_database):
    conn = connect('staging'); init_db(conn)
    conn.execute('''INSERT INTO strategy_reports
        (chain_id,version,vault_address,strategy_address,tx_hash,log_index,block_number,
         block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw)
        VALUES(1,'v3','vault','strategy','report',0,10,100,'asset',6,'7000000','0','7000000')''')
    conn.executemany('''INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status,raw_json)
        VALUES(1,'asset',100,?,?,'ok',?)''', [
        ('defillama', 1, json.dumps({'source': 'canonical_ethereum_stable_fallback'})),
        ('yearn-prices', 2, json.dumps({'provider': 'yearn-prices'})),
    ])
    conn.commit()
    run_id = run_lifetime_yield(conn, price_source='defillama', fallback_price_source='yearn-prices')
    row = from_json(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='reports'", (run_id,)).fetchone()[0])
    assert row['price_source'] == 'yearn-prices' and row['price_classification'] == 'provider-quote'
    assert Decimal(row['net_yield_usd']) == 14


@pytest.mark.parametrize('raw', [0, 100_000_000])
def test_tvl_rejects_cached_parity_evidence_but_preserves_genuine_zero(raw):
    from types import SimpleNamespace
    from yearn_data import tvl_sources
    vault = dict(chain_id=1, version='v3', address='0x'+'11'*20, asset=USDC,
                 asset_decimals=6, name='fixture')
    reader = SimpleNamespace(block_at=lambda t: 10, exists=lambda *a: True,
                             uint=lambda *a: raw)
    cache = {(1, USDC.lower(), 1000): (1, 'ok', {'source': 'canonical_ethereum_stable_fallback'})}
    row = tvl_sources._snapshot(vault, 1000, reader, cache, 'defillama')
    if raw:
        assert row['tvl_usd'] is None and row['status'] == 'unavailable'
        assert row['reason'] == 'rejected_estimate'
        assert row['pricing']['classification'] == 'estimated'
        assert Decimal(row['asset_units']) == 100
    else:
        assert row['tvl_usd'] == '0' and row['status'] == 'ok'
