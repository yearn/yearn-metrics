"""Regressions for mixed import commands, metadata evidence, and HTTP failures."""

import json

import pytest
import requests

import test_envio as helpers
from yearn_data import cli, envio


@pytest.fixture(autouse=True)
def isolated_commands(monkeypatch):
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 1000)
    monkeypatch.setattr(cli, "load_environment", lambda *args: None)


def retained_vaults(tmp_path, version="v3"):
    conn = helpers._db(tmp_path)
    (helpers._seed_vault if version == "v3" else helpers._seed_v2_vault)(conn)
    retired = "0x" + "4" * 40
    conn.execute("""INSERT INTO vaults
        (chain_id,version,address,asset,asset_decimals,active,updated_at)
        VALUES (1,?,?,?,6,0,1)""", (version, retired, helpers.ASSET))
    conn.commit()
    node_for = helpers._v3_report_node if version == "v3" else helpers._v2_report_node
    nodes = [{**node_for(block=75, log_index=2), "vaultAddress": retired},
             node_for(block=90, log_index=1)]
    return conn, retired, nodes


def report_endpoint(monkeypatch, nodes, version="v3"):
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    calls = []

    def pull(requested_entity, fields, chain_id, lo, hi, **scope):
        calls.append((requested_entity, lo, hi, scope))
        return [n for n in nodes if requested_entity == entity
                and lo <= n["blockNumber"] <= hi and n["vaultAddress"] in scope["address_values"]]

    monkeypatch.setattr(envio, "_pull_window", pull)
    return calls


