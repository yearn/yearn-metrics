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

## Supported dated relationships

- **Allocator → tokenized strategy:** use the allocator's recorded debt, capped
  by the strategy's dated TVL. Allocators need not hold tokenized strategy shares.
- **Intermediary → child vault:** measure `balanceOf(holder) / totalSupply` at
  that block, multiply by child TVL, and cap by the parent's recorded debt.
- **Compounder → child vault:** retain this as a second nesting level. Use the
  compounder's asset value as its budget and measure its child shares.
- **Vault with a vault-share asset:** value those shares from the dated child,
  including when the price provider lacks a share-token price. Ownership uses
  share-denominated `totalAssets`, including deployed shares, rather than only
  the wrapper's idle wallet balance.
- **Registered cross-chain strategy:** verify both remote-counterpart getters
  and the destination vault at the two dated blocks. Cap remote share ownership
  by source debt and both vaults' TVL. Both chains must be included in collection;
  source and destination accounting can differ because bridge reports lag.
- **Morpho V2 adapters:** verify historical `isAdapter` membership and read
  `realAssets()` before valuing the adapter's child holdings. See the
  [Morpho contract reference](https://docs.morpho.org/developers/contracts/morpho-vaults-v2/)
  and [adapter interface](https://github.com/morpho-org/vault-v2/blob/main/src/interfaces/IAdapter.sol).

V2 and V3 use their different `strategies(address)` layouts. A holder shared by
several parents is counted once and split by parent debt. Several child positions
share the same debt budget. Incoming deductions cannot exceed child TVL, and
outgoing deductions cannot exceed parent TVL.

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
evidence, rather than treating a missing adjusted value as a deduction. Graph
`diagnostics.unavailableNodes` also identifies nodes with incomplete ownership;
their observed `rawTvlUsd` remains available while adjusted fields remain null.

Diagnostic `known_gross_tvl_usd` sums observed gross values, including vaults whose
ownership is incomplete. `known_overlap_usd` sums verified position deductions,
including partial evidence for such vaults. `known_external_tvl_usd` sums only
available vault-level external values. These are independently known subtotals,
not a reconciled equation when ownership is incomplete. For example, two $100
parents and a $200 child with one verified $30 holding and one unknown holding
produce known gross $400, known overlap $30 and known external $200. The child's
full external value and overlap remain null; its missing value is not a $200
deduction. Public `overlapExcluded` sums only available vault-level overlap values
($0 in this example), while the position evidence retains the verified $30.

`tvl export` uses unified curation deductions by default. `--include-curation`
keeps that default; `--no-include-curation` explicitly selects the separate-product
export convention, including its best-effort ownership availability. `tvl.json`
records both `include_curation` and `require_complete_ownership` in its run
metadata. This export switch does not change a publication's policy.

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

## Preparing a publication under this policy

The manifest adds `ownershipAccounting: all-yearn-v1`, changing the dataset hash
even when the raw runs are unchanged. Old manifests retain their previous
accounting and remain reproducible while retained. The current analytics methodology
`powerglove-analytics-25` keeps unresolved corrected ownership partial in the fee view. Reprepare analytics even when the selected source IDs are
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

[Documentation index](../README.md).

## Explicit reporting scope and corrected destinations

Four Turtle Club Katana predeposit contracts were classified as curation by the
old explicit extra-product list. They are Yearn V3.0.4 contracts. Their inclusion
was a reporting mistake, and the approved policy excludes them from **all
historical TVL**, not merely from dates after migration.

`inventories/tvl-vault-policy.json` records their V3 classification and exclusion.
Discovery corrects retained catalogue records even when the upstream omits these
retired vaults; generic enrichment cannot restore the old category. Publication
normalizes the catalogue without writing to the source database. Accounting
removes these vaults and their incoming/outgoing edges before computing totals,
so an excluded parent cannot also deduct a child. Stored balances are preserved.
This is an explicit reporting exclusion, not a claim about a migration date.

Curation V2 → V1 holdings reduce curation TVL. At the time of this rollout,
Vaults → Curation holdings retained the separate-product-layer headline policy.
New `all-yearn-v1` publications supersede that convention with
[canonical ownership accounting](tvl-accounting.md).
Missing prices remain unavailable and make internal overlap accounting incomplete.

New manifests pin `vaultPolicies` and `deductInternalCuration`. Their presence
changes the dataset hash, requiring new prepared history and matching analytics.
Existing manifests lacking these fields retain their previous scope and accounting,
including when rebuilding their chart cache. The raw summary fields (`curationTvl`,
`tvlByCategory`) retain their existing gross semantics; external history, graph,
comparison and total TVL use deductions.
Current default ownership is unified; references to separate curation in the
[original rollout](../archive/rollouts/tvl-curation-corrections.md) describe the
prior convention. Policy exclusions remove affected vaults/edges before bridge
and ownership deductions; stored observations remain evidence.

Graph flow fields and display filters are defined in [API contracts](api.md#tvl-graph).
Historical comparison values are [dated evidence](../archive/rollouts/tvl-overlaps.md),
not deductions to subtract from a different inventory, price set or close.
