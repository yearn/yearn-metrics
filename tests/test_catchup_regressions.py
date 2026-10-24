"""Catch-up preserves lifecycle facts and raw evidence while reusing work."""

from collections import Counter
import json
from types import SimpleNamespace

from eth_abi import encode
from eth_utils import event_abi_to_log_topic
from hexbytes import HexBytes
import pytest
import requests
from web3 import Web3

from yearn_data import abis, catchup, envio
from yearn_data.history_sources import HistoricalSource, inventory_targets, report_scope, write_rows
from yearn_data.storage import connect, init_db

VAULT = '0x' + '41' * 20
ASSET = '0x' + '43' * 20
NEW_ASSET = '0x' + '44' * 20
HASH = '0x' + 'ab' * 32
TX = '0x' + 'cd' * 32


@pytest.fixture
def db(tmp_path):
    conn = connect('staging')
    init_db(conn)
    yield conn
    conn.close()


def seed(conn, address=VAULT, version='v2'):
    with conn:
        conn.execute("""INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,
            api_version,active,updated_at) VALUES(10,?,?,?,6,?,0,0)""",
            (version, address, ASSET, '0.4.6' if version == 'v2' else '3.0.4'))


def source(logs=(), block_hash=None):
    calls = Counter()

    def block(number):
        calls[number] += 1
        return dict(number=90 if number == 'finalized' else number,
                    hash=HexBytes(block_hash(number, calls[number]) if block_hash else HASH),
                    timestamp=1000)

    w3 = SimpleNamespace(codec=Web3().codec, eth=SimpleNamespace(chain_id=10,
        get_block=block, get_logs=lambda params: [log for log in logs
            if params['fromBlock'] <= log['blockNumber'] <= params['toBlock']]))
    return HistoricalSource('op', w3), calls


def report_log(version='v2'):
    abi = abis.V2_STRATEGY_REPORTED_EVENTS[-1] if version == 'v2' else abis.V3_STRATEGY_REPORTED_EVENT
    values = dict(strategy=ASSET, gain=12, loss=0, debtAdded=0,
                  totalGain=12, totalLoss=0, totalDebt=100, debtRatio=100,
                  current_debt=100, protocol_fees=1, total_fees=2, total_refunds=0)
    indexed = [field for field in abi['inputs'] if field['indexed']]
    plain = [field for field in abi['inputs'] if not field['indexed']]
    return dict(address=VAULT, blockNumber=25, blockHash=HexBytes(HASH), logIndex=0,
        transactionHash=HexBytes(TX), transactionIndex=0,
        topics=[event_abi_to_log_topic(abi)] + [encode([field['type']], [values[field['name']]]) for field in indexed],
        data=HexBytes(encode([field['type'] for field in plain], [values[field['name']] for field in plain])))


@pytest.mark.parametrize('existing_active', [None, 0, 1])
@pytest.mark.parametrize('later_actions,expected', [([], 1), (['removed'], 0), (['removed', 'added'], 1)])
def test_materialization_uses_latest_known_lifecycle(db, existing_active, later_actions, expected):
    target = next(t for t in inventory_targets('eth') if t.version == 'v3' and t.action == 'added')
    # A remove and re-add at the same block also exercise log-index ordering.
    actions = [('added', 20, 0)] + [(action, 80, index) for index, action in enumerate(later_actions)]
    rows = [dict(chain_id=1, version='v3', vault_address=VAULT, source_kind='role_manager',
        action=action, source_address=target.address, asset=None, tx_hash='0x' + f'{index+1:02x}' * 32,
        log_index=log_index, block_number=number, block_timestamp=1000,
        decoded_json=json.dumps(dict(vault=VAULT)))
        for index, (action, number, log_index) in enumerate(actions)]
    with db:
        write_rows(db, 'vault_inventory_events', rows)
        if existing_active is not None:
            db.execute("""INSERT INTO vaults(chain_id,version,address,active,updated_at)
                VALUES(1,'v3',?,?,0)""", (VAULT, existing_active))
    facts = catchup.inventory_vaults(db, 'eth', 30, False)
    assert len(facts) == 1 and facts[0]['active'] == 1
    catchup.materialize_inventory(db, facts)
    assert db.execute('SELECT active FROM vaults WHERE address=?', (VAULT,)).fetchone()[0] == expected


