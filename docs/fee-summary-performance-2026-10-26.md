# Fee summary performance — October 26, 2026

The summary aggregation now converts each fee Decimal once and accumulates the
overall and contract-family totals together. It retains each accumulator's source
order and precision; lifetime earnings still use their existing ordered arithmetic.
This follows the grouped-history optimization measured separately in
[fee-serving-performance-2026-10-26.md](fee-serving-performance-2026-10-26.md).

## Method

The frozen baseline is commit `f0307a7`. Both `pairing.py` and `hosted_fees.py`
were copied before backend edits. The opt-in
`scripts/benchmark_fee_summary.py` runs the old and new algorithms against the
same synthetic fixture: 32 monthly periods, 1,000 vault identities on four chains,
32,000 fee rows and 32,000 earnings rows. Long Decimal strings, unavailable values
and genuine zeros are included. Synthetic addresses exercise the product grouping
path, without representing registered multi-contract products; domain regression
tests cover those product semantics.

Each invocation creates, prepares and drops its own unique schema in disposable
localhost Postgres. Preparation is outside the timers. Five fresh objects are
constructed and queried per scope, followed by one same-object cached query each.
Root, backend and consumer agents confirmed that heavy checks were stopped before
the final paired runs; the baseline run preceded the candidate run. Earlier
exploratory runs are excluded. Source SHA-256 digests, all samples and output hashes
are retained in [the raw measurement](fee-summary-performance-2026-10-26.json).

The profile records total connection-establishment time, JSON decode time and
row-loading wall time. Row loading includes connection setup, SQL execution,
transfer, Python row iteration and JSON decoding. Aggregation is calculation time
minus row-loading time, so also includes filtering/grouping and ordinary view
bookkeeping. JSON decoding timers wrap each call and add overhead in both runs.
These are diagnostic phase measurements rather than an uninstrumented throughput
benchmark. Initial in-memory cProfile attributed almost all summary computation to
ordered Decimal summation, motivating this narrow aggregation change.

## Results

Medians of five samples, milliseconds. Component medians need not sum to the total.

| Scope / version | Fresh-object total | Row loading | JSON decode (within loading) | Aggregation | Connection setup (all phases) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Contract / baseline | 1,087.32 | 517.06 | 225.64 | 555.25 | 12.63 |
| Contract / candidate | 837.17 | 527.58 | 233.99 | 267.02 | 12.61 |
| Product / baseline | 1,151.62 | 547.50 | 241.77 | 594.44 | 12.85 |
| Product / candidate | 861.12 | 528.74 | 234.25 | 311.02 | 12.76 |

Fresh-object totals fell 23.0% and 25.2% in this fixture. Aggregation medians fell
51.9% and 47.7%. Row-loading differences are small relative to the aggregation
change and do not establish a SQL improvement. Construction medians were around
8 ms and serialization below 0.1 ms. Same-object cached queries were already
below 0.005 ms in both versions; this change does not introduce that cache.

All ten baseline and ten candidate responses match the complete serialized output
hash within each scope, including every financial field and response metadata:

- Contract: `f1c23b9dbdb2e3b3269f361177ce91e707cdea43e03d7e75e848bf27aab91b3d`
- Product: `ab9a2fe458c2732ef8dda867f9c27a6b27e00ff3550565ddb9ee8643a10cadb9`

The remaining dominant measured cost is row loading, including about 234 ms of
JSON parsing. Investigating prepared exact summaries or SQL/transfer separately
would require its own measurements and implementation boundary.

## Consumer request removal

The paired Powerglove change passes the parent fee panel's selected dataset ID
into the monthly table. Previously that table requested an independent unpinned
`/api/fees` summary solely to discover an ID already available in its parent.
In the captured original browser trace, that request started about 6.24 seconds
after the first API request and took 4.035 seconds. It was one of four summary
requests in that trace.

The revised component test uses the actual fetching hooks and records no summary
discovery request, with two pinned product requests for the catalog and grouped
history. A replacement parent dataset triggers new pinned requests. Missing IDs
remain explicitly unavailable. Retry still refreshes the parent summary as well
as retrying the product data, preserving recovery from an expired publication;
the regression covers HTTP 410 followed by a replacement dataset.

This removes one request dependency, not a measured four-second improvement to
the deployed page. The earlier default-range summary followed by a TVL-bounded
range summary remains unchanged; eliminating that duplication requires a separate
decision about shared date-range readiness and failure behavior.

## Reproduce and limits

Use an explicitly provisioned disposable localhost Postgres and the repository's
Python dependencies. Export its URL as `YEARN_TEST_POSTGRES_URL`; the script rejects
remote database hosts. No infrastructure is started implicitly.

```bash
git show f0307a7:src/yearn_data/pairing.py > /tmp/summary-before-pairing.py
git show f0307a7:src/yearn_data/hosted_fees.py > /tmp/summary-before-hosted_fees.py
PYTHONPATH=src python scripts/benchmark_fee_summary.py --postgres \
  --pairing-source /tmp/summary-before-pairing.py \
  --hosted-source /tmp/summary-before-hosted_fees.py \
  --output /tmp/fee-summary-before.json
PYTHONPATH=src python scripts/benchmark_fee_summary.py --postgres \
  --compare-to /tmp/fee-summary-before.json --output /tmp/fee-summary-after.json

# Offline smoke or algorithm-only measurement, without Postgres:
PYTHONPATH=src python scripts/benchmark_fee_summary.py \
  --vaults 25 --repeats 1 --output /tmp/fee-summary-smoke.json
```

This is synthetic data, five samples per scope, fixed version order and one host.
Database/OS caches may be warm. A fresh application object is distinct from a cold
process or cold database. The result does not include hosted selection lookup,
HTTP, network, TLS, CDN cache lookup, browser work or chart rendering. It does not
predict production or whole-page speed, and same-object process-cache measurements
must not be compared directly with CDN HIT timings. No production database,
publication, release selection, deployment or CI policy was changed by this
measurement.

## Final validation

- Full unchanged backend suite: **1,352 passed, 7 skipped**, with the existing
  websockets deprecation warning, using isolated Postgres. Command:
  `PYTHONPATH=src python -m pytest -q --durations=10` with the disposable
  `YEARN_TEST_POSTGRES_URL` configured.
- Paired Powerglove full suite: **481 tests passed across 95 files**;
  TypeScript and production Vite build passed (`bun run test`, `bun run build`).
- Focused Ruff, targeted Biome and both repositories' `git diff --check` passed.
  CI files remain unchanged.
- A temporary comparison of 30 mixed-value row sets matched the old fee and
  family calculation for every field, including null, negative zero, negative
  amounts and values spanning the precision boundary. This exploratory check
  was not retained as redundant permanent coverage.

The new permanent coverage protects ordered global-versus-family sums and
consumer missing-ID/publication-rollover recovery. Existing hosted tests cover
filtering, product semantics, stale pins and mid-request release changes.
