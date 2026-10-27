"""One dated ownership ledger feeds the headline, charts and Powerglove details."""
import json
from decimal import Decimal

import pytest

from yearn_data.analytics import fee_stack
from yearn_data.tvl import account, export_tvl
from yearn_data.tvl_api import TvlDataset, TvlStore
from yearn_data.tvl_history_cache import prepare
from test_tvl import edge, snapshot
from test_tvl_api import source as source, run, DAY, PARENT, CHILD


def curate(conn):
    for stored in conn.execute('SELECT run_id,data_json FROM tvl_snapshots WHERE vault=?', (CHILD,)).fetchall():
        row = json.loads(stored['data_json'])
        row['category'] = 'curation'
        conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE run_id=? AND vault=?',
                     (json.dumps(row), stored['run_id'], CHILD))
    conn.commit()


@pytest.mark.parametrize('method', ['vault-held-shares', 'service-registry', 'direct-strategy'])
def test_curated_ownership_reaches_every_canonical_consumer(source, method):
    conn, path, root = source
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200')], [(CHILD, '30')])
    run(conn, 2, DAY + 86400, [(PARENT, '100'), (CHILD, '200')], [(CHILD, '80')])
    curate(conn)
    for stored in conn.execute('SELECT run_id,data_json FROM tvl_positions').fetchall():
        row = json.loads(stored['data_json'])
        row['method'] = method
        row['strategy'] = PARENT if method == 'vault-held-shares' else CHILD if method == 'direct-strategy' else '0x'+'44'*20
        conn.execute('UPDATE tvl_positions SET strategy=?,data_json=? WHERE run_id=?',
                     (row['strategy'], json.dumps(row), stored['run_id']))
    conn.commit()
    dataset = TvlStore(path, root).get()
    assert dataset.summary()['totalTvl'] == 220
    assert dataset.summary()['curationTvl'] == 200
    assert dataset.summary()['overlapExcluded'] == 80
    history = dataset.history(interval='daily', group='category', constant=True)
    assert [p['curation'] for p in history['actualChart']] == [170, 120]
    assert history['constantPriceChart'] == history['actualChart']
    assert [p['curation'] for p in dataset.history(interval='daily', group='category', mode='raw')['chart']] == [200, 200]
    _, rows, positions, _ = dataset.frames((dataset.as_of(),))[0]
    configs = {'vaults': [{'chainId': 1, 'address': a, 'performanceFee': 1000, 'managementFee': 0}
                         for a in (PARENT, CHILD)]}
    entries = fee_stack(rows, positions, configs)['entries']
    child = next(e for e in entries if e['vault']['address'] == CHILD)
    assert child['adjustedTvlUsd'] == 120
    assert child['roots'][0]['capitalUsd'] == 200  # Reported gross stays gross.
    assert child['performanceFee'] == 1000
    assert next(v for v in dataset.audit()['vaults'] if v['address'] == CHILD)['countedTvlUsd'] == 120
    graph = dataset.graph()
    assert next(v for v in graph['nodes'] if v['address'] == CHILD)['externalTvlUsd'] == 120
    assert sum(e['deductedTvlUsd'] for e in graph['edges']) == 80
    # Fresh objects ensure this verifies persisted history rather than an LRU hit.
    prepare(dataset)
    prepared = TvlDataset(dataset.manifest)
    assert prepared.history(interval='daily', group='category', constant=True) == history
    # Stored dataset IDs must retain the previous separate curation policy.
    legacy = dict(dataset.manifest, datasetId='legacy')
    legacy.pop('ownershipAccounting')
    assert TvlDataset(legacy).summary()['totalTvl'] == 300
    assert TvlDataset(legacy).history(interval='daily', group='category')['chart'][0]['curation'] == 200


