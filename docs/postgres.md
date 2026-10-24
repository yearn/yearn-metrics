# Postgres storage

Postgres is the only supported backend for indexing, accounting, publication,
serving and tests. The existing TVL and fee/earnings history is reused. This
retirement changes no stored quantities or unavailable-versus-zero semantics.

## Select a database

`neon` is the default when neither `--db` nor `YEARN_DATA_DB` is supplied.
Set `NEON_DB_URL` server-side, or select `--db staging` with `STAGING_DB_URL` for
local work. A missing URL fails explicitly. File paths and in-memory SQLite
selectors are rejected before a file is opened or created; there is no fallback.
Direct Postgres URLs remain supported for connections, but publications require
a logical name so credentials cannot appear in manifests or status files.

The pooled Neon URL works for ordinary jobs and APIs. Explicit direct operations
use its direct endpoint. Local Postgres URLs need `?sslmode=disable`; hosted Neon
retains TLS. `YEARN_DATA_DB_SCHEMA` selects an already-created schema, defaulting
to `public`. Use the same schema and logical database when reading a publication.

```bash
yearn-data --env /path/to/.env --db neon init-db
yearn-data --env /path/to/.env --db neon tvl discover
yearn-data --env /path/to/.env --db neon analyze lifetime-yield
```

`migrate-postgres`, `merge-databases` and `tvl import-catalog` are removed. The
completed import is preserved in Git history. Existing files and verified archives
are not deleted, rewritten or silently converted. Recovery uses retained receipts
and a historical checkout in isolation; old file-based publication manifests do
not work with this Postgres-only runtime. Republish from the existing Postgres data.

## Tests

All database regressions run against Postgres. Each test creates random schemas
and drops only those schemas after closing its connections. There is no SQLite
fallback and a missing test URL fails explicitly.

```bash
docker compose -p yearn-metrics-tests -f ops/test-postgres.compose.yml up -d --wait
export YEARN_TEST_POSTGRES_URL='postgresql://yearn_test:yearn_test@127.0.0.1:55436/yearn_test?sslmode=disable'
python -m pytest -q
# Optional: include the seven frozen local Anvil replays when Anvil is installed.
RUN_LOCAL_EVM_REPLAY=1 python -m pytest -q
docker compose -p yearn-metrics-tests -f ops/test-postgres.compose.yml down
```

The test container uses temporary storage and a dedicated localhost port. These
credentials are test-only examples. Never use a production database for tests.
CI runs the same suite against a disposable Postgres service. Pure SQL splitting,
row access and parameter helpers have no SQLite module dependency.

## Prepare chart history after publishing TVL

Run this once for each newly published TVL dataset, before sending chart traffic
through the API:

```bash
yearn-data --env /path/to/.env --db neon tvl prepare-history \
  --publication data/tvl-neon
```

This runs the existing dated accounting in bounded batches and stores slim vault
rows plus daily chain/version totals in `tvl_chart_rows`, `tvl_chart_totals` and
`tvl_chart_publications`. Nested holdings, repairs and unavailable values use the
same calculations as the source history. Preparation commits atomically; an
interruption can be retried. Repeating the command skips completed preparation.
The cache is keyed by immutable dataset ID and accounting version. Bump the cache
version when changing the accounting used to produce these rows.

For an inventory-only quote-rejection update, use the previous prepared TVL
publication as the source instead of recomputing every date:

```bash
yearn-data --env /path/to/.env --db neon tvl prepare-history \
  --publication data/tvl-neon --previous-dataset-id <previous-dataset-id>
```

The command publishes the current inventory with the existing TVL policy, then
copies unchanged prepared dates and recalculates newly rejected dates. Use the
same `--current-bridge-policy` as the previous publication. Source history and
totals must both be complete; every other accounting input and the effective
accounting close must match. Quote
rejections cannot be removed or conflict at the same chain/asset/date. The old
publication remains intact. Evidence-only metadata updates copy all dates without
recalculation. Copied and recomputed dates are bounded by the publication close;
unpublished partial closes are excluded from the repair receipt.
On Postgres, full preparation and repair share the single-builder lock. A held
lock reports that preparation is already running. If another builder finishes
before the repair writes, its complete output is reused; an interrupted total
build can be retried. An older cache version requires full preparation.
A successful Postgres repair refreshes chart-table planner statistics before
committing, matching full preparation after its bulk load.
Without `--previous-dataset-id`, preparation retains
its full-history behavior.

