"""Consolidate published history without discarding any historical identity.

Publication IDs identify the current revision. Collection run IDs remain small
provenance/checkpoint records, not a retention boundary for historical dates.
Consolidation checks canonical keys and verifies retained values on tables it edits.
"""
from contextlib import closing
import json
import time

from . import chart_day_rows
from .storage import connect, database_reference, table_exists, table_columns
from .tvl_api import observation_selection


OBSERVATIONS = ('tvl_snapshots', 'tvl_positions')
DERIVED = {
    'tvl_chart_rows': ('dataset_id', 'tvlDatasetId'),
    'tvl_chart_totals': ('dataset_id', 'tvlDatasetId'),
    'tvl_chart_publications': ('dataset_id', 'tvlDatasetId'),
    'tvl_chart_inputs': ('dataset_id', 'tvlDatasetId'),
    'tvl_chart_days': ('dataset_id', 'tvlDatasetId'),
    'tvl_chart_preparations': ('dataset_id', 'tvlDatasetId'),
    'api_tvl_dates': ('dataset_id', 'tvlDatasetId'),
    'api_fee_rows': ('dataset_id', 'feesDatasetId'),
    'api_fee_datasets': ('dataset_id', 'feesDatasetId'),
    'analytics_publications': ('id', 'analyticsPublicationId'),
    'api_releases': ('id', 'releaseId'),
}
# These indexes were introduced after legacy chart publications. Required
# serving tables must still fail verification if missing.
OPTIONAL_DERIVED = {'tvl_chart_days', 'tvl_chart_inputs', 'tvl_chart_preparations'}
DERIVED_KEYS = {
    'tvl_chart_rows': 'dataset_id,timestamp,chain_id,vault',
    'tvl_chart_totals': 'dataset_id,timestamp,group_by,mode,series',
    'tvl_chart_publications': 'dataset_id', 'api_tvl_dates': 'dataset_id',
    'tvl_chart_days': 'dataset_id,timestamp', 'tvl_chart_inputs': 'dataset_id,timestamp', 'tvl_chart_preparations': 'dataset_id',
    'api_fee_rows': 'dataset_id,kind,ordinal', 'api_fee_datasets': 'dataset_id',
    'analytics_publications': 'id', 'api_releases': 'id',
}
LOCK_TABLES = (*OBSERVATIONS, 'tvl_runs', 'analysis_runs', 'analysis_outputs',
               *DERIVED, 'api_selection', 'api_manifests', 'analytics_selection')
SCHEMA = '''CREATE TABLE IF NOT EXISTS canonical_history_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, completed_at INTEGER NOT NULL,
    release_id TEXT NOT NULL, receipt_json TEXT NOT NULL)'''


def _sha(conn, expression):
    return f"encode(sha256(convert_to({expression},'UTF8')),'hex')"


def _count(conn, table, where='1=1', params=()):
    return conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}', params).fetchone()[0]


def _record_sha(conn, columns, alias=''):
    values = ','.join(alias+column for column in columns)
    body = f'CAST(json_build_array({values}) AS TEXT)'
    return _sha(conn, body)


def _available_derived(conn):
    return {table: fields for table, fields in DERIVED.items()
            if table not in OPTIONAL_DERIVED or table_exists(conn, table)}


def _stage_derived(conn, table, column, identity):
    keys = DERIVED_KEYS[table]
    conn.execute(f'''CREATE TEMP TABLE canonical_keep_{table} AS SELECT {keys},
        {_record_sha(conn, table_columns(conn, table))} AS value_sha256
        FROM {table} WHERE {column}=?''', (identity,))
    conn.execute(f'CREATE UNIQUE INDEX canonical_keep_{table}_key ON canonical_keep_{table} ({keys})')
    return {'before': _count(conn, table), 'retained_rows': _count(conn, 'canonical_keep_'+table)}


