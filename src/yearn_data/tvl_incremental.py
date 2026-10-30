"""Input identities and reuse planning for immutable accounted daily history."""
from contextlib import closing
import hashlib
import json
import time

from .storage import connect

DAY = 86400
INPUT_VERSION = 'daily-inputs-v2'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tvl_chart_inputs (
 dataset_id TEXT NOT NULL, timestamp INTEGER NOT NULL, input_digest TEXT NOT NULL,
 PRIMARY KEY(dataset_id,timestamp));
CREATE TABLE IF NOT EXISTS tvl_chart_preparations (
 dataset_id TEXT PRIMARY KEY, context_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
 prepared_at BIGINT NOT NULL);
ALTER TABLE tvl_chart_inputs ADD COLUMN IF NOT EXISTS tracking_revision TEXT;
ALTER TABLE tvl_chart_inputs ADD COLUMN IF NOT EXISTS known_chains TEXT;
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def context_identity(dataset):
    from .tvl_api import accounting_revision
    # observationRevision is the aggregate evidence identity used by refresh
    # manifests, not accounting policy. input_identity still hashes each date.
    excluded = {'runs', 'datasetId', 'publishedAt', 'asOfTimestamp', 'reportingCutoff', 'observationRevision', 'diagnostics', 'priceRejections', 'catalog'}
    context = {k:v for k,v in dataset.manifest.items() if k not in excluded}
    return digest([INPUT_VERSION, accounting_revision(), context])


def catalog_state(dataset):
    """Preserve unknown metadata fields, but ignore acquisition time and casing."""
    return {(row['chain_id'], row['address'].lower()): digest(
        {k:(v.lower() if k == 'address' else v) for k,v in row.items() if k != 'updated_at'})
        for row in dataset.manifest['catalog']}


def catalog_dates(conn, dataset, previous, current):
    """Find potential dependencies in observations, including both edge ends.

    Raw retained rows are a conservative superset of selected winners. Rebuild
    the whole affected network frame; a child change can alter parent deductions.
    """
    old = {tuple(row[:2]): row[2] for row in previous['catalog']}
    changed = sorted(key for key in old.keys() | current.keys() if old.get(key) != current.get(key))
    runs = sorted(set(dataset.run_ids) | set(previous.get('runIds', [])))
    marks = ','.join('?' for _ in runs)
    affected = set()
    for offset in range(0, len(changed), 200):
        keys = changed[offset:offset+200]
        values = ','.join('(CAST(? AS BIGINT),CAST(? AS TEXT))' for _ in keys)
        args = tuple(value for key in keys for value in key)
        prefix = f'WITH changed(chain_id,address) AS (VALUES {values}) '
        affected.update(row[0] for row in conn.execute(prefix + f'''
            SELECT DISTINCT s.timestamp FROM tvl_snapshots s JOIN changed c
            ON s.chain_id=c.chain_id AND lower(s.vault)=c.address
            WHERE s.run_id IN ({marks}) AND s.timestamp<=?
            UNION SELECT DISTINCT p.timestamp FROM tvl_positions p JOIN changed c
            ON (p.chain_id=c.chain_id AND lower(p.parent)=c.address)
            OR (COALESCE((p.data_json::jsonb->>'child_chain_id')::bigint,p.chain_id)=c.chain_id
                AND lower(p.child)=c.address)
            WHERE p.run_id IN ({marks}) AND p.timestamp<=?''',
            (*args, *runs, dataset.as_of(), *runs, dataset.as_of())))
    chains = {key[0] for key in old}.symmetric_difference(key[0] for key in current)
    if chains and dataset.manifest.get('currentBridgePolicy') == 'retired-registry':
        chain_marks = ','.join('?' for _ in chains)
        affected.update(row[0] for row in conn.execute(f'''
            SELECT DISTINCT timestamp FROM tvl_snapshots WHERE run_id IN ({marks}) AND timestamp=?
            AND (data_json::jsonb->>'bridge_target_chain')::bigint IN ({chain_marks})''',
            (*runs, dataset.as_of(), *sorted(chains))))
    return affected


def input_identity(dataset, timestamp, points, positions, catalog=None):
    def evidence(rows):
        return [{k:v for k,v in row.items() if k != 'source_run_id'} for row in rows]
    catalog = catalog_state(dataset) if catalog is None else catalog
    keys = {(p['chain_id'], p['vault'].lower()) for p in points}
    for edge in positions:
        keys.add((edge['chain_id'], edge['parent'].lower()))
        keys.add((edge.get('child_chain_id') or edge['chain_id'], edge['child'].lower()))
    bridge_chains = ({p['bridge_target_chain'] for p in points if p.get('bridge_target_chain') is not None}
                     if timestamp == dataset.as_of() and dataset.manifest.get('currentBridgePolicy') == 'retired-registry' else set())
    present = {key[0] for key in catalog}
    return digest([timestamp, evidence(points), evidence(positions),
                   [r for r in dataset.manifest.get('priceRejections', []) if r['timestamp'] == timestamp],
                   dataset.manifest.get('currentBridgePolicy') == 'retired-registry' and timestamp == dataset.as_of(),
                   [[*key, catalog.get(key)] for key in sorted(keys)],
                   [(chain, chain in present) for chain in sorted(bridge_chains)]])


