"""Input identities and reuse planning for immutable accounted daily history."""
from contextlib import closing
import hashlib
import json
import time

from .storage import connect

DAY = 86400
INPUT_VERSION = 'daily-inputs-v1'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tvl_chart_inputs (
 dataset_id TEXT NOT NULL, timestamp INTEGER NOT NULL, input_digest TEXT NOT NULL,
 PRIMARY KEY(dataset_id,timestamp));
CREATE TABLE IF NOT EXISTS tvl_chart_preparations (
 dataset_id TEXT PRIMARY KEY, context_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
 prepared_at BIGINT NOT NULL);
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def context_identity(dataset):
    from .tvl_api import accounting_revision
    excluded = {'runs', 'datasetId', 'publishedAt', 'asOfTimestamp', 'reportingCutoff', 'diagnostics', 'priceRejections'}
    context = {k:v for k,v in dataset.manifest.items() if k not in excluded}
    # Acquisition timestamps do not affect accounting. Every other catalog field,
    # including unknown future fields, conservatively invalidates global reuse.
    context['catalog'] = [{k:v for k,v in row.items() if k != 'updated_at'}
                          for row in dataset.manifest['catalog']]
    return digest([INPUT_VERSION, accounting_revision(), context])


def input_identity(dataset, timestamp, points, positions):
    def evidence(rows):
        return [{k:v for k,v in row.items() if k != 'source_run_id'} for row in rows]
    return digest([timestamp, evidence(points), evidence(positions),
                   [r for r in dataset.manifest.get('priceRejections', []) if r['timestamp'] == timestamp],
                   dataset.manifest.get('currentBridgePolicy') == 'retired-registry' and timestamp == dataset.as_of()])