def _delete_only_tables(conn):
    # Outside writers are locked out. Transaction-local counters include trigger
    # writes. Restrict this proof to heap tables with no inheriting children.
    return {row[0] for row in conn.execute('''SELECT s.relname
        FROM pg_catalog.pg_stat_xact_user_tables s
        JOIN pg_catalog.pg_class c ON c.oid=s.relid
        JOIN pg_catalog.pg_am am ON am.oid=c.relam
        WHERE pg_catalog.pg_table_is_visible(c.oid) AND c.relkind='r'
        AND NOT c.relhassubclass AND am.amname='heap'
        AND s.n_tup_ins=0 AND s.n_tup_upd=0''')}


def _verify_selected_subset(conn, table, predicate, params, expected):
    # With deletes alone, survivors satisfying the original selection are a
    # subset of its staged rows. Equal size proves every key and value survived.
    total, selected = conn.execute(f'''SELECT COUNT(*), COUNT(*) FILTER (WHERE {predicate})
        FROM {table}''', params).fetchone()
    if total != expected or selected != expected:
        raise ValueError(f'{table}: selected row verification failed')
    return 0


def _verify_derived(conn, table, expected_rows, selection, delete_only):
    if table in delete_only:
        column, field = DERIVED[table]
        return _verify_selected_subset(conn, table, f'{column}=?', (selection[field],), expected_rows)
    keys = DERIVED_KEYS[table].split(',')
    joins = ' AND '.join(f's.{key}=k.{key}' for key in keys)
    mismatches = conn.execute(f'''SELECT COUNT(*) FROM canonical_keep_{table} k
        LEFT JOIN {table} s ON {joins} WHERE s.{keys[0]} IS NULL
        OR {_record_sha(conn, table_columns(conn, table), 's.')}<>k.value_sha256''').fetchone()[0]
    if mismatches or _count(conn, table) != expected_rows:
        raise ValueError(f'{table}: current publication key/value verification failed')
    return mismatches


def _selection(conn):
    if not table_exists(conn, 'api_selection'):
        raise ValueError('publish a coherent hosted release before consolidating history')
    rows = list(conn.execute('SELECT * FROM api_selection'))
    if len(rows) != 1 or rows[0]['name'] != 'powerglove':
        raise ValueError('consolidation requires one canonical powerglove selection')
    release = rows[0]['release_id']
    row = conn.execute('SELECT body_json FROM api_releases WHERE id=?', (release,)).fetchone()
    if row is None:
        raise ValueError('selected release is missing')
    selection = json.loads(row[0]) | {'releaseId': release}
    manifests = {}
    for kind, field in (('fees', 'feesDatasetId'), ('tvl', 'tvlDatasetId')):
        row = conn.execute('SELECT body_json FROM api_manifests WHERE kind=? AND id=?',
                           (kind, selection[field])).fetchone()
        if row is None:
            raise ValueError('selected manifest is missing')
        manifests[kind] = json.loads(row[0])
    marker = conn.execute('SELECT version,row_count FROM tvl_chart_publications WHERE dataset_id=?',
                          (selection['tvlDatasetId'],)).fetchone()
    if marker is None or not marker['version'].endswith('+totals'):
        raise ValueError('selected TVL history and totals must be prepared')
    if marker['row_count'] != _count(conn, 'tvl_chart_rows', 'dataset_id=?', (selection['tvlDatasetId'],)):
        raise ValueError('selected chart row count does not match preparation')
    if not _count(conn, 'api_fee_datasets', 'dataset_id=?', (selection['feesDatasetId'],)):
        raise ValueError('selected financial serving rows are missing')
    row = conn.execute('SELECT body_json FROM analytics_publications WHERE id=?',
                       (selection['analyticsPublicationId'],)).fetchone()
    if row is None or any(json.loads(row[0])['meta'][k] != selection[k]
                          for k in ('feesDatasetId', 'tvlDatasetId')):
        raise ValueError('selected analytics do not match the canonical release')
    return selection, manifests


