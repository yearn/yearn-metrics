# Accounting contract

This page defines economic meanings used by the current collectors, analyses and
published APIs. API routes are implemented; historical consolidated underlying yield
requires ownership and recognition evidence, and treasury attribution requires recipient
evidence.

## Historical earnings scope

`analyze lifetime-yield` includes all stored `strategy_reports`, regardless of a vault's
current `active` flag. Use both active and retired history; `active=0` alone would discard
active-vault history. Retain stored reports when vault metadata is missing. Raw report data
stays unchanged, known incident adjustments apply to analysis outputs, and unavailable
prices remain visible in report counts and event values.

Earnings and fee acceptance have independent evidence requirements. A report's gain/loss can
be known while its fee mint still lacks execution evidence. Price, cutoff and discovery
coverage must be reported separately from fee acceptance. The existing lifetime-yield price
policy can use a DefiLlama fallback; the fee USD policy is Yearn-only. A paired API must
declare its chosen price policy and align both metrics to it.

The lifetime-yield export is an incident-adjusted sum of stored report P&L across active and
retired contracts. Consolidating nested earnings additionally requires historical ownership
and recognition evidence.

## Different aggregation rules

| Metric | Aggregation rule | Required boundary |
| --- | --- | --- |
| Reported earnings | Preserve gain/loss recognition at each reporting contract. | V2 vault, V3 allocator and Tokenized Strategy remain identifiable. |
| Consolidated economic yield | Count underlying performance once within the chosen universe. | Establish historical parent/strategy/child ownership and recognition; unknown overlap remains an explicit limitation. |
| Gross fees charged across the stack | Sum distinct accepted charges at every included layer. | Each charge is counted once; carry family and accounting/pricing completeness. |
| Protocol/treasury revenue | Attribute the relevant receipts to the defined recipient universe. | Establish revenue through recipient and transfer attribution. |
| TVL | Count capital once within the chosen universe. | Deduct only the internal exposure covered by that metric. |

For example, suppose a strategy earns $100 and charges $10. An allocator holding that
position recognizes the remaining $90 and charges $9. Gross fees across those two layers are
$19, assuming both charges occurred and were accepted. The two reported gains are $100 and
$90, totaling $190 of contract-level report recognition. The underlying strategy earned $100
before the two fee charges. Depositor net earnings in this simplified example are $81.
Protocol-wide gross yield, vault-reported gain, and depositor net yield are different
metrics.

Deduplicate fee representations by their bound charge/event identity (chain, transaction,
log/report binding). A report and its paired fee evidence can describe one charge. V3
allocator total fees include protocol fees; Tokenized total fees include protocol and
performance components. Recipient distributions substantiate or move an existing charge.
Count that charge once, and retain missing or conditional fees as unavailable.

The exports continue to retain separate family rollups. A combined gross-fee view projects
the existing accounting tables and formulas under an explicit inclusion scope. Yield
consolidation must also handle nesting within the vault universe, such as V2 routers into V3
vaults; merely excluding Tokenized Strategy earnings is insufficient.

## Implementation and remaining boundaries

| Capability | Boundary |
| --- | --- |
| Core fees/earnings, TVL, fee stacks, profitability and comparison | Implemented in Yearn Data; serving reads prepared data. See [API contracts](api.md) and [analytics methodology](analytics.md). |
| Active and retired history | Retained within the discovered inventory; discovery coverage defines the historical boundary. |
| Consolidated underlying yield across nested contracts | Requires historical ownership intervals and a policy for recognizing underlying earnings once. |
| Protocol/treasury revenue and donations | Require recipient and transfer attribution for the defined treasury universe. |
| Historical fee-configuration backfill and unknown evidence repair | Require explicitly scoped acquisition of dated configuration and execution evidence. |

Dated TVL relationships identify capital at a point in time. Consolidated yield would
additionally need historical ownership intervals,
reporting delays, downstream retained fees, losses/recoveries and a valuation
policy. Missing evidence must remain unknown or explicitly estimated.

## Acceptance checks

- Active and retired V2/V3 reports appear together in lifetime exports and rollups; earlier
  P&L remains included after retirement. Retired incident reports still use the existing
  adjustments. Unpriced retired reports remain counted with null event USD values.
- Separate nested strategy and allocator charges both contribute once to the defined
  gross-fees metric; paired evidence, components and distributions describe their bound
  charge.
- Nested $100/$90 gains retain their contract-level reported labels and recognition scope.
  V2-to-V3 nesting, partial ownership and delayed parent recognition are represented in the
  consolidation specification.
- Consolidated yield requires complete relationship and historical recognition evidence.
  Yearn-data retains snapshot cutoff, pricing/discovery diagnostics and repair targets.
  Powerglove can render the available amounts without public diagnostic counters or
  warnings.

## Product policies

- [yvUSD](../accounting/yvusd.md): actual fees, locker redistribution and one earnings leg.
- [yBOLD](../accounting/ybold.md): allocator distributions and actual staking fees.
- [yETH](../accounting/yeth.md): creditor recovery, protocol fees and product earnings.
- [Effective configured rates](../accounting/effective-fee-products.md): a separate policy
  from realized paid fees.

The original [pairing](../archive/design/powerglove-pairing-requirements.md) and
[cutover requirements](../archive/design/powerglove-remaining-cutover-requirements.md)
retain approved scope and detailed acceptance criteria. Their old starting
branches, local paths and rollout sequencing are historical.

[Documentation index](../README.md).
