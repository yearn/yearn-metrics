# Test cleanup coverage review — October 26, 2026

Reviewed the complete test cleanup against the original PR #69 tip, then rebased
onto `origin/ross/pr-08-fantom-replay` at
`49b25982e76a99142b02968128f56713970714f6` for delivery.

No actionable coverage gaps were found in the planned reductions. This is a
local review, not an upstream approval. The scope is test behavior and fixture
isolation; it is not a new audit of production accounting.

- Verified that the bulk of changed test files add only the explicit database
  fixture argument; an AST comparison confirmed their bodies are unchanged.
- Checked each of the four removed cases against the surviving coverage listed
  in the measurement report. Pipeline transport, canonical and USD export
  rollback, accepted-price preservation, and the Gnosis namespace remain covered.
- Reviewed the database guard, fixture dependency marking, owned-schema cleanup,
  URL/schema rejection and narrowly scoped local TLS handshake exception. The
  isolated database and no-database selections both remain supported.
- Checked all 12 fee components and five states at the pure boundary. Persisted
  event values remain checked individually; asset/vault buckets isolate each
  component, and family/day/chain buckets preserve aggregate counter checks.
- Verified pure malformed-length cases for all three products, plus real
  ingestion and cached replay rejection for each. Existing known-good receipt
  cases retain valid-encoding and historical accounting checks.
- Checked that cheap hosted request fixtures fail on unexpected data loading;
  registry tests still use real transactions; compact fee routes still prepare
  and query real SQL rows after deleting source analysis outputs. Full
  publication, read-only request, revision selection, rollback and retirement
  contracts remain intact.
- Reviewed the recorded temporary mutation checks: omitted fee-component
  detection, accepted trailing receipt bytes, bypassed comparison validation,
  and removed SQL chain filtering all fail retained coverage.

The requested base added a four-case price-repair regression after this work
started. Its direct database access now requests `postgres_database`. Other new
base tests inherit the explicit fixtures or are pure. The base's production
changes remain outside this PR's diff. CI workflows and the lockfile are unchanged.

See [the measurement report](testing-measurement-2026-10-26.md) for historical
cumulative measurements and delivery validation. The 16.9% measurement compares
the original baseline and cleanup on the same original base; a rebased run is
reported separately because the base added code and tests.
