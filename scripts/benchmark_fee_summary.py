#!/usr/bin/env python3
"""Opt-in summary benchmark on synthetic data, optionally disposable local Postgres.

Freeze pairing.py and hosted_fees.py before editing, then pass both with
--pairing-source and --hosted-source. Run separate before/after invocations on
an idle host using identical fixture sizes. Use --no-profile for latency and
--include-history for complete grouped-history response parity. No production
or CI mutation.
"""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from benchmark_fee_serving import fixture, source
from yearn_data import hosted_fees, pairing, postgres


def load(path, name, default):
    if path is None:
        return default
    spec = importlib.util.spec_from_file_location('yearn_data.' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def profile_io(module):
    """Time connection establishment and JSON parsing without changing SQL behavior.

    Execute measures server work and libpq transfer together, fetch measures
    driver conversion and Row construction together, and row_wrap is nested
    inside fetch. Per-row timers perturb latency: use --no-profile for that.
    """
    timings = {'connect_ms': 0., 'decode_ms': 0., 'execute_ms': 0.,
               'fetch_ms': 0., 'row_wrap_ms': 0., 'row_wrap_calls': 0,
               'connect_calls': 0, 'decode_calls': 0}
    original_row_init = postgres.Row.__init__
    def row_init(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            original_row_init(self, *args, **kwargs)
        finally:
            timings['row_wrap_ms'] += (time.perf_counter() - start) * 1000
            timings['row_wrap_calls'] += 1
    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
        def fetchone(self):
            start = time.perf_counter()
            try:
                return self.cursor.fetchone()
            finally:
                timings['fetch_ms'] += (time.perf_counter() - start) * 1000
        def __iter__(self):
            iterator = iter(self.cursor)
            while True:
                start = time.perf_counter()
                try:
                    row = next(iterator)
                except StopIteration:
                    return
                finally:
                    timings['fetch_ms'] += (time.perf_counter() - start) * 1000
                yield row
    class Connection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, *args, **kwargs):
            start = time.perf_counter()
            try:
                return Cursor(self.conn.execute(*args, **kwargs))
            finally:
                timings['execute_ms'] += (time.perf_counter() - start) * 1000
        def __getattr__(self, name):
            return getattr(self.conn, name)
    original_connect, original_loads = module.connect, json.loads
    def connect(*args, **kwargs):
        start = time.perf_counter()
        try:
            return Connection(original_connect(*args, **kwargs))
        finally:
            timings['connect_ms'] += (time.perf_counter() - start) * 1000
            timings['connect_calls'] += 1
    def loads(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_loads(*args, **kwargs)
        finally:
            timings['decode_ms'] += (time.perf_counter() - start) * 1000
            timings['decode_calls'] += 1
    with patch.object(module, 'connect', connect), patch.object(json, 'loads', loads), patch.object(postgres.Row, '__init__', row_init):
        yield timings


def measure(make, module, scope, repeats, view='summary', profile=True):
    samples = []
    for _ in range(repeats):
        with (profile_io(module) if profile else nullcontext({})) as io:
            start = time.perf_counter()
            data = make()
            constructed = time.perf_counter()
            rows_ms = 0.
            original = data.view_rows
            def view_rows(*args, **kwargs):
                nonlocal rows_ms
                before = time.perf_counter()
                try:
                    return original(*args, **kwargs)
                finally:
                    rows_ms += (time.perf_counter() - before) * 1000
            data.view_rows = view_rows
            result = data.view(view, scope=scope)
            computed = time.perf_counter()
            encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            finished = time.perf_counter()
        # This is a same-object process-cache measurement, separate from fresh reads.
        warm_start = time.perf_counter()
        assert data.view(view, scope=scope) == result
        warm_ms = (time.perf_counter() - warm_start) * 1000
        samples.append(io | {'construct_ms': (constructed-start)*1000,
            'row_load_ms': rows_ms, 'compute_ms': (computed-constructed)*1000,
            'aggregation_ms': (computed-constructed)*1000-rows_ms,
            'serialize_ms': (finished-computed)*1000, 'total_ms': (finished-start)*1000,
            'warm_same_object_ms': warm_ms, 'json_bytes': len(encoded),
            'financial_sha256': hashlib.sha256(encoded).hexdigest()})
    return {'scope': scope, 'view': view, 'medians': {key: statistics.median(s[key] for s in samples)
            for key in samples[0] if key.endswith('_ms')}, 'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months', type=int, default=32)
    parser.add_argument('--vaults', type=int, default=1000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--no-profile', action='store_true', help='Disable per-row timers for latency comparison')
    parser.add_argument('--include-history', action='store_true', help='Also hash and measure complete grouped vault-history responses')
    parser.add_argument('--pairing-source', type=Path)
    parser.add_argument('--hosted-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-to', type=Path, help='Require identical fixture and full response hashes to a baseline output')
    args = parser.parse_args()
    algorithm = load(args.pairing_source, '_summary_pairing_before', pairing)
    storage = load(args.hosted_source, '_summary_hosted_before', hosted_fees)
    data, _ = fixture(algorithm.PairingDataset, args.months, args.vaults)
    with source(data, args.postgres, storage) as make:
        results = [measure(make, storage, scope, args.repeats, view, not args.no_profile)
                   for view in (('summary', 'vault-history') if args.include_history else ('summary',))
                   for scope in ('contract', 'product')]
    output = {'fixture': {'months': args.months, 'vaults': args.vaults,
        'fee_rows': len(data.fees), 'earning_rows': len(data.earnings)},
        'source': 'disposable-postgres' if args.postgres else 'in-memory',
        'profiled': not args.no_profile,
        'limits': 'Fresh object per sample; database/OS caches may be warm. Timers add overhead. Aggregation includes grouping and Decimal work. Row loading includes SQL/transfer/iteration/decoding. Same-object warm call is process cache. Execute includes server execution and libpq transfer; fetch includes driver decoding and Row wrapping; row_wrap is nested inside fetch. Per-row timers add overhead; use uninstrumented benchmarks for latency. No CDN, HTTP, TLS, production or whole-page latency claim.',
        'source_sha256': {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for name, module in (('pairing', algorithm), ('hosted_fees', storage))},
        'results': results}
    if args.compare_to:
        baseline = json.loads(args.compare_to.read_text())
        if baseline['fixture'] != output['fixture'] or baseline['source'] != output['source']:
            raise ValueError('Baseline fixture or storage source differs')
        for result in results:
            previous = next(row for row in baseline['results'] if row['scope'] == result['scope'] and row.get('view', 'summary') == result['view'])
            hashes = {sample['financial_sha256'] for row in (previous, result) for sample in row['samples']}
            if len(hashes) != 1:
                raise ValueError(f"{result['scope']} {result['view']} response differs from baseline")
    args.output.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps({r['view']+':'+r['scope']: r['medians'] for r in results}, indent=2))


if __name__ == '__main__':
    main()