def _dated_observation_keys(dimensions):
    return ','.join(['timestamp', *(key for key in dimensions.split(',') if key != 'timestamp')])


def _inventory_observations(conn, table, run_ids):
    dimensions, _ = observation_selection(table)
    marks = ','.join('?' for _ in run_ids)
    # Refuse to erase an independently prepared update. Failed-only keys are
    # retained, with their failed run provenance, without becoming published.
    if conn.execute(f'''SELECT 1 FROM {table} s JOIN tvl_runs r ON r.id=s.run_id
        WHERE r.status IN ('complete','incomplete') AND r.completed_at IS NOT NULL
        AND s.run_id NOT IN ({marks}) LIMIT 1''', run_ids).fetchone():
        raise ValueError('finished observations exist outside the selected TVL publication')
    # A single version of every key needs no ranking. On overlapping input
    # stop at the first duplicate; staging will count all versions per key.
    dated_keys = _dated_observation_keys(dimensions)
    overlap = conn.execute(f'''SELECT 1 FROM {table} GROUP BY {dated_keys}
        HAVING COUNT(*)>1 ORDER BY {dated_keys} LIMIT 1''').fetchone() is not None
    return _count(conn, table), overlap


def _observation_payload(table, column):
    # Position JSON is the largest part of the staged winner table. Hash the
    # original bytes so even a change in JSON spelling remains detectable.
    if table == 'tvl_positions':
        return f"sha256(convert_to({column},'UTF8'))"
    return f'{column} COLLATE "C"'


def _stage_observations(conn, table, run_ids):
    dimensions, quality = observation_selection(table)
    if table == 'tvl_positions':
        # The timestamp index supplies one date at a time to the window sort.
        # Build the temporary unique index in the same order as the winners.
        dimensions = _dated_observation_keys(dimensions)
    block = ',block_number' if table == 'tvl_snapshots' else ''
    expected = 'canonical_keep_' + table
    # A lower-preference copy of the preferred row's payload cannot beat it
    # on quality. Keep the source row so a nullable block stays with its run.
    # Singletons still evaluate quality to reject malformed evidence. OFFSET 0
    # lets each candidate share one JSONB conversion across its field reads.
    winners = f'''WITH source AS (SELECT *,run_id = ANY(?::bigint[]) AS published FROM {table}), versions AS (
            SELECT {dimensions},run_id,data_json AS raw_payload{block},published,
                first_value(run_id) OVER k AS preferred, first_value(data_json) OVER k AS preferred_payload,
                COUNT(*) OVER k AS copies
            FROM source WINDOW k AS (PARTITION BY {dimensions} ORDER BY published DESC,run_id DESC
                ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )
        SELECT DISTINCT ON ({dimensions}) {dimensions},run_id,
            {_observation_payload(table, 'raw_payload')} AS expected_payload{block},copies FROM versions
        CROSS JOIN LATERAL (SELECT raw_payload::jsonb AS data_json OFFSET 0) parsed
        WHERE (run_id=preferred OR raw_payload COLLATE "C"<>preferred_payload)
          AND (copies>1 OR ({quality}) IS NOT NULL)
        ORDER BY {dimensions},published DESC,CASE WHEN copies>1 THEN ({quality}) END DESC,run_id DESC'''
    params = (list(run_ids),)
    create_table = f'CREATE TEMP TABLE {expected} AS {winners}'
    index = f'CREATE UNIQUE INDEX {expected}_key ON {expected} ({dimensions})'
    if table == 'tvl_positions':
        # Winners arrive in index order. Enforce uniqueness as they arrive to
        # avoid a separate disk sort, and pack the read-only index pages.
        conn.execute(create_table + ' WITH NO DATA', params)
        conn.execute(index + ' WITH (fillfactor=100)')
        conn.execute(f'INSERT INTO {expected} {winners}', params)
    else:
        conn.execute(create_table, params)
        conn.execute(index)
    return conn.execute(f'SELECT COUNT(*),COALESCE(SUM(copies),0) FROM {expected}').fetchone()


