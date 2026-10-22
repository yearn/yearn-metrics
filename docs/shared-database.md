# Shared Postgres database

TVL, fee and earnings pipelines share Postgres, keeping their existing table
names, source provenance and accounting boundaries. Select `neon` for the hosted
database or `staging` for a separate local installation. Credentials stay in the
environment; publication manifests contain only logical database names.

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

## Retired migration commands

SQLite file merging, SQLite-to-Postgres migration and legacy catalog import
commands are retired. The historical migration is complete; do not rerun it or
replace existing Postgres history. Archived source files and their restore
receipts remain untouched. Use the corresponding historical Git revision if an
archive must be inspected in an isolated recovery environment.

See [Postgres operations](postgres.md) for configuration, backup boundaries and
isolated tests. New inventory comes from `yearn-data --db neon tvl discover`.
