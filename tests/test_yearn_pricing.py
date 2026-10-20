import json

import pytest
from yearn_data.config import CHAINS
from yearn_data.pricing import _yearn_prices_batches, fetch_yearn_price, normalize_yearn_price_timestamp, price_unpriced_reports
from yearn_data.storage import connect, from_json, init_db, seed_chains

def test_yearn_prices_batches_normalize_and_respect_route_limits():
    first = "0x0000000000000000000000000000000000000001"
    second = "0x0000000000000000000000000000000000000002"
    targets = [(1, first, day * 86_400) for day in range(91)] + [(1, second, 100)]

    batches = _yearn_prices_batches(targets, max_tokens=2)

    assert sum(len(batch) for batch in batches) == 92
    for batch in batches:
        by_token = {}
        for chain_id, token, timestamp in batch:
            by_token.setdefault((chain_id, token), []).append(timestamp)
            assert timestamp % 86_400 == 86_399
        assert len(by_token) <= 2
        assert all(len(timestamps) <= 90 for timestamps in by_token.values())


def test_price_unpriced_reports_uses_yearn_prices_eod_and_exact_fallback(monkeypatch, tmp_path):
    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    asset = "0x0000000000000000000000000000000000000002"
    reports = [("0xaaa", 100), ("0xbbb", 200), ("0xccc", 90_000)]
    for tx_hash, timestamp in reports:
        conn.execute(
            """
            INSERT INTO strategy_reports (
                chain_id, version, vault_address, strategy_address, tx_hash, log_index,
                block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw,
                net_raw, extra_json
            )
            VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
                    '0x0000000000000000000000000000000000000003',
                    ?, 0, 10, ?, ?, 6, '1', '0', '1', '{}')
            """,
            (tx_hash, timestamp, asset),
        )
    conn.commit()

    batch_calls = []
    exact_calls = []

    def fake_batch(targets):
        batch_calls.extend(targets)
        first_eod = normalize_yearn_price_timestamp(100)
        return {
            (1, asset, first_eod): (
                1.0,
                "ok",
                {
                    "adapter": "batchHistorical",
                    "confidence": 0.99,
                    "normalized_timestamp": first_eod,
                    "provider": "yearn-prices",
                    "symbol": "USDC",
                    "upstream_source": "defillama",
                },
            )
        }

    def fake_exact(chain_id, token_address, timestamp):
        exact_calls.append((chain_id, token_address, timestamp))
        return (
            2.0,
            "ok",
            {
                "adapter": "historical",
                "confidence": 0.8,
                "normalized_timestamp": timestamp,
                "provider": "yearn-prices",
                "symbol": "USDC",
                "upstream_source": "chainlink",
            },
        )

    monkeypatch.setattr("yearn_data.pricing.fetch_yearn_prices_batch", fake_batch)
    monkeypatch.setattr("yearn_data.pricing.fetch_yearn_price", fake_exact)

    count = price_unpriced_reports(conn, source="yearn-prices")

    assert count == 3
    assert sorted(batch_calls) == [
        (1, asset, normalize_yearn_price_timestamp(100)),
        (1, asset, normalize_yearn_price_timestamp(90_000)),
    ]
    assert exact_calls == [(1, asset, normalize_yearn_price_timestamp(90_000))]
    rows = conn.execute(
        "SELECT timestamp, price_usd, status, raw_json FROM prices ORDER BY timestamp"
    ).fetchall()
    assert [(row["timestamp"], row["price_usd"], row["status"]) for row in rows] == [
        (100, 1.0, "ok"),
        (200, 1.0, "ok"),
        (90_000, 2.0, "ok"),
    ]
    payloads = [from_json(row["raw_json"]) for row in rows]
    assert [payload["requested_timestamp"] for payload in payloads] == [100, 200, 90_000]
    assert [payload["normalized_timestamp"] for payload in payloads] == [86_399, 86_399, 172_799]
    assert [payload["adapter"] for payload in payloads] == [
        "batchHistorical",
        "batchHistorical",
        "historical",
    ]