@pytest.mark.parametrize('version', ['v2', 'v3'])
@pytest.mark.parametrize('column,new_value', [('asset', NEW_ASSET), ('asset_decimals', 18)])
def test_metadata_correction_replays_and_commits_coverage(db, version, column, new_value):
    seed(db, version=version)
    client, _ = source([report_log(version)])
    catchup.catch_up(db, 'op', 10, 30, client=client)
    old_report = dict(db.execute('SELECT * FROM strategy_reports').fetchone())
    old_raw = dict(db.execute('SELECT * FROM events_raw').fetchone())
    old_policy = db.execute('SELECT policy FROM history_coverage').fetchone()[0]
    with db:
        db.execute(f'UPDATE vaults SET {column}=?', (new_value,))
    client, _ = source([report_log(version)])
    result = catchup.catch_up(db, 'op', 10, 30, client=client)
    assert result['completed_ranges'] == 1 and result['inserted_rows'] == 0
    assert dict(db.execute('SELECT * FROM strategy_reports').fetchone()) == {**old_report, column: new_value}
    assert dict(db.execute('SELECT * FROM events_raw').fetchone()) == old_raw
    policies = {row[0] for row in db.execute('SELECT policy FROM history_coverage')}
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    assert policies == {report_scope(vault).policy} and old_policy not in policies
    client, _ = source([report_log(version)])
    repeated = catchup.catch_up(db, 'op', 10, 30, client=client)
    assert repeated['completed_ranges'] == repeated['inserted_rows'] == 0
    # Returning to a previously used value must replay, not reuse its old policy.
    with db:
        db.execute(f'UPDATE vaults SET {column}=?', (old_report[column],))
    client, _ = source([report_log(version)])
    restored = catchup.catch_up(db, 'op', 10, 30, client=client)
    assert restored['completed_ranges'] == 1 and restored['inserted_rows'] == 0
    assert dict(db.execute('SELECT * FROM strategy_reports').fetchone()) == old_report
    assert dict(db.execute('SELECT * FROM events_raw').fetchone()) == old_raw
    assert {row[0] for row in db.execute('SELECT policy FROM history_coverage')} == {old_policy}


@pytest.mark.parametrize('table,column,value', [
    ('strategy_reports', 'gain_raw', '13'),
    ('strategy_reports', 'extra_json', '{"debtRatio":999}'),
    ('strategy_reports', 'block_number', 26),
    ('events_raw', 'decoded_json', '{"gain":999}'),
])
def test_metadata_correction_does_not_hide_raw_conflicts(db, table, column, value):
    seed(db)
    client, _ = source([report_log()])
    catchup.catch_up(db, 'op', 10, 30, client=client)
    with db:
        db.execute('UPDATE vaults SET asset_decimals=18')
        db.execute(f'UPDATE {table} SET {column}=?', (value,))
    before = {name: [dict(row) for row in db.execute(f'SELECT * FROM {name}')]
              for name in ['strategy_reports', 'events_raw', 'history_coverage']}
    client, _ = source([report_log()])
    with pytest.raises(ValueError, match='conflicting observation'):
        catchup.catch_up(db, 'op', 10, 30, client=client)
    for name, rows in before.items():
        assert [dict(row) for row in db.execute(f'SELECT * FROM {name}')] == rows


@pytest.mark.parametrize('selected_vault', [False, True])
def test_report_writer_rejects_unapproved_metadata_changes(db, selected_vault):
    seed(db)
    client, _ = source([report_log()])
    catchup.catch_up(db, 'op', 10, 30, client=client)
    before = dict(db.execute('SELECT * FROM strategy_reports').fetchone())
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    options = {'report_metadata': vault} if selected_vault else {}
    with pytest.raises(ValueError, match='conflict'):
        with db:
            write_rows(db, 'strategy_reports', [{**before, 'asset': NEW_ASSET}], **options)
    assert dict(db.execute('SELECT * FROM strategy_reports').fetchone()) == before


@pytest.mark.parametrize('vault_count', [1, 3])
def test_resume_verifies_shared_boundaries_once_per_run(db, vault_count):
    for i in range(vault_count):
        seed(db, '0x' + f'{65+i:02x}' * 20)
    client, _ = source()
    catchup.catch_up(db, 'op', 10, 30, chunk_size=10, client=client)
    for _ in range(2):
        client, calls = source()
        result = catchup.catch_up(db, 'op', 10, 30, chunk_size=7, client=client)
        assert result['completed_ranges'] == result['inserted_rows'] == 0
        # The end block also has its initial pin and final fresh verification.
        assert calls == {'finalized': 1, 19: 1, 29: 1, 30: 3}


def test_resume_rejects_conflicting_hash_after_another_vault_verified_it(db):
    seed(db)
    other = '0x' + '42' * 20
    seed(db, other)
    client, _ = source()
    catchup.catch_up(db, 'op', 10, 30, chunk_size=10, client=client)
    with db:
        db.execute("UPDATE history_coverage SET end_hash=? WHERE target=? AND to_block=19",
                   ('0x' + 'ef' * 32, other))
    client, calls = source()
    with pytest.raises(ValueError, match='hash'):
        catchup.catch_up(db, 'op', 10, 30, client=client)
    assert calls[19] == 1


def test_resume_still_checks_end_hash_fresh_at_completion(db):
    seed(db)
    client, _ = source()
    catchup.catch_up(db, 'op', 10, 30, chunk_size=10, client=client)
    client, calls = source(block_hash=lambda number, count:
        '0x' + 'ef' * 32 if number == 30 and count == 3 else HASH)
    with pytest.raises(ValueError, match='hash changed'):
        catchup.catch_up(db, 'op', 10, 30, client=client)
    assert calls[30] == 3