def preparation(conn, identity):
    row = conn.execute('SELECT receipt_json FROM tvl_chart_preparations WHERE dataset_id=?', (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def build(dataset, progress=None, *, full_rebuild=False, backfill=None):
    from .tvl_history_cache import FIELDS, SCHEMA as CHART_SCHEMA, VERSION
    from .tvl_api import accounting_revision
    from .tvl_day_tracking import install, state, TABLES
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
        install(conn)
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
            old = {r[0]:r for r in conn.execute('''SELECT timestamp,input_digest,tracking_revision,known_chains
                FROM tvl_chart_inputs WHERE dataset_id=?''', (previous,))} if previous else {}
            prior_receipt = preparation(conn, previous) if previous else None
            tracking, revisions = state(conn)
            prior_tracking = (prior_receipt or {}).get('dayTracking', {})
            tracked = bool(tracking and prior_tracking.get('signature') == tracking)
            changed_runs = set(dataset.run_ids).symmetric_difference(prior_tracking.get('runIds', []))
            changed_dates = set()
            if tracked and changed_runs:
                placeholders = ','.join('?' for _ in changed_runs)
                for table in TABLES:
                    changed_dates.update(r[0] for r in conn.execute(
                        f'SELECT DISTINCT timestamp FROM {table} WHERE run_id IN ({placeholders})', tuple(changed_runs)))
            old_rejections = prior_tracking.get('priceRejections', [])
            new_rejections = dataset.manifest.get('priceRejections', [])
            if old_rejections != new_rejections:
                changed_dates.update(r['timestamp'] for r in old_rejections + new_rejections)
            if (dataset.manifest.get('currentBridgePolicy') == 'retired-registry'
                    and prior_tracking.get('asOf') != dataset.as_of()):
                changed_dates.update((prior_tracking.get('asOf'), dataset.as_of()))
            reason = ('unsupported-cache-format' if unsupported else 'explicit-full-rebuild' if full_rebuild
                      else None if previous else 'no-compatible-input-index')
            if not full_rebuild and not unsupported and not previous and conn.execute('''SELECT 1 FROM tvl_chart_preparations p
                JOIN tvl_chart_publications c ON c.dataset_id=p.dataset_id WHERE c.version=? LIMIT 1''',
                (VERSION+'+totals',)).fetchone():
                reason = 'accounting-or-catalog-context-changed'
            marks = ','.join('?' for _ in dataset.run_ids)
            dates = tuple(r[0] for r in conn.execute(f'''SELECT DISTINCT timestamp FROM tvl_snapshots
                WHERE run_id IN ({marks}) AND timestamp<=? ORDER BY timestamp''', (*dataset.run_ids, dataset.as_of())))
            catalog = catalog_state(dataset)
            catalog_changed_dates = (catalog_dates(conn, dataset, prior_tracking, catalog)
                                     if previous and 'catalog' in prior_tracking else set(dates) if previous else set())
            changed_dates.update(catalog_changed_dates)
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
            compare = []
            skipped = []
            for day in dates:
                prior = old.get(day)
                revision = str(revisions.get(day, 0))
                forced = backfill is not None and backfill[0] <= day <= backfill[1]
                if (tracked and prior and prior[2] == revision and prior[3] is not None
                        and day not in changed_dates and not forced):
                    reuse.append(day)
                    skipped.append(day)
                    known_by_day[day] = set(json.loads(prior[3]))
                    identities.append((dataset.id, day, prior[1], revision, prior[3]))
                else:
                    compare.append(day)
            notify(f'History change tracking: {len(skipped)} dates unchanged; {len(compare)} dates need input reads')
            for offset in range(0, len(compare), 31):
                batch = []
                for timestamp, points, positions in dataset.input_frames(conn, compare[offset:offset+31]):
                    rows_read += len(points) + len(positions)
                    known_by_day[timestamp] = {p['chain_id'] for p in points if p.get('asset_units') is not None}
                    identity = input_identity(dataset, timestamp, points, positions, catalog)
                    identities.append((dataset.id, timestamp, identity, str(revisions.get(timestamp, 0)),
                                       json.dumps(sorted(known_by_day[timestamp]))))
                    forced = backfill is not None and backfill[0] <= timestamp <= backfill[1]
                    if not forced and old.get(timestamp) is not None and old[timestamp][1] == identity:
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
                notify(f'History read {min(offset+31,len(compare))}/{len(compare)} changed dates: {len(reuse)} reused; {len(recompute)} recomputed')
            reuse.sort()
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
                           inputDatesRead=compare, inputDatesSkipped=skipped,
                           catalogAffectedDates=sorted(catalog_changed_dates & set(dates)),
                           dayTracking=dict(signature=tracking, runIds=list(dataset.run_ids),
                                            catalog=[[*key, value] for key,value in sorted(catalog.items())],
                                            asOf=dataset.as_of(), priceRejections=new_rejections),
                           recomputedRowsWritten=written, copiedRows=copied,
                           rows=written+copied, copySeconds=copy_seconds, seconds=time.monotonic()-started)
            conn.executemany('''INSERT INTO tvl_chart_inputs
                (dataset_id,timestamp,input_digest,tracking_revision,known_chains) VALUES (?,?,?,?,?)''', identities)
            conn.execute('INSERT INTO tvl_chart_preparations VALUES (?,?,?,?)',
                         (dataset.id, context, json.dumps(receipt, sort_keys=True), time.time_ns()//1000))
            conn.execute('INSERT INTO tvl_chart_publications VALUES (?,?,?,?)',
                         (dataset.id, VERSION, written+copied, int(time.time())))
            notify(f'History staged: {len(reuse)} dates reused, {len(recompute)} recomputed, {len(pending)} pending chain/date gaps; commit and totals pending')
        conn.raw.execute('ANALYZE tvl_chart_rows')
    dataset._prepared = True
    return receipt
