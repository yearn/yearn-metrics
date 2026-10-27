"""Explicit one-chain report acquisition; accounting and valuation stay separate."""

import json
import time

from web3 import Web3

from . import abis
from .chains import web3_for
from .config import CHAINS
from .coverage import commit_range, uncovered_ranges
from .history_sources import HistoricalSource, inventory_targets, report_scope, route, write_rows
from .indexing import _hex
from .kong import vault_metadata_from_kong

REPORT_COVERAGE_BATCH_SIZE = 100


def inventory_vaults(conn, chain, end, experimental):
    """Membership comes from historical facts, not current active flags."""
    targets = list(inventory_targets(chain, experimental))
    selected = {}
    for target in targets:
        for row in conn.execute("""SELECT * FROM vault_inventory_events WHERE chain_id=?
            AND lower(source_address)=? AND source_kind=? AND version=? AND block_number<=?
            ORDER BY block_number,log_index""",
            (target.chain_id, target.address.lower(), target.kind, target.version, end)):
            address = row["vault_address"].lower()
            fact = selected.setdefault(address, dict(row))
            if row["asset"]:
                fact["asset"] = row["asset"]
    # Replay all lifecycle events together (the targets above are separated by ABI).
    for fact in selected.values():
        fact["as_of_block"] = end
        if fact["version"] == "v3":
            latest = conn.execute("""SELECT action FROM vault_inventory_events WHERE chain_id=?
                AND lower(vault_address)=? AND lower(source_address)=? AND source_kind='role_manager'
                AND block_number<=? ORDER BY block_number DESC,log_index DESC LIMIT 1""",
                (fact["chain_id"], fact["vault_address"].lower(), fact["source_address"].lower(), end)).fetchone()
            fact["active"] = int(latest[0] == "added")
        else:
            fact["active"] = 1
    return list(selected.values())


def materialize_inventory(conn, facts):
    with conn:
        for fact in facts:
            old = conn.execute("SELECT version,asset,active FROM vaults WHERE chain_id=? AND lower(address)=?",
                               (fact["chain_id"], fact["vault_address"].lower())).fetchone()
            if old and (old["version"] != fact["version"] or (old["asset"] and fact["asset"] and old["asset"].lower() != fact["asset"].lower())):
                raise ValueError("inventory conflicts with cached vault identity")
            active = fact["active"]
            if fact["version"] == "v3":
                # Selection is bounded, but the shared vault row reflects the
                # latest recorded lifecycle, including facts beyond this window.
                latest = conn.execute(
                    """SELECT action FROM vault_inventory_events WHERE chain_id=? AND lower(vault_address)=?
                    AND lower(source_address)=? AND source_kind='role_manager'
                    ORDER BY block_number DESC,log_index DESC LIMIT 1""",
                    (fact["chain_id"], fact["vault_address"].lower(), fact["source_address"].lower()),
                ).fetchone()
                if latest:
                    active = int(latest["action"] == "added")
            arguments = json.loads(fact["decoded_json"])
            conn.execute("""INSERT INTO vaults(chain_id,version,address,source_address,asset,api_version,active,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chain_id,address) DO UPDATE SET
                management='yearn',
                active=CASE WHEN excluded.version='v2' THEN vaults.active ELSE excluded.active END,
                asset=coalesce(vaults.asset,excluded.asset),
                api_version=coalesce(vaults.api_version,excluded.api_version),updated_at=excluded.updated_at""",
                (fact["chain_id"], fact["version"], Web3.to_checksum_address(fact["vault_address"]),
                 fact["source_address"], fact["asset"], arguments.get("api_version", arguments.get("apiVersion")),
                 active, int(time.time())))


def enrich_metadata(conn, client, vault, block, metadata):
    """Fill required metadata, or return False for proven absent contract code."""
    address = vault["address"]
    if not metadata or metadata.get("asset_decimals") is None or not metadata.get("api_version"):
        if not client.w3.eth.get_code(Web3.to_checksum_address(address), block_identifier=block):
            return False
        contract = client.w3.eth.contract(address=Web3.to_checksum_address(address), abi=abis.VAULT_METADATA_ABI)
        fn = contract.functions.token if vault["version"] == "v2" else contract.functions.asset
        asset = Web3.to_checksum_address(fn().call(block_identifier=block))
        token = client.w3.eth.contract(address=asset, abi=abis.ERC20_ABI)
        metadata = dict(asset=asset, asset_decimals=int(token.functions.decimals().call(block_identifier=block)),
                        api_version=contract.functions.apiVersion().call(block_identifier=block))
    if vault["asset"] and vault["asset"].lower() != metadata["asset"].lower():
        raise ValueError("metadata conflicts with inventory asset")
    if not 0 <= metadata["asset_decimals"] <= 255:
        raise ValueError("invalid asset decimals")
    if not metadata.get("api_version"):
        raise ValueError("missing vault release")
    with conn:
        conn.execute("""UPDATE vaults SET asset=?,asset_decimals=?,api_version=?,
            asset_symbol=coalesce(?,asset_symbol),name=coalesce(?,name),updated_at=?
            WHERE chain_id=? AND address=?""",
            (metadata["asset"], metadata["asset_decimals"], metadata["api_version"], metadata.get("asset_symbol"),
             metadata.get("name"), int(time.time()), client.chain_id, address))
    return True


