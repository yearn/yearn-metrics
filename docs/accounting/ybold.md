# yBOLD fee classification

The Ethereum yBOLD allocator (`0x9F4330700a36B29952869fac9b33f45EEdd8A3d8`)
uses the V3 fee mechanism to allocate yield to staked yBOLD. These allocations
are depositor yield, not manager revenue. Underlying strategy fees remain separate.

The verified accountant is `0x53acEBB9470Cfc9D231075154f5dcF1586A4c6fa`.
Its `report()` returns the gain after any loss restoration as `totalFees`;
`distribute()` transfers the accumulated tokens to its configured recipient.
The verified staking recipient is `0x23346B04a7f55b8760E5860AA5A77383D63491cD`.

Source evidence: [deployed accountant verification](https://sourcify.dev/server/v2/contract/1/0x53acEBB9470Cfc9D231075154f5dcF1586A4c6fa?fields=all).
The exact-match runtime hash is
`0xa3098fa22faa347d2cded2cac45f884a69895472d3af03f2d369a4247d477e90`.

## Accounting rule

This rule was introduced in `canonical-fees-8`.

The current `fees.METHOD_VERSION` (`canonical-fees-9`) requires a successful receipt matching the report transaction,
block, log index, strategy and amounts. Historical `accountant()`, `feeRecipient()`
and runtime code are read at the receipt's block hash. Unknown implementations,
recipients or unreconciled amounts remain unresolved instead of falling back to
counting the entire deduction as revenue.

- `reported_total_fees_raw` / `reportedTotalFeesUsd` preserve the raw deduction.
- `staker_distribution_raw` / `stakerDistributionUsd` contain the deduction less
  genuine protocol fees. This measures allocation to the staking recipient, not
  a separate timestamped payment or a new earnings event.
- `total_fees_paid_raw` / `totalFeesPaidUsd` retain genuine protocol fees.
- Manager, management and performance revenue at this allocator layer are zero
  under this verified implementation. This does not zero fees on its strategies.

Reported gains, losses, prices, incident adjustments, and the earnings publication
are unchanged. The chart consumes the corrected fee metric without a vault exclusion.

## Refresh and publication

Normal `index-fees --canonical --version v3` upgrades stale yBOLD rows and collects
evidence for new reports. `recompute-fees formulas --version v3` replays retained
evidence without RPC calls. Regenerate `fee-usd` with the existing financial
cutoff and deferred-event manifest, validate `select-pairing`, and use
`publish-hosted --prepare-analytics` to prepare and select a coherent release.
Keep the TVL publication and earnings run unchanged for a fee-only correction.
Do not edit completed analysis outputs or reuse their dataset IDs.

Receipt fixtures cover zero deductions and positive distributions. Tests also
cover actual protocol fees, rejected evidence, stale-row upgrades, offline replay,
USD valuation, and API aggregation. Full historical evidence and original canonical
rows are retained locally under ignored `artifacts/ybold-fees/` for reconciliation.

## Product reporting and staking catch-up

The yBOLD product comprises the allocator and ysyBOLD staking contract. ysyBOLD
charges the genuine performance fee; its Tokenized Strategy `Reported` events
record that fee separately from the allocator's redistribution. A contract-only
view of yBOLD therefore has zero fees while the product has real staking fees.

The three financial endpoints (`/api/fees`, `/api/fees/vaults`, and
`/api/fees/history`) accept `scope=product`. The default remains `scope=contract`.
Product scope groups both addresses under the yBOLD allocator address, returns
`productId`, `memberAddresses`, and `earningsAddress` on the vault row, and sums
fee events at their original timestamps. Filtering either member address in
product scope selects the complete product. `byContractFamily` preserves the
actual fee charging layer. Product scope does not append an extra aggregate row
to the contract rows, so total fees do not increase when switching scope.

Only the allocator supplies product earnings. Recognizing the same yield at the
staking layer does not create another earnings contribution. Powerglove requests
product scope for comparison rankings, its picker, and filtered history; its
contract-level fee stack retains contract scope.
Acquisition must include the product staking contracts within explicitly verified
block bounds. Do not equate a contract's zero allocator revenue with zero product
fees. See [effective rates](effective-fee-products.md) for configured-rate display.
The [historical reconciliation](../archive/rollouts/ybold-fee-accounting.md)
records past runs and amounts, not current retained releases.

[Documentation index](../README.md).
