"""Cheap weekly checkpoints must still detect corrections and concurrent writes."""
from contextlib import closing
from types import SimpleNamespace

import pytest

from yearn_data import refresh, refresh_fingerprints as fingerprints
from yearn_data.refresh_state import digest, sql_digest
from yearn_data.storage import connect
from test_refresh_workflow import workflow as workflow


@pytest.fixture
def facts(postgres_database, tmp_path):
    with closing(connect('staging')) as conn:
        conn.executescript('''CREATE TABLE fingerprint_facts(id INTEGER PRIMARY KEY, amount TEXT);
            CREATE TABLE fingerprint_selection(id INTEGER PRIMARY KEY, included BOOLEAN);''')
        with conn:
            conn.execute("INSERT INTO fingerprint_facts VALUES(1,NULL),(2,'0')")
            conn.execute('INSERT INTO fingerprint_selection VALUES(1,true),(2,true)')
    options = SimpleNamespace(database='staging', publication_root=tmp_path, chain_ids=(1,), chains=(),max_targets=500,max_dates=30)
    worker = refresh.RefreshWorker(options, SimpleNamespace(id='fingerprint-test'))
    return worker


QUERY = [('fingerprint_facts', 'SELECT * FROM fingerprint_facts', ())]


def test_partial_checkpoints_never_scan_history_and_full_hash_reuses_only_unchanged_content(facts, monkeypatch):
    calls = []
    original = fingerprints.sql_digest
    def counted(*args):
        calls.append(args[1])
        return original(*args)
    monkeypatch.setattr(fingerprints, 'sql_digest', counted)
    initial = facts.signature(QUERY)
    assert facts.signature(QUERY) == initial
    assert len(calls) == 1
    with closing(connect('staging', readonly=True)) as conn, conn:
        assert initial == digest([sql_digest(conn, QUERY[0][1])])
    # Same IDs/count, correction outside any weekly date filter. NULL is not zero.
    previous = facts.signature(QUERY, partial=True)
    for value in ('0', '25', None):
        with closing(connect('staging')) as conn, conn:
            conn.execute('UPDATE fingerprint_facts SET amount=? WHERE id=1', (value,))
        current = facts.signature(QUERY, partial=True)
        assert current != previous
        previous = current
    assert len(calls) == 1  # All three committed checkpoints used only markers.
    assert facts.signature(QUERY) == initial  # Values restored, content ID preserved.
    assert len(calls) == 2
    with closing(connect('staging')) as conn:
        with pytest.raises(RuntimeError), conn:
            conn.execute("UPDATE fingerprint_facts SET amount='999' WHERE id=1")
            raise RuntimeError('roll back correction')
    assert facts.signature(QUERY) == initial
    assert len(calls) == 2
    with closing(connect('staging')) as conn, conn:
        conn.execute('DELETE FROM fingerprint_facts WHERE id=1')
    assert facts.signature(QUERY) != initial
    with closing(connect('staging')) as conn, conn:
        conn.execute('TRUNCATE fingerprint_facts')
    empty = facts.signature(QUERY)
    with closing(connect('staging', readonly=True)) as conn, conn:
        assert empty == digest([sql_digest(conn, QUERY[0][1])])


def test_join_membership_change_invalidates_hash_even_when_fact_rows_do_not_change(facts):
    query = [(('fingerprint_facts', 'fingerprint_selection'), '''SELECT f.* FROM fingerprint_facts f
        JOIN fingerprint_selection s ON s.id=f.id WHERE s.included''', ())]
    initial = facts.signature(query)
    with closing(connect('staging')) as conn, conn:
        conn.execute('UPDATE fingerprint_selection SET included=false WHERE id=1')
    assert facts.signature(query) != initial


def test_concurrent_commit_cannot_label_new_rows_with_an_old_revision(facts, monkeypatch):
    original = fingerprints.sql_digest
    changed = False
    def concurrent(conn, statement, params=()):
        nonlocal changed
        if not changed:
            changed = True
            with closing(connect('staging')) as writer, writer:
                writer.execute("UPDATE fingerprint_facts SET amount='100' WHERE id=1")
        return original(conn, statement, params)
    with closing(connect('staging', readonly=True)) as conn, conn:
        before = digest([original(conn, QUERY[0][1])])
    monkeypatch.setattr(fingerprints, 'sql_digest', concurrent)
    # Revision and rows use one snapshot, despite a commit between the two reads.
    assert facts.signature(QUERY) == before
    after = facts.signature(QUERY)
    assert after != before
    assert facts.signature(QUERY) == after


def test_disabled_tracking_fails_closed_and_fresh_worker_rehashes_after_table_replacement(facts):
    initial = facts.signature(QUERY)
    with closing(connect('staging')) as conn:
        conn.raw.execute("ALTER TABLE fingerprint_facts ADD COLUMN correction TEXT DEFAULT 'known'")
    assert facts.signature(QUERY) != initial
    with closing(connect('staging')) as conn:
        conn.raw.execute('ALTER TABLE fingerprint_facts DISABLE TRIGGER refresh_track_changes')
    with pytest.raises(ValueError, match='change tracking is unavailable'):
        facts.signature(QUERY)
    with closing(connect('staging')) as conn:
        conn.raw.execute('DROP TABLE fingerprint_facts')
        conn.raw.execute('CREATE TABLE fingerprint_facts(id INTEGER PRIMARY KEY,amount TEXT)')
        with conn:
            conn.execute("INSERT INTO fingerprint_facts VALUES(1,'changed')")
    with pytest.raises(ValueError, match='change tracking is unavailable'):
        facts.signature(QUERY)
    fresh = refresh.RefreshWorker(facts.options, SimpleNamespace(id='fresh'))
    assert fresh.signature(QUERY) != initial


def test_weekly_preparation_hashes_tvl_once_per_scope_not_after_every_day(workflow, monkeypatch):
    options, _ = workflow
    calls = []
    original = fingerprints.sql_digest
    def counted(conn, statement, params=()):
        if 'FROM tvl_snapshots ' in statement:
            calls.append(statement)
        return original(conn, statement, params)
    monkeypatch.setattr(fingerprints, 'sql_digest', counted)
    result = refresh.run_refresh(options)
    assert result['status'] == 'prepared' and result['result']['publishReady'], result
    # Two full scopes: acquisition chains, then all retained financial inputs.
    # Daily checkpoints, manifest reuse, history dependencies and final audit
    # must not repeat those scans. Financial content identities stay unchanged.
    assert len(calls) == len(set(calls)) == 2
    assert all('SELECT s.*' in statement for statement in calls)
