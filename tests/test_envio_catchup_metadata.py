"""The existing bounded catch-up repairs metadata retained by Envio imports."""

from decimal import Decimal

import pytest

import test_catchup_regressions as helpers
from yearn_data import catchup, envio, kong
from yearn_data.analysis import run_lifetime_yield
from yearn_data.storage import connect, from_json, init_db


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("conflicting_raw", [False, True])
def test_explicit_catchup_repairs_resolved_envio_metadata(tmp_path, monkeypatch, version, conflicting_raw):
    conn = connect('staging')
    init_db(conn)
    helpers.seed(conn, version=version)
    conn.execute("UPDATE vaults SET chain_id=1,asset_decimals=NULL,name='Known vault',asset_symbol='USDC'")
    conn.commit()
    entity = "StrategyReported" if version == "v3" else "V2StrategyReported"
    node = dict(gain="12", loss="0", strategy=helpers.ASSET, vaultAddress=helpers.VAULT,
                chainId=1, blockNumber=25, blockTimestamp=1000,
                transactionHash=helpers.TX, logIndex=0,
                current_debt="100", protocol_fees="1", total_fees="2", total_refunds="0",
                debtAdded="0", debtPaid="0", totalGain="12", totalLoss="0", totalDebt="100", debtRatio="100")
    monkeypatch.setattr(envio, "latest_processed_block", lambda _: 100)
    monkeypatch.setattr(envio, "_pull_window", lambda requested, *a, **kw: [node] if requested == entity else [])
    envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100, include_inactive=True)
    original = dict(conn.execute("SELECT * FROM strategy_reports").fetchone())

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"vaults": [{"address": helpers.VAULT,
                    "name": None, "apiVersion": None,
                    "asset": {"address": helpers.ASSET, "symbol": None, "decimals": 6}}]}}

    monkeypatch.setattr(kong.requests, "post", lambda *a, **kw: Response())
    records = envio._build_envio_vault_records(conn, "eth", version, [helpers.VAULT])
    envio._upsert_envio_vaults(conn, records)
    messages = []
    envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100,
                                   include_inactive=True, progress=messages.append)
    assert any(helpers.VAULT in message and "blocks 25-25" in message
               and "bounded catch-up" in message for message in messages)
    assert dict(conn.execute("SELECT * FROM strategy_reports").fetchone()) == original
    vault = conn.execute("SELECT * FROM vaults").fetchone()
    assert vault["name"] == "Known vault" and vault["asset_symbol"] == "USDC"
    assert vault["asset_decimals"] == 6 and vault["api_version"] == ("0.4.6" if version == "v2" else "3.0.4")
    forward_state = [tuple(row) for row in conn.execute("SELECT * FROM index_state ORDER BY target,event_name")]

    client, _ = helpers.source([helpers.report_log(version)])
    client.chain, client.chain_id, client.w3.eth.chain_id = "eth", 1, 1
    if conflicting_raw:
        conn.execute("UPDATE strategy_reports SET gain_raw='13'")
        conn.commit()
        with pytest.raises(ValueError, match="conflicting observation"):
            catchup.catch_up(conn, "eth", 10, 30, source="rpc", client=client)
        row = conn.execute("SELECT * FROM strategy_reports").fetchone()
        assert row["asset_decimals"] is None and row["gain_raw"] == "13"
        assert conn.execute("SELECT COUNT(*) FROM history_coverage").fetchone()[0] == 0
    else:
        summary = catchup.catch_up(conn, "eth", 10, 30, source="rpc", client=client)
        assert summary["completed_ranges"] == 1 and summary["inserted_rows"] == 0
        assert dict(conn.execute("SELECT * FROM strategy_reports").fetchone()) == {**original, "asset_decimals": 6}
        conn.execute("""INSERT INTO prices(chain_id,token_address,timestamp,source,price_usd,status)
            VALUES (1,?,1000,'defillama',1,'ok')""", (helpers.ASSET,))
        conn.commit()
        run_id = run_lifetime_yield(conn)
        outputs = {r["name"]: from_json(r["row_json"]) for r in conn.execute(
            "SELECT name,row_json FROM analysis_outputs WHERE run_id=? AND name IN ('reports','total_yield_summary')", (run_id,))}
        assert Decimal(outputs["reports"]["net_yield_usd"]) == Decimal("0.000012")
        assert outputs["total_yield_summary"]["priced_reports"] == 1
        assert outputs["total_yield_summary"]["unpriced_reports"] == 0
        messages.clear()
        envio.import_reports_from_envio(conn, ["eth"], versions=[version], to_block=100,
                                       include_inactive=True, progress=messages.append)
        assert not any("bounded catch-up" in message for message in messages)
    assert [tuple(row) for row in conn.execute("SELECT * FROM index_state ORDER BY target,event_name")] == forward_state