def test_price_unpriced_reports_uses_defillama_only_for_unresolved_yearn_prices(
    monkeypatch, tmp_path
):
    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    first_asset = "0x0000000000000000000000000000000000000002"
    second_asset = "0x0000000000000000000000000000000000000004"
    for index, asset in enumerate((first_asset, second_asset)):
        conn.execute(
            """
            INSERT INTO strategy_reports (
                chain_id, version, vault_address, strategy_address, tx_hash, log_index,
                block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw,
                net_raw, extra_json
            ) VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
                      '0x0000000000000000000000000000000000000003',
                      ?, 0, ?, 100, ?, 6, '1', '0', '1', '{}')
            """,
            (f"0x{index}", 10 + index, asset),
        )
    conn.commit()

    normalized = normalize_yearn_price_timestamp(100)

    def fake_yearn_batch(_targets):
        return {
            (1, first_asset, normalized): (
                1.0,
                "ok",
                {
                    "adapter": "batchHistorical",
                    "normalized_timestamp": normalized,
                    "provider": "yearn-prices",
                    "upstream_source": "chainlink",
                },
            )
        }

    monkeypatch.setattr("yearn_data.pricing.fetch_yearn_prices_batch", fake_yearn_batch)
    monkeypatch.setattr(
        "yearn_data.pricing.fetch_yearn_price",
        lambda *_args: (
            None,
            "missing",
            {
                "adapter": "historical",
                "failure_class": "not-found",
                "normalized_timestamp": normalized,
                "provider": "yearn-prices",
            },
        ),
    )
    defillama_requests = []

    def fake_defillama_batch(targets):
        defillama_requests.extend(targets)
        return {
            target: (2.0, "ok", {"source": "defillama"})
            for target in targets
        }

    monkeypatch.setattr("yearn_data.pricing.fetch_defillama_prices_batch", fake_defillama_batch)

    count = price_unpriced_reports(conn)

    assert count == 3
    assert defillama_requests == [(1, second_asset, 100)]
    rows = conn.execute(
        "SELECT token_address, source, price_usd, status FROM prices ORDER BY token_address, source"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (first_asset, "yearn-prices", 1.0, "ok"),
        (second_asset, "defillama", 2.0, "ok"),
        (second_asset, "yearn-prices", None, "missing"),
    ]


def test_price_unpriced_reports_falls_back_after_yearn_prices_transport_failure(
    monkeypatch, tmp_path
):
    from yearn_data.pricing import YearnPricesRequestError

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    asset = "0x0000000000000000000000000000000000000002"
    conn.execute(
        """
        INSERT INTO strategy_reports (
            chain_id, version, vault_address, strategy_address, tx_hash, log_index,
            block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw,
            net_raw, extra_json
        ) VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
                  '0x0000000000000000000000000000000000000003',
                  '0xabc', 0, 10, 100, ?, 6, '1', '0', '1', '{}')
        """,
        (asset,),
    )
    conn.commit()

    monkeypatch.setattr(
        "yearn_data.pricing.fetch_yearn_prices_batch",
        lambda _targets: (_ for _ in ()).throw(YearnPricesRequestError("batch unavailable")),
    )
    monkeypatch.setattr(
        "yearn_data.pricing.fetch_yearn_price",
        lambda *_args: (
            None,
            "retryable",
            {"failure_class": "retryable", "provider": "yearn-prices"},
        ),
    )
    monkeypatch.setattr(
        "yearn_data.pricing.fetch_defillama_prices_batch",
        lambda targets: {target: (2.0, "ok", {"source": "defillama"}) for target in targets},
    )

    assert price_unpriced_reports(conn) == 2
    rows = conn.execute(
        "SELECT source, price_usd, status FROM prices ORDER BY source"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("defillama", 2.0, "ok"),
        ("yearn-prices", None, "retryable"),
    ]


