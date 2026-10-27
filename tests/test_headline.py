import json
from decimal import Decimal

import pytest

from yearn_data.analysis import create_analysis_run, complete_analysis_run, write_output, run_lifetime_yield
from yearn_data.headline import (
    LIFETIME_YIELD_HEADLINE_KEY,
    build_lifetime_yield_headline,
    latest_lifetime_yield_anchor,
    lifetime_yield_headline_json,
    publish_lifetime_yield_headline,
    _comparable_previous,
    _coverage_digest,
)
from yearn_data.storage import connect, init_db


class FakeRedis:
    def __init__(self, raw=None):
        self.raw = raw
        self.set_args = None
        self.set_kwargs = None

    def get(self, key):
        assert key == LIFETIME_YIELD_HEADLINE_KEY
        return self.raw

    def set(self, *args, **kwargs):
        self.set_args = args
        self.set_kwargs = kwargs
        self.raw = args[1]
        return True


def _db_with_lifetime_yield(tmp_path, value="125.123456789"):
    conn = connect('staging')
    init_db(conn)
    run_id = create_analysis_run(conn, "lifetime-yield", {'before_timestamp': 86400})
    write_output(
        conn,
        run_id,
        "total_yield_summary",
        {"dimension": "all", "key": "all", "net_yield_usd": value,
         "reports": 1, "priced_reports": 1, "unpriced_reports": 0},
    )
    write_output(conn, run_id, 'reports', {'tx_hash': '0xreport', 'block_timestamp': 100,
                                        'net_yield_usd': value})
    conn.commit()
    complete_analysis_run(conn, run_id)
    return conn, run_id


def test_latest_lifetime_yield_anchor_reads_completed_summary(postgres_database, tmp_path):
    conn, run_id = _db_with_lifetime_yield(tmp_path)

    value, default_run_id = latest_lifetime_yield_anchor(conn)

    assert value == Decimal("125.123456789")
    assert default_run_id.startswith(f"analysis-run-{run_id}:")


def test_build_lifetime_yield_headline_computes_non_negative_rate():
    payload = build_lifetime_yield_headline(
        Decimal("125"),
        {"net_yield_usd": "100", "as_of_ms": 1_000},
        now_ms=11_000,
        run_id="run",
    )

    assert payload["prev_net_yield_usd"] == Decimal("100")
    assert payload["prev_as_of_ms"] == 1_000
    assert payload["rate_usd_per_sec"] == 2.5

    payload = build_lifetime_yield_headline(
        Decimal("90"),
        {"net_yield_usd": "100", "as_of_ms": 1_000},
        now_ms=11_000,
        run_id="run",
    )
    assert payload["rate_usd_per_sec"] == 0.0


def test_lifetime_yield_headline_json_keeps_decimal_values_numeric():
    payload = build_lifetime_yield_headline(Decimal("125.123456789"), None, now_ms=1_000, run_id="run")

    payload_json = lifetime_yield_headline_json(payload)

    assert '"net_yield_usd": 125.123456789' in payload_json
    assert '"prev_net_yield_usd": 125.123456789' in payload_json
    assert json.loads(payload_json)["net_yield_usd"] == 125.123456789


def test_publish_lifetime_yield_headline_sets_atomic_json_with_ttl(postgres_database, tmp_path):
    conn, _ = _db_with_lifetime_yield(tmp_path)
    redis = FakeRedis(raw=b'{"net_yield_usd":"100","as_of_ms":20000}')

    payload, payload_json = publish_lifetime_yield_headline(
        conn,
        redis_client=redis,
        ttl=3600,
        now_ms=11_000,
        run_id="run",
    )

    # A legacy anchor has no evidence of complete valuation or stable history.
    assert payload["rate_usd_per_sec"] == 0
    assert payload['as_of_ms'] == 20_000
    assert redis.set_args == (LIFETIME_YIELD_HEADLINE_KEY, payload_json)
    assert redis.set_kwargs == {"ex": 3600}
    assert json.loads(payload_json)["run_id"] == "run"