The API reads prepared values without reconstructing raw snapshots and positions
for each request. It still serves unprepared datasets through the original path,
so **a new publication needs this preparation step to retain fast chart loading**.
This does not schedule ingestion or change which dataset is selected.

Chart clients can request `format=chart` to omit duplicate diagnostic points and
price references. On constant-price vault history, `top=10` returns the ten vaults
with the largest latest known values plus an `All other vaults` series, and one
price-neutral total across every vault. `meta.topSeries` identifies the prepared
series so clients do not reduce them again. Default requests keep the full
response. Responses use gzip when the client accepts it.

## Local operation after cutover

Set `YEARN_DATA_DB=neon` alongside `NEON_DB_URL` in the ignored local `.env`.
Commands then default to Neon; an explicit `--db` always wins. Run commands from
the Postgres checkout rather than an older branch without this configuration.
No automatic ingestion schedule is installed.

`scripts/update_postgres.py` runs a bounded report acquisition, fee calculation,
Yearn-only pricing, paired analyses, one full-catalog TVL close and chart
preparation. Select block bounds and a closed UTC cutoff explicitly. A report
window covers the selected chain; it does not claim all-chain fee freshness.
For chains with product Tokenized Strategies, choose `--to-block` at a finalized
block whose timestamp is strictly before `--cutoff`. A finalized head at or after
the cutoff is incompatible with this updater; choose the last finalized block
before the cutoff instead. The updater checks this relationship by RPC before
catch-up, fee indexing or any database write. RPC failures also stop preflight.
The publication root contains `fees/` and `tvl/` directories. Preparation uses a
separate pointer before publishing the serving TVL selection. The updater then
publishes a coherent hosted release and performs
[verified canonical-history consolidation](canonical-history.md). Daily historical
coverage is retained; obsolete publication editions are retired.

```bash
python scripts/update_postgres.py --db neon --env /path/to/.env \
  --chain eth --from-block START --to-block END --cutoff UTC_MIDNIGHT \
  --publication-root data/neon-operations \
  --deferred-manifest /path/to/validated-deferred-events.json
```

Coordinate writers: run one update at a time. Pricing and fee-calculation batches
are limited to 500 targets; inspect their results and repeat acquisition/pricing
for larger catch-ups before publishing. Missing values retain existing semantics.

Serve selected publications with the read-only API described in
[Hosted Powerglove API](hosted-api.md). Comparison requests read prepared
database-backed analytics, whose reference observations are acquired directly
by `analytics_collect.collect()`; no separate legacy SQLite reference database
is required.

The old Neon preview and compatibility socket are retired, and their unit
templates have been removed from `ops/`. Do not reinstall those services.
See [Legacy service retirement](legacy-service-retirement.md) for retained
recovery evidence. The remaining `ops/staging-postgres.compose.yml` manages
the local staging database described below.

## Local Postgres staging

Staging uses a separate Postgres 18 container (matching Neon), database, role and Docker volume.
It binds only `127.0.0.1:55435` and restarts automatically. Its credentials are in
ignored `.env` / `data/local-postgres.env` files. Set `STAGING_DB_URL` to its local
connection URL with `?sslmode=disable`; hosted Neon continues to require TLS.
Select staging explicitly with `--db staging`; its publications store `staging`
rather than a credential-bearing URL. Keep staging publications separate from
Neon publications.

Seed an empty staging database from a Postgres dump and restore, with explicit
operator approval and verification; the retired SQLite importer is unavailable. The staging copy is a point-in-time development copy, not a live
replica or Neon recovery backup. Integration tests can use `STAGING_DB_URL` as
`YEARN_TEST_POSTGRES_URL`; they create isolated temporary schemas.

Existing SQLite archives and their verified restore metadata remain historical
recovery evidence. Code retirement does not authorize deleting those archives.
Neon recovery settings and a tested recovery path remain a separate requirement.

The existing staging container can be started with
`docker start yearn-data-staging-postgres`. `ops/staging-postgres.compose.yml`
records its image, localhost binding and external volume for future management.
Set `YEARN_STAGING_ENV_FILE` to the existing ignored credential file when using
Compose. Adopting the existing volume requires removing only the stopped
container first; do not remove the volume. Do not run two containers on this port.

The main checkout's virtual environment can use the operational checkout with
`python -m pip install -e /path/to/yearn-data-neon`. Check
`python -c 'import yearn_data.cli; print(yearn_data.cli.__file__)'` before assuming
that an older checkout's command supports the Neon/staging selectors.
