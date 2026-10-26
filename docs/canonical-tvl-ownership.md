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
projected backward. Missing prices, incomplete ownership or mismatched blocks
leave the affected child's `overlap_usd` and `external_tvl_usd` null. Observed
zero debt remains zero even without a child valuation. Unknown positions stay in
diagnostics. Coverage remains limited to known candidates.

Aggregate endpoints retain available-subtotal semantics. They do not certify
complete TVL when some vaults are unavailable. Historical series omit unavailable
vault values rather than inserting zero. Prepared history and constant-price
history preserve those distinctions. Graph deductions sum available accounting
evidence, rather than treating a missing adjusted value as a deduction.

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

## Publication rollout

The manifest adds `ownershipAccounting: all-yearn-v1`, changing the dataset hash
even when the raw runs are unchanged. Old manifests retain their previous
accounting and remain reproducible while retained. Analytics methodology advances
to `powerglove-analytics-23`. Prepared rows/totals must be regenerated for the new
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
