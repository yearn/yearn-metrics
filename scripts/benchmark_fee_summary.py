#!/usr/bin/env python3
"""Opt-in summary benchmark on synthetic data, optionally disposable local Postgres.

Freeze pairing.py and hosted_fees.py before editing, then pass both with
--pairing-source and --hosted-source. Run separate before/after invocations on
an idle host using identical fixture sizes. No production or CI mutation.
"""
import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from benchmark_fee_serving import fixture, source
from yearn_data import hosted_fees, pairing


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

    Row loading includes execute, iteration, decode and connection close; its
    residual after connection/decode is SQL transfer and Python row handling.
    """
    timings = {'connect_ms': 0., 'decode_ms': 0., 'connect_calls': 0, 'decode_calls': 0}
    original_connect, original_loads = module.connect, json.loads
    def connect(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_connect(*args, **kwargs)
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
    with patch.object(module, 'connect', connect), patch.object(json, 'loads', loads):
        yield timings


def measure(make, module, scope, repeats):
    samples = []
    for _ in range(repeats):
        with profile_io(module) as io:
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
            result = data.view('summary', scope=scope)
            computed = time.perf_counter()
            encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            finished = time.perf_counter()
        # This is a same-object process-cache measurement, separate from fresh reads.
        warm_start = time.perf_counter()
        assert data.view('summary', scope=scope) == result
        warm_ms = (time.perf_counter() - warm_start) * 1000
        samples.append(io | {'construct_ms': (constructed-start)*1000,
            'row_load_ms': rows_ms, 'compute_ms': (computed-constructed)*1000,
            'aggregation_ms': (computed-constructed)*1000-rows_ms,
            'serialize_ms': (finished-computed)*1000, 'total_ms': (finished-start)*1000,
            'warm_same_object_ms': warm_ms, 'json_bytes': len(encoded),
            'financial_sha256': hashlib.sha256(encoded).hexdigest()})
    return {'scope': scope, 'medians': {key: statistics.median(s[key] for s in samples)
            for key in samples[0] if key.endswith('_ms')}, 'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months', type=int, default=32)
    parser.add_argument('--vaults', type=int, default=1000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--pairing-source', type=Path)
    parser.add_argument('--hosted-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-to', type=Path, help='Require identical fixture and full response hashes to a baseline output')
    args = parser.parse_args()
    algorithm = load(args.pairing_source, '_summary_pairing_before', pairing)
    storage = load(args.hosted_source, '_summary_hosted_before', hosted_fees)
    data, _ = fixture(algorithm.PairingDataset, args.months, args.vaults)
    with source(data, args.postgres, storage) as make:
        results = [measure(make, storage, scope, args.repeats) for scope in ('contract', 'product')]
    output = {'fixture': {'months': args.months, 'vaults': args.vaults,
        'fee_rows': len(data.fees), 'earning_rows': len(data.earnings)},
        'source': 'disposable-postgres' if args.postgres else 'in-memory',
        'limits': 'Fresh object per sample; database/OS caches may be warm. Timers add overhead. Aggregation includes grouping and Decimal work. Row loading includes SQL/transfer/iteration/decoding. Same-object warm call is process cache. No CDN, HTTP, TLS, production or whole-page latency claim.',
        'source_sha256': {name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for name, module in (('pairing', algorithm), ('hosted_fees', storage))},
        'results': results}
    if args.compare_to:
        baseline = json.loads(args.compare_to.read_text())
        if baseline['fixture'] != output['fixture'] or baseline['source'] != output['source']:
            raise ValueError('Baseline fixture or storage source differs')
        for result in results:
            previous = next(row for row in baseline['results'] if row['scope'] == result['scope'])
            hashes = {sample['financial_sha256'] for row in (previous, result) for sample in row['samples']}
            if len(hashes) != 1:
                raise ValueError(f"{result['scope']} full summary response differs from baseline")
    args.output.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps({r['scope']: r['medians'] for r in results}, indent=2))


if __name__ == '__main__':
    main()
