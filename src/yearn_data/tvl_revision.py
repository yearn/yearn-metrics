"""Fingerprint the maintained row/total preparation dependency boundary."""
import ast
from functools import lru_cache
import hashlib
from pathlib import Path

# Entry points, not whole serving modules. Module-local dependencies (including
# constants and import bindings) are followed below. Add new financial instance
# methods here; keep serving/cache initialization outside _init_accounting.
ROOTS = {
    'tvl.py': ('decimal', 'value_nested_shares', 'asset_units', 'account', '_account'),
    'tvl_policy.py': ('classify_vault', 'correct_catalog'),
    'tvl_api.py': ('CHAINS', 'encoded', 'read_db', 'key', 'observation_selection', 'TvlDataset'),
    'tvl_history_cache.py': ('ready', 'totals_ready', 'rows', 'prepare', 'prepare_totals', 'prepare_price_repair'),
    'tvl_incremental.py': ('digest', 'context_identity', 'input_identity', 'preparation', 'build'),
}
DATASET_METHODS = {'_init_accounting', 'dates', 'as_of', 'selected_rows',
                   'validate_price', 'input_frames', 'iter_frames', 'account_frame'}


def accounting_revision():
    root = Path(__file__).parent
    sources = tuple((name, (root/name).read_bytes()) for name in (*ROOTS, 'tvl_revision.py'))
    return _revision(sources)


@lru_cache(maxsize=4)
def _revision(sources):
    digest = hashlib.sha256(b'accounted-row-total-source-v2')
    for name, source in sources:
        tree = ast.parse(source)
        if name == 'tvl_revision.py':
            # Changes to this financial boundary/algorithm also invalidate it.
            selected = [tree]
        else:
            bindings = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    if name == 'tvl_api.py' and node.name == 'TvlDataset':
                        methods = {method.name: method for method in node.body if isinstance(method, ast.FunctionDef)}
                        if not DATASET_METHODS <= methods.keys():
                            raise ValueError('TVL financial method boundary is incomplete')
                        pending_methods = list(DATASET_METHODS)
                        selected_methods = set()
                        while pending_methods:
                            method = pending_methods.pop()
                            if method in selected_methods:
                                continue
                            selected_methods.add(method)
                            pending_methods.extend(item.attr for item in ast.walk(methods[method])
                                                   if isinstance(item, ast.Attribute) and
                                                   isinstance(item.value, ast.Name) and item.value.id == 'self'
                                                   and item.attr in methods)
                        node.body = [methods[method] for method in sorted(selected_methods)]
                    bindings[node.name] = node
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        for item in ast.walk(target):
                            if isinstance(item, ast.Name):
                                bindings[item.id] = node
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        binding = alias.asname or (alias.name.split('.')[0] if isinstance(node, ast.Import) else alias.name)
                        bindings[binding] = (ast.Import(names=[alias]) if isinstance(node, ast.Import) else
                                             ast.ImportFrom(module=node.module, names=[alias], level=node.level))
            selected_names = set()
            pending = list(ROOTS[name])
            if any(symbol not in bindings for symbol in pending):
                raise ValueError(f'TVL financial source boundary is incomplete: {name}')
            while pending:
                symbol = pending.pop()
                if symbol in selected_names or symbol not in bindings:
                    continue
                selected_names.add(symbol)
                pending.extend(item.id for item in ast.walk(bindings[symbol])
                               if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load))
            selected = [bindings[symbol] for symbol in sorted(selected_names)]
        digest.update(name.encode())
        digest.update(ast.dump(ast.Module(body=selected, type_ignores=[]), include_attributes=False).encode())
    return digest.hexdigest()