@pytest.mark.parametrize('status', [401, 503])
def test_envio_catchup_inherits_http_policy_without_advancing_forward_cursor(db, monkeypatch, status):
    seed(db, version='v3')
    with db:
        db.execute('UPDATE vaults SET chain_id=1')
    envio._set_envio_cursor(db, 1, 'StrategyReported', 7)
    cursors = [dict(row) for row in db.execute('SELECT * FROM index_state')]
    calls, sleeps = [], []
    node = dict(chainId=1, vaultAddress=VAULT, strategy=ASSET, blockNumber=25,
        blockHash=HASH, blockTimestamp=1000, transactionHash=TX, logIndex=0,
        gain='12', loss='0', current_debt='100', protocol_fees='1', total_fees='2', total_refunds='0')

    def post(url, **kwargs):
        payload = kwargs['json']
        response = requests.Response()
        response.status_code = 200
        response.url = url
        if 'chain_metadata' in payload['query']:
            data = {'chain_metadata': [{'latest_processed_block': 90}]}
        else:
            variables = payload['variables']
            assert VAULT in variables['addresses']
            calls.append((variables['lb'], variables['li']))
            if len(calls) == 1:
                response.status_code = status
            data = {'StrategyReported': [node] if calls[-1] < (25, 0) else []}
        response._content = json.dumps({'data': data}).encode()
        return response

    monkeypatch.setattr(envio, 'get_envio_graphql_url', lambda: 'https://example.invalid/graphql')
    monkeypatch.setattr(envio.requests, 'post', post)
    monkeypatch.setattr(envio.time, 'sleep', sleeps.append)
    rpc, _ = source()
    rpc.w3.eth.chain_id = 1
    client = HistoricalSource('eth', rpc.w3)
    if status == 401:
        with pytest.raises(requests.HTTPError):
            catchup.catch_up(db, 'eth', 10, 30, client=client)
        assert calls == [(10, -1)] and sleeps == []
        for table in ['strategy_reports', 'events_raw', 'history_coverage']:
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    else:
        result = catchup.catch_up(db, 'eth', 10, 30, client=client)
        assert result['vaults'] == result['inserted_rows'] == result['completed_ranges'] == 1
        assert calls == [(10, -1), (10, -1), (25, 0)] and sleeps == [1]
        assert db.execute('SELECT count(*) FROM events_raw').fetchone()[0] == 1
        assert db.execute('SELECT source FROM history_coverage').fetchone()[0] == 'envio'
    assert db.execute('SELECT active FROM vaults').fetchone()[0] == 0
    assert [dict(row) for row in db.execute('SELECT * FROM index_state')] == cursors


@pytest.mark.parametrize('membership', ['trusted', 'foreign', 'future'])
def test_bounded_discovery_recovers_only_selected_trusted_classifications(db, membership):
    target = next(t for t in inventory_targets('eth') if t.version == 'v3' and t.action == 'added')
    seed(db, version='v3')
    with db:
        db.execute("UPDATE vaults SET chain_id=1,management='non_yearn',active=1,protocol='cached protocol'")
        # Keep a retained Yearn vault so excluded discoveries still have a valid selection.
        db.execute("""INSERT INTO vaults(chain_id,version,address,asset,asset_decimals,
            api_version,management,active,updated_at) VALUES(1,'v3',?,?,6,'3.0.4','yearn',0,0)""",
            ('0x'+'45'*20, ASSET))
        rows = [dict(chain_id=1, version='v3', vault_address=VAULT, source_kind='role_manager',
            action=action, source_address=(target.address if membership != 'foreign' else '0x'+'99'*20),
            asset=ASSET, tx_hash='0x'+f'{index+1:02x}'*32, log_index=0,
            block_number=number, block_timestamp=1000, decoded_json=json.dumps(dict(vault=VAULT)))
            for index, (action, number) in enumerate([('added', 20 if membership != 'future' else 40), ('removed', 80)])]
        write_rows(db, 'vault_inventory_events', rows)
    before = dict(db.execute('SELECT * FROM vaults WHERE address=?', (VAULT,)).fetchone())
    client, _ = source([report_log('v3')])
    client.chain = 'eth'
    client.chain_id = client.w3.eth.chain_id = 1
    client.inventory = lambda *args: []
    original_reports = client.reports
    requested = []
    def reports(vault, *args):
        requested.append(vault['address'])
        return original_reports(vault, *args) if vault['address'] == VAULT else []
    client.reports = reports
    result = catchup.catch_up(db, 'eth', 10, 30, source='rpc', discover=True, client=client)
    recovered = dict(db.execute('SELECT * FROM vaults WHERE address=?', (VAULT,)).fetchone())
    if membership == 'trusted':
        assert result['vaults'] == 2 and VAULT in requested
        assert recovered['management'] == 'yearn' and recovered['active'] == 0
        assert recovered['deployment_block'] is None
        assert recovered['protocol'] == 'cached protocol'
        assert db.execute('SELECT block_number FROM strategy_reports WHERE vault_address=?', (VAULT,)).fetchone()[0] == 25
    else:
        assert result['vaults'] == 1 and VAULT not in requested
        assert recovered == before
