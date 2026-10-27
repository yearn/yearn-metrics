# Incremental captured TVL history

Routine preparation compares all selected daily inputs, reuses unchanged
accounted dates, and recalculates new or corrected network frames. It then
copies the complete immutable chart revision and reuses unchanged totals.
It does not reacquire historical observations or change accounting policy.

## Invalidation contract

| Input change | Invalidated scope |
| --- | --- |
| A new or corrected completed observation, including its captured price | Every vault/chain in that date's network frame |
| Changed ownership/position evidence | The whole affected date, including both cross-chain endpoints |
| Changed dated price-rejection policy | Each affected date, including derived dependencies |
| Advancing the current close under current-only bridge retirement | The previous latest close and the new latest close |
| Catalog metadata, allocator recognition, global accounting policy, unknown future context fields | Full history |
| Accounting/preparation implementation revision | A new dataset identity and full history |
| Catalog `updated_at` only, or identical selected evidence with a new run provenance ID | No recalculation of unchanged rows |
| Reporting cutoff changes | Compare dates normally; current-close accounting still invalidates the affected closes |
| Aggregate `observationRevision` changes | Compare dated evidence normally; the digest names captured inputs, not accounting policy |
| No compatible durable input index | Full history; this includes first adoption and legacy prepared sources |

The global context retains every manifest field except run inventory and known
publication metadata, and retains every catalog field except `updated_at`.
Daily hashes cover the selected snapshots, positions, dated quote rejections,
and whether the date is the current close. They exclude only `source_run_id`,
which is absent from the slim chart outputs and does not affect accounting.
The native network accounting function handles each changed frame; preparation
does not introduce a second financial calculation.

When a bounded refresh supplies `reportingCutoff`, that field limits the reporting
window rather than changing global accounting policy. Future-only collection
runs remain retained but do not add false missing-chain gaps to that earlier
window. Changing the current close still changes its dated input identity.

`observationRevision` is the maintained refresh worker’s aggregate digest of
captured TVL evidence. It gives corrected inputs a new immutable manifest ID.
It is excluded from global invalidation because each selected snapshot and
position is already hashed by date; a changed value still recalculates its full
network frame. Accounting policy must use its own context fields, not this
evidence-only digest. Unknown future context fields still invalidate history.

New manifests include an `accountingRevision` digest over the accounting,
selection, cache and incremental-preparation source files. A code change cannot
silently reassign new financial results to the same publication ID. A captured
revision that differs from the running code cannot be prepared: publish a new
manifest using the current revision. Already prepared older publications keep
their captured rows. Legacy manifests without a revision remain readable; their
first indexed preparation takes the full path.

Completed observations are immutable inputs. Supported corrections are captured
in a new completed collection run and therefore a new manifest/run union; do
not rewrite a published run with ad hoc SQL. Changing an independent earnings
price cache does not reprice an existing TVL observation: acquire corrected TVL
observations to capture that price in the TVL backfill. The planner reads the
entire candidate input history, so a new correction outside an explicitly
forced backfill range still invalidates its date.

## Commands

After collection has captured a new revision, normal preparation automatically
chooses the most recently prepared compatible input index in the same database:

```bash
yearn-data --db staging tvl prepare-history --publication ./publication/tvl
```

Force recalculation of an inclusive range of captured timestamps, while still
detecting corrections elsewhere:

```bash
yearn-data --db staging tvl prepare-history --publication ./publication/tvl \
  --backfill-from "$FROM_CLOSE" --backfill-to "$TO_CLOSE"
```

Force every date of a new immutable revision:

```bash
yearn-data --db staging tvl prepare-history --publication ./publication/tvl --full-rebuild
```

A force flag against a compatibly prepared ID is rejected, rather than replacing
its immutable rows. The existing recovery path for an unsupported/unreadable
cache format remains: rebuild that cache only with its matching captured
financial revision; a changed accounting revision requires a new manifest. Run without force flags to resume an interrupted totals stage. Use a
new collection/manifest revision for a full recalculation or validation. The
existing `--previous-dataset-id` remains the narrower quote-rejection-only
repair mode; it cannot be combined with the general backfill/full flags.

## Checkpoints, gaps and coherent selection

Preparation acquires the existing single-builder advisory lock. Winner reads,
input hashes, accounting and chart copying use one repeatable-read database
snapshot. New chart rows, their daily input index, and the rows-prepared marker
commit together; an exception before that commit rolls them all back. Each
new publication owns a full set of rows, so it does not depend on a superseded
publication at serving time.

Totals retain the existing resumable second stage. Their copies, recalculation
and completion marker commit together. A failed totals stage leaves complete
chart rows and a `rows-prepared` receipt; retry finishes totals. If an older
source disappears before totals resume, totals rebuild from the candidate's
already captured chart rows. A compatible source must have completed totals
before it can be reused by another builder.

The hosted publication writer still requires complete chart rows and totals,
and matched fee/TVL/analytics identities, before its atomic release selection.
No selection or consolidation is performed by preparation. Preparation failure
therefore preserves the prior hosted release. Existing publication and
consolidation contracts remain in place; consolidation also retires superseded
input indexes and preparation receipts, keeping the selected index for the
next refresh.

The machine-readable receipt reports reused/recomputed dates, reason for a
full rebuild, selected input rows read, recalculated/copy row writes, copied
row counts and separate copy/totals timings. Progress reports compared dates,
reuse, recalculation and pending gaps. The final `prepared` status follows
successful totals; `already-prepared` retries retain the saved diagnostic
receipt.

`pendingChainDates` lists missing chain evidence at UTC daily closes between
the first retained observation and the captured common close. A chain's raw
zero with known asset units counts as observed; missing asset units do not.
No row is invented for a missing date. Existing unavailable prices and ownership
values remain unavailable, and genuine zero remains zero. This receipt is not
proof of per-vault acquisition coverage or coverage before the first retained
observation. Collection/backfill remains responsible for repairing those gaps.

## Benchmark and validation

[The saved receipt](tvl-incremental-benchmark-2026-10-27.json) contains three
sequential local trials against the actual original full-history implementation
at integration commit `3db530831ce79bec597a43f5ab795d45d1ed8ab7`. The original cache
module was loaded with the same financial accounting and the surviving guarded
fixture builder. Each trial uses 80 vaults, one ownership edge per date, 32
initial dates and the same final 33 dates; daily refresh adds one date, then a
targeted repair changes a captured quote and ownership on an older date.

| Per refresh/backfill | Original full path | Incremental |
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
sorted chart/totals digests between incremental and original full output.
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

The surviving benchmark compares the current explicit full path with incremental
preparation. The one-off original-module probe was consolidated into the saved
receipt; its shared fixture/result/work assertions remain in the benchmark.
CI still runs the complete existing suite on both Python matrices.

Gap receipts begin at each chain’s first retained observation. A newly added chain
does not create gaps before that date. Targeted-backfill receipts record the
requested inclusive bounds and matched dates; a range matching no captured date
is rejected. A retry that completes interrupted totals reports `prepared`; only a
retry with both rows and totals already complete reports `already-prepared`.

If consolidation retires a newly prepared dataset before its final receipt is
read, preparation raises an explicit retirement error. The CLI does not report
`null` as a successful preparation; retry from the retained captured inputs.