def test_yearn_prices_exact_keeps_exhausted_transport_failure_retryable(monkeypatch):
    from yearn_data.pricing import YearnPricesRequestError

    monkeypatch.setattr(
        "yearn_data.pricing._yearn_prices_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(YearnPricesRequestError("request exhausted")),
    )

    price, status, payload = fetch_yearn_price(
        1,
        "0x0000000000000000000000000000000000000002",
        100,
    )

    assert price is None
    assert status == "retryable"
    assert payload["failure_class"] == "retryable"
    assert payload["normalized_timestamp"] == 86_399


def test_yearn_prices_exact_keeps_terminal_response_invalid(monkeypatch):
    from yearn_data.pricing import YearnPricesRequestError

    monkeypatch.setattr(
        "yearn_data.pricing._yearn_prices_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            YearnPricesRequestError("bad response", retryable=False)
        ),
    )

    price, status, payload = fetch_yearn_price(
        1,
        "0x0000000000000000000000000000000000000002",
        100,
    )

    assert price is None
    assert status == "invalid"
    assert payload["failure_class"] == "invalid"




@pytest.mark.parametrize('problem', ['wrong_day', 'wrong_asset', 'duplicate_zero', 'invalid_price', 'missing_source'])
def test_yearn_batch_rejects_invalid_evidence(monkeypatch, problem):
    from yearn_data.pricing import fetch_yearn_prices_batch
    asset = '0x0000000000000000000000000000000000000002'
    raw = {'timestamp': 86399, 'price': 2.5, 'source': 'oracle'}
    key = 'ethereum:' + asset
    points = [raw]
    if problem == 'wrong_day': raw['timestamp'] += 86400
    if problem == 'wrong_asset': key = 'ethereum:' + '0x' + '3' * 40
    if problem == 'duplicate_zero': points = [{**raw, 'price': 0}, raw]
    if problem == 'invalid_price': raw['price'] = True
    if problem == 'missing_source': raw.pop('source')
    monkeypatch.setattr('yearn_data.pricing._yearn_prices_get', lambda *a, **kw: {'coins': {key: {'prices': points}}})
    assert fetch_yearn_prices_batch([(1, asset, 100)]) == {}


@pytest.mark.parametrize('status', [401, 403])
def test_yearn_authentication_stops_without_fallback(monkeypatch, status):
    from yearn_data import pricing
    monkeypatch.setenv('YEARN_PRICE_PROD_KEY', 'test-only')
    class Response:
        status_code = status
    calls = []
    monkeypatch.setattr(pricing.requests, 'get', lambda *a, **kw: calls.append(a) or Response())
    with pytest.raises(pricing.YearnPricesAuthenticationError):
        pricing.fetch_yearn_price(1, '0x' + '2' * 40, 100)
    assert len(calls) == 1


def test_yearn_only_command_disables_both_fallbacks(tmp_path, monkeypatch):
    from yearn_data import cli
    calls = []
    monkeypatch.setattr(cli, 'price_unpriced_reports', lambda conn, **kw: calls.append(kw) or 0)
    assert cli.main(['--db', str(tmp_path / 'prices.sqlite'), 'price', '--source', 'yearn-prices',
                     '--no-provider-fallback', '--no-onchain-fallbacks']) == 0
    assert calls[0]['source'] == 'yearn-prices'
    assert calls[0]['fallback'] is None
    assert calls[0]['onchain_fallbacks'] is False


def test_gnosis_price_namespace_requires_no_rpc():
    from yearn_data.pricing import yearn_prices_token_key
    assert yearn_prices_token_key(100, '0x' + '2' * 40) == 'gnosis:0x' + '2' * 40


