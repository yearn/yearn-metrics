"""A fixed retained inventory protects coverage-query cost and result parity.

Run with -s to print a benchmark receipt. Provider calls are deterministic offline
boundaries; optional simulated database latency is reported, never a live claim.
"""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
import time
from types import SimpleNamespace

from hexbytes import HexBytes

from yearn_data import catchup
from yearn_data.coverage import commit_range
from yearn_data.history_sources import HistoricalSource, report_scope, write_rows
from yearn_data.indexing import normalize_strategy_report
from yearn_data.storage import connect, init_db

HASH = '0x' + 'ab' * 32
ASSET = '0x' + '43' * 20


def report(vault):
    return normalize_strategy_report(10, 'v2', vault['address'], ASSET, 6,
        dict(transactionHash='0x' + vault['address'][2:] + '00' * 12, logIndex=0, blockNumber=25),
        1000, dict(strategy=ASSET, gain=12, loss=0, debtAdded=0, debtPaid=0,
                   totalGain=12, totalLoss=0, totalDebt=100, debtRatio=100))


class MeasuredSource(HistoricalSource):
    def __init__(self):
        self.requests = dict(chain_identity=0, block_header=0, reports=0)
        source = self
        class Eth:
            @property
            def chain_id(self):
                source.requests['chain_identity'] += 1
                return 10

            def get_block(self, number):
                source.requests['block_header'] += 1
                return dict(number=90 if number == 'finalized' else number,
                            hash=HexBytes(HASH), timestamp=1000)
        super().__init__('op', SimpleNamespace(eth=Eth()))

    def reports(self, vault, lo, hi, source):
        self.requests['reports'] += 1
        return [report(vault)] if lo <= 25 <= hi else []


class MeasuredConnection:
    def __init__(self, conn, delay):
        self.conn, self.delay = conn, delay
        self.coverage_reads = 0

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *args):
        return self.conn.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, params=()):
        if 'FROM history_coverage' in sql and sql.lstrip().startswith('SELECT'):
            self.coverage_reads += 1
            if self.delay:
                time.sleep(self.delay)
        return self.conn.execute(sql, params)


def test_retained_inventory_reuses_coverage_with_bounded_database_reads(postgres_database):
    delay = float(os.environ.get('YEARN_BENCHMARK_ROUNDTRIP_MS', '0')) / 1000
    baseline_path = os.environ.get('YEARN_BENCHMARK_BASELINE_MODULE')
    collector = catchup
    if baseline_path:
        spec = importlib.util.spec_from_file_location('yearn_data._coverage_baseline', baseline_path)
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
    with closing(connect('staging')) as conn:
        init_db(conn)
        vaults = [dict(chain_id=10, version='v2', address=f'0x{i+1:040x}',
                       asset=ASSET, asset_decimals=6, api_version='0.4.6', active=i % 2)
                  for i in range(120)]
        with conn:
            conn.executemany('''INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,
                api_version,active,updated_at) VALUES(10,'v2',?,?,6,'0.4.6',?,0)''',
                [(v['address'], ASSET, v['active']) for v in vaults])
        for index, vault in enumerate(vaults[:80]):
            commit_range(conn, report_scope(vault), 10, 30 if index < 40 else 19,
                         source='rpc', end_hash=HASH, run_id=None,
                         write=lambda db, v=vault, i=index: write_rows(
                             db, 'strategy_reports', [report(v)] if i < 40 else [], report_metadata=v))
        measured = MeasuredConnection(conn, delay)
        receipts = []
        diagnostics = []
        for phase in ('extend', 'resume'):
            client = MeasuredSource()
            reads_before = measured.coverage_reads
            started = time.perf_counter()
            result = collector.catch_up(measured, 'op', 10, 30, source='rpc', client=client)
            # Same update ordering as update_postgres.py: TVL cannot start before
            # catch-up returns (other accounting stages are excluded here).
            until_tvl = time.perf_counter() - started
            receipts.append(dict(phase=phase, secondsUntilTvlEligible=until_tvl,
                coverageReadQueries=measured.coverage_reads-reads_before,
                providerBoundaryRequests=client.requests, completedRanges=result['completed_ranges'],
                insertedRows=result['inserted_rows']))
            diagnostics.append(result.get('report_work'))
        rows = [dict(r) for r in conn.execute('SELECT * FROM strategy_reports ORDER BY tx_hash')]
        coverage = [tuple(r) for r in conn.execute('''SELECT target,from_block,to_block,row_count
            FROM history_coverage ORDER BY target,from_block,to_block''')]
        receipt = dict(inventory=120, active=60, inactive=60, initiallyFullyCovered=40,
                       initiallyPartiallyCovered=40, initiallyUncovered=40,
                       fromBlock=10, toBlock=30, simulatedCoverageReadLatencyMs=delay*1000,
                       reportRows=len(rows), coverageRows=len(coverage),
                       reportsDigest=hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                       coverageDigest=hashlib.sha256(json.dumps(coverage).encode()).hexdigest(), runs=receipts)
        print('\nCOVERAGE_BENCHMARK=' + json.dumps(receipt, sort_keys=True))
        # Historical collectors have no activity diagnostics. The default
        # optimized run must retain and validate them; baseline runs reach the
        # query-budget assertion only after printing their complete receipt.
        if not baseline_path:
            for phase, work_by_activity in zip(('extend', 'resume'), diagnostics):
                assert work_by_activity is not None
                for activity in ('active', 'inactive'):
                    work = work_by_activity[activity]
                    assert work['vaults'] == 60
                    assert work['reused_blocks'] == (620 if phase == 'extend' else 1260)
                    assert work['scanned_blocks'] == (640 if phase == 'extend' else 0)
        assert len(rows) == 120
        assert {r['gain_raw'] for r in rows} == {'12'}
        assert len(coverage) == 160
        assert receipts[0]['providerBoundaryRequests']['reports'] == 80
        assert receipts[0]['completedRanges'] == receipts[0]['insertedRows'] == 80
        assert receipts[1]['providerBoundaryRequests']['reports'] == 0
        assert receipts[1]['completedRanges'] == receipts[1]['insertedRows'] == 0
        assert all(r['coverageReadQueries'] <= 2 for r in receipts)
