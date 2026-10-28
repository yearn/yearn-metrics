# Full refresh measurement through TVL dispatch

The manual harness in `tests/benchmark_refresh.py` uses the actual maintained
refresh worker, native fee projection/pricing, financial analyses, matching
preparation and history cache. Only provider boundaries use deterministic offline
responses. It compares exact catch-up module revisions while retaining the same
worker, inventory, database schema, cutoff and prices. Each trial requests its
own explicitly guarded disposable Postgres schema.

The fixture contains 120 V3 vaults, 60 active and 60 inactive, with 40 fully
covered, 40 partially covered and 40 initially uncovered report ranges. Every
vault has one report with gain 1000000 at six decimals; the accepted provider
quote is 2 USD. Independently expected net yield is 240 USD and paid fees 24 USD.
The cutoff is 172800, with two TVL closes and identical offline archive responses.
These are fixture amounts, not production coverage or market-price claims.

Each trial runs extension followed by a fully covered repeat. Both explicitly
repair the same TVL dates so the timer ends at actual collection dispatch. The
first interval starts immediately before `run_refresh` and ends when its
`tvl-collection-started` event is emitted immediately before `collect_tvl` runs.
It includes bound resolution, state/identity work, report/catalog/Tokenized
acquisition, canonical-fee projection and report/fee pricing. The worker collects
TVL before running financial analyses; the second interval ends after all
accounting and matching dataset preparation. Stage timings and both intervals
are retained. This distinguishes the actual workflow ordering from the older
single-chain script and from a catch-up-only measurement.

The harness counts coverage SELECTs made by the worker's data connections,
including discovery scopes. It records report fetches at the public provider
boundary, not transport requests or hidden provider retries. Optional latency
is a model adding delay only to coverage reads; it does not measure hosted
Postgres round trips. There is no CI timing threshold or changed CI selection.
The default regression suite owns recovery and financial correctness; this
explicit harness supplies reproducible performance evidence.

## Reproduce

Configure the explicit disposable service from [Postgres tests](postgres.md#tests).
Use the interpreter/dependencies from the README, then run:

The after revision belongs to sibling PR #88. Fetch its pull-request ref before
extracting the pinned commit (a shallow checkout must also fetch the before
revision). GitHub pull refs remain usable after the source branch is deleted.

```bash
git fetch origin refs/pull/88/head:refs/remotes/origin/benchmark-pr-88
git fetch origin 3db530831ce79bec597a43f5ab795d45d1ed8ab7
git show 3db530831ce79bec597a43f5ab795d45d1ed8ab7:src/yearn_data/catchup.py > /tmp/yearn-catchup-before-56.py
git show 664c25c09f7be237d199cc0a5b6d1a2f9984f602:src/yearn_data/catchup.py > /tmp/yearn-catchup-after-56.py
YEARN_BENCHMARK_BEFORE_CATCHUP=/tmp/yearn-catchup-before-56.py \
YEARN_BENCHMARK_AFTER_CATCHUP=/tmp/yearn-catchup-after-56.py \
YEARN_BENCHMARK_RECEIPTS=/tmp/yearn-full-refresh-receipts.jsonl \
python -m pytest -q -s tests/benchmark_refresh.py
```

The receipt path appends six trials; remove a previous measurement file before
collecting a new set. Add `YEARN_BENCHMARK_ROUNDTRIP_MS=2` for a separately labeled
latency model. Both module paths are mandatory; omission fails rather than
silently skipping the measurement. The receipt contains module hashes, all
stage timings, raw first-dispatch/prepared intervals, report/coverage digests,
counts and independently checked totals.

## Results

[Raw trials and stage timings](refresh-benchmark-2026-10-27.json) preserve identical report and coverage digests in all twelve trials. Each cell below is the median of three independent schemas.

| Coverage-read delay model | Phase | First TVL dispatch, before → after (s) | Prepared, before → after (s) |
| --- | --- | --- | --- |
| 0 ms | extend | 1.374 → 1.282 | 2.963 → 2.838 |
| 0 ms | covered-repeat | 0.624 → 0.620 | 2.164 → 2.261 |
| 2 ms | extend | 1.890 → 1.196 | 3.482 → 2.861 |
| 2 ms | covered-repeat | 1.181 → 0.601 | 2.858 → 2.253 |

Coverage reads including report discovery fall from **256 to 10** in every phase. Report fetches remain **80 during extension and zero during a covered repeat**. Both paths retain 120 reports and 160 report-coverage rows. Native analyses independently yield 240 USD and 24 USD fees.

Local zero-delay timings show modest/noisy end-to-end gains; the covered repeat did not improve complete-preparation time. This optimization primarily removes redundant database reads. The separate two-millisecond model illustrates latency sensitivity and is not a production speed claim. Financial analyses, daily accounting, provider transport and deployment performance require their own measurements. The worker’s stage ordering is described above; these results do not claim to benchmark the older update script end to end.
