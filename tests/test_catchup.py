"""Supported bounded acquisition uses retained inventory and durable ranges."""

import json
from types import SimpleNamespace

from hexbytes import HexBytes
import pytest
from web3 import Web3

from yearn_data import catchup, cli, kong
from yearn_data.history_sources import HistoricalSource, inventory_targets
from yearn_data.indexing import normalize_strategy_report
from yearn_data.storage import connect, init_db

VAULT = '0x' + '41' * 20
NEW = '0x' + '42' * 20
ASSET = '0x' + '43' * 20
HASH = '0x' + 'ab' * 32


def seed(conn, address=VAULT, management='yearn'):
    conn.execute("""INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,
        api_version,management,active,updated_at) VALUES(10,'v2',?,?,6,'0.4.6',?,0,0)""",
        (address, ASSET, management))
    conn.commit()


@pytest.fixture
def db(postgres_database, tmp_path):
    conn = connect('staging')
    init_db(conn)
    seed(conn)
    yield conn
    conn.close()


class Source(HistoricalSource):
    def __init__(self):
        w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=10, block_number=100,
            get_block=lambda n: dict(number=90 if n == 'finalized' else n,
                                     hash=HexBytes(HASH), timestamp=1000)))
        def gql(query, variables):
            assert variables == {'chain': 10}
            assert 'chain_metadata' in query
            return {'chain_metadata': [{'latest_processed_block': 90}]}
        super().__init__('op', w3, gql)
        self.calls = []
        self.fail = None

    def reports(self, vault, lo, hi, source):
        self.calls.append((vault['address'], lo, hi))
        if self.fail == vault['address']:
            raise RuntimeError('provider unavailable')
        if not lo <= 25 <= hi:
            return []
        return [normalize_strategy_report(10, 'v2', vault['address'], vault['asset'], vault['asset_decimals'],
            dict(transactionHash='0x'+vault['address'][2:4]*32, logIndex=0, blockNumber=25),
            1000, dict(strategy=ASSET, gain=12, loss=0, debtAdded=0, debtPaid=0,
                       totalGain=12, totalLoss=0, totalDebt=100, debtRatio=100))]

    def inventory(self, target, lo, hi, source):
        if target != next(inventory_targets('op')) or not lo <= 20 <= hi:
            return []
        return [dict(chain_id=10, version='v2', vault_address=NEW,
                     source_kind=target.kind, action='added', source_address=target.address,
                     asset=ASSET, tx_hash='0x'+'33'*32, log_index=0, block_number=20,
                     block_timestamp=900, decoded_json=json.dumps(dict(
                         token=ASSET, vault=NEW, api_version='0.4.6', vault_id=0)))]


def test_discovery_unions_retained_inactive_vaults_and_resumes(db, monkeypatch):
    seed(db, '0x'+'44'*20, management='external')
    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', lambda *args: {
        Web3.to_checksum_address(NEW): dict(asset=ASSET, asset_decimals=6, api_version='0.4.6')})
    from test_catchup_performance import MeasuredConnection
    measured = MeasuredConnection(db, 0)
    expected_reads = len(list(inventory_targets('op'))) + 1
    client = Source()
    result = catchup.catch_up(measured, 'op', 10, 30, discover=True, chunk_size=10, client=client)
    assert measured.coverage_reads == result['coverage_read_queries'] == expected_reads
    assert result['vaults'] == 2
    assert {a for a, _, _ in client.calls} == {VAULT, NEW}
    assert db.execute('SELECT COUNT(*) FROM strategy_reports').fetchone()[0] == 2
    assert db.execute('SELECT COUNT(*) FROM events_raw').fetchone()[0] == 2
    assert result['pin']['finality'] == 'finalized'
    assert result['source'] == 'envio'
    assert db.execute('SELECT active FROM vaults WHERE address=?', (VAULT,)).fetchone()[0] == 0
    client.calls.clear()
    before = measured.coverage_reads
    repeated = catchup.catch_up(measured, 'op', 10, 30, discover=True, chunk_size=7, client=client)
    assert measured.coverage_reads-before == repeated['coverage_read_queries'] == expected_reads
    assert repeated['completed_ranges'] == repeated['inserted_rows'] == 0
    assert client.calls == []


