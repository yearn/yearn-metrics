"""Transaction hash spelling cannot create another observation identity."""

import os
import uuid

import pytest

import test_catchup_regressions as helpers
from yearn_data import catchup, envio
from yearn_data.coverage import insert_checked
from yearn_data.history_sources import HistoricalSource, inventory_targets, write_rows
from yearn_data.indexing import insert_raw_event
from yearn_data.storage import connect, init_db

MIXED = '0x' + 'cD' * 32



@pytest.fixture
def db(pg):
    conn,_=pg
    init_db(conn)
    return conn


@pytest.mark.parametrize('version', ['v2', 'v3'])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('gain', [12, 13])
def test_envio_then_rpc_reuses_hash_identity_and_rejects_changed_amounts(db, monkeypatch, version, legacy, gain):
    helpers.seed(db, version=version)
    with db:
        db.execute('UPDATE vaults SET chain_id=1')
    entity = 'V2StrategyReported' if version == 'v2' else 'StrategyReported'
    node = dict(gain=str(gain), loss='0', strategy=helpers.ASSET, vaultAddress=helpers.VAULT,
        chainId=1, blockNumber=25, blockTimestamp=1000, transactionHash=MIXED, logIndex=0,
        current_debt='100', protocol_fees='1', total_fees='2', total_refunds='0',
        debtAdded='0', debtPaid='0', totalGain='12', totalLoss='0', totalDebt='100', debtRatio='100')
    monkeypatch.setattr(envio, 'latest_processed_block', lambda _: 100)
    monkeypatch.setattr(envio, '_pull_window', lambda requested, *a, **kw: [node] if requested == entity else [])
    envio.import_reports_from_envio(db, ['eth'], versions=[version], from_block=10, to_block=30, include_inactive=True)
    assert db.execute('SELECT tx_hash FROM strategy_reports').fetchone()[0] == helpers.TX
    if legacy:
        with db:
            db.execute('UPDATE strategy_reports SET tx_hash=?', (MIXED,))
        envio.import_reports_from_envio(db, ['eth'], versions=[version], from_block=10, to_block=30, include_inactive=True)
        assert db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0] == 1
    client, _ = helpers.source([helpers.report_log(version)])
    client.chain, client.chain_id, client.w3.eth.chain_id = 'eth', 1, 1
    if gain == 13:
        with pytest.raises(ValueError, match='conflicting observation'):
            catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
        assert db.execute('SELECT count(*) FROM history_coverage').fetchone()[0] == 0
    else:
        result = catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
        assert result['inserted_rows'] == 0 and result['completed_ranges'] == 1
    assert db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0] == 1
    assert db.execute('SELECT gain_raw FROM strategy_reports').fetchone()[0] == str(gain)
    assert db.execute('SELECT tx_hash FROM strategy_reports').fetchone()[0] == (MIXED if legacy else helpers.TX)


def test_checked_and_legacy_raw_writers_share_case_insensitive_identity(db):
    values = dict(chain_id=1, contract_address=helpers.VAULT, event_name='test', tx_hash=MIXED,
                  log_index=0, block_number=25, block_timestamp=1000, decoded_json='{"gain":12}')
    identity = ('chain_id', 'tx_hash', 'log_index')
    with db:
        assert insert_checked(db, 'events_raw', values, identity) == 1
        assert insert_checked(db, 'events_raw', {**values, 'tx_hash': helpers.TX}, identity) == 0
    assert db.execute('SELECT tx_hash FROM events_raw').fetchone()[0] == helpers.TX
    with db:
        db.execute('UPDATE events_raw SET tx_hash=?', (MIXED,))
        insert_raw_event(db, 1, helpers.VAULT, 'test', dict(transactionHash=helpers.TX, logIndex=0, blockNumber=25),
                         1000, dict(gain=12))
    assert db.execute('SELECT count(*) FROM events_raw').fetchone()[0] == 1