@pytest.mark.parametrize("exact_outcome", ["ok", "missing", "invalid"])
def test_malformed_batch_preserves_other_targets_and_exact_provider_fallbacks(
    tmp_path, monkeypatch, exact_outcome
):
    from yearn_data import pricing

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    assets = ["0x" + "2" * 40, "0x" + "4" * 40]
    for index, asset in enumerate(assets):
        conn.execute(
            """
            INSERT INTO strategy_reports (
                chain_id, version, vault_address, strategy_address, tx_hash, log_index,
                block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw,
                net_raw, extra_json
            ) VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
                      '0x0000000000000000000000000000000000000003', ?, 0,
                      10, 100, ?, 6, '1000000', '0', '1000000', '{}')
            """, (f"0x{index}", asset),
        )
    conn.commit()
    monkeypatch.setenv("YEARN_PRICE_BATCH_TOKENS", "1")
    exact_calls = []

    def fake_get(path, *, params=None, **kwargs):
        if params is not None:
            token_key = next(iter(json.loads(params["coins"])))
            point = {"timestamp": 86399, "price": True if token_key.endswith(assets[0]) else 2.0,
                     "source": "oracle"}
            return {"coins": {token_key: {"prices": [point]}}}
        exact_calls.append(path)
        if exact_outcome == "missing":
            return None
        return {"coins": {"ethereum:" + assets[0]: {
            "timestamp": 172799 if exact_outcome == "invalid" else 86399,
            "price": 3.0, "source": "oracle",
        }}}

    fallback_calls = []
    def fake_fallback(targets):
        fallback_calls.extend(targets)
        return {target: (7.0, "ok", {"source": "defillama"}) for target in targets}

    monkeypatch.setattr(pricing, "_yearn_prices_get", fake_get)
    monkeypatch.setattr(pricing, "fetch_defillama_prices_batch", fake_fallback)
    count = price_unpriced_reports(conn, onchain_fallbacks=False)
    rows = {(row["token_address"], row["source"]): row for row in conn.execute("SELECT * FROM prices")}
    assert rows[assets[1], "yearn-prices"]["price_usd"] == 2.0
    assert len(exact_calls) == 1
    if exact_outcome == "ok":
        assert count == 2
        assert rows[assets[0], "yearn-prices"]["price_usd"] == 3.0
        assert fallback_calls == []
    else:
        assert count == 3
        row = rows[assets[0], "yearn-prices"]
        assert row["price_usd"] is None
        assert row["status"] == exact_outcome
        assert from_json(row["raw_json"])["failure_class"] == (
            "invalid" if exact_outcome == "invalid" else "not-found"
        )
        assert fallback_calls == [(1, assets[0], 100)]
        assert rows[assets[0], "defillama"]["price_usd"] == 7.0


def test_yearn_only_pricing_rejects_enabled_onchain_fallbacks_before_requests(monkeypatch, tmp_path):
    from yearn_data import pricing

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    monkeypatch.setattr(pricing, "_price_rows_with_yearn_prices",
                        lambda *_: pytest.fail("Must reject unsupported policy before fetching"))
    with pytest.raises(ValueError, match="onchain.*DefiLlama.*no-onchain-fallbacks"):
        price_unpriced_reports(conn, source="yearn-prices", fallback=None)


@pytest.mark.parametrize("failure", ["batch-auth", "exact-auth", "missing-key"])
def test_response_error_recovery_preserves_fatal_authentication_and_configuration(
    monkeypatch, tmp_path, failure
):
    from yearn_data import pricing

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    row = {"chain_id": 1, "asset": "0x" + "2" * 40,
           "block_timestamp": 100, "block_number": 10}
    if failure == "missing-key":
        monkeypatch.delenv("YEARN_PRICE_PROD_KEY", raising=False)
        expected_error = ValueError
        message = "missing Yearn Prices API key"
    else:
        def fake_get(path, **kwargs):
            if failure == "exact-auth" and path.endswith("batchHistorical"):
                return {"coins": {}}
            raise pricing.YearnPricesAuthenticationError("authentication failed")
        monkeypatch.setattr(pricing, "_yearn_prices_get", fake_get)
        expected_error = pricing.YearnPricesAuthenticationError
        message = "authentication failed"

    with pytest.raises(expected_error, match=message):
        pricing._price_rows_with_yearn_prices(conn, [row])
    assert conn.execute("SELECT count(*) FROM prices").fetchone()[0] == 0


