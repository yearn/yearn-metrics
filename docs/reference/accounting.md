# Accounting contract

This page defines economic meanings used by the current collectors, analyses and
published APIs. API routes are implemented; historical consolidated underlying
yield and treasury attribution remain separate work, not implied by reported P&L.

## Historical earnings scope

`analyze lifetime-yield` includes all stored `strategy_reports`, regardless of a vault's current `active` flag. Use both active and retired history; `active=0` alone would discard active-vault history. Missing vault metadata must not erase a stored report. Raw report data stays unchanged, known incident adjustments apply to analysis outputs, and unavailable prices remain visible in report counts and event values.

Earnings do not require accepted fee accounting. A report's gain/loss can be known while its fee mint still lacks execution evidence. Price, cutoff and discovery coverage must be reported separately from fee acceptance. The existing lifetime-yield price policy can use a DefiLlama fallback; the fee USD policy is Yearn-only. A paired API must declare and align its chosen policy rather than treating the two caches as interchangeable.

The lifetime-yield export is currently an incident-adjusted sum of stored report P&L. It is not yet a consolidation of earnings across nested contracts. Including retired reports fixes historical membership; it does not fix layered recognition.

## Different aggregation rules

| Metric | Aggregation rule | Required boundary |
| --- | --- | --- |
| Reported earnings | Preserve gain/loss recognition at each reporting contract. | V2 vault, V3 allocator and Tokenized Strategy remain identifiable. |
| Consolidated economic yield | Count underlying performance once within the chosen universe. | Establish historical parent/strategy/child ownership and recognition; unknown overlap remains an explicit limitation. |
| Gross fees charged across the stack | Sum distinct accepted charges at every included layer. | Each charge is counted once; carry family and accounting/pricing completeness. |
| Protocol/treasury revenue | Attribute the relevant receipts to the defined recipient universe. | Gross depositor-paid fees are not a substitute for revenue attribution. |
| TVL | Count capital once within the chosen universe. | Deduct only the internal exposure covered by that metric. |

For example, suppose a strategy earns $100 and charges $10. An allocator holding that position recognizes the remaining $90 and charges $9. Gross fees across those two layers are $19, assuming both charges occurred and were accepted. The two reported gains are $100 and $90; adding them gives $190 of report recognition, not $190 of underlying economic yield. Depositor net earnings in this simplified example are $81. Protocol-wide gross yield, vault-reported gain, and depositor net yield are different metrics.

Deduplicate fee representations by their bound charge/event identity (chain, transaction, log/report binding), not by nesting or matching amount. A report and its paired fee evidence can describe one charge. Components are not extra charges: V3 allocator protocol fees are already included in total fees, while Tokenized total fees include protocol and performance components. Recipient distributions substantiate or move an existing charge and must not add it again. Missing or conditional fees never become zero.

The exports continue to retain separate family rollups. A combined gross-fee view is a consumer projection with an explicit inclusion scope; it does not change existing accounting tables or formulas. Yield consolidation must also handle nesting within the vault universe, such as V2 routers into V3 vaults; merely excluding Tokenized Strategy earnings is insufficient.

## Implementation and remaining boundaries

| Capability | Boundary |
| --- | --- |
| Core fees/earnings, TVL, fee stacks, profitability and comparison | Implemented in Yearn Data; serving reads prepared data. See [API contracts](api.md) and [analytics methodology](analytics.md). |
| Active and retired history | Retained within the discovered inventory; no claim of exhaustive historical discovery. |
| Consolidated underlying yield across nested contracts | Not established by adding reported gains or by applying today's TVL ownership to historical flows. |
| Protocol/treasury revenue and donations | Require separate recipient/transfer attribution; gross charged fees are not treasury receipts. |
| Historical fee-configuration backfill and unknown evidence repair | Separate explicitly scoped acquisition work; current observed rates are not historical realized fees. |

Dated TVL relationships identify capital at a time, not earnings flows over a
period. Consolidated yield would additionally need historical ownership intervals,
reporting delays, downstream retained fees, losses/recoveries and a valuation
policy. Missing evidence must remain unknown or explicitly estimated.

## Acceptance checks

- Active and retired V2/V3 reports appear together in lifetime exports and rollups; retiring a vault does not remove earlier P&L. Retired incident reports still use the existing adjustments. Unpriced retired reports remain counted with null event USD values.
- Separate nested strategy and allocator charges both contribute once to the defined gross-fees metric; paired fee evidence, components and distributions do not create additional charges.
- Nested $100/$90 gains are retained as reported values but not labeled as $190 consolidated economic yield. V2-to-V3 nesting, partial ownership and delayed parent recognition are represented in the consolidation specification.
- Missing relationship/history evidence cannot certify consolidated yield. Yearn-data retains snapshot cutoff, pricing/discovery diagnostics and repair targets. Powerglove can render the available amounts without public diagnostic counters or warnings.

## Product policies

- [yvUSD](../accounting/yvusd.md): actual fees, locker redistribution and one earnings leg.
- [yBOLD](../accounting/ybold.md): allocator distributions and actual staking fees.
- [yETH](../accounting/yeth.md): creditor recovery, protocol fees and product earnings.
- [Effective configured rates](../accounting/effective-fee-products.md): a separate policy from realized paid fees.

The original [pairing](../archive/design/powerglove-pairing-requirements.md) and
[cutover requirements](../archive/design/powerglove-remaining-cutover-requirements.md)
retain approved scope and detailed acceptance criteria. Their old starting
branches, local paths and rollout sequencing are historical.

[Documentation index](../README.md).
