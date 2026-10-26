"""Partial catalog refreshes preserve facts and identify explicit report repair."""

import pytest

import test_envio as helpers
from yearn_data import envio, kong
from yearn_data.analysis import run_lifetime_yield
from yearn_data.storage import from_json


@pytest.fixture(autouse=True)
def processed_frontier(monkeypatch):
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 200)


def kong_response(monkeypatch, *, asset=helpers.ASSET, **optional):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"vaults": [{
                "address": helpers.VAULT,
                "name": optional.get("name"),
                "apiVersion": optional.get("api_version"),
                "asset": {"address": asset, "symbol": optional.get("asset_symbol"), "decimals": 6},
            }]}}

    monkeypatch.setattr(kong.requests, "post", lambda *args, **kwargs: Response())


@pytest.mark.parametrize(("refresh", "expected"), [
    ({}, ("yvUSDC-A", "USDC", "3.0.0")),
    ({"name": "Updated", "asset_symbol": "USDC.e", "api_version": "3.0.4"},
     ("Updated", "USDC.e", "3.0.4")),
    ({"name": "Updated"}, ("Updated", "USDC", "3.0.0")),
])
def test_partial_kong_refresh_preserves_each_cached_field(postgres_database, tmp_path, monkeypatch, refresh, expected):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    conn.execute("UPDATE vaults SET asset_decimals=NULL,name='yvUSDC-A'")
    conn.commit()
    helpers._seed_inventory(conn)
    monkeypatch.setattr(envio, "_import_v3_vault_inventory", lambda *a: 0)
    kong_response(monkeypatch, **refresh)
    envio.discover_from_envio(conn, ["eth"], skip_v2=True, include_retired=True, to_block=100)
    row = conn.execute("SELECT name,asset_symbol,api_version,asset_decimals,deployment_block FROM vaults").fetchone()
    assert tuple(row) == (*expected, 6, 42)


def test_symbol_is_not_copied_to_a_different_asset(postgres_database, tmp_path, monkeypatch):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    conn.execute("UPDATE vaults SET asset_decimals=NULL")
    conn.commit()
    other_asset = "0x" + "5" * 40
    kong_response(monkeypatch, asset=other_asset)
    records = envio._build_envio_vault_records(conn, "eth", "v3", [helpers.VAULT])
    envio._upsert_envio_vaults(conn, records)
    row = conn.execute("SELECT asset,asset_symbol FROM vaults").fetchone()
    assert tuple(row) == (other_asset, None)


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_resolved_metadata_names_reports_requiring_explicit_catchup(postgres_database, tmp_path, monkeypatch, version):
    conn = helpers._db(tmp_path)
    (helpers._seed_vault if version == "v3" else helpers._seed_v2_vault)(conn)
    conn.execute("UPDATE vaults SET asset_decimals=NULL")
    conn.commit()
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    node = (helpers._v3_report_node if version == "v3" else helpers._v2_report_node)()
    monkeypatch.setattr(envio, "_pull_window", lambda requested, *a, **kw: [node] if requested == entity else [])
    messages = []
    envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100, progress=messages.append)
    original = tuple(conn.execute("SELECT * FROM strategy_reports").fetchone())
    assert not any("bounded catch-up" in message for message in messages)

    kong_response(monkeypatch)
    records = envio._build_envio_vault_records(conn, "eth", version, [helpers.VAULT])
    envio._upsert_envio_vaults(conn, records)
    messages.clear()
    assert envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100, progress=messages.append) == 0
    assert len(messages) == 1
    assert helpers.VAULT in messages[0] and "blocks 100-100" in messages[0]
    assert "bounded catch-up" in messages[0] and "index-events replay does not repair" in messages[0]
    envio.import_reports_from_envio(conn, ["eth"], versions=[version], from_block=0,
        to_block=100, vault_addresses=[helpers.VAULT], include_inactive=True)
    assert tuple(conn.execute("SELECT * FROM strategy_reports").fetchone()) == original
    conn.execute("""INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status)
        VALUES (1,?,1700000000,'defillama',1,'ok')""", (helpers.ASSET,))
    conn.commit()
    run_id = run_lifetime_yield(conn)
    output = from_json(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'", (run_id,)).fetchone()[0])
    assert output["unpriced_reports"] == 1 and output["priced_reports"] == 0
    messages.clear()
    other_version = "v2" if version == "v3" else "v3"
    envio.import_reports_from_envio(conn, ["eth"], versions=[other_version],
        from_block=0, to_block=100, progress=messages.append)
    assert not any("bounded catch-up" in message for message in messages)
