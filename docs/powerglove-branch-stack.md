# Powerglove and Neon branch stack

Updated October 20, 2026. Source branches live in `yearn/yearn-metrics`.
A PR's **head/source** contains its changes; its **base/target** receives them.

## Current review order

| PR / branch | Head / source branch | Base / target branch | Status and scope |
| --- | --- | --- | --- |
| [#38](https://github.com/yearn/yearn-metrics/pull/38) | `ross/powerglove-hosted-api` | `ross/pr-19-yvusd-fees` (#37) | Open draft; hosted API and integrated backend foundation |
| [#43](https://github.com/yearn/yearn-metrics/pull/43) | `ross/tvl-comparison-followup` | `ross/powerglove-hosted-api` (#38) | Service retirement, current adapter comparison, mapping corrections and complete-position selection |

PRs #22, #23 and #24 have merged. The open stack starts at #25 targeting `main`.
Review integrated behavior at #43 and trace findings downward to the owning PR.
Carry lower-layer fixes through every descendant; merge from #25 upward.
Each other open PR targets its immediate predecessor.

PRs [#40](https://github.com/yearn/yearn-metrics/pull/40),
[#42](https://github.com/yearn/yearn-metrics/pull/42), and
[#41](https://github.com/yearn/yearn-metrics/pull/41) merged on October 12,
in that order. Their curation, overlap and canonical-history work is now
contained in `origin/ross/powerglove-hosted-api` at `f6d723d`.
PR [#39](https://github.com/yearn/yearn-metrics/pull/39) was merged into
`ross/tvl-overlaps` and is also included through those merges. These PRs
do not need to be reopened or replayed.

The follow-up originally started at `f6d723d`. That base has the same committed file tree
as the former working branch, `ross/curation-tvl-corrections` at `f572814`;
the difference is three merge commits. Its six implementation commits separate:

1. Legacy API service retirement and recovery documentation.
2. PostgreSQL catalog active-flag compatibility.
3. Current Yearn curation registry discovery.
4. Finance adapter contribution replay and comparison evidence.
5. Corrected overlap destinations and stale mapping suppression.
6. Complete-position selection, preserving valid zero-debt updates.

Review the follow-up against its immediate base:

```bash
git diff origin/ross/powerglove-hosted-api...ross/tvl-comparison-followup
```

Merge from the bottom of the stack. After a parent PR merges, retarget its
child to the parent's former base. Preserve ancestry when merging, or
restack the remaining children after a squash or rebase merge. Changes to
a lower layer must also be carried through its descendants.

## Working checkouts and names

- `/home/dev/raaaws/yearn-data`: `ross/tvl-comparison-followup`.
- `ross/curation-tvl-corrections` retains its merged tip at `f572814`.
- The local `ross/powerglove-hosted-api` branch is older than its remote
  tracking ref. Use `origin/ross/powerglove-hosted-api` when creating work.
- `/home/dev/raaaws/worktrees/yearn-data-effective-fee-products` retains
  `ross/ybold-fee-classification`, the merged PR #39 snapshot at `6136221`.
- Older occupied Neon and historical-ingestion worktrees remain separate.
  Do not force-push older local branches merely to clear ahead/behind labels.

Git branches, running services and selected database publications are
independent. This branch organization does not deploy or select data.
The original working tip is retained at
`refs/archive/20261015-comparison-followup/curation`.

## Earlier PRs

PR #25 targets `main`; PRs #26–#37 target their immediate predecessor
branches. Local branch numbering does not match their current GitHub PR numbers. The replacement PRs use branches in `yearn/yearn-metrics`,
not the earlier fork PRs:

| Branch | Current PR | Base / target branch |
| --- | --- | --- |
| `ross/pr-04-canonical-fees` | [#25](https://github.com/yearn/yearn-metrics/pull/25) | `main` |
| `ross/pr-05-verified-fee-recovery` | [#26](https://github.com/yearn/yearn-metrics/pull/26) | `ross/pr-04-canonical-fees` |
| `ross/pr-06-closed-day-exports` | [#27](https://github.com/yearn/yearn-metrics/pull/27) | `ross/pr-05-verified-fee-recovery` |
| `ross/pr-07-price-fallbacks` | [#28](https://github.com/yearn/yearn-metrics/pull/28) | `ross/pr-06-closed-day-exports` |
| `ross/pr-08-fantom-replay` | [#29](https://github.com/yearn/yearn-metrics/pull/29) | `ross/pr-07-price-fallbacks` |
| `ross/tvl-history` | [#30](https://github.com/yearn/yearn-metrics/pull/30) | `ross/pr-08-fantom-replay` |
| `ross/pr-13-powerglove-fees` | [#31](https://github.com/yearn/yearn-metrics/pull/31) | `ross/tvl-history` |
| `ross/pr-14-powerglove-tvl` | [#32](https://github.com/yearn/yearn-metrics/pull/32) | `ross/pr-13-powerglove-fees` |
| `ross/pr-15-neon-storage` | [#33](https://github.com/yearn/yearn-metrics/pull/33) | `ross/pr-14-powerglove-tvl` |
| `ross/pr-16-neon-operations` | [#34](https://github.com/yearn/yearn-metrics/pull/34) | `ross/pr-15-neon-storage` |
| `ross/pr-17-tvl-price-repairs` | [#35](https://github.com/yearn/yearn-metrics/pull/35) | `ross/pr-16-neon-operations` |
| `ross/pr-18-powerglove-analytics` | [#36](https://github.com/yearn/yearn-metrics/pull/36) | `ross/pr-17-tvl-price-repairs` |
| `ross/pr-19-yvusd-fees` | [#37](https://github.com/yearn/yearn-metrics/pull/37) | `ross/pr-18-powerglove-analytics` |

Earlier on October 20, the pricing review carried #24's four review-fix commits through all
16 open PRs and restored the missing #32 ancestry in #33, retaining the newer
bounded-catch-up and hash-identity safeguards.

The integrated pricing findings were placed in their owning layers:

- #24 derives report-chain eligibility from the Yearn client mapping.
- #27 preserves accepted fee quotes after failed refreshes and propagates
  configuration errors instead of recording invalid prices.
- #28 preserves pending-day retry selection and cached-price protection while
  using parallel fallback adapters; exhausted individual provider requests remain
  retryable without aborting other targets.
- #33 preserves the conditional writes and tests both writers on PostgreSQL.

The integrated code passed 877 tests, with 7 execution-dependent skips, including
disposable local PostgreSQL schemas. #24 independently passed 294 tests, with
12 skips. These are local checks; the integrated review did not request a new
upstream bot review or claim remote CI approval.

Out-of-scope follow-ups are tracked separately: [zero-decimal scaling](https://github.com/yearn/yearn-metrics/issues/45),
[direct-provider versus synthetic estimates](https://github.com/yearn/yearn-metrics/issues/46),
and [pricing-client/result extraction](https://github.com/yearn/yearn-metrics/issues/47).
Each is recommended for a separate PR. No historical data or selected publication
was changed by the review.

Pre-review heads are retained under `refs/archive/pricing-stack-review-20261020/`
and in `artifacts/pricing-stack-review-20261020/before.bundle`. The original
checkout and its four uncommitted files were preserved. Older recovery notes
below describe their dated operations, not the current open-PR base layout.

## Integrated review after PR #24 merged

PR #24 merged into `main` at `c501ddfc5316839d2abeb14051544fd2383b4e18`.
GitHub retargeted #25 to `main` and restacked all 15 descendants. Every resulting
file tree matched its pre-merge counterpart before this review began.

The local review started at integrated PR #43 and traced defects downward:

| Owner | Corrected behavior |
| --- | --- |
| #25 | Failed canonical-fee exports roll back partial output and finish as failed; retries can complete. |
| #27 | Failed fee-USD exports, including rejected deferral manifests, have the same recovery behavior. |
| #30 | Authentication and configuration errors stop TVL pricing without request fan-out or unavailable-price records. |
| #33 | Legacy catalog imports store integer active flags; real PostgreSQL tests cover import and export recovery. |
| #35 | Price repairs require complete source totals and resume interrupted destination totals. |
| #38 | Effective fee stacks follow the child's chain identity; analytics publication version advances. |
| #43 | Unavailable adapter contributions remain unresolved instead of disappearing; analytics publication version advances again. |

Each fix was inserted into its owning PR and carried through all descendants.
Before the documentation update, the rebuilt integrated tree exactly matched
the reviewed implementation. Focused suites passed at each changed intermediate
PR head (#25: 33; #27: 76; #30: 134; #33: 20; #35: 25; #38: 64 tests).
The final integrated suite passed **901 tests**, including disposable PostgreSQL
schemas, hosted HTTP checks, and all seven frozen local Anvil execution replays.
These are local checks; no new upstream bot review or formal GitHub approval was
requested. PR #38's existing draft and production-readiness limitations remain.

The older analysis jobs' failed-run lifecycle is tracked in
[issue #48](https://github.com/yearn/yearn-metrics/issues/48), for a separate
correctness PR. Existing issues #45–#47 remain separate follow-ups. No deployment,
production ingestion, or selected data publication was performed.

Recovery refs are under `refs/archive/integrated-stack-review-20261020/`.
The pre-merge bundle, post-merge heads, failing regressions, owner checks and
publication receipts are retained in `artifacts/integrated-stack-review-20261020/`.
The original checkout and its four uncommitted files remain untouched.

## October 16 ancestry reconciliation

The hosted API branch contained rewritten copies of the earlier layers,
but not their original commit IDs. All 25 commits through #37 had
patch-equivalent copies in the integration history.

Commit `e182dc6` joins #37's original head `e0896bc` to the hosted API head
`f6d723d`. Its file tree is exactly the same as `f6d723d`; the integrated
#39–#42 work is retained. Commit `5a83aaa` carries that ancestry into #43
and also preserves its existing tree. This documentation update is the
only file change made for the stack repair. No force-push is needed.

Before the repair, all 18 open PR heads and `main` were saved under
`refs/archive/20261016-stack-repair/` in the original checkout. A verified
Git bundle and the original PR descriptions are retained locally under
`artifacts/stack-repair-20261016/`. The original checkout and its unrelated
uncommitted changes were not used to prepare the new commits.

## Recovery and validation

Before the October 12 repair, exact local tips were saved under
`refs/archive/20261012-recent-stack/` for the curation, yBOLD, TVL-overlap,
canonical-history and former effective-fee branches. The pre-repair curation
tip is `5725078`; the former local yBOLD tip is `0f2bd41`.

Earlier recovery tags under `archive/20261009/` and the rebase references under
`refs/archive/20261009-before-main-rebase/` remain intact. To recover the former
fee alias without disturbing its occupied worktree:

```bash
git branch ross/effective-fee-products \
  refs/archive/20261012-recent-stack/ross/effective-fee-products
```

The repaired combined backend passed **652 tests**, with **23 skipped**.
Range comparison verified the five curation/comparison commits retain their
changes; only the obsolete analytics-version assignment was dropped.
