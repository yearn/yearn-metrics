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
uses `verify-full`, checking both certificate trust and hostname. The default
CA file comes from Python's system trust configuration, with the Requests CA
bundle as a fallback when no system CA file is available. This explicit path
also works with binary libpq builds whose OpenSSL default paths differ from
Python's. Remote URLs cannot weaken this verification; `sslrootcert`
may select an explicit CA bundle. Only loopback or Unix-socket connections may
explicitly use `sslmode=disable`. `YEARN_DATA_DB_SCHEMA` selects an already-created schema, defaulting
to `public`. Use the same schema and logical database when reading a publication.

```bash
yearn-data --env /path/to/.env --db staging init-db
yearn-data --env /path/to/.env --db staging tvl discover
yearn-data --env /path/to/.env --db staging analyze lifetime-yield
```

`migrate-postgres`, `merge-databases` and `tvl import-catalog` are removed. The
completed import is preserved in Git history. Existing files and verified archives
are not deleted, rewritten or silently converted. Recovery uses retained receipts
and a historical checkout in isolation; old file-based publication manifests do
not work with this Postgres-only runtime. Republish from the existing Postgres data.

## Storage boundary

The pipelines share the Postgres database, not mutable catalogs or price caches.

| Records | Tables |
| --- | --- |
| Fee/earnings inventory and source evidence | `vaults`, `events_raw`, `strategy_reports`, `history_*` |
| Fee calculations and selected earnings/fee analyses | `canonical_fee_reports`, `fee_*`, `analysis_runs`, `analysis_outputs` |
| Fee/earnings cached prices | `prices`, `fee_daily_prices` |
| TVL inventory and source evidence | `tvl_vaults`, `tvl_catalog_evidence`, `tvl_events_raw`, `tvl_inventory_events`, `tvl_history_*` |
| Dated TVL and nested positions | `tvl_runs`, `tvl_snapshots`, `tvl_positions`, `tvl_strategies`, `tvl_targets` |
| TVL cached prices and backfill checkpoints | `tvl_prices`, `tvl_backfill_*` |

TVL can read fee/earnings strategy evidence to expand its candidate mappings. It
writes its own strategy mappings and coverage; TVL discovery does not change the
fee/earnings catalog, pricing decisions or analysis outputs. Run IDs stay local to
their respective run tables.

## Tests

All database regressions run against Postgres. Tests request `postgres_database`
directly or through a shared fixture; each database test creates random schemas
and drops only those schemas after closing its connections. There is no SQLite
fallback and a missing test URL fails explicitly for database tests. Pure and
offline boundary tests run without Postgres using
`python -m pytest -q -m 'not postgres'`. See the [testing policy](../development/testing.md) for
fixture and retention rules.

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

## Local Postgres staging

Staging uses a separate Postgres 18 container (matching Neon), database, role and Docker volume.
It binds only `127.0.0.1:55435` and restarts automatically. Its credentials are in
ignored `.env` / `data/local-postgres.env` files. Set `STAGING_DB_URL` to its local
connection URL with `?sslmode=disable`; hosted Neon requires certificate and hostname verification.
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

Use a dedicated environment for each checkout; verify the imported module path
as described in [development setup](../development/setup.md). No legacy SQLite
service is needed for the hosted API or owned analytics. Historical archive
locations and verified restore hashes are in the
[retirement record](../archive/rollouts/2026-10-13-service-retirement.md).

## Bounded single-chain publisher

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
[verified canonical-history consolidation](../reference/retention.md). Daily historical
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
This is a specialized manual publisher, not the nightly chain. Its block/date
scope and 500-target limits differ from native refresh budgets. Coordinate
writers and finish the [nightly handoff](nightly.md#required-handoff-after-a-manual-publication)
after any manual publication on an installation with a timer.

For chart preparation and targeted/full rebuilds, use the
[history preparation reference](../reference/tvl-history-preparation.md).
For HTTP startup and reader permissions, use [hosted API operations](hosted-api.md).

[Documentation index](../README.md).
