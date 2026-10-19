"""Report history and valuation when Envio learns about a vault later."""

from decimal import Decimal

import pytest

import test_envio as helpers
from test_envio_review_fixes import finish_run, report_endpoint
from yearn_data import cli, envio
from yearn_data.analysis import run_lifetime_yield
from yearn_data.config import V2_ETH_REGISTRIES, V3_ROLE_MANAGERS
from yearn_data.storage import connect, from_json


LATER = "0x" + "4" * 40


def coverage(conn, address, entity="StrategyReported"):
    return envio.get_index_state(conn, 1, address.lower(), envio._cursor_event(entity))


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("creation_block", [None, 20])
def test_lifetime_run_catches_up_a_newly_registered_vault(tmp_path, monkeypatch, version, creation_block):
    conn = helpers._db(tmp_path)
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    kind = "role_manager" if version == "v3" else "registry"
    source = V3_ROLE_MANAGERS[1] if version == "v3" else V2_ETH_REGISTRIES[0]
    node_for = helpers._v3_report_node if version == "v3" else helpers._v2_report_node
    helpers._seed_inventory(conn, version=version, source_kind=kind, source_address=source, block=40)
    nodes = [node_for(block=80), {**node_for(block=50), "vaultAddress": LATER,
                                "transactionHash": "0x" + "cd" * 32}]
    calls = report_endpoint(monkeypatch, nodes, version)
    messages = []
    frontier = [100]
    monkeypatch.setenv("YEARN_DATA_EVENT_SOURCE", "envio")
    monkeypatch.setattr(cli, "load_environment", lambda *a: None)
    monkeypatch.setattr(cli, "open_db", lambda *a: conn)
    monkeypatch.setattr(cli, "progress", messages.append)
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: frontier[0])
    monkeypatch.setattr(envio, "_import_v3_vault_inventory", lambda *a: 0)
    monkeypatch.setattr(envio, "_import_v2_vault_inventory", lambda *a: 0)
    monkeypatch.setattr(envio, "vault_metadata_from_kong", lambda chain, addresses: {
        address: {"asset": helpers.ASSET, "asset_decimals": 6, "asset_symbol": "USDC",
                  "name": "vault", "api_version": version} for address in addresses
    })
    finish_run(monkeypatch)
    assert cli.main(["run", "lifetime-yield", "--chains", "eth"]) == 0
    assert envio._get_envio_cursor(conn, 1, entity) == 100

    helpers._seed_inventory(conn, version=version, vault=LATER, source_kind=kind,
                            source_address=source, block=150, log_index=8)
    if creation_block is not None:
        helpers._seed_inventory(conn, version=version, vault=LATER,
            source_kind="factory" if version == "v3" else "registry_experimental",
            source_address=source, block=creation_block, log_index=9)
    frontier[0] = 200
    calls.clear()
    messages.clear()
    assert cli.main(["run", "lifetime-yield", "--chains", "eth"]) == 0
    assert conn.execute("SELECT block_number FROM strategy_reports WHERE vault_address=?", (LATER,)).fetchone()[0] == 50
    assert conn.execute("SELECT deployment_block FROM vaults WHERE address=?", (LATER,)).fetchone()[0] == creation_block
    assert coverage(conn, LATER, entity) == 200
    assert envio._get_envio_cursor(conn, 1, entity) == 200
    assert any(LATER in message and "catching up" in message for message in messages)
    calls.clear()
    assert cli.main(["run", "lifetime-yield", "--chains", "eth"]) == 0
    assert calls == []
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 2


def test_legacy_shared_cursor_does_not_assert_vault_history(tmp_path, monkeypatch):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    envio._set_envio_cursor(conn, 1, "StrategyReported", 100)
    calls = report_endpoint(monkeypatch, [helpers._v3_report_node(block=50)])
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 200)
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=200) == 1
    assert calls[0][1] == 0
    assert coverage(conn, helpers.VAULT) == 200
    calls.clear()
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=200) == 0
    assert calls == []


