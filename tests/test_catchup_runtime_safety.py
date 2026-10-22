"""Regression cases for pinned ranges, absent contracts, and hash identities."""

import pytest
from web3 import Web3
from web3.providers.base import BaseProvider

import test_catchup_regressions as helpers
from yearn_data import catchup
from yearn_data.history_sources import HistoricalSource
from yearn_data.storage import connect, init_db


@pytest.fixture
def db(tmp_path):
    conn = connect('staging')
    init_db(conn)
    yield conn
    conn.close()


class MetadataProvider(BaseProvider):
    """An offline RPC that cannot supply metadata for one stored vault."""
    def __init__(self, code='0x', failure=None):
        super().__init__()
        self.code, self.failure, self.calls = code, failure, []

    def make_request(self, method, params):
        self.calls.append((method, params))
        if method == 'eth_chainId':
            result = '0x1'
        elif method == 'eth_getBlockByNumber':
            number = 90 if params[0] == 'finalized' else int(params[0], 16)
            result = dict(number=hex(number), hash=helpers.HASH, timestamp=hex(1000), transactions=[])
        elif method == 'eth_getLogs':
            result = []
        elif method == 'eth_getCode':
            if self.failure:
                raise self.failure
            result = self.code
        elif method == 'eth_call':
            result = '0x'
        else:
            raise AssertionError(f'unexpected RPC method {method}')
        return dict(jsonrpc='2.0', id=1, result=result)


def seed_metadata_case(db, monkeypatch, provider):
    middle = '0x' + '42' * 20
    after = '0x' + '44' * 20
    for address in [helpers.VAULT, middle, after]:
        helpers.seed(db, address, version='v3')
    with db:
        db.execute('UPDATE vaults SET chain_id=1')
        db.execute('UPDATE vaults SET asset_decimals=NULL,deployment_block=80 WHERE address=?', (middle,))
    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', lambda *args: {})
    return HistoricalSource('eth', Web3(provider)), middle, after


def test_absent_contract_does_not_block_other_vaults_or_certify_coverage(db, monkeypatch):
    provider = MetadataProvider()
    client, middle, after = seed_metadata_case(db, monkeypatch, provider)
    result = catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
    assert result['vaults'] == 2
    skipped = result['skipped_vaults']
    assert len(skipped) == 1 and skipped[0]['address'] == middle and skipped[0]['block'] == 30
    assert 'code' in skipped[0]['reason']
    assert {r[0] for r in db.execute('SELECT DISTINCT target FROM history_coverage')} == {helpers.VAULT, after}
    assert db.execute('SELECT asset_decimals FROM vaults WHERE address=?', (middle,)).fetchone()[0] is None
    code_calls = [params for method, params in provider.calls if method == 'eth_getCode']
    assert len(code_calls) == 1 and int(code_calls[0][1], 16) == 30
    assert not any(method == 'eth_call' for method, _ in provider.calls)


@pytest.mark.parametrize('kong_failure', [False, True])
def test_batched_metadata_keeps_per_vault_fallback(db, monkeypatch, kong_failure):
    provider = MetadataProvider()
    client, middle, after = seed_metadata_case(db, monkeypatch, provider)
    with db:
        db.execute('UPDATE vaults SET asset_decimals=NULL WHERE address=?', (after,))
    calls = []

    def metadata(chain_id, addresses):
        calls.append((chain_id, addresses))
        if kong_failure:
            raise RuntimeError('Kong unavailable')
        return {after: dict(asset=helpers.ASSET, asset_decimals=6, api_version='3.0.4')}

    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', metadata)
    result = catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
    assert calls == [(1, [middle, after])]
    skipped = [middle, after] if kong_failure else [middle]
    assert [vault['address'] for vault in result['skipped_vaults']] == skipped
    assert {r[0] for r in db.execute('SELECT DISTINCT target FROM history_coverage')} == (
        {helpers.VAULT} if kong_failure else {helpers.VAULT, after})
    code_calls = [params for method, params in provider.calls if method == 'eth_getCode']
    assert [params[0] for params in code_calls] == skipped
    assert all(int(params[1], 16) == 30 for params in code_calls)


def test_batched_metadata_conflict_stops_before_scanning_vault(db, monkeypatch):
    provider = MetadataProvider()
    client, middle, after = seed_metadata_case(db, monkeypatch, provider)
    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', lambda *args: {
        middle: dict(asset=after, asset_decimals=6, api_version='3.0.4')})
    with pytest.raises(ValueError, match=f'{middle}.*30') as error:
        catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
    assert 'metadata conflicts with inventory asset' in str(error.value.__cause__)
    assert {r[0] for r in db.execute('SELECT DISTINCT target FROM history_coverage')} == {helpers.VAULT}
    assert db.execute('SELECT asset_decimals FROM vaults WHERE address=?', (middle,)).fetchone()[0] is None


@pytest.mark.parametrize('provider', [MetadataProvider(failure=RuntimeError('archive unavailable')),
                                     MetadataProvider(code='0x6000')])
def test_unavailable_archive_or_unreadable_contract_still_fails_with_context(db, monkeypatch, provider):
    client, middle, after = seed_metadata_case(db, monkeypatch, provider)
    with pytest.raises(ValueError, match=f'{middle}.*30'):
        catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
    assert {r[0] for r in db.execute('SELECT DISTINCT target FROM history_coverage')} == {helpers.VAULT}


def test_unsupported_release_is_not_hidden_by_absent_contract(db, monkeypatch):
    provider = MetadataProvider()
    client, middle, after = seed_metadata_case(db, monkeypatch, provider)
    with db:
        db.execute("UPDATE vaults SET api_version='4.0.0' WHERE address=?", (middle,))
    with pytest.raises(ValueError, match=f'unsupported vault release.*{middle}'):
        catchup.catch_up(db, 'eth', 10, 30, source='rpc', client=client)
    assert not any(method == 'eth_getCode' for method, _ in provider.calls)


def test_reorg_during_fetch_leaves_no_orphaned_rows_or_successful_coverage(db):
    helpers.seed(db)
    state = dict(reorganized=False)
    new_hash = '0x' + 'ef' * 32
    def block_hash(number, count):
        return new_hash if state['reorganized'] and isinstance(number, int) and number >= 20 else helpers.HASH
    client, _ = helpers.source([helpers.report_log()], block_hash=block_hash)
    client.w3.eth.block_number = 100
    reports = client.reports
    def fetch(vault, lo, hi, source):
        rows = reports(vault, lo, hi, source)
        if lo <= 25 <= hi:
            state['reorganized'] = True
        return rows
    client.reports = fetch
    with pytest.raises(ValueError, match='hash changed'):
        catchup.catch_up(db, 'op', 10, 30, confirmations=10, chunk_size=10, client=client)
    assert [tuple(r) for r in db.execute('SELECT from_block,to_block FROM history_coverage')] == [(10, 19)]
    assert db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM events_raw').fetchone()[0] == 0
    fresh, _ = helpers.source([], block_hash=block_hash)
    fresh.w3.eth.block_number = 100
    result = catchup.catch_up(db, 'op', 10, 30, confirmations=10, chunk_size=10, client=fresh)
    assert result['completed_ranges'] == 2 and result['inserted_rows'] == 0
    assert db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0] == 0
