# Owned Powerglove analytics on Postgres

The active stats consumers can use Yearn Data for TVL, fees, reported earnings,
DefiLlama comparison and membership, nested fee stacks and profitability. The API serves
stored observations and prepared analytics from Neon. Collectors acquire external provider
and chain evidence before preparation.

## Prepare and serve

Collection and preparation are explicit writer operations on a deliberately
selected database. Use the [hosted operations guide](../operations/hosted-api.md)
for deployment, reader credentials, consumer routing and the supported local
`serve-pairing --analytics` mode. The [native refresh](../operations/refresh.md)
refreshes owned inputs and prepares coherent analytics as part of routine work.

For a separate staging investigation with existing prepared inputs:

```bash
yearn-data --db staging collect-analytics
yearn-data --db staging prepare-analytics \
  --publication data/staging/fees --tvl-publication data/staging/tvl
```

These commands write staging inputs and preparations. Hosted release selection requires a
separate publication. Inspect
availability and matching financial/TVL IDs before a separate publication.
`--comparison-db` is retired: prepared comparisons need no legacy database.

## Stored data and selection

`analytics_inputs` retains immutable, dated reference, membership and fee
configuration observations. `analytics_publications` retains the prepared
responses, contributing IDs, cutoffs and methodology. `analytics_selection`
selects the standalone analytics publication. The hosted release separately
binds the analytics ID in `api_selection`. The same Postgres store also holds the
imported TVL, fees, reports and prepared daily TVL chart rows.

Known zero rates/amounts are preserved. An unavailable retry retains a previous
known observation and records the retained observation's age (fee entries expose
`retainedFeeConfigurations`, with per-root `feeObservation` provenance); known inputs remain
available. Retained fee configurations preserve the full supported observation,
including its block and contract family, and record the unavailable retry separately.
A changed accountant or rejected verification cannot reuse old supported rates.
Retained external references keep their timestamps and mark comparison availability
partial, including when both protocol requests fail. Source coverage remains partial
when collection fails. A preparation
failure leaves the previous selected publication usable.

Finance route collection accounts for every entry in the declared registered-route
array, independent of key order. Unsupported declarations fail collection explicitly,
preserving incomplete deduction status. Asset references from an external JavaScript
module are retained under `unresolvedFields`; affected contributions stay
unavailable until those assets can be established, while unrelated routes remain
usable. Known-zero debt needs no asset conversion. No asset address is inferred
from a constant name.

`GET /api/analytics/publication` returns the selected metadata. The owned analytics
routes and `/api/comparison` accept `publicationId`; this pins their immutable
result. Powerglove selects once per API origin and pins subsequent analytics
requests. Standalone analytics selection records its own `tvlDatasetId` and `feesDatasetId`.
The hosted API instead uses the analytics ID bound into the coherent hosted
release with those same financial and TVL IDs. Inspect these identities and
observation times; on HTTP 410 reload the hosted selection and related views
together to keep core data and analytics on matching publication IDs.

## Methodology

- DefiLlama totals: collect `yearn-finance` and `yearn-curating` directly from
  `api.llama.fi`, preserving provider timestamps and payload fingerprints. They are
  external reference figures for comparison with local Yearn accounting.
- Membership: evaluate the local catalogue against the frozen finance adapter's
  Kong candidates, exclusions, positive on-chain assets, V1 rules and nested debt
  deductions. Curation uses the current Yearn entry in the upstream curator registry,
  its explicit ERC-4626 vaults and factory creation-owner evidence. The frozen
  source URLs/text/hashes and per-vault blocks/evidence are stored with the input.
  This reconstruction applies the frozen adapter’s rules to locally acquired vault evidence.
  Unknown queue/debt reads or missing creation evidence cannot prove inclusion or
  absence. Finance chain-scope changes require explicit support before collection can
  proceed. Missing strategy-count evidence is unknown;
  an observed zero count remains distinct. Unsupported curation and factory source
  shapes fail collection explicitly.
- Fee configuration: retain Kong's declared fee metadata as diagnostic evidence;
  validate supported current rates through on-chain V2/Tokenized Strategy getters,
  standard Accountant `getVaultConfig(vault)` and Morpho V1/V2 getters. Morpho WAD
  rates become bps. Unsupported or failed configuration reads remain unavailable;
  no legacy default performance-fee estimate is applied.