def test_existing_hops_and_shared_holder_caps_are_not_deducted_twice():
    points = [snapshot(1, '100'), snapshot(2, '100'), snapshot(3, '200', 'curation'),
              snapshot(4, '200', 'curation')]
    positions = [edge(1, 2, 2, '100', '100') | {'method': 'direct-strategy'},
                 edge(2, 9, 3, '100', '200'), edge(2, 9, 4, '100', '200')]
    rows, edges, totals = account(points, positions)
    assert [Decimal(p['overlap_usd']) for p in edges] == [100, 50, 50]
    assert [Decimal(r['external_tvl_usd']) for r in rows] == [100, 0, 150, 150]
    assert Decimal(totals['external_tvl_usd']) == 400
    # Shared holder cannot deduct its whole balance for each parent.
    points = [snapshot(1, '500'), snapshot(2, '500'), snapshot(3, '100', 'curation')]
    _, edges, totals = account(points, [edge(1, 9, 3, '100', '200'), edge(2, 9, 3, '300', '200')])
    assert [Decimal(p['overlap_usd']) for p in edges] == [25, 75]
    assert Decimal(totals['overlap_usd']) == 100


def test_missing_dated_ownership_is_not_zero_or_filled_from_latest(source):
    conn, path, root = source
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200')], [(CHILD, None)])
    run(conn, 2, DAY + 86400, [(PARENT, '100'), (CHILD, '200')], [(CHILD, '80')])
    curate(conn)
    dataset = TvlStore(path, root).get()
    _, rows, positions, diagnostics = dataset.frames((DAY,))[0]
    child = next(r for r in rows if r['vault'] == CHILD)
    assert child['tvl_usd'] == '200'
    assert child['external_tvl_usd'] is child['overlap_usd'] is None
    assert positions[0]['overlap_usd'] is None
    assert diagnostics['status'] == 'incomplete'
    assert next(e for e in fee_stack(rows, positions, {})['entries'] if e['vault']['address'] == CHILD)['adjustedTvlUsd'] is None
    before = dataset.history(interval='daily', group='category', constant=True)
    assert 'curation' not in before['actualChart'][0]
    assert 'curation' not in before['constantPriceChart'][0]
    prepare(dataset)
    assert TvlDataset(dataset.manifest).history(interval='daily', group='category', constant=True) == before
    # An observed zero is complete, even when no share valuation was available.
    run(conn, 3, DAY, [], [(CHILD, '0')])
    row = json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=3').fetchone()[0])
    row['owned_usd'] = None
    conn.execute('UPDATE tvl_positions SET data_json=? WHERE run_id=3', (json.dumps(row),))
    conn.commit()
    repaired = TvlStore(path, root).get()
    assert repaired.history(interval='daily', group='category')['chart'][0]['curation'] == 200


@pytest.mark.parametrize('chain_id,category,expected', [(None, None, True), (1, 'curation', True),
                                                      (10, None, False), (None, 'v3', False)])
def test_same_chain_child_valuation_must_match_holdings_block(source, chain_id, category, expected):
    conn, path, root = source
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200')], [(CHILD, '80')])
    row = json.loads(conn.execute('SELECT data_json FROM tvl_snapshots WHERE vault=?', (CHILD,)).fetchone()[0])
    row.update(category='curation', block_number=999)
    conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE vault=?', (json.dumps(row), CHILD))
    conn.commit()
    dataset = TvlStore(path, root).get()
    _, rows, positions, _ = dataset.frames((DAY,))[0]
    assert positions[0]['reason'] == 'selected_child_block_mismatch'
    assert next(r for r in rows if r['vault'] == CHILD)['external_tvl_usd'] is None
    graph = dataset.graph(chain_id=chain_id, category=category)
    # A scope containing only unknown ownership has no available subtotal.
    assert graph['totalOverlapTvlUsd'] == (None if category == 'curation' else 0)
    assert graph['diagnostics']['unavailableNodes'] == (
        [{'nodeId': f'1:{CHILD}', 'reason': 'incomplete_ownership'}] if expected else [])
    if expected:
        node = next(node for node in graph['nodes'] if node['address'] == CHILD)
        assert node['rawTvlUsd'] == 200
        assert node['externalTvlUsd'] is node['upstreamOwnedTvlUsd'] is None


