"""Historical TVL accounting. All USD values are Decimal strings; null means unknown."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from decimal import Decimal, localcontext
from pathlib import Path

from .storage import to_json
from .tvl_policy import VAULT_POLICIES, TARGET_CORRECTIONS, classify_vault

SCHEMA = """
CREATE TABLE IF NOT EXISTS tvl_vaults (
    chain_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    address TEXT NOT NULL,
    source_address TEXT,
    asset TEXT,
    asset_symbol TEXT,
    asset_decimals INTEGER,
    name TEXT,
    api_version TEXT,
    management TEXT NOT NULL DEFAULT 'yearn',
    protocol TEXT,
    deployment_block INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL,
    tvl_category TEXT,
    PRIMARY KEY (chain_id, address)
);
CREATE TABLE IF NOT EXISTS tvl_prices (
    chain_id INTEGER NOT NULL,
    token_address TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    block_number INTEGER,
    source TEXT NOT NULL,
    price_usd REAL,
    status TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (chain_id, token_address, timestamp, source)
);
CREATE TABLE IF NOT EXISTS tvl_events_raw (
    chain_id INTEGER NOT NULL,
    contract_address TEXT NOT NULL,
    event_name TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    block_timestamp INTEGER NOT NULL,
    decoded_json TEXT NOT NULL,
    PRIMARY KEY (chain_id, tx_hash, log_index)
);
CREATE TABLE IF NOT EXISTS tvl_inventory_events (
    chain_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    vault_address TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'added',
    source_address TEXT NOT NULL,
    asset TEXT,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    block_timestamp INTEGER NOT NULL,
    decoded_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (chain_id, tx_hash, log_index, source_kind)
);
CREATE TABLE IF NOT EXISTS tvl_history_coverage (
    chain_id INTEGER NOT NULL,
    target TEXT NOT NULL,
    family TEXT NOT NULL,
    policy TEXT NOT NULL,
    from_block INTEGER NOT NULL CHECK(from_block >= 0),
    to_block INTEGER NOT NULL CHECK(to_block >= from_block),
    source TEXT NOT NULL,
    end_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK(row_count >= 0),
    run_id INTEGER REFERENCES tvl_history_sync_runs(id),
    completed_at INTEGER NOT NULL,
    PRIMARY KEY(chain_id, target, family, policy, from_block, to_block)
);
CREATE TABLE IF NOT EXISTS tvl_history_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    manifest_json TEXT NOT NULL,
    summary_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS tvl_events_raw_contract_idx
ON tvl_events_raw(chain_id,contract_address,event_name,block_number);
CREATE INDEX IF NOT EXISTS tvl_inventory_events_vault_idx
ON tvl_inventory_events(chain_id,version,vault_address,source_kind,block_number);
CREATE TABLE IF NOT EXISTS tvl_discovery_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at INTEGER NOT NULL, completed_at INTEGER,
    status TEXT NOT NULL, params_json TEXT NOT NULL, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS tvl_catalog_evidence (
    chain_id INTEGER NOT NULL, address TEXT NOT NULL, source TEXT NOT NULL,
    metadata_status TEXT NOT NULL, evidence_json TEXT NOT NULL, updated_at INTEGER NOT NULL,
    PRIMARY KEY(chain_id,address,source)
);
CREATE TABLE IF NOT EXISTS tvl_strategies (
    chain_id INTEGER NOT NULL, parent TEXT NOT NULL, strategy TEXT NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY(chain_id,parent,strategy)
);
CREATE TABLE IF NOT EXISTS tvl_targets (
    chain_id INTEGER NOT NULL, strategy TEXT NOT NULL, child TEXT NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY(chain_id,strategy,child)
);
CREATE TABLE IF NOT EXISTS tvl_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at INTEGER NOT NULL,
    completed_at INTEGER, status TEXT NOT NULL, params_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tvl_snapshots (
    run_id INTEGER NOT NULL REFERENCES tvl_runs(id), chain_id INTEGER NOT NULL,
    vault TEXT NOT NULL, timestamp INTEGER NOT NULL, block_number INTEGER,
    data_json TEXT NOT NULL, PRIMARY KEY(run_id,chain_id,vault,timestamp)
);
CREATE TABLE IF NOT EXISTS tvl_positions (
    run_id INTEGER NOT NULL REFERENCES tvl_runs(id), chain_id INTEGER NOT NULL,
    parent TEXT NOT NULL, strategy TEXT NOT NULL, child TEXT NOT NULL,
    timestamp INTEGER NOT NULL, data_json TEXT NOT NULL,
    PRIMARY KEY(run_id,chain_id,parent,strategy,child,timestamp)
);
"""


BACKFILL_SCHEMA = """
CREATE TABLE IF NOT EXISTS tvl_backfill_batches (
 chain_id INTEGER, from_timestamp INTEGER, to_timestamp INTEGER, run_id INTEGER,
 status TEXT NOT NULL, PRIMARY KEY(chain_id,from_timestamp,to_timestamp));