- Fee stacks: use the current native dated positions, including intermediary
  targets. Cap attributed path capital, bound depth at ten and detect cycles.
  Performance rates compose successive fixed-rate charges with
  `1 - product(1 - rate)` and capital weighting. Annual management rates are summed
  along each path under a common annual/capital basis, then capital-weighted.
  These are configured-rate illustrations: Accountant caps, refunds and differing
  realization times can change actual charged fees. Unsupported or unresolved
  paths have null effective rates. A known child outside the selected snapshot
  is also unresolved: the parent's own configured rates remain in the tree,
  while the combined rate through the missing child remains unavailable. Collect the
  child's dated valuation and fee configuration before reporting that effective
  rate. Capital-exhausted branches are reported under `truncatedAllocations`;
  a child capped by an observed zero TVL has a known zero-capital allocation.
  External root fractions determine the stack
  capital summary; nested roots contribute their external root fractions.
- Profitability: use the trailing 365 UTC days ending at the earlier supported
  financial/TVL cutoff, with equal-duration daily capital observations. The denominator is
  mean observed daily gross contract TVL. Preserve gaps and unsupported prehistory as
  unavailable. Eligibility requires at least 90
  observed days and 80% coverage from the first supported observation; fees before
  supported TVL history make annualized yield unavailable. Complete priced fee
  observations are required for a fee-yield ratio. Annualization uses the supported
  elapsed duration, and both trend halves use the same rules.
- Trends: adjacent half-year periods; at least three reports; changes above/below
  0.005 are improving/declining, otherwise stable. Unsupported comparisons are
  `insufficient_data`. Pricing confidence is high at 80% priced fee coverage,
  medium at 40%, otherwise low; these levels describe priced-fee coverage. Quote accuracy
  requires separate validation.
  Quadrants use the displayed cohort ($10k–$100M current gross TVL) within the
  requested chain scope, with current TVL on the capital axis and time-weighted
  fee yield on the yield axis. High yield is strictly above the cohort median.
  Explicitly observed zero fees remain low yield; missing fee observations are
  unavailable and excluded from classification.
- Fee capture summaries use only vaults with complete fee and gain observations.
  `feeCaptureCoverage` states the contributing and expected vault counts and labels
  a subset partial. The stated cohort defines the summary’s scope.
- Unpriced current vaults remain in comparable coverage as `unavailableTvlVaults`;
  confirmed zero counted TVL is excluded from the positive-capital comparison.
  Fee-stack nodes expose stored contract-family evidence (or null if unknown).
- Chain and category profitability rows include contribution counts for TVL, fees,
  and annualized yield. Partial fee subtotals distinguish contributing vaults from
  vaults with complete fee observations.
- Collector summary counts describe the stored input IDs, including retained
  observations; `attemptCoverage` separately describes the latest acquisition.
- Fees charged at different layers remain distinct. Earnings are incident-adjusted
  contract-level reported gains. Consolidated nested yield requires historical ownership and
  recognition evidence. Summary
  profitability denominators are gross contract capital and are labeled as such;
  headline external TVL retains its existing native deductions.

## Routes and developer checks

| Route | Parameters |
| --- | --- |
| `/api/comparison/defillama-comparable` | `publicationId`, `includeVaultBreakdown=false` (missing list retained) |
| `/api/fees/stack` | `publicationId` |
| `/api/profitability` | `publicationId`, optional `chainId` |
| `/api/comparison` | `publicationId`, optional matching `datasetId` |

Developer coverage is in response availability fields and publication input
metadata. Financial nulls render as unavailable amounts; detailed coverage remains in
developer-facing metadata. Only verified members show a DL indicator. The unused legacy
route fallbacks remain configured, but no active migrated stats request needs them.

Validate the three new sections, aggregate comparison and existing core charts
through the actual Powerglove proxy. Check the selected IDs, browser errors and
request routing. The API must start without a legacy comparison database and
return the prepared values with legacy services inaccessible. Recovery requires verified
retained data or a restorable backup. Verify the serving deployment separately from local
preview checks.

## Missing external chain breakdowns

Current methodology `powerglove-analytics-25` preserves an absent DefiLlama chain entry
as unavailable. A by-chain comparison requires explicit values from both finance
and curation snapshots, including explicit zero where appropriate. Without them,
`defillama` and `difference` are null; protocol-level totals remain separate.
Reprepare analytics to replace previously prepared chain comparisons.

## Dated quote rejection policy

The immutable `inventories/tvl-price-rejections.json` records supported
chain/asset/day/price rejections. Affected histories and matching analytics must
be prepared again after a policy change. No later price or fabricated zero fills
a rejected quote; other known observations still contribute to explicitly known
subtotals. Monthly denominators must use the repaired daily observations and
dated ownership deductions from that preparation. Historical incident dates,
validation counts and monthly figures are retained in the
[analytics rollout record](../archive/rollouts/2026-10-analytics-cutover.md).

[Product configured rates](../accounting/effective-fee-products.md) are a separate
policy from actual paid-fee product membership in [accounting](accounting.md).

[Documentation index](../README.md).