def test_failed_catchup_resumes_completed_windows_without_resetting_shared_cursor(tmp_path, monkeypatch):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 200)
    monkeypatch.setattr(envio, "_window_blocks", lambda: 50)
    nodes = [helpers._v3_report_node(block=75),
             {**helpers._v3_report_node(block=75), "vaultAddress": LATER, "transactionHash": "0x" + "cd" * 32}]
    report_endpoint(monkeypatch, nodes)
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=100) == 1
    conn.execute("""INSERT INTO vaults
        (chain_id,version,address,asset,asset_decimals,updated_at)
        VALUES (1,'v3',?,?,6,1)""", (LATER, helpers.ASSET))
    conn.commit()

    def failed(entity, fields, chain, lo, hi, **scope):
        assert scope["address_values"] == [LATER]
        if lo == 50:
            raise RuntimeError("endpoint unavailable")
        return []

    monkeypatch.setattr(envio, "_pull_window", failed)
    with pytest.raises(RuntimeError, match="endpoint unavailable"):
        envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=150)
    conn.close()
    conn = connect(tmp_path / "envio.sqlite")
    assert coverage(conn, LATER) == 49
    assert coverage(conn, helpers.VAULT) == 100
    assert envio._get_envio_cursor(conn, 1, "StrategyReported") == 100
    calls = report_endpoint(monkeypatch, nodes)
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=150) == 1
    assert calls[0][1:3] == (50, 99)
    assert coverage(conn, LATER) == 150
    assert coverage(conn, helpers.VAULT) == 150
    assert envio._get_envio_cursor(conn, 1, "StrategyReported") == 150
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 2


def test_bounded_replay_preserves_all_forward_coverage(tmp_path, monkeypatch):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 200)
    report_endpoint(monkeypatch, [helpers._v3_report_node(block=50)])
    envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=100)
    before = [tuple(row) for row in conn.execute("SELECT * FROM index_state ORDER BY target,event_name")]
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"],
        from_block=0, to_block=100, vault_addresses=[helpers.VAULT]) == 1
    assert [tuple(row) for row in conn.execute("SELECT * FROM index_state ORDER BY target,event_name")] == before
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 1


@pytest.mark.parametrize("decimals", [None, 6, 18])
def test_envio_reports_with_prices_require_known_decimals_for_valuation(tmp_path, monkeypatch, decimals):
    conn = helpers._db(tmp_path)
    helpers._seed_vault(conn)
    conn.execute("UPDATE vaults SET asset_decimals=?", (decimals,))
    conn.commit()
    node = {**helpers._v3_report_node(), "gain": "1000000", "loss": "0"}
    report_endpoint(monkeypatch, [node])
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 100)
    envio.import_reports_from_envio(conn, ["eth"], versions=["v3"], to_block=100)
    conn.execute("""INSERT INTO prices
        (chain_id,token_address,timestamp,source,price_usd,status)
        VALUES (1,?,1700000000,'defillama',1,'ok')""", (helpers.ASSET,))
    conn.commit()
    run_id = run_lifetime_yield(conn)
    outputs = {row["name"]: from_json(row["row_json"]) for row in conn.execute(
        "SELECT name,row_json FROM analysis_outputs WHERE run_id=? AND name IN ('reports','total_yield_summary')", (run_id,))}
    report = outputs["reports"]
    summary = outputs["total_yield_summary"]
    assert report["gain_raw"] == report["net_raw"] == "1000000"
    if decimals is None:
        assert report["gross_gain_usd"] is None
        assert report["net_yield_usd"] is None
        assert summary["priced_reports"] == 0
        assert summary["unpriced_reports"] == 1
    else:
        assert Decimal(report["net_yield_usd"]) == Decimal(1000000) / (Decimal(10) ** decimals)
        assert summary["priced_reports"] == 1
        assert summary["unpriced_reports"] == 0
