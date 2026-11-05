# Current fee entries for yBOLD and yvUSD

The Effective Fees table distinguishes a verified fee at the selected vault from
an unavailable combined rate across all of its underlying allocations. For own-layer entries, a partial
allocation history must not erase a known contract fee. The combined yvUSD product
rate includes downstream allocations and is unavailable when funded member rates
or member capital are unknown. `feeAvailability` tracks
own-fee support independently of the stack's `availability`.

## Source and display rules

- Staked yBOLD (`0x23346b04a7f55b8760e5860aa5a77383d63491cd`) is named **yBOLD**
  in fee views. Its onchain `performanceFee()` returns 1000 basis points (10%).
- The base vault (`0x9f4330700a36b29952869fac9b33f45eedd8a3d8`) is named
  **Unstaked yBOLD**. Verify its accountant's runtime hash and staking recipient
  before treating the accountant's routing as a distribution rather than a fee.
- **yvUSD** combines the currently visible unlocked and locked entries. Add their
  **external TVL**, which already deducts upstream ownership. Do not add the
  locked vault's gross TVL to the unlocked vault's gross TVL.
- The yvUSD accountant exposes `feeConfig()`, not the standard Accountant's
  `getVaultConfig(address)`. Verify its runtime and underlying asset before
  reading management and performance fees. The locker bonus is a distribution,
  not an additional fee.
- The combined yvUSD row displays member downstream effective rates weighted by
  external TVL, independently for performance and management fees. Unknown rates
  on funded members, unknown capital, or zero total external capital make the
  corresponding product rate unavailable. Zero-weight members do not invalidate
  a rate when other members supply known positive external capital. When all
  external capital is known zero, adjusted TVL remains zero but rates are unavailable. Individual roots retain
  their own fees. If no unlocked yvUSD entry exists (for example, its gross TVL
  is missing), visible lockers remain standalone own-fee entries, even with
  incomplete allocations. A present yvUSD entry with unknown external capital
  remains grouped, with unavailable product rates and adjusted TVL; component
  roots still expose their own verified fees.
  Historical paid fees and the product earnings leg follow [yvUSD accounting](yvusd.md).

`/api/fees/stack` retains raw `chains` and their calculated rates. Its canonical
`entries` provide names, component roots, adjusted TVL and `feeRateBasis` (`stack`,
`vault` or `product`). Powerglove uses those entries directly, with no product
address rules. Both yvUSD roots remain available to inspect; their allocated
capital is not additive across layers. Historical product earnings and fees use
the existing `scope=product` rules and are unchanged by this display policy.

Product-accountant failures are recorded in the stored `fee-config` input.
`failureKind: acquisition_failed` identifies a provider error or missing getter
result; `failureReason` records only the exception type. `verification_rejected`
identifies evidence that contradicts the pinned accountant, runtime hash,
recipient or asset, with the failed check recorded as its reason. Both leave the
current rate unavailable when no compatible prior verified configuration exists.
An acquisition failure may retain the last known rate for the same accountant;
verification rejection or an accountant change never retains that rate. Served
fee-stack roots expose `feeObservation`, and each fee entry lists
`retainedFeeConfigurations` (chain/address, original observation time, source
input ID, retry time and failure kind), including downstream rates used by the
combined yvUSD entry. An empty list means no explicitly retained configuration
was used. Retained rates are last-known observations, not fresh RPC evidence.

## Standalone direct-deposit entries

Canonical `entries` include every positive, priced vault in the selected TVL
snapshot, even if it has no allocation into another cataloged vault. A factory
vault's ordinary strategy does not have to be a recognized child vault for the
factory vault to appear. Standalone entries use the existing fee configuration
and external TVL; unavailable fee configurations still produce unavailable rates.
Raw `chains` and their summary statistics continue to describe nested stacks.
Existing product grouping, nested breakdowns and historical fee/earnings data
are preserved. Zero-TVL and unpriced standalone vaults retain their existing
eligibility; historical pricing repair is a separate task.

See [product distribution evidence](../reference/fee-accounting.md#product-distribution-evidence)
for realized-fee valuation, receipt rejection and replay.

## Preparing a changed rate policy

Reprepare analytics against matching financial and TVL publications before serving
changed rate semantics; do not edit an immutable prepared body in place. This
configured-rate change alone does not require recollecting actual paid fees or
rebuilding TVL history. Keep the publication's canonical ownership policy intact.
Use [hosted publication](../operations/hosted-api.md) and the
[nightly handoff](../operations/nightly.md#required-handoff-after-a-manual-publication)
when accepting a separately reviewed manual publication.

The [earlier product rollouts](../archive/rollouts/effective-fee-products.md)
retain methodology versions and reconciliations as historical evidence.

[Documentation index](../README.md).
