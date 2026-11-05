# Incremental captured TVL history

Routine preparation identifies changed daily inputs, reuses unchanged
accounted dates, and recalculates new or corrected network frames. It then
copies the complete immutable chart revision and reuses unchanged totals.
It prepares retained historical observations under the captured accounting policy.

## Invalidation contract

| Input change | Invalidated scope |
| --- | --- |
| A new or corrected completed observation, including its captured price | Every vault/chain in that date's network frame |
| Changed ownership/position evidence | The whole affected date, including both cross-chain endpoints |
| Changed dated price-rejection policy | Each affected date, including derived dependencies |
| Advancing the current close under current-only bridge retirement | The previous latest close and the new latest close |
| Catalog additions, removals, or metadata changes | Dates with snapshots or ownership endpoints referencing the changed vaults |
| Catalog chain membership changes under current-only bridge retirement | Also the current close when a bridge points to an added or removed chain |
| Allocator recognition, global accounting policy, unknown future global context fields | Full history |
| Accounting/preparation implementation revision | A new dataset identity and full history |
| Catalog `updated_at` only, or identical selected evidence with a new run provenance ID | No recalculation of unchanged rows |
| Reporting cutoff changes | Compare dates normally; current-close accounting still invalidates the affected closes |
| Aggregate `observationRevision` changes | Compare dated evidence normally; the digest identifies captured inputs; accounting policy has its own revision |
| No compatible durable input index | Full history; this includes first adoption, legacy prepared sources, and quote-rejection-only repair outputs |

The global context retains every manifest field except run inventory, catalog,
and known publication metadata. Each preparation stores per-vault catalog
fingerprints retaining all metadata fields except `updated_at`, including
unknown future fields. Fingerprints normalize catalog address casing.
Daily hashes cover the selected snapshots, positions, relevant catalog
fingerprints, dated quote rejections, current-close bridge target availability,
and whether the date is the current close. They exclude only `source_run_id`,
a provenance field outside the slim chart outputs and accounting inputs.
The native network accounting function handles each changed frame; preparation reuses that
accounting calculation.

Catalog dependency planning checks retained snapshots and both parent and child
position endpoints, including cross-chain children without their own snapshots.
It uses the union of the previous and current captured runs. Potentially affected
dates are selected and hashed again; unrelated dates keep their fast reuse path.
Recalculation always covers the whole date's network so ownership deductions
stay consistent. An added vault affects dates with corresponding historical evidence;
unrelated dates remain reusable. Adding a catalog chain can still affect a retired bridge on
another chain at the current close, even without target-chain observations.

The `daily-inputs-v2` index records catalog fingerprints and affected dates in
the preparation receipt (`dayTracking.catalog` and `catalogAffectedDates`).
Adopting this algorithm requires one new compatible baseline; older immutable prepared
publications remain available under their original input identities. Subsequent catalog-only
changes use scoped invalidation.

When a bounded refresh supplies `reportingCutoff`, that field sets the reporting window
while global accounting policy retains its own revision. Future-only collection runs remain
retained outside the earlier window’s gap calculation. Changing the current close still
changes its dated input identity.

`observationRevision` is the maintained refresh worker’s aggregate digest of
captured TVL evidence. It gives corrected inputs a new immutable manifest ID.
It is excluded from global invalidation because each selected snapshot and
position is already hashed by date; a changed value still recalculates its full
network frame. Accounting policy uses its own context fields; this digest identifies
evidence. Unknown future context fields still invalidate history.

New manifests include an `accountingRevision` over normalized Python definitions
for captured-row selection, financial initialization/accounting, chart writes,
price-repair preparation and totals aggregation. `tvl_revision.py` declares the
entry points and follows their module-local helpers, constants and import
bindings; financial instance-method dependencies are followed too. The revision
algorithm and its declared boundary are included in the digest. New financial
entry points must be added to that boundary. Serving-only methods, chart response
formatting, public response limits, exports and serving-cache initialization are
excluded. Chain names used as persisted totals-series keys remain included.
Source revisions track behavior-relevant code inputs; cosmetic edits preserve the revision.

This financial revision is separate from the refresh worker's whole-package
checkpoint identity. A serving-only edit leaves the financial revision, dataset
identity and reusable accounting context unchanged for identical captured
inputs, but still requires a new refresh job after deployment. The new job can reuse
persisted collection coverage and observations.

A captured financial revision that differs from the running code cannot be
prepared: publish a new manifest using the current revision. Already prepared
older publications keep their captured rows. Adopting this narrower digest
format changes the revision once and requires a full preparation of the new
identity. Legacy manifests without a revision remain readable; their first
indexed preparation takes the full path.

Completed observations are immutable inputs. Supported corrections are captured
in a new completed collection run and therefore a new manifest/run union; preserve published
runs as immutable source evidence. Acquire corrected TVL observations to capture a changed
price in the TVL backfill; the earnings price cache has an independent valuation path. The
planner reads the
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

A force flag against a compatibly prepared ID is rejected to preserve its immutable rows.
The existing recovery path for an unsupported/unreadable
cache format remains: rebuild that cache only with its matching captured
financial revision; a changed accounting revision requires a new manifest. Run without force
flags to resume an interrupted totals stage. Use a
new collection/manifest revision for a full recalculation or validation. The
existing `--previous-dataset-id` remains the narrower quote-rejection-only
repair mode; it cannot be combined with the general backfill/full flags.
That repair path intentionally writes serving rows and totals without creating
an incremental input index. After selecting the repaired revision and
consolidating away older indexes, the next normal preparation rebuilds the
full history once to establish a compatible index. Subsequent compatible
refreshes resume dated reuse. The repair receipt’s recomputed dates describe that repair. A
subsequent refresh evaluates reuse against its own captured inputs.

## Checkpoints, gaps and coherent selection

Preparation acquires the existing single-builder advisory lock. Winner reads,
input hashes, accounting and chart copying use one repeatable-read database
snapshot. New chart rows, their daily input index, and the rows-prepared marker
commit together; an exception before that commit rolls them all back. Each new publication
owns the full set of rows it needs for serving.

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
the first retained observation and the captured common close. A chain’s raw zero with known
asset units counts as observed; missing asset units retain unavailable status.
No row is invented for a missing date. Existing unavailable prices and ownership
values remain unavailable, and genuine zero remains zero. Per-vault acquisition coverage and
earlier historical coverage require their own range evidence. Collection/backfill remains
responsible for repairing those gaps.

Gap receipts begin at each chain’s first retained observation. Gap accounting for a newly
added chain starts at that date. Targeted-backfill receipts record the
requested inclusive bounds and matched dates; a range matching no captured date
is rejected. A retry that completes interrupted totals reports `prepared`; only a
retry with both rows and totals already complete reports `already-prepared`.

If consolidation retires a newly prepared dataset before its final receipt is
read, preparation raises an explicit retirement error. The CLI treats `null` as an
incomplete preparation; retry from the retained captured inputs.

[Performance recipes](../development/performance.md) describe independent validation;
[frozen measurements](../archive/measurements/2026-10/incremental-tvl-history.md) retain the
original trials.

[Documentation index](../README.md).
