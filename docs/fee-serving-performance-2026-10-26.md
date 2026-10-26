# Fee serving performance measurement — October 26, 2026

The compact grouped monthly vault history replaces repeated full monthly vault
responses. This measurement compares the frozen implementation at `3f6df4b` with
the grouped implementation on the same disposable local Postgres fixture. It does
not publish a dataset, change a selected production release, or measure a deployed
version of the new route.

## Method

`scripts/benchmark_fee_serving.py` generates 32 monthly periods, 250 distinct vault
identities across four chains, 8,000 fee rows and 8,000 earnings rows. Values include
long decimal strings, unavailable values and genuine zero. Each scope gets three
fresh view instances; each measured instance is queried again to distinguish its
existing process cache from its first calculation. The SQL fixture lives in a
unique temporary schema, which is dropped afterward. The benchmark rejects remote
Postgres hosts. It does not use production credentials or require database access
for its default in-memory mode.

The frozen baseline performs 32 full `/vaults` equivalent views per scope. The new
implementation performs one compact monthly grouped view. Both read the same
prepared compact financial rows. Timing includes construction, calculation and
JSON serialization; fixture insertion, canonical comparison and the second warm
query are outside the first-query timing. The final paired run started after the
other agents confirmed their heavy checks were stopped. Earlier exploratory runs
had competing tests/builds and are excluded from the results below.

## Results

Median of three samples, in milliseconds. Component medians need not add to the
median total. Raw samples and canonical hashes are retained in
[`fee-serving-performance-2026-10-26.json`](fee-serving-performance-2026-10-26.json).

| Scope and strategy | First query total | Calculation | Serialization | Source-row reads | JSON bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Contract, full monthly fanout | 1,907.49 | 1,664.58 | 146.70 | 32 | 14,105,761 |
| Contract, compact grouped | 361.88 | 324.08 | 12.98 | 1 | 1,907,248 |
| Product, full monthly fanout | 1,096.07 | 1,015.47 | 70.49 | 32 | 14,105,729 |
| Product, compact grouped | 265.42 | 246.96 | 9.67 | 1 | 1,907,247 |

Within this fixture, first-query time fell 81.0% for contract scope and 75.8% for
product scope. Both strategies already hit the existing process cache on their
second query: zero source-row reads, with median calculation times below 0.05 ms.
This change does not introduce that cache advantage. JSON serialization remains
necessary for a warm HTTP request.

The benchmark projects the baseline onto the new endpoint's consumer fields:
identity/name/family and optional product metadata, `totalFeesPaidUsd`, and
`lifetimeEarnings.netYieldUsd`. All samples produce the same canonical hash for
those fields. Full old endpoint payloads are measured for baseline bytes; compact
new payloads are measured for after bytes. The existing full endpoint remains
available for consumers that need its other financial fields.

## Hosting payload check

A full grouped response would combine the old endpoints' large detailed rows.
Using locally retained, successful existing endpoint responses, deduplicating
identical monthly ranges, and excluding overview/multimonth ranges gives:

| Observed scope | Monthly buckets | Vault rows | Old detailed JSON bytes combined | Modeled compact grouped JSON bytes |
| --- | ---: | ---: | ---: | ---: |
| Contract | 48 | 11,151 | 12,215,207 | 2,500,021 |
| Product | 12 | 2,919 | 3,235,298 | 668,091 |

These are local projections of captured responses, not responses from a deployed
new endpoint. The model retains the selected metadata and the two financial
fields; actual period/cutoff metadata adds a small amount. The compact projection
avoids combining more than 12 MB of detailed contract rows into one response in
this observed publication. Future publication growth still requires response-size
monitoring. The synthetic benchmark's detailed payload is intentionally large and
is a stress case rather than a claim about a typical user's response.

## Reproduction and limits

Use the repository's Python dependencies with `PYTHONPATH=src`; an installed
editable package may otherwise resolve to another checkout. Start a disposable
local Postgres database first, then set `YEARN_TEST_POSTGRES_URL` to its local
connection URL. No service setup is implicit.

```bash
git show 3f6df4b:src/yearn_data/pairing.py > /tmp/fee-pairing-before.py
PYTHONPATH=src python scripts/benchmark_fee_serving.py \
  --postgres --compare --pairing-source /tmp/fee-pairing-before.py \
  --output /tmp/fee-serving-paired.json

# Offline algorithm-only comparison; no Postgres connection or schema.
PYTHONPATH=src python scripts/benchmark_fee_serving.py \
  --compare --output /tmp/fee-serving-memory.json
```

Frozen baseline file SHA-256:
`af4d8ed71455ecb9d0af9d01f54ee208ee1d5472d63b3b5add318117820d6fb0`.

This is a repeatable diagnostic, not a CI performance threshold. Three samples,
fixed strategy order, synthetic data and host scheduling do not support a precise
production speedup forecast. Postgres buffer and OS caches may be warm; “first
query” means a fresh application view, not a cold database or cold Vercel process.
The synthetic addresses exercise the product grouping path without representing
actual registered products; domain regression tests own those financial cases.

The benchmark does not include HTTP, network, TLS, gzip, CDN lookup, selection
registry reads, or browser rendering. Earlier live request observations include
CDN HIT/MISS headers; those observations must not be equated with process-LRU
or fresh-view timings. Browser route-interception checks use generated fixture
data and verify consumer wiring, rather than deployed latency or production
financial correctness.

## Implementation boundary and follow-up

The backend adds `/api/fees/vaults/history` without a schema migration or
publication backfill. The paired Powerglove change replaces its per-period
vault-type and monthly-table requests, adds the hosted rewrite, and validates
the compact response before caching or rendering it. Release the backend before
the consumer; older backends do not provide this route. Existing detailed fee
endpoints and CI execution policy are unchanged.

The remaining work should follow a deployed measurement, not assume that the
local speedup applies to the whole page:

1. Compare initial request counts, chart-ready time, response sizes and CDN
   HIT/MISS latency against the same publication after an authorized rollout.
2. Attribute the remaining slow summary requests to connection setup, SQL,
   decoding and aggregation. Consider a cheap publication bootstrap to remove
   summary-dependent request ordering and exact prepared common summaries.
3. Obtain query plans before adding indexes or changing connection pooling.
   Evaluate shared response storage only if prepared outputs and the existing
   publication-pinned CDN policy leave a measured gap.

Do not replace ordered lifetime-earnings arithmetic by recombining monthly
totals: its Decimal precision can make that transformation change results.
No production database, publication selection, or deployment was changed by
this implementation.

## Final validation

- Full unchanged backend suite against isolated Postgres: **1,351 passed,
  7 skipped**, with the existing websockets deprecation warning. Command:
  `PYTHONPATH=src python -m pytest -q --durations=10`, with the disposable
  `YEARN_TEST_POSTGRES_URL` configured.
- Paired Powerglove suite: **479 tests passed across 95 files**; production
  TypeScript/Vite build passed. Commands: `bun run test`, `bun run build`.
- Focused Ruff and Biome checks, plus both repositories' `git diff --check`,
  passed. CI files were not changed.
- A Chromium check loaded the actual consumer helper and intercepted the new
  route with generated backend fixtures: 32 periods and 8,000 rows per scope,
  one request each, with exact projected financial and metadata parity against
  the old monthly responses. This verifies browser wiring, not deployed speed.

The full-suite duration is not a test-performance comparison: frontend
validation ran concurrently after the isolated serving benchmark completed.
