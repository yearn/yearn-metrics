"""A bounded Envio repair must recover gaps without discarding observations."""
import pytest

from yearn_data import catchup, cli
from yearn_data.coverage import Scope, commit_range
from yearn_data.history_sources import inventory_targets, report_scope, write_rows
from test_catchup import db as db, Source, VAULT, HASH


def cover(db, scope, lo, hi, source='rpc'):
    commit_range(db, scope, lo, hi, source=source, end_hash=HASH, run_id=None, write=lambda _: 0)


def test_revalidation_repairs_only_selected_window_and_reuses_envio(db):
    client = Source()
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    scope = report_scope(vault)
    # A wider untrusted scan overlaps a trusted subrange. Neither unrelated
    # windows nor another chain may be invalidated by the repair.
    cover(db, scope, 0, 50)
    cover(db, scope, 10, 19, 'envio')
    cover(db, scope, 20, 30)
    other = Scope(1, VAULT, 'reports', scope.policy)
    cover(db, other, 0, 50)
    before = [tuple(r) for r in db.execute('SELECT * FROM history_coverage WHERE from_block=0')]
    # Retained reports survive replay; zero inserts still certify the new read.
    with db:
        write_rows(db, 'strategy_reports', client.reports(vault, 20, 30, 'envio'))
    client.calls.clear()
    result = catchup.catch_up(db, 'op', 10, 30, source='envio', chunk_size=100,
                             revalidate_rpc_coverage=True, client=client)
    assert result['completed_ranges'] == 1 and result['inserted_rows'] == 0
    assert client.calls == [(VAULT, 20, 30)]
    assert db.execute('SELECT source FROM history_coverage WHERE chain_id=10 AND from_block=20').fetchone()[0] == 'envio'
    assert [tuple(r) for r in db.execute('SELECT * FROM history_coverage WHERE from_block=0')] == before
    assert db.execute('SELECT gain_raw FROM strategy_reports').fetchone()[0] == '12'
    client.calls.clear()
    assert catchup.catch_up(db, 'op', 10, 30, source='envio', chunk_size=7,
                           revalidate_rpc_coverage=True, client=client)['completed_ranges'] == 0
    assert client.calls == []


def test_failed_revalidation_preserves_rpc_evidence_and_can_resume(db):
    client = Source()
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    cover(db, report_scope(vault), 10, 30)
    before = tuple(db.execute('SELECT * FROM history_coverage').fetchone())
    reports = client.reports
    def fetch(vault, lo, hi, source):
        if lo == 20:
            raise RuntimeError('provider unavailable')
        return reports(vault, lo, hi, source)
    client.reports = fetch
    with pytest.raises(RuntimeError, match='provider unavailable'):
        catchup.catch_up(db, 'op', 10, 30, chunk_size=10, revalidate_rpc_coverage=True, client=client)
    assert tuple(db.execute("SELECT * FROM history_coverage WHERE source='rpc'").fetchone()) == before
    assert [tuple(r) for r in db.execute("SELECT from_block,to_block FROM history_coverage WHERE source='envio'")] == [(10, 19)]
    client.reports = reports
    client.calls.clear()
    result = catchup.catch_up(db, 'op', 10, 30, chunk_size=10, revalidate_rpc_coverage=True, client=client)
    assert result['inserted_rows'] == 1 and result['completed_ranges'] == 2
    assert client.calls == [(VAULT, 20, 29), (VAULT, 30, 30)]


def test_inventory_revalidation_recovers_registration_and_report(db, monkeypatch):
    from test_catchup import NEW, ASSET
    from web3 import Web3
    monkeypatch.setattr(catchup, 'vault_metadata_from_kong', lambda *args: {
        Web3.to_checksum_address(NEW): dict(asset=ASSET, asset_decimals=6, api_version='0.4.6')})
    for target in inventory_targets('op'):
        cover(db, target.scope, 10, 30)
    vault = dict(db.execute('SELECT * FROM vaults').fetchone())
    cover(db, report_scope(vault), 10, 30)
    result = catchup.catch_up(db, 'op', 10, 30, discover=True, revalidate_rpc_coverage=True, client=Source())
    assert result['vaults'] == 2 and result['inserted_rows'] == 3
    assert db.execute('SELECT count(*) FROM history_coverage WHERE source=\'rpc\'').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM vault_inventory_events').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM strategy_reports').fetchone()[0] == 2


@pytest.mark.parametrize('chain,source', [('eth', 'envio'), ('op', 'rpc'), ('ftm', 'auto')])
def test_revalidation_rejects_other_chains_or_rpc_before_io(chain, source):
    with pytest.raises(ValueError, match='requires Optimism with Envio'):
        catchup.catch_up(None, chain, 10, 30, source=source, revalidate_rpc_coverage=True)


def test_cli_exposes_bounded_revalidation_flag():
    args = cli.build_parser().parse_args(['catch-up', '--chain', 'op', '--from-block', '10',
                                         '--to-block', '30', '--source', 'envio', '--revalidate-rpc-coverage'])
    assert args.revalidate_rpc_coverage and args.from_block == 10 and args.to_block == 30
