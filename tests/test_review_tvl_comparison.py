"""Run the evidence builder offline through both decimal-dependent deductions."""
import hashlib
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from yearn_data import analytics_collect

PARENT = '0x' + '11' * 20
CHILD = '0x' + '22' * 20
SCRIPT = Path(__file__).parents[1] / 'scripts/review_tvl_comparison.py'


@pytest.mark.parametrize('mode', ['finance-missing', 'curation-missing', 'finance-known', 'curation-known', 'current-unpriced'])
def test_comparison_builder_requires_decimals_only_for_deductions(tmp_path, monkeypatch, mode):
    # Stub the pinned source gate to exercise deduction behavior with offline fixtures.
    import ast
    module = ast.parse(SCRIPT.read_text())
    assignment = next(n for n in module.body if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'SUPPORTED_SOURCE_HASHES')
    hashes = ast.literal_eval(assignment.value)
    if mode == 'current-unpriced':
        hashes.update({'projects_yearn_index.js': 'f40884966b5257067ad98076d95f3a283255778beb77bf58c7370c0fc6f29e96',
                       'registries_curators.js': 'ba341896a16f189846fd315983b052c22bb19c0d65693bedd63634238bc1b67c'})
    fixture_hashes = {}
    for name, digest in hashes.items():
        content = ('fixture:' + name).encode()
        (tmp_path / name).write_bytes(content)
        fixture_hashes[content] = digest
    original_hash = hashlib.sha256
    monkeypatch.setattr(hashlib, 'sha256', lambda data=b'':
        SimpleNamespace(hexdigest=lambda:fixture_hashes[data]) if data in fixture_hashes else original_hash(data))
    monkeypatch.setattr(analytics_collect, 'js_array', lambda *args: [])
    monkeypatch.setattr(analytics_collect, 'rules', lambda *args: {'cross': {}, 'owners': {}})
    curated = mode.startswith('curation')
    rows = [{'chain_id': 1, 'vault': a, 'timestamp': 123, 'asset': PARENT, 'asset_units': '100',
             'asset_decimals': 2, 'tvl_usd': '100', 'name': a, 'category': 'curation' if curated else 'v3',
             'total_assets_raw': '10000'} for a in (PARENT, CHILD)]
    if mode.endswith('missing') or mode == 'current-unpriced':
        rows[1 if curated else 0]['asset_decimals'] = None
    position = {'chain_id': 1, 'parent': PARENT, 'child': CHILD, 'strategy': CHILD,
                'debt_raw': '10', 'method': 'direct-strategy', 'overlap_usd': '0'}
    frame = {'timestamp': 123, 'rows': rows, 'catalog': [],
             'positions': [] if mode == 'current-unpriced' else [position], 'selection': {'tvlDatasetId': 'dataset'}}
    discovery = [{'chain': 1, 'vaults': [{'chain': 1, 'address': a} for a in (PARENT, CHILD)],
                  'state': 'complete', 'factory': {'chain_id': 1}}] if curated else []
    data = {'frame.json': frame, 'kong.json': {} if curated else {'1': [{'address': a} for a in (PARENT, CHILD)]},
            'independent-discovery.json': discovery, 'verified-membership.json': [], 'prices.json': {},
            'sources.json': {}, 'reverse-bad-debt.json': [], 'adapter-replay-hyper.json': [], 'adapter-replay-katana.json': [],
            'adapter-replay.json': [{'chain': 1, 'vault': PARENT, 'child': CHILD, 'convertedAssets': '10'}] if curated else [],
            'finance-replay.json': [{'chainId': 1, 'timestamp': 123, 'edges': [],
                'records': [{'address': a, 'adjustedRaw': '1000'} for a in (PARENT, CHILD)]}]}
    for name, body in data.items():
        (tmp_path / name).write_text(json.dumps(body))
    monkeypatch.setattr(sys, 'argv', [str(SCRIPT), str(tmp_path)])
    if mode.endswith('missing'):
        address = CHILD if curated else PARENT
        with pytest.raises(SystemExit, match=f'Missing asset decimals.*chain 1.*{address}'):
            runpy.run_path(str(SCRIPT), run_name='__main__')
        assert not (tmp_path / 'evidence.json').exists()
    else:
        runpy.run_path(str(SCRIPT), run_name='__main__')
        output = json.loads((tmp_path / 'evidence.json').read_text())
        row = next(r for r in output['vaults'] if r['address'] == (CHILD if curated else PARENT))
        assert row['units'] == (None if mode == 'current-unpriced' else '99.9')