@pytest.mark.parametrize("problem", ["invalid_price", "duplicate", "wrong_day", "missing_source", "malformed_point"])
def test_default_batch_preserves_valid_points_and_only_verifies_invalid_target(monkeypatch, tmp_path, problem):
    from yearn_data import pricing

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    first, second = "0x" + "2" * 40, "0x" + "4" * 40
    rows = [{"chain_id": 1, "asset": asset, "block_timestamp": timestamp, "block_number": 10}
            for asset, timestamp in [(first, 100), (first, 90000), (second, 100)]]
    monkeypatch.delenv("YEARN_PRICE_BATCH_TOKENS", raising=False)
    batch_calls, exact_calls = [], []

    def fake_get(path, *, params=None, **kwargs):
        if params is not None:
            batch_calls.append(json.loads(params["coins"]))
            bad_point = {"timestamp": 86399, "price": True, "source": "oracle"}
            bad_points = [bad_point]
            if problem == "duplicate":
                bad_points = [{**bad_point, "price": 1.0}, {**bad_point, "price": 0}]
            elif problem == "wrong_day":
                bad_point["timestamp"] = 259199
            elif problem == "missing_source":
                bad_point.update(price=1.0)
                bad_point.pop("source")
            elif problem == "malformed_point":
                bad_points = [None]
            return {"coins": {
                "ethereum:" + first: {"prices": bad_points + [
                    {"timestamp": 172799, "price": 4.0, "source": "oracle"}]},
                "ethereum:" + second: {"prices": [
                    {"timestamp": 86399, "price": 2.0, "source": "oracle"}]},
            }}
        _, timestamp, token_key = path.rsplit("/", 2)
        exact_calls.append((token_key, int(timestamp)))
        return {"coins": {token_key: {"timestamp": int(timestamp), "price": 9.0, "source": "oracle"}}}

    monkeypatch.setattr(pricing, "_yearn_prices_get", fake_get)
    count, unresolved = pricing._price_rows_with_yearn_prices(conn, rows)
    assert count == 3
    assert unresolved == []
    assert len(batch_calls) == 1 and len(batch_calls[0]) == 2
    assert exact_calls == [("ethereum:" + first, 86399)]
    saved = {(row["token_address"], row["timestamp"]): row for row in conn.execute("SELECT * FROM prices")}
    assert saved[first, 90000]["price_usd"] == 4.0
    assert from_json(saved[first, 90000]["raw_json"])["adapter"] == "batchHistorical"
    assert saved[second, 100]["price_usd"] == 2.0


@pytest.mark.parametrize("boolean_price", [False, True])
def test_boolean_prices_are_invalid_in_batch_and_exact_paths(monkeypatch, boolean_price):
    from yearn_data import pricing

    asset = "0x" + "2" * 40
    point = {"timestamp": 86399, "price": boolean_price, "source": "oracle"}
    def fake_get(path, **kwargs):
        return {"coins": {"ethereum:" + asset: {"prices": [point]} if path.endswith("batchHistorical") else point}}
    monkeypatch.setattr(pricing, "_yearn_prices_get", fake_get)
    assert pricing.fetch_yearn_prices_batch([(1, asset, 100)]) == {}
    price, status, evidence = pricing.fetch_yearn_price(1, asset, 100)
    assert price is None
    assert status == evidence["failure_class"] == "invalid"