def preparation(conn, identity):
    row = conn.execute('SELECT receipt_json FROM tvl_chart_preparations WHERE dataset_id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def build(dataset, progress=None, *, full_rebuild=False, backfill=None):
    from .tvl_history_cache import FIELDS, SCHEMA as CHART_SCHEMA, VERSION
    from .tvl_api import accounting_revision
    notify = progress or (lambda message: None)
    captured_revision = dataset.manifest.get('accountingRevision')
    if captured_revision and captured_revision != accounting_revision():
        raise ValueError('captured accounting revision differs; publish a new dataset before preparation')
    if backfill is not None and (len(backfill) != 2 or backfill[0] > backfill[1]):
        raise ValueError('backfill must be an ordered inclusive timestamp range')
    started = time.monotonic()
    context = context_identity(dataset)
    with closing(connect(dataset.database, direct=True)) as conn:
        conn.executescript(CHART_SCHEMA + SCHEMA)
        with conn:
            # Hashing, winner selection, accounting and copies use one snapshot.
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            if not conn.execute('SELECT pg_try_advisory_xact_lock(743219889)').fetchone()[0]:
                raise ValueError('TVL chart preparation is already running')
            existing = conn.execute('SELECT version FROM tvl_chart_publications WHERE dataset_id=?', (dataset.id,)).fetchone()
            unsupported = bool(existing and existing[0] not in (VERSION, VERSION+'+totals'))
            if existing and not unsupported:
                raise ValueError('TVL dataset was already prepared; retry using its immutable rows')
            if unsupported:
                # Preserve the supported recovery contract: an unreadable cache
                # format can be rebuilt from the same captured financial revision.
                # Compatible immutable rows are never replaced.
                for table in ('tvl_chart_rows', 'tvl_chart_totals', 'tvl_chart_inputs',
                              'tvl_chart_preparations', 'tvl_chart_publications'):
                    conn.execute(f'DELETE FROM {table} WHERE dataset_id=?', (dataset.id,))
            previous = None if full_rebuild or unsupported else conn.execute('''SELECT p.dataset_id FROM tvl_chart_preparations p
                JOIN tvl_chart_publications c ON c.dataset_id=p.dataset_id
                WHERE p.context_digest=? AND c.version=? AND p.dataset_id<>?
                ORDER BY p.prepared_at DESC,p.dataset_id LIMIT 1''', (context, VERSION+'+totals', dataset.id)).fetchone()
            previous = previous[0] if previous else None
            old = dict(conn.execute('SELECT timestamp,input_digest FROM tvl_chart_inputs WHERE dataset_id=?',
                                    (previous,)).fetchall()) if previous else {}
            reason = ('unsupported-cache-format' if unsupported else 'explicit-full-rebuild' if full_rebuild
                      else None if previous else 'no-compatible-input-index')
            if not full_rebuild and not unsupported and not previous and conn.execute('''SELECT 1 FROM tvl_chart_preparations p
                JOIN tvl_chart_publications c ON c.dataset_id=p.dataset_id WHERE c.version=? LIMIT 1''',
                (VERSION+'+totals',)).fetchone():
                reason = 'accounting-or-catalog-context-changed'
            marks = ','.join('?' for _ in dataset.run_ids)
            dates = tuple(r[0] for r in conn.execute(f'''SELECT DISTINCT timestamp FROM tvl_snapshots
                WHERE run_id IN ({marks}) AND timestamp<=? ORDER BY timestamp''', (*dataset.run_ids, dataset.as_of())))
            recompute, reuse, identities = [], [], []
            rows_read = written = 0
            known_by_day = {}
            # A chain is repairable only from its own first retained observation.
            # Adding a chain today must not invent gaps before its inventory existed.
            first_by_chain = dict(conn.execute(
                f'SELECT chain_id,MIN(timestamp) FROM tvl_snapshots WHERE run_id IN ({marks}) '
                'AND timestamp<=? GROUP BY chain_id', (*dataset.run_ids, dataset.as_of())))
            forced_dates = [day for day in dates if backfill is not None and backfill[0] <= day <= backfill[1]]
            if backfill is not None and not forced_dates:
                raise ValueError('backfill range matches no captured dates')
            notify(f'History plan: {len(dates)} dates; full rebuild reason: {reason or "compare captured daily inputs"}')
            for offset in range(0, len(dates), 31):
                batch = []
                for timestamp, points, positions in dataset.input_frames(conn, dates[offset:offset+31]):
                    rows_read += len(points) + len(positions)
                    known_by_day[timestamp] = {p['chain_id'] for p in points if p.get('asset_units') is not None}
                    identity = input_identity(dataset, timestamp, points, positions)
                    identities.append((dataset.id, timestamp, identity))
                    forced = backfill is not None and backfill[0] <= timestamp <= backfill[1]
                    if not forced and old.get(timestamp) == identity:
                        reuse.append(timestamp)
                        continue
                    recompute.append(timestamp)
                    accounted, _, _ = dataset.account_frame(timestamp, points, positions)
                    for row in accounted:
                        values = [row.get(field) for field in FIELDS]
                        values[3] = row.get('category', row['version'])
                        values[5:] = [None if v is None else str(v) for v in values[5:]]
                        batch.append((dataset.id, timestamp, *values))
                with conn.raw.cursor() as cursor, cursor.copy('COPY tvl_chart_rows FROM STDIN') as copy:
                    for record in batch:
                        copy.write_row(record)
                written += len(batch)
                notify(f'History compared {min(offset+31,len(dates))}/{len(dates)} dates: {len(reuse)} reused; {len(recompute)} recomputed')
            copied = 0
            copy_started = time.monotonic()
            # Bound SQL parameters independently of the full historical date count.
            for offset in range(0, len(reuse), 400):
                days = reuse[offset:offset+400]
                placeholders = ','.join('?' for _ in days)
                copied += conn.execute(f'''INSERT INTO tvl_chart_rows
                    SELECT ?,timestamp,{','.join(FIELDS)} FROM tvl_chart_rows
                    WHERE dataset_id=? AND timestamp IN ({placeholders})''', (dataset.id, previous, *days)).rowcount
            copy_seconds = time.monotonic()-copy_started
            closes = range((dates[0]//DAY)*DAY+DAY-1, dataset.as_of()+1, DAY) if dates else ()
            pending = []
            for day in closes:
                missing = sorted(chain for chain, first in first_by_chain.items()
                                 if first <= day and chain not in known_by_day.get(day, set()))
                if missing:
                    pending.append({'timestamp': day, 'chainIds': missing})
            receipt = dict(datasetId=dataset.id, status='rows-prepared', inputVersion=INPUT_VERSION,
                           previousDatasetId=previous, fullRebuildReason=reason,
                           dates=len(dates), reusedDates=reuse, recomputedDates=recompute,
                           requestedBackfill=(dict(fromTimestamp=backfill[0], toTimestamp=backfill[1],
                                                   matchedDates=forced_dates) if backfill is not None else None),
                           pendingChainDates=pending, selectedInputRowsRead=rows_read,
                           recomputedRowsWritten=written, copiedRows=copied,
                           rows=written+copied, copySeconds=copy_seconds, seconds=time.monotonic()-started)
            conn.executemany('INSERT INTO tvl_chart_inputs VALUES (?,?,?)', identities)
            conn.execute('INSERT INTO tvl_chart_preparations VALUES (?,?,?,?)',
                         (dataset.id, context, json.dumps(receipt, sort_keys=True), time.time_ns()//1000))
            conn.execute('INSERT INTO tvl_chart_publications VALUES (?,?,?,?)',
                         (dataset.id, VERSION, written+copied, int(time.time())))
            notify(f'History staged: {len(reuse)} dates reused, {len(recompute)} recomputed, {len(pending)} pending chain/date gaps; commit and totals pending')
        conn.raw.execute('ANALYZE tvl_chart_rows')
    dataset._prepared = True
    return receipt