def test_discovery_batches_missing_metadata_and_reuses_cached_values(db, monkeypatch):
    addresses = [Web3.to_checksum_address(f'0x{0xabcdef000 + i:040x}') for i in range(205)]
    external = '0x' + '44' * 20
    seed(db, external, management='external')
    with db:
        db.execute('UPDATE vaults SET asset_decimals=NULL WHERE address=?', (external,))
    cached = dict(db.execute('SELECT * FROM vaults WHERE address=?', (VAULT,)).fetchone())
    client = Source()

    def inventory(target, lo, hi, source):
        rows = Source.inventory(client, target, lo, hi, source)
        return [{**row, 'vault_address': address, 'log_index': index,
                 'decoded_json': json.dumps(dict(token=ASSET, vault=address, api_version='0.4.6', vault_id=index))}
                for row in rows for index, address in enumerate(addresses)]

    requests = []

    def post(url, json, timeout):
        variables = json['variables']
        requests.append(variables)
        vaults = [dict(address=address.lower(), apiVersion='0.4.6',
                       asset=dict(address=ASSET, decimals=6)) for address in variables['addresses']]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dict(data=dict(vaults=vaults)))

    monkeypatch.setattr(client, 'inventory', inventory)
    monkeypatch.setattr(kong.requests, 'post', post)
    result = catchup.catch_up(db, 'op', 10, 20, discover=True, client=client)
    assert result['vaults'] == 206 and result['skipped_vaults'] == []
    assert [len(request['addresses']) for request in requests] == [100, 100, 5]
    assert all(request['chainId'] == 10 for request in requests)
    assert {address for request in requests for address in request['addresses']} == set(addresses)
    assert dict(db.execute('SELECT * FROM vaults WHERE address=?', (VAULT,)).fetchone()) == cached
    assert db.execute('SELECT asset_decimals FROM vaults WHERE address=?', (external,)).fetchone()[0] is None

    requests.clear()
    repeated = catchup.catch_up(db, 'op', 10, 20, discover=True, client=client)
    assert repeated['vaults'] == 206 and repeated['completed_ranges'] == 0
    assert requests == []


@pytest.mark.parametrize('failure_stage', ['reports', 'header'])
def test_failure_keeps_only_completed_ranges_then_resumes(db, monkeypatch, failure_stage):
    seed(db, NEW)
    client = Source()
    client.fail = NEW
    if failure_stage == 'header':
        block = client.block

        def fetch_header(number):
            if client.fail and number == 19 and client.calls and client.calls[-1] == (VAULT, 30, 30):
                raise RuntimeError('provider unavailable https://private-rpc.invalid/secret')
            return block(number)

        monkeypatch.setattr(client, 'block', fetch_header)
    events = []
    with pytest.raises(RuntimeError, match='provider unavailable'):
        catchup.catch_up(db, 'op', 10, 30, chunk_size=10, client=client, progress=events.append)
    failed = json.loads(events[-1])
    assert failed['event'] == 'range-failed'
    assert all('private-rpc' not in event and 'secret' not in event for event in events)
    assert json.loads(events[-2])['event'] == 'range-started'
    assert (failed['target'], failed['from_block'], failed['to_block']) == (NEW, 10, 19)
    assert db.execute('SELECT DISTINCT target FROM history_coverage').fetchall()[0][0] == VAULT
    assert db.execute('SELECT COUNT(*) FROM strategy_reports').fetchone()[0] == 1
    client.fail = None
    client.calls.clear()
    events.clear()
    result = catchup.catch_up(db, 'op', 10, 30, chunk_size=7, client=client, progress=events.append)
    assert {a for a, _, _ in client.calls} == {NEW}
    assert result['vaults'] == 2
    assert db.execute('SELECT COUNT(*) FROM strategy_reports').fetchone()[0] == 2
    planned = [json.loads(e) for e in events if json.loads(e)['event'] == 'coverage-planned']
    assert [(e['target'], e['pending_ranges']) for e in planned] == [(VAULT.lower(), 0), (NEW.lower(), 3)]
    assert json.loads(events[-1])['event'] == 'completed'
    assert result['report_work']['inactive']['reused_blocks'] == 21
    assert result['report_work']['inactive']['scanned_blocks'] == 21


def test_explicit_confirmations_are_recorded_and_reused_hash_is_checked(db):
    client = Source()
    result = catchup.catch_up(db, 'op', 10, 30, confirmations=20, client=client)
    assert result['pin']['finality'] == 'confirmations:20'
    db.execute("UPDATE history_coverage SET end_hash='changed'")
    db.commit()
    client.calls.clear()
    with pytest.raises(ValueError, match='hash changed'):
        catchup.catch_up(db, 'op', 10, 30, client=client)
    assert client.calls == []


@pytest.mark.parametrize('options', [dict(from_block=-1, to_block=30),
    dict(from_block=30, to_block=20), dict(from_block=0,to_block=30,chunk_size=0),
    dict(from_block=0,to_block=30,confirmations=0), dict(from_block=0,to_block=30,experimental=True)])
def test_invalid_scope_fails_before_client_creation(db, monkeypatch, options):
    monkeypatch.setattr(catchup, 'web3_for', lambda *args: pytest.fail('unexpected RPC'))
    with pytest.raises(ValueError):
        catchup.catch_up(db, 'op', **options)


