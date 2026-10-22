# Shared Postgres database

TVL, fee and earnings pipelines share Postgres, keeping their existing table
names, source provenance and accounting boundaries. Select `neon` for the hosted
database or `staging` for a separate local installation. Credentials stay in the
environment; publication manifests contain only logical database names.

SQLite file merging, SQLite-to-Postgres migration and legacy catalog import
commands are retired. The historical migration is complete; do not rerun it or
replace existing Postgres history. Archived source files and their restore
receipts remain untouched. Use the corresponding historical Git revision if an
archive must be inspected in an isolated recovery environment.

See [Postgres operations](postgres.md) for configuration, backup boundaries and
isolated tests. New inventory comes from `yearn-data --db neon tvl discover`.