@pytest.mark.parametrize('priced', [0, 1, 2])
def test_incomplete_analyses_cannot_replace_redis_anchor(postgres_database, priced):
    conn = connect('staging')
    init_db(conn)
    for index in range(2):
        conn.execute('''INSERT INTO strategy_reports
            (chain_id,version,vault_address,strategy_address,tx_hash,log_index,block_number,
             block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw)
            VALUES(1,'v3','vault','strategy',?,0,10,?,'asset',6,'7000000','0','7000000')''',
            (f'report-{index}', 100+index))
        if index < priced:
            conn.execute('''INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status)
                VALUES(1,'asset',?,'yearn-prices',2,'ok')''', (100+index,))
    conn.commit()
    run_lifetime_yield(conn, fallback_price_source=None)
    previous = b'{"net_yield_usd":100,"as_of_ms":1000}'
    redis = FakeRedis(previous)
    if priced < 2:
        with pytest.raises(ValueError, match='incomplete valuation'):
            publish_lifetime_yield_headline(conn, redis_client=redis, now_ms=2000)
        assert redis.raw == previous and redis.set_args is None
        with pytest.raises(ValueError, match='incomplete valuation'):
            publish_lifetime_yield_headline(conn, dry_run=True)
    else:
        payload, _ = publish_lifetime_yield_headline(conn, redis_client=redis, now_ms=2000)
        assert payload['net_yield_usd'] == 28 and redis.set_args is not None


def test_complete_zero_is_publishable_but_missing_completeness_is_unknown(postgres_database, tmp_path):
    conn, run_id = _db_with_lifetime_yield(tmp_path, value='0')
    payload, _ = publish_lifetime_yield_headline(conn, redis_client=FakeRedis())
    assert payload['net_yield_usd'] == 0
    conn.execute("UPDATE analysis_outputs SET row_json=? WHERE run_id=? AND name='total_yield_summary'",
                 (json.dumps({'net_yield_usd': '0'}), run_id))
    conn.commit()
    with pytest.raises(ValueError, match='incomplete valuation'):
        latest_lifetime_yield_anchor(conn)


def test_only_new_tail_earnings_contribute_to_redis_extrapolation(postgres_database, tmp_path):
    conn, _ = _db_with_lifetime_yield(tmp_path, value='100')
    redis = FakeRedis()
    publish_lifetime_yield_headline(conn, redis_client=redis, now_ms=1000)
    original = {'tx_hash': '0xreport', 'block_timestamp': 100, 'net_yield_usd': '100'}
    tail = {'tx_hash': '0xtail', 'block_timestamp': 86500, 'net_yield_usd': '5'}
    cases = [
        ([original, tail], '105', 11_000, Decimal('0.5')),
        # Historical repricing must not appear as newly earned yield.
        ([{**original, 'net_yield_usd': '110'}, tail], '115', 21_000, 0),
        # This late report is newer than the last stored report but inside the prior closed cutoff.
        ([{**original, 'net_yield_usd': '110'}, tail,
          {'tx_hash': '0xbackfill', 'block_timestamp': 87000, 'net_yield_usd': '7'}], '122', 31_000, 0),
    ]
    for reports, value, now, expected_rate in cases:
        run_id = create_analysis_run(conn, 'lifetime-yield', {'before_timestamp': 172800})
        write_output(conn, run_id, 'total_yield_summary', dict(
            net_yield_usd=value, reports=len(reports), priced_reports=len(reports), unpriced_reports=0))
        for report in reports:
            write_output(conn, run_id, 'reports', report)
        complete_analysis_run(conn, run_id)
        payload, serialized = publish_lifetime_yield_headline(conn, redis_client=redis, now_ms=now)
        assert payload['rate_usd_per_sec'] == expected_rate
        assert json.loads(serialized)['valuation_complete'] is True


@pytest.mark.parametrize('change', ['policy', 'removed', 'missing', 'unknown_cutoff', 'stable'])
def test_rate_requires_comparable_historical_coverage(change):
    reports = [{'block_timestamp': 100, 'net_yield_usd': '100'}]
    old = dict(valuation_complete=True, valuation_policy='policy', coverage_through_timestamp=100,
               coverage_digest=_coverage_digest(reports))
    if change == 'policy':
        old['valuation_policy'] = 'different-policy'
    elif change == 'removed':
        old['coverage_digest'] = 'previously-included-another-report'
    elif change == 'missing':
        del old['valuation_complete']
    elif change == 'unknown_cutoff':
        old['coverage_through_timestamp'] = None
    assert _comparable_previous(old, reports, 'policy') == (old if change == 'stable' else None)