def test_cli_catchup_only_calls_acquisition(postgres_database, tmp_path, monkeypatch, capsys):
    captured = {}
    def acquire(conn, chain, first, last, **kwargs):
        captured.update(chain=chain, first=first, last=last, **kwargs)
        kwargs['progress']('report-acquisition progress')
        return dict(status='complete', pin=dict(finality='confirmations:1000'))
    monkeypatch.setattr(catchup, 'catch_up', acquire)
    monkeypatch.setattr(cli, 'price_unpriced_reports', lambda *args, **kwargs: pytest.fail('unexpected pricing'))
    assert cli.main(['--db', 'staging', 'catch-up', '--chain', 'kat',
        '--from-block', '1', '--to-block', '2', '--confirmations', '1000', '--discover']) == 0
    assert callable(captured.pop('progress'))
    assert captured == dict(chain='kat', first=1, last=2, source='auto', confirmations=1000,
                            chunk_size=50000, discover=True, experimental=False, revalidate_rpc_coverage=False)
    output = capsys.readouterr()
    assert json.loads(output.out)['pin']['finality'] == 'confirmations:1000'
    assert output.err.strip() == 'report-acquisition progress'


def test_existing_reports_deduplicate_and_conflicts_leave_no_coverage(db):
    from yearn_data.history_sources import write_rows
    client = Source()
    vault = dict(db.execute("SELECT * FROM vaults WHERE address=?", (VAULT,)).fetchone())
    report = client.reports(vault, 10, 30, "rpc")[0]
    with db:
        assert write_rows(db, "strategy_reports", [report]) == 1
    result = catchup.catch_up(db, "op", 10, 30, client=client)
    assert result["inserted_rows"] == 0
    db.execute("DELETE FROM history_coverage")
    db.execute("UPDATE strategy_reports SET gain_raw='13'")
    db.commit()
    with pytest.raises(ValueError, match="conflicting observation"):
        catchup.catch_up(db, "op", 10, 30, client=client)
    assert db.execute("SELECT COUNT(*) FROM history_coverage").fetchone()[0] == 0
    assert db.execute("SELECT gain_raw FROM strategy_reports").fetchone()[0] == '13'


@pytest.mark.parametrize('deployment', [None, 5])
def test_discovery_promotes_cached_member_without_inventing_deployment(db, deployment):
    seed(db, NEW, management='non_yearn')
    unrelated = '0x' + '44' * 20
    seed(db, unrelated, management='non_yearn')
    with db:
        db.execute("UPDATE vaults SET deployment_block=?,protocol='cached protocol' WHERE address=?", (deployment, NEW))
    before = dict(db.execute('SELECT * FROM vaults WHERE address=?', (unrelated,)).fetchone())
    client = Source()
    result = catchup.catch_up(db, 'op', 10, 30, discover=True, client=client)
    recovered = dict(db.execute('SELECT * FROM vaults WHERE address=?', (NEW,)).fetchone())
    assert result['vaults'] == 2
    assert recovered['management'] == 'yearn'
    assert recovered['deployment_block'] == deployment
    assert recovered['protocol'] == 'cached protocol'
    assert dict(db.execute('SELECT * FROM vaults WHERE address=?', (unrelated,)).fetchone()) == before
    assert db.execute('SELECT block_number FROM strategy_reports WHERE vault_address=?', (NEW,)).fetchone()[0] == 25
    assert {address for address, _, _ in client.calls} == {VAULT, NEW}


@pytest.mark.parametrize('active', [0, 1])
def test_prefetched_coverage_uses_enriched_policy_and_replays_changed_policy(db, monkeypatch, active):
    from yearn_data.coverage import commit_range
    from yearn_data.history_sources import report_scope
    client = Source()
    with db:
        db.execute('UPDATE vaults SET active=? WHERE address=?', (active, VAULT))
    activity = 'active' if active else 'inactive'
    other = 'inactive' if active else 'active'
    empty = dict(vaults=0, reused_blocks=0, reused_ranges=0, scanned_blocks=0, completed_ranges=0, seconds=0)
    vault = dict(db.execute('SELECT * FROM vaults WHERE address=?', (VAULT,)).fetchone())
    commit_range(db, report_scope(vault), 10, 30, source='rpc', end_hash=HASH,
                 run_id=None, write=lambda conn: 0)
    with db:
        db.execute('UPDATE vaults SET asset_decimals=NULL WHERE address=?', (VAULT,))
    metadata = dict(asset=ASSET, asset_decimals=6, api_version='0.4.6')
    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', lambda *args: {Web3.to_checksum_address(VAULT): metadata})
    result = catchup.catch_up(db, 'op', 10, 30, source='rpc', client=client)
    assert client.calls == []
    assert result['report_work'][activity]['reused_blocks'] == 21
    assert result['report_work'][activity]['vaults'] == 1
    assert result['report_work'][other] == empty
    # A different policy from the same prefetched target is never proof of
    # coverage. Metadata changes require replay and invalidate the old policy.
    with db:
        db.execute('UPDATE vaults SET asset_decimals=18 WHERE address=?', (VAULT,))
    result = catchup.catch_up(db, 'op', 10, 30, source='rpc', client=client)
    assert client.calls == [(VAULT, 10, 30)]
    assert result['report_work'][activity]['scanned_blocks'] == 21
    assert result['report_work'][activity]['vaults'] == 1
    assert result['report_work'][other] == empty
    saved = db.execute('SELECT policy FROM history_coverage').fetchall()
    assert [r[0] for r in saved] == [report_scope({**vault, 'asset_decimals': 18}).policy]
