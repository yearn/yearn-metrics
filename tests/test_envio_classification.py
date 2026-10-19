"""Trusted membership overrides cached classification without inventing creation."""

import pytest

import test_envio as helpers
from test_envio_review_fixes import finish_run, report_endpoint
from yearn_data import cli, envio
from yearn_data.config import V2_ETH_REGISTRIES, V3_ROLE_MANAGERS


OTHER = "0x" + "4" * 40


def cached_factory_vault(conn, version="v3", deployment=None):
    (helpers._seed_vault if version == "v3" else helpers._seed_v2_vault)(conn)
    conn.execute("UPDATE vaults SET management='non_yearn',active=1,deployment_block=?,protocol='cached protocol'", (deployment,))
    conn.commit()


@pytest.fixture(autouse=True)
def offline_inventory(monkeypatch):
    monkeypatch.setenv("YEARN_DATA_EVENT_SOURCE", "envio")
    monkeypatch.setattr(cli, "load_environment", lambda *a: None)
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 200)
    monkeypatch.setattr(envio, "_import_v3_vault_inventory", lambda *a: 0)
    monkeypatch.setattr(envio, "_import_v2_vault_inventory", lambda *a: 0)
    monkeypatch.setattr(envio, "vault_metadata_from_kong", lambda *a: {})


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("deployment", [None, 20])
def test_default_lifetime_run_promotes_trusted_cached_vault_and_backfills_history(tmp_path, monkeypatch, version, deployment):
    conn = helpers._db(tmp_path)
    cached_factory_vault(conn, version, deployment)
    kind = "role_manager" if version == "v3" else "registry"
    source = V3_ROLE_MANAGERS[1] if version == "v3" else V2_ETH_REGISTRIES[0]
    helpers._seed_inventory(conn, version=version, source_kind=kind, source_address=source, block=150)
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    envio._set_envio_cursor(conn, 1, entity, 100)
    node_for = helpers._v3_report_node if version == "v3" else helpers._v2_report_node
    nodes = [node_for(block=50)]
    report_endpoint(monkeypatch, nodes, version)
    monkeypatch.setattr(cli, "open_db", lambda *a: conn)
    finish_run(monkeypatch)
    assert cli.main(["run", "lifetime-yield", "--chains", "eth"]) == 0
    vault = conn.execute("SELECT * FROM vaults").fetchone()
    assert vault["management"] == "yearn" and vault["active"] == 1
    assert vault["deployment_block"] == deployment
    assert vault["protocol"] == "cached protocol"
    assert conn.execute("SELECT block_number FROM strategy_reports").fetchone()[0] == 50
    assert envio._get_envio_cursor(conn, 1, entity) == 200


@pytest.mark.parametrize("later_actions,expected_active", [([], 0), (["added"], 1), (["added", "removed"], 0)])
def test_retired_cached_member_uses_latest_trusted_state_during_past_discovery(tmp_path, monkeypatch, later_actions, expected_active):
    conn = helpers._db(tmp_path)
    cached_factory_vault(conn)
    helpers._seed_inventory(conn, block=40)
    helpers._seed_inventory(conn, action="removed", block=80, log_index=2)
    for i, action in enumerate(later_actions):
        helpers._seed_inventory(conn, action=action, block=120 + i, log_index=3 + i)
    # A foreign manager cannot override this Yearn membership state.
    helpers._seed_inventory(conn, action="added", source_address=OTHER, block=180, log_index=9)
    envio.discover_from_envio(conn, ["eth"], skip_v2=True, include_retired=True, to_block=100)
    row = conn.execute("SELECT management,active,deployment_block FROM vaults").fetchone()
    assert tuple(row) == ("yearn", expected_active, None)
    nodes = [helpers._v3_report_node(block=30), helpers._v3_report_node(block=90, log_index=8)]
    report_endpoint(monkeypatch, nodes)
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"],
        include_inactive=True, to_block=200) == 2
    assert [r[0] for r in conn.execute("SELECT block_number FROM strategy_reports ORDER BY block_number")] == [30, 90]
    assert conn.execute("SELECT active FROM vaults").fetchone()[0] == expected_active


@pytest.mark.parametrize("evidence", ["factory", "foreign_manager", "future_member", "retired_not_requested"])
def test_unqualified_cached_factory_vault_remains_non_yearn(tmp_path, monkeypatch, evidence):
    conn = helpers._db(tmp_path)
    cached_factory_vault(conn, deployment=20)
    if evidence == "factory":
        helpers._seed_inventory(conn, source_kind="factory", block=20)
    elif evidence == "foreign_manager":
        helpers._seed_inventory(conn, source_address=OTHER, block=40)
    elif evidence == "future_member":
        helpers._seed_inventory(conn, block=150)
    else:
        helpers._seed_inventory(conn, block=40)
        helpers._seed_inventory(conn, action="removed", block=80, log_index=2)
    before = dict(conn.execute("SELECT * FROM vaults").fetchone())
    envio.discover_from_envio(conn, ["eth"], skip_v2=True, include_retired=False, to_block=100)
    assert dict(conn.execute("SELECT * FROM vaults").fetchone()) == before
    calls = report_endpoint(monkeypatch, [helpers._v3_report_node()])
    assert envio.import_reports_from_envio(conn, ["eth"], versions=["v3"],
        include_inactive=True, to_block=200) == 0
    assert calls == []