@pytest.mark.parametrize("numeric_zero", [0, 0.0])
def test_numeric_zero_price_still_means_missing_instead_of_a_usable_zero_price(monkeypatch, numeric_zero):
    from yearn_data import pricing

    asset = "0x" + "2" * 40
    point = {"timestamp": 86399, "price": numeric_zero, "source": "oracle"}
    def fake_get(path, **kwargs):
        return {"coins": {"ethereum:" + asset: {"prices": [point]} if path.endswith("batchHistorical") else point}}
    monkeypatch.setattr(pricing, "_yearn_prices_get", fake_get)
    assert pricing.fetch_yearn_prices_batch([(1, asset, 100)]) == {}
    price, status, evidence = pricing.fetch_yearn_price(1, asset, 100)
    assert price is None
    assert status == "missing"
    assert evidence["failure_class"] == "not-found"


@pytest.mark.parametrize("event_source", ["rpc", "envio"])
def test_run_rejects_unsupported_fallback_flags_before_discovery_or_indexing(monkeypatch, tmp_path, event_source):
    from yearn_data import cli

    monkeypatch.setattr(cli, "get_event_source", lambda: event_source)
    for name in ["discover", "discover_from_envio", "index_all_reports", "import_reports_from_envio"]:
        monkeypatch.setattr(cli, name, lambda *_args, **_kwargs: pytest.fail("Must reject before writes"))
    with pytest.raises(ValueError, match="onchain.*DefiLlama"):
        cli.main(["--db", str(tmp_path / "test.sqlite"), "run", "lifetime-yield", "--no-provider-fallback"])


@pytest.mark.parametrize("fallback_status", ["missing", "retryable", "invalid", "ok"])
def test_yearn_fallback_does_not_replace_good_stored_prices_with_failures(monkeypatch, tmp_path, fallback_status):
    from yearn_data import pricing
    from yearn_data.analysis import run_lifetime_yield

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    assets = ["0x" + digit * 40 for digit in ["2", "4", "6"]]
    for index, asset in enumerate(assets):
        conn.execute(
            """INSERT INTO strategy_reports (
                chain_id, version, vault_address, strategy_address, tx_hash, log_index,
                block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw,
                net_raw, extra_json
            ) VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
                      '0x0000000000000000000000000000000000000003', ?, 0,
                      10, 100, ?, 6, '1000000', '0', '1000000', '{}')""", (f"0x{index}", asset),
        )
    conn.execute("""INSERT INTO prices (chain_id, token_address, timestamp, block_number,
                    source, price_usd, status, raw_json)
                    VALUES (1, ?, 100, 1, 'defillama', 5.0, 'ok', '{"source":"existing-adapter"}')""",
                 (assets[0],))
    conn.execute("""INSERT INTO prices (chain_id, token_address, timestamp, source, status)
                    VALUES (1, ?, 100, 'defillama', 'missing')""", (assets[2],))
    conn.commit()
    monkeypatch.setattr(pricing, "fetch_yearn_prices_batch", lambda _: {})
    monkeypatch.setattr(pricing, "fetch_yearn_price", lambda *_: (None, "missing", {}))

    def fallback(targets):
        return {target: ((9.0 if fallback_status == "ok" else None), fallback_status, {"source": "fetched"})
                if target[1] == assets[0] else (7.0, "ok", {"source": "fetched"}) for target in targets}
    monkeypatch.setattr(pricing, "fetch_defillama_prices_batch", fallback)
    count = price_unpriced_reports(conn, onchain_fallbacks=False)
    saved = {row["token_address"]: row for row in conn.execute("SELECT * FROM prices WHERE source='defillama'")}
    assert saved[assets[0]]["status"] == "ok"
    expected = 9.0 if fallback_status == "ok" else 5.0
    assert saved[assets[0]]["price_usd"] == expected
    if fallback_status != "ok":
        assert saved[assets[0]]["block_number"] == 1
        assert from_json(saved[assets[0]]["raw_json"]) == {"source": "existing-adapter"}
    assert count == (6 if fallback_status == "ok" else 5)
    assert saved[assets[1]]["price_usd"] == saved[assets[2]]["price_usd"] == 7.0
    run_id = run_lifetime_yield(conn)
    summary = from_json(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'",
                                    (run_id,)).fetchone()[0])
    assert summary["unpriced_reports"] == 0
    assert float(summary["net_yield_usd"]) == expected + 14