def test_existing_duplicate_hash_spellings_require_explicit_reconciliation(db):
    values = dict(chain_id=1, contract_address=helpers.VAULT, event_name='test', tx_hash=MIXED,
                  log_index=0, block_number=25, block_timestamp=1000, decoded_json='{"gain":12}')
    with db:
        for tx_hash in [MIXED, helpers.TX]:
            row = {**values, 'tx_hash': tx_hash}
            db.execute('INSERT INTO events_raw (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    with pytest.raises(ValueError, match='duplicate.*identity.*events_raw'):
        with db:
            insert_checked(db, 'events_raw', values, ('chain_id', 'tx_hash', 'log_index'))
    assert db.execute('SELECT count(*) FROM events_raw').fetchone()[0] == 2


def test_repairing_metadata_preserves_legacy_referenced_hash(db):
    helpers.seed(db, version='v3')
    client, _ = helpers.source([helpers.report_log('v3')])
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    with db:
        write_rows(db, 'strategy_reports', client.reports(vault, 10, 30, 'rpc'))
        db.execute('UPDATE strategy_reports SET tx_hash=?,asset_decimals=NULL', (MIXED,))
        db.execute('''CREATE TABLE hash_reference (
            chain_id INTEGER,tx_hash TEXT,log_index INTEGER,
            FOREIGN KEY(chain_id,tx_hash,log_index) REFERENCES strategy_reports(chain_id,tx_hash,log_index))''')
        db.execute('INSERT INTO hash_reference VALUES(10,?,0)', (MIXED,))
    result = catchup.catch_up(db, 'op', 10, 30, client=client)
    assert result['inserted_rows'] == 0 and result['completed_ranges'] == 1
    row = db.execute('SELECT tx_hash,asset_decimals FROM strategy_reports').fetchone()
    assert tuple(row) == (MIXED, 6)
    assert db.execute('''SELECT count(*) FROM hash_reference child JOIN strategy_reports parent
        ON parent.chain_id=child.chain_id AND parent.tx_hash=child.tx_hash AND parent.log_index=child.log_index''').fetchone()[0] == 1


def test_envio_and_rpc_inventory_reuse_legacy_hash_spelling(db, monkeypatch):
    from hexbytes import HexBytes
    import test_history_sources as rpc_helpers
    target = next(inventory_targets('op'))
    spec = envio.V2_VAULT_INVENTORY_ENTITIES[0]
    node = dict(token=helpers.ASSET, deployment_id='1', vault=helpers.VAULT, api_version='0.4.6',
        registryAddress=target.address, chainId=10, blockNumber=20, blockTimestamp=1000,
        transactionHash=MIXED, logIndex=0)
    monkeypatch.setattr(envio, '_pull_window', lambda *args, **kwargs: [node])
    assert envio._import_vault_inventory_entities(db, 'op', 30, 'v2', [spec], None) == 1
    assert db.execute('SELECT tx_hash FROM vault_inventory_events').fetchone()[0] == helpers.TX
    with db:
        db.execute('UPDATE vault_inventory_events SET tx_hash=?', (MIXED,))
    envio._set_envio_cursor(db, 10, spec[0], 0)
    assert envio._import_vault_inventory_entities(db, 'op', 30, 'v2', [spec], None) == 0
    log = rpc_helpers.registry_log(target.abi, dict(token=helpers.ASSET, vault_id=1,
        vault=helpers.VAULT, api_version='0.4.6'))
    log.update(address=target.address, transactionHash=HexBytes(helpers.TX))
    rows = HistoricalSource('op', rpc_helpers.client(logs=[log])).inventory(target, 10, 30, 'rpc')
    with db:
        assert write_rows(db, 'vault_inventory_events', rows) == 0
    assert db.execute('SELECT count(*) FROM vault_inventory_events').fetchone()[0] == 1
    assert db.execute('SELECT tx_hash FROM vault_inventory_events').fetchone()[0] == MIXED