CREATE TABLE IF NOT EXISTS tvl_backfill_births (
 chain_id INTEGER, address TEXT, block_number INTEGER, timestamp INTEGER,
 status TEXT, reason TEXT, PRIMARY KEY(chain_id,address));
CREATE TABLE IF NOT EXISTS tvl_backfill_blocks (
 chain_id INTEGER, timestamp INTEGER, block_number INTEGER,
 PRIMARY KEY(chain_id,timestamp));
"""
SCHEMA += BACKFILL_SCHEMA

def decimal(value):
    if value is None:
        return None
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid nonnegative quantity: {value}")
    return result



def value_nested_shares(points, *, derived_only=False, require_same_block=False):
    """Resolve tracked share-token assets only after their children are valued."""
    pending = {key:(key[0],(p.get('asset') or '').lower()) for key,p in points.items()
               if (p.get('pricing',{}).get('source')=='nested-share' if derived_only else
                   (key[0],(p.get('asset') or '').lower()) in points)
               and p.get('status') != 'not_deployed' and p.get('total_assets_raw') != '0'}
    for key,child_key in pending.items():
        p = points[key]
        # Provider share-token quotes are provisional: unknown child state must
        # not leave a seemingly complete parent valuation behind.
        p.update(tvl_usd=None,price_usd=None)
        if p.get('total_assets_raw') is not None:
            p.update(status='unavailable',reason='missing_nested_share_valuation',
                     pricing={'source':'nested-share','child':child_key[1]})
    while pending:
        ready = [key for key,child in pending.items() if child not in pending]
        if not ready:
            # Cycles have no independently valued leaf. Leave them unknown.
            break
        for key in ready:
            child = points.get(pending.pop(key))
            p = points[key]
            if child is None or (require_same_block and (p.get('block_number') is None or
                    p.get('block_number')!=child.get('block_number'))):
                continue
            supply = int(child.get('total_supply_raw') or 0)
            if child['tvl_usd'] is None or p.get('total_assets_raw') is None or p.get('asset_units') is None or not supply:
                continue
            with localcontext() as ctx:
                ctx.prec = 78
                value = decimal(child['tvl_usd'])*Decimal(p['total_assets_raw'])/Decimal(supply)
                units = decimal(p['asset_units'])
                p.update(tvl_usd=str(value),price_usd=str(value/units) if units else None,
                         status='ok',pricing={'source':'nested-share','child':child['vault']})
                p.pop('reason',None)


def asset_units(raw, decimals):
    if decimals is None:
        raise ValueError("missing asset decimals")
    with localcontext() as ctx:
        ctx.prec = 78
        return Decimal(int(raw)) / Decimal(10) ** int(decimals)


def account(snapshots, positions, *, include_curation=True, deduct_internal_curation=True,
            require_complete_ownership=True, vault_policies=VAULT_POLICIES, target_corrections=TARGET_CORRECTIONS):
    """Deduct each actual holding once, capped by parent debt and child TVL.

    A shared holder's balance is split among its parents by recorded debt. Several
    child positions share that same debt budget. This prevents duplicate deductions.
    Curated vaults share the same ownership ledger. Legacy publications can
    retain their separate-product policy and known-subtotal row semantics.
    """
    excluded = {(p['chainId'],p['address'].lower()) for p in vault_policies if p.get('excludeFromTvl')}
    snapshots = [classify_vault(s,vault_policies) for s in snapshots
                 if (s['chain_id'],s['vault'].lower()) not in excluded]
    positions = [p for p in positions if (p['chain_id'],p['parent'].lower()) not in excluded
                 and (p.get('child_chain_id',p['chain_id']),p['child'].lower()) not in excluded]
    with localcontext() as ctx:
        ctx.prec = 78
        return _account(snapshots, positions, include_curation=include_curation,
                        deduct_internal_curation=deduct_internal_curation,
                        target_corrections=target_corrections, require_complete_ownership=require_complete_ownership)


def _account(snapshots, positions, *, include_curation, deduct_internal_curation, target_corrections, require_complete_ownership):
    by_key = {(s['chain_id'], s['vault'].lower()): s for s in snapshots}
    adjustments = defaultdict(lambda: Decimal(0))
    edges = [dict(p, overlap_usd=None, accounting_unresolved=False) for p in positions]
    groups = defaultdict(list)
    issues = []
    unavailable_children = set()
    corrections = {(r['chainId'],r['strategyAddress'].lower(),old.lower()):r['targetVaultAddress'].lower()
                   for r in target_corrections for old in r['supersededTargetVaultAddresses']}

    def dated_destination(edge):
        return (edge.get('status') == 'ok' and edge.get('vault_getter')
                and edge.get('block_number') is not None)

    def identity(edge, child):
        return (edge['chain_id'],edge['parent'].lower(),edge['strategy'].lower(),child,
                edge.get('timestamp'),edge.get('block_number'))

    verified = {identity(e,e['child'].lower()) for e in edges if dated_destination(e)
                and e.get('debt_usd') is not None
                and (e.get('owned_usd') is not None or decimal(e['debt_usd']) == 0)}
    chains = {s['chain_id'] for s in snapshots}
    for s in snapshots:
        if s.get('bridge_target_chain') in chains and decimal(s.get('tvl_usd')):
            issues.append('bridge_migration_timing_unverified')
    for edge in edges:
        replacement = corrections.get((edge['chain_id'],edge['strategy'].lower(),edge['child'].lower()))
        if replacement and edge.get('method') == 'service-registry' and not dated_destination(edge):
            # Today's registry cannot disprove a dated getter observation, but
            # an explicitly corrected, unverified mapping must not deduct forever.
            # Keep the original identity for audit; never transfer its balances.
            edge.update(overlap_usd='0',accounting_excluded=True,reason='superseded_registry_target',
                        replacement_child=replacement)
            known_zero = edge.get('status') == 'ok' and decimal(edge.get('debt_usd')) == 0
            if (not known_zero and identity(edge,replacement) not in verified
                    and (edge['chain_id'],edge['parent'].lower()) in by_key):
                edge.update(status='unavailable',accounting_unresolved=True)
                issues.append(edge['reason'])
                # The corrected destination has unknown ownership until a dated
                # replacement is observed; never relabel the stale balance.
                unavailable_children.add((edge['chain_id'], replacement))
            continue
        if edge.get('method') == 'strategy-allocation':
            if (edge.get('reason') == 'unresolved_adapter_mapping'
                    or (edge.get('mapping_status') == 'unresolved' and edge.get('status') != 'ok')):
                issues.append(edge.get('reason') or 'unresolved_adapter_mapping')
                edge['accounting_unresolved'] = True
            else:
                edge['overlap_usd'] = '0'
            continue
        parent = by_key.get((edge['chain_id'], edge['parent'].lower()))
        child_chain = edge.get('child_chain_id',edge['chain_id'])
        child = by_key.get((child_chain, edge['child'].lower()))
        if parent is None or child is None:
            edge['reason'] = 'endpoint_outside_selection'
            continue
        if edge['chain_id'] == child_chain and edge['parent'].lower() == edge['child'].lower():
            edge['reason'] = 'self_edge'
            continue
        if (not include_curation and child.get('category',child['version']) == 'curation'
                and not (deduct_internal_curation
                         and parent.get('category',parent['version']) == 'curation')):
            edge['reason'] = 'curation_separate_product'
            continue
        # A known zero needs no child valuation. Missing prices must not mask it.
        if edge.get('status') == 'ok' and decimal(edge.get('debt_usd')) == 0:
            edge['overlap_usd'] = '0'
            continue
        if (edge.get('status') != 'ok' or parent.get('tvl_usd') is None
                or child.get('tvl_usd') is None or edge.get('owned_usd') is None
                or edge.get('debt_usd') is None):
            edge['reason'] = edge.get('reason') or 'missing_position_valuation'
            issues.append(edge['reason'])
            edge['accounting_unresolved'] = True
            unavailable_children.add((child_chain, edge['child'].lower()))
            continue
        kind = 'recorded-debt' if edge.get('method') == 'direct-strategy' else 'held-shares'
        groups[(edge['chain_id'], edge['strategy'].lower(), kind)].append(edge)

    candidates = []
    for group in groups.values():
        # One balance belongs to a holder, regardless of how many parents use it.
        debts = {}
        owned = {}
        for e in group:
            debts[e['parent'].lower()] = decimal(e['debt_usd'])
            owned[(e.get('child_chain_id',e['chain_id']),e['child'].lower())] = decimal(e['owned_usd'])
        total_debt = sum(debts.values(), Decimal(0))
        total_owned = sum(owned.values(), Decimal(0))
        budget = min(total_debt, total_owned)
        for e in group:
            value = (budget * debts[e['parent'].lower()] / total_debt
                     * owned[(e.get('child_chain_id',e['chain_id']),e['child'].lower())] / total_owned) if total_debt and total_owned else Decimal(0)
            candidates.append((e, value))

    # Cap all incoming ownership at the child's TVL, and all outgoing at parent TVL.
    incoming = defaultdict(lambda: Decimal(0))
    outgoing = defaultdict(lambda: Decimal(0))
    for e, value in candidates:
        incoming[(e.get('child_chain_id',e['chain_id']), e['child'].lower())] += value
        outgoing[(e['chain_id'], e['parent'].lower())] += value
    for e, value in candidates:
        pk = (e['chain_id'], e['parent'].lower())
        ck = (e.get('child_chain_id',e['chain_id']), e['child'].lower())
        scale = min(Decimal(1), decimal(by_key[ck]['tvl_usd']) / incoming[ck] if incoming[ck] else Decimal(1),
                    decimal(by_key[pk]['tvl_usd']) / outgoing[pk] if outgoing[pk] else Decimal(1))
        value *= scale
        e['overlap_usd'] = str(value)
        adjustments[ck] += value

    rows = []
    for key, s in by_key.items():
        row = dict(s, accounting_unresolved=bool(s.get('bridge_target_chain') in chains and decimal(s.get('tvl_usd'))))
        value = decimal(s.get('tvl_usd'))
        row['overlap_usd'] = str(adjustments[key]) if value is not None else None
        row['external_tvl_usd'] = str(max(Decimal(0), value - adjustments[key])) if value is not None else None
        if require_complete_ownership and key in unavailable_children:
            row['overlap_usd'] = None
            row['external_tvl_usd'] = None
        rows.append(row)
    valued = [r for r in rows if r['tvl_usd'] is not None]
    complete = len(valued) == len(rows) and not issues
    gross = sum((decimal(r['tvl_usd']) for r in valued), Decimal(0))
    overlap = sum(adjustments.values(), Decimal(0))
    known_external = sum((decimal(r['external_tvl_usd']) for r in rows
                          if r['external_tvl_usd'] is not None), Decimal(0))
    summary = {'timestamp': snapshots[0]['timestamp'] if snapshots else None,
               'gross_tvl_usd': str(gross) if complete else None,
               'overlap_usd': str(overlap) if complete else None,
               'external_tvl_usd': str(max(Decimal(0), gross-overlap)) if complete else None,
               'known_gross_tvl_usd': str(gross), 'known_overlap_usd': str(overlap),
               'known_external_tvl_usd': str(known_external),
               'vault_count': len(rows), 'valued_vault_count': len(valued),
               'position_count': len(edges), 'unresolved_positions': len(issues),
               'status': 'complete' if complete else 'incomplete',
               'mapping_scope': 'known candidates', 'issues': sorted(set(issues))}
    return rows, edges, summary


def export_tvl(conn, out, run_id=None, *, include_curation=True):
    if run_id is None:
        row = conn.execute("SELECT id FROM tvl_runs WHERE status IN ('complete','incomplete') ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise ValueError('no collected TVL run')
        run_id = row['id']
    run = conn.execute('SELECT * FROM tvl_runs WHERE id=?', (run_id,)).fetchone()
    if run is None or run['status'] not in ('complete', 'incomplete'):
        raise ValueError('TVL run is not finished')
    snapshots = defaultdict(list)
    positions = defaultdict(list)
    for row in conn.execute('SELECT data_json FROM tvl_snapshots WHERE run_id=? ORDER BY timestamp,chain_id,vault', (run_id,)):
        data = json.loads(row['data_json'])
        snapshots[data['timestamp']].append(data)
    for row in conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=? ORDER BY timestamp,chain_id,parent,strategy,child', (run_id,)):
        data = json.loads(row['data_json'])
        positions[data['timestamp']].append(data)
    # The export opt-out retains both legacy layering and legacy availability.
    require_complete_ownership = include_curation
    output = {'vaults': [], 'positions': [], 'history': []}
    for timestamp, points in snapshots.items():
        rows, edges, summary = account(points, positions[timestamp], include_curation=include_curation,
                                       require_complete_ownership=require_complete_ownership)
        output['vaults'].extend(rows)
        output['positions'].extend(edges)
        output['history'].append(summary)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in output.items():
        with (out / f'{name}.csv').open('w', newline='') as f:
            fields = list(dict.fromkeys(k for row in rows for k in row))
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows({k: to_json(v) if isinstance(v, (dict, list)) else v for k, v in row.items()} for row in rows)
    metadata = dict(run)
    metadata['params'] = json.loads(metadata.pop('params_json'))
    metadata['include_curation'] = include_curation
    metadata['require_complete_ownership'] = require_complete_ownership
    metadata['mapping_scope'] = 'known candidates; positive share scans expand coverage, absence is not proof of no nesting'
    (out / 'tvl.json').write_text(json.dumps({'run': metadata, **output}, indent=2) + '\n')
    return out
