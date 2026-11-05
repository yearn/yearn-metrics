# Bidirectional TVL comparison

The category drill-down compares independently checked adapter membership. Net contribution
is calculated separately. Yearn Data deducts ownership from
children; DefiLlama Finance deducts nested debt from parents. A fully invested
parent can therefore contribute zero there without being excluded from scope.

Prepared `comparison-evidence` inputs are pinned to a TVL dataset and close.
`analytics.prepare` includes their content ID in the publication identity and
exposes the result as `reconciliation` on `/api/comparison/defillama-comparable`.
Evidence from another dataset returns unavailable. While retained, prior publications remain
unchanged. These inputs supply a complete replacement for the prior inventory.

The drill-down separates:

- Our valued vaults outside their independently checked scope.
- External valued contributions absent from our valued data. Missing local prices remain
  null; independently absent local observations are separate.
- Positive ownership deductions whose route their adapter does not recognize.
- Routes already handled by both, and unresolved checks.

Reverse contributions use historical DefiLlama token quotes with confidence at
least 0.5 and timestamps within one hour of the close. They are labeled adapter replay
values and carry their reconstruction provenance. Known nested curator shares
are deducted and the pinned upstream bad-debt helper is checked first. Overlap amounts
measure our capped dated deductions. Reconciliation with published protocol totals
additionally depends on dates, prices and attribution. Different dates, prices, rounding,
attribution and unpriced assets can leave a residual.

## Historical example: October 8 close reviewed October 12

At dataset `9f5d1262367eb80c55793f6041c7973d9ecdee383b0d5ae7fb665c48e1de1f57`,
using adapter revision `e5c4535df5ce9193b5f7f350943c4018553a4c23`:

- 22 intermediary/router routes: $30,190,158.28 deducted here; the upstream
  Finance registry contains only the two DAI-1 routes, which are handled separately.
- Five vault-share asset wrappers: $11,305,900.09 deducted here, including staked
  yBOLD and locked yvUSD. The Finance loop retains gross ownership of its vault share assets.
- One cross-chain ownership route: $928,357.17 deducted here (USD yVault to Katana
  vbUSDC). The adapter's nested-debt detection is same-chain.
- Total deductions on those 28 routes: $42,424,415.54. This quantifies a method difference
  at our close. Asset pricing in DefiLlama’s published total requires separate verification.
- OUSD V2/V1 nesting is already handled by the curator adapter. Our comparable
  capped deductions total $5,073,928.20; these routes are already handled by DefiLlama.
- Reverse-price differences include Yearn KAT (about $300,918), Yearn OG WETH on
  Arbitrum (about $854), and OUSD V1/V2 on HyperEVM (about $733 combined after
  removing their internal ownership). Their local prices remain unavailable.

All 929 independently fetched Finance Kong candidates are present in our frame.
Curator discovery covers configured factories and supplements them with verified
creation-owner evidence. The HyperEVM V1 factory scan remains incomplete. Coverage remains
incomplete there, and USD differences remain unresolved for 195 Finance vaults with
unverified external prices.

## Reproduce and publish

`PYTHONPATH=src python scripts/review_tvl_comparison.py ARTIFACT_DIRECTORY`

The directory is a captured evidence bundle with:

- `frame.json`: selected publication identity, timestamp, raw/accounted rows,
  dated positions and catalogue, read from the hosted TVL dataset.
- `kong.json`: full per-chain `origin=yearn` candidate responses, excluding the
  adapter blacklist. Explicit V1 addresses are added separately.
- The pinned Finance, curator registry, curator helper and factory source files
  listed in `sources.json`, plus `morpho-bad-debt.cjs`.
- `independent-discovery.json`, `verified-membership.json`: archive factory scans
  and individual creation-event evidence at the selected close.
- `adapter-replay*.json`: archive liquidity-adapter/share conversion calls.
- `reverse-bad-debt.json`: outputs from executing the pinned upstream bad-debt
  helper at those archive blocks. The reviewer refuses missing/changed haircuts
  until a matching reconciliation has been supplied.
- `prices.json`: cached historical coin responses; if absent, the reviewer obtains
  them from DefiLlama. No external writes occur.

The script checks known source hashes and writes `evidence.json`, including hashes
of its inputs. It fails if independent discovery finds a vault without a matching
archive observation; collect that observation before proceeding. Review new adapter
revisions and update recognition rules explicitly.

To release, insert the reviewed body using
`put_input(conn, 'comparison-evidence', body, 'partial')`, prepare matching analytics,
and select a coherent hosted release through the existing publication workflow.
Matching evidence must include `vaults`, `asOfTimestamp`, `coverage` and `sources`,
with protocol/chain/address identities on vault rows and protocol/status on overlap
checks (plus a known, finite numeric `ourDeductionUsd` for handled or missed checks).
Null, nonnumeric and non-finite deductions are incomplete evidence. Vault `units`
and `replayTvlUsd` must be finite decimal strings when known; null or absent values
remain unresolved observations. Malformed values make reconciliation unavailable. Incomplete
evidence
leaves reconciliation unavailable; analytics preparation and hosted publication
can still complete. Scope exclusions and overlap conclusions require complete matching evidence.
Collect a new evidence bundle for each new TVL dataset, preserving the old bundle’s
identity. No price
repairs or exclusions are made by this comparison workflow.

The current private preview uses a separate read-only response server over the
captured bundle. The captured preview is isolated from production analytics and Neon selection.

## Refresh after the Finance and curation adapter updates

The current collector reads the Yearn entry in `registries/curators.js`. Isolate that entry
before parsing
chain configuration: other curators use the same chain names.

For a new close, capture all upstream files at one commit, then run:

```bash
PYTHONPATH=src python scripts/collect_finance_comparison.py ARTIFACT_DIRECTORY \
  --timestamp UTC_CLOSE_TIMESTAMP
```

This saves current Kong candidates and dated Finance contributions, including
registered route destination checks, ownership caps, converter decimals, wrapper
exclusions, and the verified remote allocation. Missing reads remain unavailable.
Factory discovery, curation adapter/share calls, bad-debt checks and price quotes
must also be refreshed at the same close before running `review_tvl_comparison.py`.
The reviewer accepts only reviewed source hashes, and the new Finance source
requires its dated `finance-replay.json`; it cannot reuse the old recognition-only
replay. Retained factory events can support discovery only with continuous scan
coverage and exactly matching factory ABI and owner filters.

Registry destination corrections also apply when accounting for stored history.
An explicitly superseded, unverified service-registry position contributes no
deduction. A complete replacement must verify the destination at the same block,
date, parent and strategy; derive the corrected child’s amounts from matching verified evidence.
Without that evidence (or observed zero debt), accounting diagnostics remain
incomplete and the complete external-TVL total is null. Known subtotals remain
available, so inspect diagnostics before treating them as final totals. Recollect
affected dates and publish through the normal workflow to complete the repair.
Dated getter-verified old destinations remain valid historical evidence. The
correction policy is captured in new publication manifests; pinned publications
and raw observations retain their original evidence and accounting policy.

[Documentation index](../README.md).

Related tools: `scripts/review_tvl_overlaps.py` and `scripts/review_curation_corrections.py`
build isolated single-close review datasets. Their input/assumption examples are retained in
the [overlap](../archive/rollouts/tvl-overlaps.md) and
[curation](../archive/rollouts/tvl-curation-corrections.md) records. Apply comparison deltas
only to their captured inventory and close.
