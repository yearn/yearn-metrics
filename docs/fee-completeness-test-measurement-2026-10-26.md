# Fee-completeness matrix: October 26, 2026

The 12-component × 5-state matrix now calls `value_event` directly in 60 pure
cases. Five Postgres scenarios retain persistence and rollup coverage, each
containing all 12 components in distinct Ethereum asset/vault buckets.
Production code and CI configuration are unchanged.

| Measurement | Before | After |
| --- | ---: | ---: |
| Matrix file: pytest elapsed | 23.88 s | 2.92 s |
| Matrix pure cases | 0 | 60 |
| Matrix Postgres scenarios | 60 | 5 |
| Whole-suite Postgres cases | 816 | 761 |

The focused file is approximately 8.2× faster (88% less elapsed time), saving
20.96 seconds in this pair of runs. This is not a measured whole-suite speedup.
The earlier full-suite measurement remains in
[the initial cleanup report](testing-measurement-2026-10-26.md).

## Retained coverage

Both layers check exact USD component values and valuation states for missing
prices, retryable prices, missing metadata, priced amounts, and genuine zero.
Pure cases additionally check price status. Expected values are explicit,
independent of the production calculation.

Integration cases persist every component/state combination and check family,
day, vault, asset, and chain completeness flags and event counters. Separate
asset/vault buckets prevent one component from masking another. Family/day/chain
buckets additionally exercise multi-event aggregation. All persisted events are
checked individually.

A temporary copy of production source was mutated to omit
`reported_total_fees_raw` from the rollup's unpriced-component detection. The
retained missing-price integration scenario failed specifically at the
`unpriced_component_events` assertion. Repository production source was never
modified; the normal matrix passed all 65 cases.

## Validation and method

- Revised matrix: **65 passed in 2.92 s**.
- Adjacent fee valuation and distribution review: **50 passed in 20.69 s**.
- Full non-Postgres selection after stopping the task-owned database:
  **532 passed, 7 skipped, 761 deselected in 7.96 s**.
- Focused Ruff and `git diff --check` passed.
- Production source, CI workflows, and dependency lockfile have no diff.

The before/after matrix runs used CPython 3.12.3 with the same locked dependencies
and disposable local Postgres 18 on port 55439. The baseline was the working-tree
matrix after the earlier fixture split, before this rewrite. Timings are pytest
elapsed times from one successful run of each version, not repeated statistical
benchmarks; container startup is excluded. The full database suite was not rerun
for this isolated test refactor. Each run emitted the existing websockets legacy
deprecation warning.

Reproduce with the configured disposable database:

```bash
python -m pytest -q tests/test_fee_component_completeness.py --durations=5
python -m pytest -q tests/test_fee_valuation.py tests/test_distribution_review.py
python -m pytest -q -m 'not postgres'
```
