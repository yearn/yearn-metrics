# Canonical TVL ownership

New publications include Yearn-curated vaults in the same ownership accounting
as other Yearn vaults. Deposits owned by another counted Yearn vault reduce the
receiving vault's external TVL, whether held directly or through a strategy.

The existing `account()` engine remains the only deduction ledger. Shared holders
share a debt budget; incoming ownership is capped by child TVL and outgoing
ownership by parent TVL. Existing V2/V3, internal curation and bridge deductions
remain in place. A tracked compounder retains both hops; an excluded bridge
parent cannot also deduct its child. Reported `tvl_usd`, raw history, gross category
summary fields and fee rates retain their meanings.

## Evidence and consumers

Every historical frame uses its own dated positions and valuations. Same-chain
holdings must match both selected endpoint blocks; cross-chain positions must
match the dated source and destination blocks. No current ownership ratio is
projected backward. Missing prices leave USD valuations unavailable. Failed
ownership reads or mismatched blocks do not erase a known child's TVL: retain
its gross value less verified deductions, mark `ownership_incomplete`, and leave
the failed position's deduction unavailable. This contribution is provisional
and may include unresolved double counting. Observed zero debt remains zero even
without a child valuation. Unknown positions stay in diagnostics. Coverage remains
limited to known candidates.

Aggregate endpoints retain available-subtotal semantics. They do not certify
complete TVL when some vaults are unavailable. Historical series omit unavailable
vault values rather than inserting zero. Prepared history and constant-price
history preserve those distinctions. Graph deductions sum available accounting
evidence, rather than treating a missing adjusted value as a deduction. Graph
`diagnostics.unavailableNodes` also identifies nodes with incomplete ownership;
their observed gross and provisional adjusted values remain available.

Diagnostic `known_gross_tvl_usd` sums observed gross values, including vaults whose
ownership is incomplete. `known_overlap_usd` sums verified position deductions,
including partial evidence for such vaults. `known_external_tvl_usd` sums only
available vault-level provisional external values. For example, two $100
parents and a $200 child with one verified $30 holding and one unknown holding
produce known gross $400, known overlap $30 and provisional known external $370.
The child retains $170 after the verified deduction, with incomplete ownership
flagged. Public `overlapExcluded` and the position evidence both retain the $30.
The complete external total remains unavailable while diagnostics are incomplete.

`tvl export` uses unified curation deductions by default. `--include-curation`
keeps that default; `--no-include-curation` explicitly selects the separate-product
export convention. Both scopes preserve provisional known capital and flag
incomplete ownership. `tvl.json` records `include_curation` in its run metadata.
The publication retains its captured policy across export choices.

Powerglove's existing contract consumes these values directly:

| Surface | Canonical field |
| --- | --- |
| Headline | `/api/tvl`: `totalTvl` |
| Actual and constant-price history | dated `external_tvl_usd` |
| Adjusted TVL and Fees by Vault | `/api/fees/stack`: `entries[].adjustedTvlUsd` |
| Expanded vault details | audit `countedTvlUsd`; graph `externalTvlUsd`, `upstreamOwnedTvlUsd`, `deductedTvlUsd` |
| Reported gross in expanded details | audit `tvlUsd`; stack root `capitalUsd` |

The frontend needs no new subtraction or address rules. Its existing table and
expanded details consume the new publication's values. yvUSD product grouping
continues to sum canonical member values once; any unavailable member leaves the
combined value unavailable.

Prepared history retains `ownership_incomplete` for each dated row, including
reused dates and quote-repair copies. Refresh receipts distinguish unavailable
amounts from provisional ownership contributions and summarize both in the
`history-availability` warning. Comparable membership responses identify
provisional values under `unavailableTvlVaults` and report partial availability.

## Publication rollout

The manifest adds `ownershipAccounting: all-yearn-v1`, changing the dataset hash
even when the raw runs are unchanged. Accounting changes also advance the
financial source fingerprint; prepare a new dataset with the current history
format before deployment. Preparation adds the ownership-status column to the
chart cache and writes the new immutable rows; retained prepared datasets keep
their original values. Analytics methodology advances
to `powerglove-analytics-25`, which keeps unresolved corrected ownership partial
in the fee view. Reprepare analytics even when the selected source IDs are
unchanged; an older prepared fee stack can otherwise retain a known rate for
unresolved ownership. Prepared rows/totals must be regenerated for the new
TVL identity; the price-repair copy shortcut rejects changed accounting policy.

For an authorized deployment, prepare a new TVL publication and its full dated
history, then prepare matching analytics using the same financial publication.
Review missing evidence before activation; repair historical gaps only through
dated collection. Use the existing `publish-hosted` flow to select a coherent
release and verify its canonical-history consolidation. Verify the selected
release, TVL dataset and analytics IDs through the Powerglove proxy and check the
table, expanded details and both historical charts together. Preserve the normal
backup and recovery process before consolidation retires old publications.

Implementation and local tests do not deploy the API, recollect historical data,
or activate a hosted release.

## Verification

`tests/test_canonical_ownership.py` exercises direct shares, direct strategies,
strategy-routed holdings, shared-holder/child caps, retained nesting hops, changing
dated balances, prepared-history parity, legacy manifests, missing evidence,
observed zero and block mismatches. Existing TVL, bridge, graph, analytics and fee
product tests cover their unchanged behavior. The Powerglove consumer suites
`direct-deposit-fees`, `effective-fee-capital` and `EffectiveFeesTable` validate
canonical values and unavailable handling without a frontend accounting change.

DefiLlama reconciliation requires complete local counted TVL for each compared
scope. Unknown ownership withholds the overall and affected chain/category totals
and differences, with partial availability and explicit unavailable vaults.
Complete scopes remain comparable; raw gross observations remain available.

The TVL summary preserves known subtotals when prices or ownership are missing.
Its `completeness` object labels these as `known-subtotals`, reports `complete` or
`incomplete`, and includes valued/vault counts, unresolved positions, and issues.
The diagnostic scope is always `network`, including chain-filtered summaries:
cross-chain ownership depends on evidence outside the requested chain. A known
subtotal must not be interpreted as a complete total while status is incomplete.