def test_incomplete_ownership_keeps_gross_and_partial_evidence_distinct(source):
    conn, path, root = source
    other_parent = '0x' + '66' * 20
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200'), (other_parent, '100')], [(CHILD, '30')])
    stored = conn.execute('SELECT data_json FROM tvl_positions').fetchone()[0]
    missing = json.loads(stored)
    missing.update(parent=other_parent, strategy=other_parent, owned_usd=None, debt_usd='50')
    conn.execute('INSERT INTO tvl_positions VALUES (?,?,?,?,?,?,?)',
                 (1, 1, other_parent, other_parent, CHILD, DAY, json.dumps(missing)))
    conn.commit()
    dataset = TvlStore(path, root).get()
    _, rows, positions, diagnostics = dataset.frames((DAY,))[0]
    child = next(row for row in rows if row['vault'] == CHILD)
    assert child['tvl_usd'] == '200'
    assert child['overlap_usd'] is child['external_tvl_usd'] is None
    assert diagnostics['known_gross_tvl_usd'] == '400'
    assert Decimal(diagnostics['known_overlap_usd']) == 30
    assert diagnostics['known_external_tvl_usd'] == '200'
    assert diagnostics['external_tvl_usd'] is None
    assert sum(Decimal(p['overlap_usd']) for p in positions if p['overlap_usd'] is not None) == 30
    summary = dataset.summary()
    assert summary['tvlByChain']['Ethereum'] == 400
    assert summary['totalTvl'] == 200
    assert summary['completeness']['status'] == 'incomplete'
    assert summary['completeness']['unresolvedPositions'] > 0
    assert summary['overlapExcluded'] == 0  # No complete child-level deduction is available.


@pytest.mark.parametrize('include_curation', [True, False])
def test_export_records_current_and_legacy_ownership_policy(source, tmp_path, include_curation):
    conn, _, _ = source
    # A non-curation edge verifies that legacy availability is restored too.
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200')], [(CHILD, None)])
    out = export_tvl(conn, tmp_path / 'export', include_curation=include_curation)
    exported = json.loads((out / 'tvl.json').read_text())
    assert exported['run']['include_curation'] is include_curation
    assert exported['run']['require_complete_ownership'] is include_curation
    child = next(row for row in exported['vaults'] if row['vault'] == CHILD)
    assert child['tvl_usd'] == '200'
    assert child['external_tvl_usd'] == (None if include_curation else '200')
    assert child['overlap_usd'] == (None if include_curation else '0')
    assert exported['history'][0]['known_external_tvl_usd'] == ('100' if include_curation else '300')
    assert exported['history'][0]['external_tvl_usd'] is None


def test_curation_preserves_unknown_corrected_destination_ownership(source, monkeypatch):
    from yearn_data import tvl_api as api
    from test_tvl_api import BRIDGE
    conn, database, publication = source
    monkeypatch.setattr(api, 'TARGET_CORRECTIONS', [
        {'chainId':1, 'strategyAddress':BRIDGE, 'targetVaultAddress':CHILD,
         'supersededTargetVaultAddresses':[BRIDGE]}])
    run(conn, 1, DAY, [(PARENT, '100'), (CHILD, '200')], [(BRIDGE, '50')])
    for stored in conn.execute('SELECT vault,data_json FROM tvl_snapshots').fetchall():
        row = json.loads(stored['data_json']); row['category'] = 'curation'
        conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE vault=?', (json.dumps(row), stored['vault']))
    edge = json.loads(conn.execute('SELECT data_json FROM tvl_positions').fetchone()[0])
    edge['method'] = 'service-registry'
    conn.execute('UPDATE tvl_positions SET data_json=?', (json.dumps(edge),)); conn.commit()
    dataset = TvlStore(database, publication).get()
    _, accounted, positions, _ = dataset.frames((DAY,))[0]
    assert positions[0]['accounting_excluded'] is True
    assert positions[0]['reason'] == 'superseded_registry_target'
    assert next(row for row in accounted if row['vault'] == CHILD)['external_tvl_usd'] is None
    products = dataset.curation()
    child = next(row for row in products['vaults'] if row['address'] == CHILD)
    assert child['grossTvlUsd'] == 200
    assert child['knownInternalOverlapTvlUsd'] is child['netTvlUsd'] is None
    assert products['grossTvlUsd'] == 300
    assert products['totalTvlUsd'] == dataset.summary()['totalTvl'] == 100