def _retained_analyses(conn, financial):
    # Preserve one completed result for each supported analysis job, including
    # raw canonical-fee exports. Published fee/earnings runs take precedence.
    keep = {r['name']: r['id'] for r in conn.execute(
        "SELECT name,MAX(id) id FROM analysis_runs WHERE status='complete' GROUP BY name")}
    for field, name in (('earningsRunId', 'lifetime-yield'), ('feesRunId', 'fee-usd')):
        run_id = financial[field]
        row = conn.execute('SELECT name,status FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if row is None or row['name'] != name or row['status'] != 'complete':
            raise ValueError('selected financial analysis is not complete')
        keep[name] = run_id
    return tuple(sorted(keep.values()))


def _missing_analysis_events(conn, keep):
    # An identical payload in the same retained projection already witnesses
    # the event. Compare raw text: JSONB equality merges numeric 1 and 1.0,
    # which yield distinct text when extracted as an identity field.
    marks = ','.join('?' for _ in keep)
    identity = """analysis, output, body->>'chain_id', lower(body->>'tx_hash'),
        COALESCE(body->>'log_index', body->>'report_log_index')"""
    return conn.execute(f'''WITH retiring_only AS (
        SELECT r.name AS analysis, o.name AS output, o.row_json COLLATE "C" AS payload
        FROM analysis_outputs o JOIN analysis_runs r ON r.id=o.run_id
        GROUP BY r.name, o.name, o.row_json COLLATE "C"
        HAVING NOT bool_or(o.run_id IN ({marks}))
    ), retired_bodies AS MATERIALIZED (
        SELECT analysis, output, payload::jsonb AS body FROM retiring_only
    ), at_risk AS MATERIALIZED (
        SELECT {identity} FROM retired_bodies WHERE body->>'tx_hash' IS NOT NULL
    ), retained_bodies AS MATERIALIZED (
        SELECT r.name AS analysis, o.name AS output, o.row_json::jsonb AS body
        FROM analysis_outputs o JOIN analysis_runs r ON r.id=o.run_id
        WHERE o.run_id IN ({marks}) AND (r.name,o.name) IN (SELECT analysis,output FROM at_risk)
    )
    SELECT COUNT(*) FROM (
        SELECT * FROM at_risk
        EXCEPT SELECT {identity} FROM retained_bodies WHERE body->>'tx_hash' IS NOT NULL
    ) missing''', (*keep, *keep)).fetchone()[0]


def _stage_analyses(conn, keep):
    marks = ','.join('?' for _ in keep)
    # Parsing here also rejects malformed retained JSON when no event comparison
    # is needed. The digest still covers the original stored bytes.
    conn.execute(f'''CREATE TEMP TABLE canonical_keep_outputs AS
        SELECT rowid AS output_id,run_id,name,{_sha(conn, 'row_json')} AS value_sha256
        FROM analysis_outputs WHERE run_id IN ({marks}) AND row_json::jsonb IS NOT NULL''', keep)
    conn.execute('CREATE UNIQUE INDEX canonical_keep_outputs_key ON canonical_keep_outputs(output_id)')
    before = _count(conn, 'analysis_outputs')
    retained = _count(conn, 'canonical_keep_outputs')
    missing = _missing_analysis_events(conn, keep) if before != retained else 0
    if missing:
        raise ValueError(f'canonical analyses would lose {missing} historical event identities')
    return {'before': before, 'retained_rows': retained, 'run_ids': list(keep), 'missing_event_keys': missing}


def _verify_analysis_outputs(conn, expected_rows, keep, delete_only):
    if 'analysis_outputs' in delete_only:
        return _verify_selected_subset(conn, 'analysis_outputs', 'run_id = ANY(?)', (list(keep),), expected_rows)
    mismatches = conn.execute(f'''SELECT COUNT(*) FROM canonical_keep_outputs k
        LEFT JOIN analysis_outputs o ON o.rowid=k.output_id
        WHERE o.rowid IS NULL OR o.run_id<>k.run_id OR o.name<>k.name
        OR {_sha(conn, 'o.row_json')}<>k.value_sha256''').fetchone()[0]
    if mismatches or _count(conn, 'analysis_outputs') != expected_rows:
        raise ValueError('analysis output order/value verification failed')
    return mismatches


def _verify_observations(conn, table, expected_rows, runs=None):
    params = ()
    scope = ''
    if runs is None:
        actual = _count(conn, table)
    else:
        # Whole retained runs can only shrink. For partial runs, even correct
        # per-run counts could hide a different winner, so check the staged keys.
        survivors = dict(conn.execute(f'SELECT run_id, COUNT(*) FROM {table} GROUP BY run_id'))
        expected = {run: kept for run, before, kept in runs if kept}
        actual = sum(survivors.values())
        if survivors != expected or actual != expected_rows:
            raise ValueError(f'{table}: canonical key/value verification failed')
        mixed = [run for run, before, kept in runs if 0 < kept < before]
        if not mixed:
            return {'after': actual, 'missing_or_changed_keys': 0}
        scope = 'k.run_id = ANY(?) AND '
        params = (mixed,)
    dimensions, _ = observation_selection(table)
    joins = ' AND '.join(f's.{key}=k.{key}' for key in dimensions.split(','))
    extra = ' OR s.block_number IS DISTINCT FROM k.block_number' if table == 'tvl_snapshots' else ''
    mismatches = conn.execute(f'''SELECT COUNT(*) FROM canonical_keep_{table} k
        LEFT JOIN {table} s ON {joins} AND s.run_id=k.run_id
        WHERE {scope}(s.run_id IS NULL OR {_observation_payload(table, 's.data_json')}
            IS DISTINCT FROM k.expected_payload{extra})''', params).fetchone()[0]
    if mismatches or actual != expected_rows:
        raise ValueError(f'{table}: canonical key/value verification failed')
    return {'after': actual, 'missing_or_changed_keys': mismatches}


def _observation_run_counts(conn, table):
    # Staging selected a subset of the rows of each run. Only runs that
    # contribute some but not all of their rows need per-key comparison.
    return conn.execute(f'''SELECT src.run_id, src.n, COALESCE(kept.n,0) AS retained
        FROM (SELECT run_id, COUNT(*) AS n FROM {table} GROUP BY run_id) src
        LEFT JOIN (SELECT run_id, COUNT(*) AS n FROM canonical_keep_{table} GROUP BY run_id) kept
            ON kept.run_id=src.run_id''').fetchall()


def _delete_obsolete_observations(conn, table, runs):
    obsolete = [run for run, before, retained in runs if retained == 0]
    if obsolete:
        # Match the bigint column even when every ID fits in a smallint.
        # PostgreSQL can then choose a heap scan for a large obsolete set.
        conn.execute(f'DELETE FROM {table} WHERE run_id = ANY(?::bigint[])', (obsolete,))
    mixed = [run for run, before, retained in runs if 0 < retained < before]
    if mixed:
        dimensions, _ = observation_selection(table)
        joins = ' AND '.join(f'k.{key}=s.{key}' for key in dimensions.split(','))
        conn.execute(f'''DELETE FROM {table} s WHERE s.run_id = ANY(?) AND NOT EXISTS (
            SELECT 1 FROM canonical_keep_{table} k WHERE {joins} AND k.run_id=s.run_id)''', (mixed,))


def _delete_obsolete(conn, selection, keep):
    marks = ','.join('?' for _ in keep)
    conn.execute(f'DELETE FROM analysis_outputs WHERE run_id NOT IN ({marks})', keep)
    conn.execute(f"UPDATE analysis_runs SET status='superseded' WHERE status='complete' AND id NOT IN ({marks})", keep)
    # The same canonical analytics powers both the file-backed and hosted API.
    conn.execute("INSERT INTO analytics_selection VALUES ('powerglove',?) "
                 "ON CONFLICT(name) DO UPDATE SET publication_id=excluded.publication_id",
                 (selection['analyticsPublicationId'],))
    if conn.execute("SELECT 1 FROM analytics_selection WHERE name<>'powerglove' LIMIT 1").fetchone():
        raise ValueError('additional analytics selections require explicit consolidation')
    for table, (column, field) in _available_derived(conn).items():
        if table == 'tvl_chart_days':
            chart_day_rows.retire(conn, selection[field], keep_selected=True)
        elif table == 'tvl_chart_totals' and chart_day_rows.totals_enabled(conn):
            continue
        elif table != 'tvl_chart_rows' or not chart_day_rows.enabled(conn):
            conn.execute(f'DELETE FROM {table} WHERE {column}<>?', (selection[field],))
    conn.execute("DELETE FROM api_manifests WHERE NOT ((kind='fees' AND id=?) OR (kind='tvl' AND id=?))",
                 (selection['feesDatasetId'], selection['tvlDatasetId']))


class PublicationTimings:
    """Elapsed seconds between successive boundaries in one publication attempt."""

    def __init__(self):
        self.seconds = {}
        self._previous = time.monotonic()

    def finished(self, phase):
        now = time.monotonic()
        self.seconds[phase] = now - self._previous
        self._previous = now


def consolidate(database, *, apply=False, progress=None, timings=None):
    """Plan by default; apply atomically only after validating all preserved keys.

    Source events, receipts, traces, prices, coverage and run/checkpoint metadata
    are retained. The only removed data are superseded observations and derived
    outputs. No compaction or external calls occur inside this transaction.
    """
    timings = timings or PublicationTimings()
    notify = progress or (lambda message: None)
    with closing(connect(database, direct=True)) as conn:
        shared = apply and chart_day_rows.enabled(conn)
        conn.raw.execute('BEGIN ISOLATION LEVEL REPEATABLE READ')
        conn.raw.execute("SET LOCAL lock_timeout='5s'")
        if shared:
            chart_day_rows.lock_memberships(conn)
        if not conn.execute('SELECT pg_try_advisory_xact_lock(743219890)').fetchone()[0]:
            raise ValueError('another canonical consolidation is running')
        if apply:
            tables = sorted(t for t in set(LOCK_TABLES) if table_exists(conn, t))
            conn.raw.execute('LOCK TABLE '+','.join(tables)+' IN SHARE ROW EXCLUSIVE MODE')
        try:
            for table in ('tvl_runs', 'analysis_runs'):
                if conn.execute(f"SELECT 1 FROM {table} WHERE status='running' LIMIT 1").fetchone():
                    raise ValueError('finish or resolve running jobs before consolidation')
            selection, manifests = _selection(conn)
            run_ids = tuple(r['id'] for r in manifests['tvl']['runs'])
            if not run_ids:
                raise ValueError('selected TVL publication has no historical runs')
            timings.finished('cleanup.lock-and-validate')
            report = {'database': database_reference(database), 'applied': apply,
                      'selection': selection, 'tvlRunIds': list(run_ids), 'observations': {}, 'derived': {}}
            staged = []
            untouched_storage = {}
            track_writes = conn.execute('SHOW track_counts').fetchone()[0] == 'on'
            for table in OBSERVATIONS:
                notify('Inventorying canonical keys: '+table)
                before, overlap = _inventory_observations(conn, table, run_ids)
                timings.finished('cleanup.check-keys.' + table)
                canonical_keys = before
                if overlap or (apply and not track_writes):
                    staged.append(table)
                    canonical_keys, covered = _stage_observations(conn, table, run_ids)
                    # The unique key index allows at most one winner per key.
                    # Every source version must be accounted for by those keys.
                    if covered != before:
                        raise ValueError(f'{table}: staged keys do not cover all observations')
                    timings.finished('cleanup.stage.' + table)
                elif apply:
                    # TRUNCATE replaces the heap without counting individual deletes.
                    untouched_storage[table] = conn.execute(
                        'SELECT pg_relation_filenode(?::regclass)', (table,)).fetchone()[0]
                report['observations'][table] = {'before': before, 'canonical_keys': canonical_keys}
            keep = _retained_analyses(conn, manifests['fees'])
            notify('Verifying analysis event coverage')
            report['analysis_outputs'] = _stage_analyses(conn, keep)
            timings.finished('cleanup.stage.analysis_outputs')
            for table, (column, field) in _available_derived(conn).items():
                notify('Inventorying current publication: '+table)
                report['derived'][table] = _stage_derived(conn, table, column, selection[field])
                timings.finished('cleanup.stage.' + table)
            if not apply:
                conn.rollback()
                return report
            notify('Removing superseded records inside the verification transaction')
            # Capture the original membership before any delete can fire triggers.
            runs_by_table = {}
            for table in staged:
                runs_by_table[table] = _observation_run_counts(conn, table)
                timings.finished('cleanup.delete.classify.' + table)
            for table in staged:
                _delete_obsolete_observations(conn, table, runs_by_table[table])
                timings.finished('cleanup.delete.' + table)
            _delete_obsolete(conn, selection, keep)
            timings.finished('cleanup.delete')
            delete_only = _delete_only_tables(conn) if track_writes else set()
            # A join of two delete-only tables can only lose logical rows.
            if {'tvl_chart_days','tvl_chart_row_values'} <= delete_only:
                delete_only.add('tvl_chart_rows')
            if {'tvl_chart_days','tvl_chart_total_values','tvl_chart_publications'} <= delete_only:
                delete_only.add('tvl_chart_totals')
            for table in staged:
                expected = report['observations'][table]['canonical_keys']
                report['observations'][table].update(_verify_observations(
                    conn, table, expected, runs_by_table[table] if table in delete_only else None))
                timings.finished('cleanup.verify.' + table)
            outputs = report['analysis_outputs']
            outputs['missing_or_changed_rows'] = _verify_analysis_outputs(
                conn, outputs['retained_rows'], keep, delete_only)
            timings.finished('cleanup.verify.analysis_outputs')
            for table, counts in report['derived'].items():
                counts['missing_or_changed_keys'] = _verify_derived(
                    conn, table, counts['retained_rows'], selection, delete_only)
                timings.finished('cleanup.verify.' + table)
            if _selection(conn)[0] != selection:
                raise ValueError('canonical publication changed during consolidation')
            # Outside writers are locked out. Check this transaction, including
            # trigger effects, before claiming that unique rows were untouched.
            for table, counts in report['observations'].items():
                if table not in staged:
                    writes, storage = conn.execute('''SELECT n_tup_ins + n_tup_upd + n_tup_del, pg_relation_filenode(relid)
                        FROM pg_catalog.pg_stat_xact_user_tables WHERE relid = ?::regclass''',
                        (table,)).fetchone()
                    if writes or storage != untouched_storage[table]:
                        raise ValueError(f'{table}: unique observations changed during consolidation')
                    counts.update(after=counts['before'], missing_or_changed_keys=0)
            conn.execute(SCHEMA)
            conn.execute('INSERT INTO canonical_history_receipts(completed_at,release_id,receipt_json) VALUES (?,?,?)',
                         (int(time.time()), selection['releaseId'], json.dumps(report, sort_keys=True)))
            conn.commit()
            timings.finished('cleanup.final-check-and-commit')
            notify('Committed: every canonical historical key and value preserved')
            return report
        except BaseException:
            conn.rollback()
            raise
