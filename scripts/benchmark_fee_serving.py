#!/usr/bin/env python3
"""Opt-in fee view benchmark; synthetic rows, optionally disposable local Postgres.

PYTHONPATH=src python scripts/benchmark_fee_serving.py --output /tmp/fees.json
Add --postgres with YEARN_TEST_POSTGRES_URL pointing at disposable localhost
Postgres to include SQL/deserialization costs. No production data or CI changes.
Use --pairing-source /tmp/pairing-before.py to measure a frozen baseline source.
"""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import time
import uuid

from yearn_data import pairing


def months(count):
    return [int(datetime(2024 + i // 12, i % 12 + 1, 1, tzinfo=timezone.utc).timestamp())
            for i in range(count + 1)]


def fixture(cls, count, vault_count):
    data = object.__new__(cls)
    data.id = 'b' * 64
    data.database = 'staging'
    bounds = months(count)
    data.cutoff = bounds[-1]
    data.chains = (1, 10, 137, 8453)
    data.vault_names = {}
    data.fees, data.earnings = [], []
    for period in range(count):
        for vault in range(vault_count):
            address = f'0x{vault + 100:040x}'
            base = {'chain_id': data.chains[vault % 4], 'vault_address': address,
                    'block_timestamp': bounds[period] + 86400,
                    'contract_family': pairing.FAMILIES[vault % 3], 'version': 'v3'}
            # Realistic decimal strings plus unavailable and genuine zero values.
            values = None if vault % 47 == 0 else ('0' if vault % 31 == 0 else '123.45678901234567890123456789')
            data.fees.append(base | {field: values for field in pairing.FEE_FIELDS.values()})
            data.earnings.append(base | {field: values for field in pairing.EARNINGS_FIELDS.values()})
    data.vaults = frozenset((row['chain_id'], row['vault_address']) for row in data.fees)
    return data, bounds


@contextmanager
def source(data, postgres):
    if not postgres:
        yield lambda: copy.copy(data)
        return
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from yearn_data import hosted_fees
    from yearn_data.storage import connect
    from contextlib import closing
    url = os.environ['YEARN_TEST_POSTGRES_URL']
    if conninfo_to_dict(url).get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Benchmark Postgres must be disposable localhost')
    schema = 'yearn_bench_' + uuid.uuid4().hex
    old = {key: os.environ.get(key) for key in ('STAGING_DB_URL', 'YEARN_DATA_DB_SCHEMA')}
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            os.environ['STAGING_DB_URL'] = url
            os.environ['YEARN_DATA_DB_SCHEMA'] = schema
            with closing(connect('staging')) as conn:
                hosted_fees.prepare(conn, data)
            # Use the loaded class's algorithm for before/after comparison.
            class SQLData(hosted_fees.PublishedFees):
                view = type(data).view
            yield lambda: SQLData('staging', data.id)
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def measure(make, bounds, scope, grouped, repeats):
    samples = []
    for repeat in range(repeats):
        start = time.perf_counter()
        data = make()
        if grouped:
            data.view = pairing.PairingDataset.view.__get__(data, type(data))
        constructed = time.perf_counter()
        reads = 0
        original = data.view_rows
        def read(*args, **kwargs):
            nonlocal reads
            reads += 1
            return original(*args, **kwargs)
        data.view_rows = read
        def query():
            if grouped:
                return data.view('vault-history', since=bounds[0], until=bounds[-1], interval='monthly', scope=scope)
            return [data.view('vaults', since=left, until=right, scope=scope)
                    for left, right in zip(bounds, bounds[1:])]
        result = query()
        computed = time.perf_counter()
        encoded = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        finished = time.perf_counter()
        identity = ('chainId', 'address', 'name', 'contractFamily', 'productId', 'memberAddresses', 'earningsAddress')
        normalized = [{'count': item['count'], 'vaults': [
            {key: row[key] for key in identity if key in row} | {
                'totalFeesPaidUsd': row['totalFeesPaidUsd'],
                'lifetimeEarnings': {'netYieldUsd': row['lifetimeEarnings']['netYieldUsd']}}
            for row in item['vaults']]}
            for item in (result['buckets'] if grouped else result)]
        canonical = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        cold_reads = reads
        reads = 0
        warm_start = time.perf_counter()
        query()
        warm_ms = (time.perf_counter() - warm_start) * 1000
        samples.append({'repeat': repeat + 1, 'construct_ms': (constructed - start) * 1000,
                        'compute_ms': (computed - constructed) * 1000,
                        'serialize_ms': (finished - computed) * 1000,
                        'total_ms': (finished - start) * 1000, 'view_rows_calls': cold_reads,
                        'canonical_sha256': canonical, 'warm_compute_ms': warm_ms, 'warm_view_rows_calls': reads,
                        'json_bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()})
    return {'scope': scope, 'strategy': 'grouped' if grouped else 'monthly-fanout',
            'median_total_ms': statistics.median(row['total_ms'] for row in samples),
            'samples': samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months', type=int, default=32)
    parser.add_argument('--vaults', type=int, default=250)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--grouped', action='store_true')
    parser.add_argument('--compare', action='store_true', help='Run frozen fanout and current grouped against the same fixture')
    parser.add_argument('--pairing-source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    module = pairing
    if args.pairing_source:
        spec = importlib.util.spec_from_file_location('yearn_data._benchmark_before', args.pairing_source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    data, bounds = fixture(module.PairingDataset, args.months, args.vaults)
    with source(data, args.postgres) as make:
        results = [measure(make, bounds, scope, grouped, args.repeats)
                   for scope in ('contract', 'product')
                   for grouped in ((False, True) if args.compare else (args.grouped,))]
    if args.compare:
        for scope in ('contract', 'product'):
            hashes = {sample['canonical_sha256'] for result in results if result['scope'] == scope
                      for sample in result['samples']}
            if len(hashes) != 1:
                raise ValueError(f'{scope} grouped values differ from monthly fanout')
    output = {'fixture': {'months': args.months, 'vaults': args.vaults,
                           'fee_rows': len(data.fees), 'earning_rows': len(data.earnings)},
              'source': 'disposable-postgres' if args.postgres else 'in-memory',
              'limits': 'New view per repeat; Postgres/OS caches may be warm. Second call measures same-view process cache. No CDN, HTTP, TLS or production latency claim.',
              'results': results}
    args.output.write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({scope['scope'] + ':' + scope['strategy']: round(scope['median_total_ms'], 2) for scope in results}))


if __name__ == '__main__':
    main()
