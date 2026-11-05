> Historical reference captured from `docs/powerglove-accounting-contract.md` at `ef03f16`.
> This record captures a past design, measurement or rollout. Use the current
> guides and live checks for ports, IDs, branch state and recovery options.
> Current guidance: [docs/reference/accounting.md](../../reference/accounting.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/powerglove-accounting-contract.md).

# Earnings and fee accounting for Powerglove

Updated October 5, 2026. This defines the proposed integration boundary. API implementation
and historical yield consolidation require their own delivery and validation.

For the first pairing's observable behavior, scope and acceptance checks, see the [pairing
requirements](../design/powerglove-pairing-requirements.md). Yearn-data retains
completeness, reasons, provenance and reporting cutoffs for developer diagnosis and repair.
Powerglove receives the published financial values; public gap counters, quality warnings
and cutoff notices are not required.

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

## What to reuse from yearn-tvl-service

Inspected local `yearn-tvl-service` branch `codex/yearn-price-prod-benchmark` at
`fb1b561579e7e4855916c81726dbb81ed48dee0f`, read-only. This identifies the reference code.
Establish the service’s deployed revision and live coverage through operational checks.

- `packages/api/src/services/tvl.ts`: `computeOverlap()` detects direct strategy-to-vault
  allocations and explicit intermediary mappings. The flow graph retains
  parent/strategy/child relationships and provenance, including retired V2 router paths.
- `packages/shared/src/strategy-overlaps.ts`: direct, intermediary, V2-to-V3 router and
  cross-chain mappings demonstrate that relationships are not a simple V2-versus-V3 split.
  Some current V2 router values are explicitly source-vault-TVL estimates.
- `packages/api/src/services/tvl-history.ts`: `computeOwnershipPoints()` uses historical
  block-specific `balanceOf(holder)` and `totalSupply()` values;
  `aggregateExternalTvlHistory()` deducts owned child TVL at each timestamp.
- `packages/shared/src/curation-products.ts`: defines the included product universe and
  exposes gross TVL, known internal overlap, resulting TVL, and unresolved mapping
  limitations.

Reuse the relationship identities, dated evidence, explicit universe and
gross/overlap/result distinction. Use separate consolidation policies for TVL stocks at a
timestamp and earnings flows recognized over periods.
Today's allocation or a retirement/migration mapping cannot establish historical earnings
overlap. A missing archive observation leaves historical ownership unknown.

Before claiming consolidated yield, define the accounting boundary (gross underlying yield,
vault-recognized P&L, or depositor net), historical ownership intervals, fees retained
downstream, reporting delays, losses/recoveries, and asset/USD valuation policy. Keep
unresolved or estimated attribution distinct from verified adjustments. Preserve the
original reports and adjustment evidence so the resulting subtotal can be audited.

## Powerglove handoff

Powerglove `codex/qtov-on-improved-fee-data@04a1a30586d17a3b3da86ccdd30ecf2560a2435a`
requests core fee summary, monthly history and vault rows, alongside optional
fee-stack/profitability analytics.

1. Expose core JSON views from a pinned completed snapshot, using consistent
   time/chain/family filters and cutoff. Include active and retired history. Retain snapshot
   identity and independent accounting, pricing and discovery diagnostics in yearn-data
   publication records.
2. Define earnings as incident-adjusted reported P&L until historical consolidation is
   supported. Keep that methodology in yearn-data and keep per-family/per-vault financial
   views available. Gross fees across layers include each distinct accepted charge once.
3. Preserve raw and incident-adjusted earnings and independent known/unknown counts in
   yearn-data. Derive report P&L from the full report universe, independently of fee
   acceptance. The
   display API can serve available aggregate amounts while developer records retain the
   missing-source diagnostics.
4. Preserve the existing fee-stack/profitability and TVL features on their current sources
   during the core migration, separating routing as needed. Rebuilding those analytics in
   yearn-data is later work. TVL relationships can inform historical earnings attribution.

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

Base branch `ross/pr-08-fantom-replay@5fcea03f2803d6d4fed761aaea1d03ddb9d16965` already
includes all stored reports and supports the closed-day earnings cutoff. The continuation
branch `ross/powerglove-accounting` adds accounting documentation and regression coverage
without reapplying the older active-filter patch. JSON serving, shared paired valuation and
historical yield consolidation remain follow-up work.
