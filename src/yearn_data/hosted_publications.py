"""Explicit publication writer and read-only database registry for hosted serving."""
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .storage import connect, database_reference

SCHEMA = '''
CREATE TABLE IF NOT EXISTS api_tvl_dates (dataset_id TEXT PRIMARY KEY, dates_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_manifests (
 kind TEXT NOT NULL, id TEXT NOT NULL, body_json TEXT NOT NULL,
 PRIMARY KEY(kind,id));
CREATE TABLE IF NOT EXISTS api_releases (
 id TEXT PRIMARY KEY, body_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS api_selection (
 name TEXT PRIMARY KEY, release_id TEXT NOT NULL REFERENCES api_releases(id));
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('invalid publication identifier')
    return value


def tvl_identity_context(manifest):
    return {key: value for key, value in manifest.items()
            if key not in {'datasetId', 'publishedAt', 'asOfTimestamp', 'diagnostics'}}


def save_release(conn, financial, tvl, analytics_id, *, tvl_dates=None):
    """Commit immutable manifests and their selection together; caller validates inputs."""
    selection = {'feesDatasetId': identifier(financial['datasetId']),
                 'tvlDatasetId': identifier(tvl['datasetId']),
                 'analyticsPublicationId': identifier(analytics_id)}
    release_id = hashlib.sha256(encoded(selection).encode()).hexdigest()
    conn.executescript(SCHEMA)
    with conn:
        for kind, manifest in (('fees', financial), ('tvl', tvl)):
            body = encoded(manifest)
            conn.execute('INSERT INTO api_manifests VALUES (?,?,?) ON CONFLICT(kind,id) DO NOTHING',
                         (kind, manifest['datasetId'], body))
            existing = conn.execute('SELECT body_json FROM api_manifests WHERE kind=? AND id=?',
                                    (kind, manifest['datasetId'])).fetchone()
            # Preserve the first published metadata for stable pinned responses.
            # Regenerating a local TVL manifest may change metadata outside its ID.
            differs = (encoded(tvl_identity_context(json.loads(existing[0]))) != encoded(tvl_identity_context(manifest))
                       if kind == 'tvl' else existing[0] != body)
            if kind == 'tvl' and json.loads(existing[0]).get('asOfTimestamp') != manifest.get('asOfTimestamp'):
                raise ValueError('immutable publication reporting close changed; publish a new dataset')
            if differs:
                raise ValueError('immutable publication manifest changed')
        if tvl_dates is not None:
            if not tvl_dates:
                raise ValueError('prepared TVL dates are unavailable')
            dates_body = encoded(tvl_dates)
            conn.execute('INSERT INTO api_tvl_dates VALUES (?,?) ON CONFLICT DO NOTHING',
                         (tvl['datasetId'], dates_body))
            previous_dates = conn.execute('SELECT dates_json FROM api_tvl_dates WHERE dataset_id=?',
                                          (tvl['datasetId'],)).fetchone()[0]
            if json.loads(previous_dates) != list(tvl_dates):
                raise ValueError('immutable publication prepared dates changed; publish a new dataset')
        conn.execute('INSERT INTO api_releases VALUES (?,?) ON CONFLICT(id) DO NOTHING',
                     (release_id, encoded(selection)))
        conn.execute("INSERT INTO api_selection VALUES ('powerglove',?) ON CONFLICT(name) DO UPDATE SET release_id=excluded.release_id",
                     (release_id,))
    return release_id


class PublicationCleanupError(RuntimeError):
    """A coherent release was published, but subsequent consolidation failed."""

    def __init__(self, release_id):
        self.release_id = release_id
        super().__init__(f'Release {release_id} was published before consolidation failed; inspect the current selection before recovery')


def publish(database, financial_directory, tvl_directory, analytics_id=None, *, prepare_analytics=False):
    """Import validated completed selections. Never invoked by an HTTP request."""
    from .pairing import PairingStore
    from .tvl_api import TvlDataset
    from .tvl_history_cache import ready, totals_ready
    from .analytics import AnalyticsStore
    reference = database_reference(database)
    financial = PairingStore(financial_directory).get()
    root = Path(tvl_directory)
    selected = identifier(json.loads((root/'current.json').read_text())['datasetId'])
    manifest = json.loads((root/'datasets'/f'{selected}.json').read_text())
    if manifest['datasetId'] != selected:
        raise ValueError('TVL manifest identity mismatch')
    context = tvl_identity_context(manifest)
    if hashlib.sha256(encoded(context).encode()).hexdigest() != selected:
        raise ValueError('TVL manifest content does not match datasetId')
    tvl = TvlDataset(manifest)
    if str(financial.database) != reference or tvl.database != reference:
        raise ValueError('publication database must match selected database')
    if not ready(tvl) or not totals_ready(tvl):
        raise ValueError('TVL history must be prepared before publication')
    if prepare_analytics:
        if analytics_id is not None:
            raise ValueError('choose prepared analytics or an explicit analytics publication')
        from .analytics import prepare as prepare_owned
        analytics_id = prepare_owned(database, financial, tvl, select=False)
    analytics = AnalyticsStore(database).get(analytics_id)['meta']
    if analytics['feesDatasetId'] != financial.id or analytics['tvlDatasetId'] != tvl.id:
        raise ValueError('analytics must reference the selected financial and TVL datasets')
    # Persist only the fields needed to reproduce financial responses. Selection
    # timestamps are mutable local metadata, not part of an immutable dataset.
    fees = {'datasetId': financial.id, 'database': reference,
            'earningsRunId': financial.context['earningsRunId'],
            'feesRunId': financial.context['feesRunId'], 'vaultNames': financial.vault_names}
    with closing(connect(database)) as conn:
        from .hosted_fees import prepare
        prepare(conn, financial)
        dates = [row[0] for row in conn.execute(
            'SELECT DISTINCT timestamp FROM tvl_chart_rows WHERE dataset_id=? ORDER BY timestamp', (tvl.id,))]
        release = save_release(conn, fees, manifest, analytics['publicationId'], tvl_dates=dates)
    # Retire superseded values only after a complete coherent publication exists.
    # A verification failure rolls back consolidation and keeps both revisions
    # for inspection; the newly published, already validated release stays usable.
    from .canonical_history import consolidate
    try:
        consolidate(database, apply=True)
    except Exception as exc:
        raise PublicationCleanupError(release) from exc
    return release


class Registry:
    def __init__(self, database):
        self.database = database

    def selection(self):
        with closing(connect(self.database, readonly=True)) as conn:
            row = conn.execute("SELECT r.id,r.body_json FROM api_releases r JOIN api_selection s ON s.release_id=r.id WHERE s.name='powerglove'").fetchone()
        if row is None:
            raise ValueError('no hosted publication selected')
        return {'releaseId': row[0], **json.loads(row[1])}

    @lru_cache(maxsize=8)
    def manifest(self, kind, identity):
        identifier(identity)
        with closing(connect(self.database, readonly=True)) as conn:
            row = conn.execute('SELECT body_json FROM api_manifests WHERE kind=? AND id=?', (kind, identity)).fetchone()
        if row is None:
            raise ValueError('unknown datasetId')
        manifest = json.loads(row[0])
        if manifest['database'] != database_reference(self.database):
            raise ValueError('publication database mismatch')
        return manifest

    @lru_cache(maxsize=2)
    def fees(self, identity):
        from .hosted_fees import PublishedFees
        self.manifest('fees', identity)
        return PublishedFees(self.database, identity)

    @lru_cache(maxsize=2)
    def tvl(self, identity):
        return PublishedTvlDataset(self.manifest('tvl', identity))


from .tvl_api import TvlDataset


class PublishedTvlDataset(TvlDataset):
    @lru_cache(maxsize=1)
    def dates(self):
        # A published hosted dataset always has prepared history. Reading its
        # date index must not scan the original ingestion observations.
        with closing(connect(self.database, readonly=True)) as conn:
            row = conn.execute('SELECT dates_json FROM api_tvl_dates WHERE dataset_id=?', (self.id,)).fetchone()
            dates = tuple(json.loads(row[0])) if row else ()
        if not dates:
            raise ValueError('published TVL history is unavailable')
        return dates


def select_release(database, release_id):
    """Re-select a retained coherent release without recomputation."""
    identifier(release_id)
    registry = Registry(database)
    with closing(connect(database, readonly=True)) as conn:
        row = conn.execute('SELECT body_json FROM api_releases WHERE id=?', (release_id,)).fetchone()
    if row is None:
        raise ValueError('unknown releaseId')
    selected = json.loads(row[0])
    registry.fees(selected['feesDatasetId'])
    from .tvl_history_cache import ready, totals_ready
    tvl = registry.tvl(selected['tvlDatasetId'])
    if not ready(tvl) or not totals_ready(tvl):
        raise ValueError('retained TVL publication is not prepared')
    from .analytics import AnalyticsStore
    meta = AnalyticsStore(database).get(selected['analyticsPublicationId'])['meta']
    if any(meta[key] != selected[key] for key in ('feesDatasetId','tvlDatasetId')):
        raise ValueError('retained analytics do not match release')
    with closing(connect(database)) as conn, conn:
        conn.execute("UPDATE api_selection SET release_id=? WHERE name='powerglove'", (release_id,))
    return release_id