def check_release(vault):
    prefix = {"v2": "0.", "v3": "3."}.get(vault["version"])
    if prefix is None or (vault["api_version"] and not vault["api_version"].startswith(prefix)):
        raise ValueError(f"unsupported vault release: {vault['address']}")



def catch_up(conn, chain, from_block, to_block, *, source="auto", confirmations=None,
             chunk_size=50_000, discover=False, experimental=False, client=None, progress=None):
    """Acquire an explicit window and reuse successfully committed ranges.

    The caller chooses historical bounds; existing observations are not treated
    as proof of coverage. Retained classified Yearn vaults stay in scope even
    when they have no current registry membership. Exceptions stop this call;
    completed ranges are reusable when the same command is repeated.
    """
    if from_block < 0 or to_block < from_block or chunk_size < 1:
        raise ValueError("invalid catch-up bounds or chunk size")
    if experimental and not discover:
        raise ValueError("experimental discovery requires --discover")
    if confirmations is not None and confirmations < 1:
        raise ValueError("confirmations must be positive")
    chain_id = CHAINS[chain].chain_id
    report_source = route(chain_id, "reports", source)
    client = client or HistoricalSource(chain, web3_for(chain))
    pin = client.pin(end=to_block, confirmations=confirmations, use_envio=report_source == "envio")
    summary = dict(chain=chain, from_block=from_block, pin=pin, source=report_source,
                   completed_ranges=0, inserted_rows=0, vaults=0, skipped_vaults=[],
                   discovery="bounded" if discover else "stored inventory",
                   coverage_read_queries=0, report_work={})
    started = time.monotonic()

    def notify(event, **details):
        if progress:
            progress(json.dumps({'stage': 'report-acquisition', 'event': event, 'chain': chain,
                                 'source': report_source, **details}, sort_keys=True))

    notify('started', from_block=from_block, to_block=to_block)

    verified_coverage = {}

    def acquire(scope, fetch, table, selected_source, report_metadata=None, retained=None, work=None):
        # Verify only reused coverage relevant to this request, not old unrelated
        # scans. New range rows and their coverage commit in one transaction.
        if retained is None:
            retained = list(conn.execute(
                """SELECT from_block,to_block,end_hash FROM history_coverage
                   WHERE chain_id=? AND target=? AND family=? AND policy=?
                   AND to_block>=? AND from_block<=? ORDER BY from_block,to_block""",
                (*scope.key, from_block, to_block),
            ))
            summary['coverage_read_queries'] += 1
        for _, number, block_hash in retained:
            key = (chain_id, number)
            if key not in verified_coverage:
                client.verify_pin(dict(number=number, hash=block_hash))
                verified_coverage[key] = block_hash.lower()
            elif verified_coverage[key] != block_hash.lower():
                raise ValueError("conflicting saved block hashes")
        gaps = list(uncovered_ranges(((r[0], r[1]) for r in retained), from_block, to_block, chunk_size))
        if work is not None:
            work['reused_blocks'] += to_block-from_block+1-sum(hi-lo+1 for lo, hi in gaps)
            work['reused_ranges'] += len(retained)
        notify('coverage-planned', target=scope.target, family=scope.family,
               reused_ranges=len(retained), pending_ranges=len(gaps))
        for lo, hi in gaps:
            notify('range-started', target=scope.target, family=scope.family, from_block=lo, to_block=hi)
            try:
                end_hash = _hex(client.block(hi)["hash"])
                rows = fetch(lo, hi)
                client.verify_pin(dict(number=hi, hash=end_hash))
            except Exception:
                # Provider exception text can contain private endpoints. A failed
                # range is neither empty coverage nor a successful retry.
                notify('range-failed', target=scope.target, family=scope.family, from_block=lo, to_block=hi)
                raise

            def persist(db):
                count = write_rows(db, table, rows, report_metadata=report_metadata)
                if report_metadata is not None:
                    # Reconciliation changes shared report rows. An earlier
                    # metadata policy must not skip replay if that value returns.
                    db.execute("""DELETE FROM history_coverage WHERE chain_id=? AND target=?
                        AND family=? AND policy<>?""", scope.key)
                return count

            inserted = commit_range(conn, scope, lo, hi, source=selected_source,
                                    end_hash=end_hash, run_id=None,
                                    write=persist)
            summary["completed_ranges"] += 1
            summary["inserted_rows"] += inserted
            if work is not None:
                work['scanned_blocks'] += hi-lo+1
                work['completed_ranges'] += 1
            notify('range-committed', target=scope.target, family=scope.family,
                   from_block=lo, to_block=hi, observed_rows=len(rows), inserted_rows=inserted)

    if discover:
        for target in inventory_targets(chain, experimental):
            selected_source = route(chain_id, target.scope.family, source)
            acquire(target.scope, lambda lo, hi: client.inventory(target, lo, hi, selected_source),
                    "vault_inventory_events", selected_source)
        materialize_inventory(conn, inventory_vaults(conn, chain, to_block, experimental))

    # No active flag or current registry join: both previously retained and newly
    # discovered Yearn vaults are eligible. External classifications stay excluded.
    vaults = [dict(row) for row in conn.execute(
        "SELECT * FROM vaults WHERE chain_id=? AND management='yearn' ORDER BY address", (chain_id,))]
    if not vaults:
        raise ValueError("no retained Yearn vaults; discover or import classified inventory first")
    metadata_targets = [vault["address"] for vault in vaults
                        if not vault["asset"] or vault["asset_decimals"] is None or not vault["api_version"]]
    try:
        metadata_by_address = vault_metadata_from_kong(chain_id, metadata_targets) if metadata_targets else {}
    except Exception:
        metadata_by_address = {}
    retained_reports = {}
    for index, vault in enumerate(vaults):
        if index % REPORT_COVERAGE_BATCH_SIZE == 0:
            # One bounded read supplies both gap planning and saved-hash checks.
            # Include all policies: enrichment below may change report_scope.
            targets = [v['address'].lower() for v in vaults[index:index+REPORT_COVERAGE_BATCH_SIZE]]
            marks = ','.join('?' for _ in targets)
            retained_reports = {}
            for target, policy, lo, hi, block_hash in conn.execute(
                f"""SELECT target,policy,from_block,to_block,end_hash FROM history_coverage
                    WHERE chain_id=? AND family='reports' AND target IN ({marks})
                    AND to_block>=? AND from_block<=? ORDER BY target,policy,from_block,to_block""",
                (chain_id, *targets, from_block, to_block),
            ):
                retained_reports.setdefault((target, policy), []).append((lo, hi, block_hash))
            summary['coverage_read_queries'] += 1
        vault_started = time.monotonic()
        check_release(vault)
        if not vault["asset"] or vault["asset_decimals"] is None or not vault["api_version"]:
            try:
                enriched = enrich_metadata(conn, client, vault, to_block,
                                           metadata_by_address.get(Web3.to_checksum_address(vault["address"])))
            except Exception as exc:
                raise ValueError(f"metadata unavailable for vault {vault['address']} at block {to_block}") from exc
            if not enriched:
                summary["skipped_vaults"].append(dict(address=vault["address"], block=to_block,
                                                    reason="no contract code at requested end block"))
                continue
            vault = dict(conn.execute("SELECT * FROM vaults WHERE chain_id=? AND address=?",
                                      (chain_id, vault["address"])).fetchone())
        check_release(vault)
        scope = report_scope(vault)
        activity = 'active' if vault['active'] else 'inactive'
        work = summary['report_work'].setdefault(activity, dict(
            vaults=0, reused_blocks=0, reused_ranges=0, scanned_blocks=0, completed_ranges=0, seconds=0))
        acquire(scope, lambda lo, hi: client.reports(vault, lo, hi, report_source),
                "strategy_reports", report_source, report_metadata=vault,
                retained=retained_reports.get((scope.target.lower(), scope.policy), []), work=work)
        work['vaults'] += 1
        work['seconds'] += time.monotonic()-vault_started
        summary["vaults"] += 1
    client.verify_pin(pin)
    summary['seconds'] = time.monotonic()-started
    notify('completed', **summary)
    return summary