@pytest.mark.parametrize("fallback", [None, "defillama"])
@pytest.mark.parametrize("ordinary_invocation", ["price", "run"])
def test_open_day_prices_wait_until_close_and_retry_on_an_ordinary_run(
    monkeypatch, tmp_path, fallback, ordinary_invocation
):
    from yearn_data import pricing
    from yearn_data.analysis import run_lifetime_yield

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    asset = "0x" + "2" * 40
    day = 20000 * 86400
    timestamp = day + 1200
    clock = [day + 3600]
    monkeypatch.setattr(pricing.time, "time", lambda: clock[0])
    conn.execute("""INSERT INTO strategy_reports (
        chain_id, version, vault_address, strategy_address, tx_hash, log_index,
        block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw, net_raw
    ) VALUES (1, 'v3', '0x0000000000000000000000000000000000000001',
              '0x0000000000000000000000000000000000000003', '0xtoday', 0,
              10, ?, ?, 6, '1000000', '0', '1000000')""", (timestamp, asset))
    conn.commit()
    calls = []

    def batch(targets):
        calls.extend(targets)
        return {target: (2.0, "ok", {"normalized_timestamp": target[2], "provider": "yearn-prices"})
                for target in targets if target[2] < clock[0]}
    monkeypatch.setattr(pricing, "fetch_yearn_prices_batch", batch)
    monkeypatch.setattr(pricing, "fetch_yearn_price", lambda *_: (None, "missing", {}))
    monkeypatch.setattr(pricing, "fetch_defillama_prices_batch",
                        lambda targets: {t: (1.0, "ok", {}) for t in targets})
    price_unpriced_reports(conn, fallback=fallback, onchain_fallbacks=False)
    assert calls == []
    row = conn.execute("SELECT * FROM prices WHERE source='yearn-prices'").fetchone()
    assert row["status"] == "pending"
    assert row["price_usd"] is None
    assert from_json(row["raw_json"])["failure_class"] == "day-not-closed"
    assert price_unpriced_reports(conn, fallback=fallback, onchain_fallbacks=False) == 0
    clock[0] = day + 86400 + 3600
    if ordinary_invocation == "price":
        assert price_unpriced_reports(conn, fallback=fallback, onchain_fallbacks=False) == 1
    else:
        from yearn_data import cli
        monkeypatch.setenv("YEARN_PRICE_PROD_KEY", "test-only")
        monkeypatch.setattr(cli, "load_environment", lambda *_: None)
        monkeypatch.setattr(cli, "open_db", lambda *_: conn)
        monkeypatch.setattr(cli, "get_event_source", lambda: "rpc")
        monkeypatch.setattr(cli, "discover", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(cli, "index_all_reports", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(cli, "export_analysis", lambda *_args, **_kwargs: [])
        command = ["run", "lifetime-yield", "--no-onchain-fallbacks"]
        if fallback is None:
            command.append("--no-provider-fallback")
        assert cli.main(command) == 0
    assert calls == [(1, asset, day + 86399)]
    row = conn.execute("SELECT * FROM prices WHERE source='yearn-prices'").fetchone()
    assert row["status"] == "ok" and row["price_usd"] == 2.0
    run_id = run_lifetime_yield(conn, fallback_price_source=fallback)
    report = from_json(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='reports'",
                                   (run_id,)).fetchone()[0])
    assert report["price_source"] == "yearn-prices"
    assert report["net_yield_usd"] == "2.0"


@pytest.mark.parametrize("unsupported_chain", [100, 999999])
def test_unsupported_chain_stays_unpriced_without_aborting_configured_chains(monkeypatch, tmp_path, unsupported_chain):
    from yearn_data import pricing

    conn = connect(tmp_path / "test.sqlite")
    init_db(conn)
    seed_chains(conn, CHAINS)
    asset = "0x" + "2" * 40
    for chain_id in [1, unsupported_chain]:
        conn.execute("""INSERT INTO strategy_reports (
            chain_id, version, vault_address, strategy_address, tx_hash, log_index,
            block_number, block_timestamp, asset, asset_decimals, gain_raw, loss_raw, net_raw
        ) VALUES (?, 'v3', '0x0000000000000000000000000000000000000001',
                  '0x0000000000000000000000000000000000000003', '0xchain', 0,
                  10, 100, ?, 6, '1000000', '0', '1000000')""", (chain_id, asset))
    conn.commit()
    monkeypatch.setattr(pricing, "fetch_yearn_prices_batch", lambda _: {})
    monkeypatch.setattr(pricing, "fetch_yearn_price", lambda *_: (None, "missing", {}))
    calls = []
    def defillama(targets):
        assert all(t[0] == 1 for t in targets)
        calls.extend(targets)
        return {t: (3.0, "ok", {}) for t in targets}
    monkeypatch.setattr(pricing, "fetch_defillama_prices_batch", defillama)
    monkeypatch.setattr(pricing, "fetch_defillama_price", lambda *_: pytest.fail("No unsupported exact fallback"))
    monkeypatch.setattr(pricing, "_row_level_fallback_price",
                        lambda *_: pytest.fail("Unsupported chains cannot use onchain adapters"))
    assert price_unpriced_reports(conn) == 4
    rows = {(row["chain_id"], row["source"]): row for row in conn.execute("SELECT * FROM prices")}
    assert calls == [(1, asset, 100)]
    assert rows[1, "defillama"]["status"] == "ok"
    row = rows[unsupported_chain, "defillama"]
    assert row["price_usd"] is None and row["status"] == "missing"
    evidence = from_json(row["raw_json"])
    assert evidence["failure_class"] == "unsupported-chain"
    assert str(unsupported_chain) in evidence["failure_reason"]


@pytest.mark.parametrize("source,has_key", [("yearn-prices", False), ("yearn-prices", True), ("defillama", False)])
@pytest.mark.parametrize("event_source", ["envio", "rpc"])
def test_run_checks_yearn_credentials_before_discovery_and_allows_configured_runs(
    monkeypatch, tmp_path, source, has_key, event_source
):
    from yearn_data import cli

    monkeypatch.setattr(cli, "load_environment", lambda *_: None)
    monkeypatch.setattr(cli, "get_event_source", lambda: event_source)
    monkeypatch.setenv("YEARN_PRICE_PROD_BASE_URL", "https://prices.yearn.dev")
    if has_key:
        monkeypatch.setenv("YEARN_PRICE_PROD_KEY", "test-only")
    else:
        monkeypatch.delenv("YEARN_PRICE_PROD_KEY", raising=False)
    calls = []
    def stage(name):
        def run(*args, **kwargs):
            calls.append(name)
            return 0
        return run
    for name in ["discover", "discover_from_envio", "index_all_reports", "import_reports_from_envio", "price_unpriced_reports"]:
        monkeypatch.setattr(cli, name, stage(name))
    monkeypatch.setattr(cli, "_run_analysis", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(cli, "export_analysis", lambda *_args, **_kwargs: [])
    command = ["--db", str(tmp_path / "test.sqlite"), "run", "lifetime-yield"]
    if source == "defillama":
        command.extend(["--price-source", source])
    if source == "yearn-prices" and not has_key:
        with pytest.raises(ValueError, match="missing Yearn Prices API key"):
            cli.main(command)
        assert calls == []
    else:
        assert cli.main(command) == 0
        assert calls[-1] == "price_unpriced_reports"
