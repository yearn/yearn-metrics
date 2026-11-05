> Historical reference captured from `docs/incremental-tvl-history.md` at `ef03f16`.
> This records a past design, measurement or rollout; its ports, IDs, branch
> states and rollback availability are not current operating instructions.
> Current guidance: [docs/reference/tvl-history-preparation.md](../../../reference/tvl-history-preparation.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/incremental-tvl-history.md).

## Benchmark and validation

[The saved receipt](../../../../benchmarks/results/2026-10/tvl-incremental-benchmark-2026-10-27.json) contains three
sequential local trials on the final recovery-corrected implementation. Each
trial compares the current explicit full-rebuild path (`--full-rebuild`) against
incremental preparation, using 80 vaults, one ownership edge per date, 32 initial
dates and the same final 33 dates. Daily refresh adds one date; targeted repair
changes a captured quote and ownership on an older date.

A separate one-off probe loaded the original cache module from integration
commit `3db530831ce79bec597a43f5ab795d45d1ed8ab7`. The receipt records that probe's
method, but the original-module loader is not retained. That probe is not the
source of the three saved trials or the timing medians below.

| Per refresh/backfill | Current explicit full rebuild | Incremental |
| --- | ---: | ---: |
| Network dates accounted | 33 | 1 |
| Dates reused | 0 | 32 |
| Selected snapshot/position input rows read | 2,673 | 2,673 |
| Chart rows read for totals aggregation | 2,640 | 80 |
| Final chart rows written, including copies | 2,640 | 2,640 |
| Final total rows written, including copies | 132 | 132 |
| Copied chart rows / total rows | 0 / 0 | 2,560 / 128 |
| Daily refresh median elapsed, seconds | 0.225 | 0.173 |
| Targeted backfill median elapsed, seconds | 0.233 | 0.192 |

Median historical chart-copy time is 0.018s for daily refresh and 0.019s for
backfill; totals-copy time is about 0.002s each. All trials retain matching
sorted chart/totals digests between incremental and current explicit full-rebuild output.
An independent known first-day external total is 7,950; the corrected older
date is 7,960. Separate integration coverage compares dependent analytics,
unknown values, genuine zero, current-only bridge transitions, broader policy
invalidation, competing builders and interruption recovery.

These are small offline fixtures with local Postgres. Counters measure logical
rows returned/read/written, not physical pages or hidden candidate rows scanned
by the database query plan. Full selected-input scanning and full immutable
row copying remain measurable costs; the change reduces accounting/aggregation
work, not every historical read/write. Single timings vary; every raw trial is
retained, and CI asserts work/output contracts without a timing threshold.
No production backfill, preparation, release selection or provider calls were run.

With the explicit disposable Postgres fixture configured:

```bash
python -m pytest -q tests/test_tvl_incremental.py tests/test_tvl_history_cache.py tests/test_canonical_history.py
python -m pytest -q -s tests/test_tvl_incremental_benchmark.py
```

The retained benchmark harness compares incremental preparation against the
current explicit full-rebuild path. The receipt's `measurement`, `baselineMethod`
and `baselineReproducibility` fields distinguish those retained trials from the
separate historical original-module probe. Its loader is not part of the
supported reproduction command. CI still runs the complete existing suite on
both Python matrices.

Gap receipts begin at each chain’s first retained observation. A newly added chain
does not create gaps before that date. Targeted-backfill receipts record the
requested inclusive bounds and matched dates; a range matching no captured date
is rejected. A retry that completes interrupted totals reports `prepared`; only a
retry with both rows and totals already complete reports `already-prepared`.

If consolidation retires a newly prepared dataset before its final receipt is
read, preparation raises an explicit retirement error. The CLI does not report
`null` as a successful preparation; retry from the retained captured inputs.