def finish_run(monkeypatch):
    monkeypatch.setenv("YEARN_PRICE_PROD_KEY", "test-only")
    monkeypatch.setattr(cli, "price_unpriced_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "price_unpriced_volume", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "_run_analysis", lambda *args, **kwargs: 1)
    monkeypatch.setattr(cli, "export_analysis", lambda *args, **kwargs: [])


def test_default_index_events_then_lifetime_run_keeps_retired_history(postgres_database, tmp_path, monkeypatch):
    conn, retired, nodes = retained_vaults(tmp_path)
    helpers._seed_inventory(conn, block=40)
    helpers._seed_inventory(conn, vault=retired, block=40, log_index=2)
    helpers._seed_inventory(conn, vault=retired, block=80, log_index=3, action="removed")
    monkeypatch.setenv("YEARN_DATA_EVENT_SOURCE", "envio")
    monkeypatch.setattr(cli, "open_db", lambda *args: conn)
    monkeypatch.setattr(envio, "_import_v3_vault_inventory", lambda *args: 0)
    monkeypatch.setattr(envio, "_import_v2_vault_inventory", lambda *args: 0)
    calls = report_endpoint(monkeypatch, nodes)
    finish_run(monkeypatch)

    assert cli.main(["index-events", "--chains", "eth", "--to-block", "100"]) == 0
    assert retired in calls[0][3]["address_values"]
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 2
    assert envio._get_envio_cursor(conn, 1, "StrategyReported") == 100
    assert cli.main(["run", "lifetime-yield", "--chains", "eth", "--to-block", "100"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 2
    assert conn.execute("SELECT active FROM vaults WHERE address=?", (retired,)).fetchone()[0] == 0


@pytest.mark.parametrize("version", ["v2", "v3"])
def test_active_only_import_requires_bounds_and_preserves_forward_cursor(postgres_database, tmp_path, monkeypatch, version):
    conn, retired, nodes = retained_vaults(tmp_path, version)
    calls = report_endpoint(monkeypatch, nodes, version)
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    envio._set_envio_cursor(conn, 1, entity, 50)
    with pytest.raises(ValueError, match="active-only Envio imports require"):
        envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100)
    assert calls == []
    assert envio._get_envio_cursor(conn, 1, entity) == 50
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 0

    assert envio.import_reports_from_envio(conn, ["eth"], versions=[version],
        from_block=0, to_block=100) == 1
    assert retired not in calls[-1][3]["address_values"]
    assert envio._get_envio_cursor(conn, 1, entity) == 50
    assert envio.import_reports_from_envio(conn, ["eth"], versions=[version],
        include_inactive=True, to_block=100) == 2
    assert conn.execute("SELECT COUNT(*) FROM strategy_reports").fetchone()[0] == 2
    assert envio._get_envio_cursor(conn, 1, entity) == 100


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("cached_asset", [None, helpers.VAULT])
def test_cached_asset_uses_known_evidence_without_guessing_decimals(postgres_database, tmp_path, monkeypatch, version, cached_asset):
    conn = helpers._db(tmp_path)
    (helpers._seed_vault if version == "v3" else helpers._seed_v2_vault)(conn)
    conn.execute("UPDATE vaults SET asset=?,asset_decimals=NULL,deployment_block=NULL", (cached_asset,))
    conn.commit()
    helpers._seed_inventory(conn, version=version,
        source_kind="factory" if version == "v3" else "registry", block=40)
    monkeypatch.setattr(envio, "vault_metadata_from_kong", lambda *args: {})
    records = envio._build_envio_vault_records(conn, "eth", version, [helpers.VAULT])
    envio._upsert_envio_vaults(conn, records)
    row = conn.execute("SELECT asset,asset_decimals,deployment_block FROM vaults").fetchone()
    assert row["asset"] == (cached_asset or helpers.ASSET)
    assert row["asset_decimals"] is None
    assert row["deployment_block"] == (40 if version == "v3" else None)


def test_envio_run_rejects_deployment_lookup_before_acquisition(postgres_database, tmp_path, monkeypatch):
    conn = helpers._db(tmp_path)
    monkeypatch.setattr(cli, "open_db", lambda *args: conn)
    monkeypatch.setenv("YEARN_DATA_EVENT_SOURCE", "envio")
    monkeypatch.setattr(cli, "discover_from_envio", lambda *args, **kwargs: pytest.fail("unsupported acquisition"))
    with pytest.raises(ValueError, match="--find-deployment requires YEARN_DATA_EVENT_SOURCE=rpc"):
        cli.main(["run", "lifetime-yield", "--chains", "eth", "--find-deployment"])


@pytest.mark.parametrize("source,job", [("rpc", "lifetime-yield"), ("envio", "vault-volume")])
def test_rpc_backed_runs_still_find_deployment(postgres_database, tmp_path, monkeypatch, source, job):
    conn = helpers._db(tmp_path)
    calls = []
    monkeypatch.setattr(cli, "open_db", lambda *args: conn)
    monkeypatch.setenv("YEARN_DATA_EVENT_SOURCE", source)
    monkeypatch.setattr(cli, "discover", lambda *args, **kwargs: calls.append(kwargs) or 0)
    monkeypatch.setattr(cli, "index_all_reports", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "index_all_volume", lambda *args, **kwargs: 0)
    finish_run(monkeypatch)
    assert cli.main(["run", job, "--chains", "eth", "--find-deployment"]) == 0
    assert len(calls) == 1 and calls[0]["find_deployment"] is True


def http_response(status, body=None):
    response = requests.Response()
    response.status_code = status
    response.url = "https://example.invalid/graphql"
    response._content = json.dumps(body or {}).encode()
    return response


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_failures_do_not_retry(monkeypatch, status):
    calls, sleeps = [], []
    monkeypatch.setattr(envio, "get_envio_graphql_url", lambda: "https://example.invalid/graphql")
    monkeypatch.setattr(envio.requests, "post", lambda *args, **kwargs: calls.append(1) or http_response(status))
    monkeypatch.setattr(envio.time, "sleep", sleeps.append)
    with pytest.raises(requests.HTTPError) as exc:
        envio._gql("query { __typename }")
    assert exc.value.response.status_code == status
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("failure", [429, 500, 503, "connection"])
def test_transient_failures_have_bounded_retries_without_final_sleep(monkeypatch, failure):
    calls, sleeps = [], []
    monkeypatch.setattr(envio, "get_envio_graphql_url", lambda: "https://example.invalid/graphql")

    def failed(*args, **kwargs):
        calls.append(1)
        if failure == "connection":
            raise requests.ConnectionError("offline")
        return http_response(failure)

    monkeypatch.setattr(envio.requests, "post", failed)
    monkeypatch.setattr(envio.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="failed after retries"):
        envio._gql("query { __typename }")
    assert len(calls) == 5 and sleeps == [1, 2, 4, 8]


@pytest.mark.parametrize("failure", [429, 503, "connection"])
def test_transient_failure_can_recover(monkeypatch, failure):
    calls, sleeps = [], []
    monkeypatch.setattr(envio, "get_envio_graphql_url", lambda: "https://example.invalid/graphql")

    def request(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            if failure == "connection":
                raise requests.ConnectionError("offline")
            return http_response(failure)
        return http_response(200, {"data": {"ok": True}})

    monkeypatch.setattr(envio.requests, "post", request)
    monkeypatch.setattr(envio.time, "sleep", sleeps.append)
    assert envio._gql("query { __typename }") == {"ok": True}
    assert len(calls) == 2 and sleeps == [1]


def test_graphql_errors_still_fail_without_retries(monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(envio, "get_envio_graphql_url", lambda: "https://example.invalid/graphql")
    monkeypatch.setattr(envio.requests, "post", lambda *args, **kwargs: calls.append(1) or http_response(
        200, {"errors": [{"message": "invalid query"}]}))
    monkeypatch.setattr(envio.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError, match="GraphQL errors"):
        envio._gql("query { badField }")
    assert len(calls) == 1 and sleeps == []
