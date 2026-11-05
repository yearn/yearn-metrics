> Historical reference captured from `docs/powerglove-analytics.md` at `ef03f16`.
> This records a past design, measurement or rollout; its ports, IDs, branch
> states and rollback availability are not current operating instructions.
> Current guidance: [docs/reference/analytics.md](../../reference/analytics.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/powerglove-analytics.md).

## Local cutover validation

The selected publication `574c64f1944806a262e1f69fa454fb3a1574fa870b8c1f266792d1e585aadeff`
contains 45 nested stacks (33 with supported effective rates), 1,023 profitability
records (547 eligible for fee-yield calculations), and 510 confirmed included / 15
confirmed missing / four unknown currently counted vault membership records.
Source gaps remain developer work; they do not require either legacy service.

The four unknown membership records are Ethereum vaults
`0x71955515adf20cbdc699b8bc556fc7fd726b31b0`,
`0xb98df7163e61bf053564bde010985f67279bbcec`,
`0xcb550a6d4c8e3517a939bc79d0c7093eb7cf56b5`, and
`0xe11ba472f74869176652c35d30db89854b5ae84d`. Their queue/debt reads are unavailable
under the supported finance-adapter ABI. The other unavailable stack rates are
identified in stored configuration and allocation coverage. Do not replace them
with zero or infer DefiLlama inclusion from their absence in the missing list.

Validation passed: 508 backend tests, 15 Postgres transport/integration tests using
disposable Neon schemas, eight affected frontend tests, TypeScript checks,
production build and browser checks of the three new sections and comparison.
The new API was started with `--analytics` and no `--comparison-db` argument. A separate browser check also passed with both legacy proxy targets deliberately
set to an unreachable port. The preview proxy uses this API for all actively
consumed TVL/fees stats routes.
Browser receipts and screenshots are retained locally under
`artifacts/owned-analytics/`; no private preview addresses belong in public PRs.


## Quadrant and monthly denominator correction

The earlier classifier treated missing fee observations as zero and calculated
thresholds across retired and tiny vaults that were absent from the scatter plot.
This could produce a zero median and classify every observed yield as high. The
current methodology excludes unavailable observations, keeps genuine zero fees
low, and uses the displayed capital cohort for the thresholds.

The exact additional quote rejections frozen in
`inventories/tvl-price-rejections.json` cover 28 asset-days:

- 19 Ethereum Curve-related quotes at the December 20, 2023 UTC close and one
  yETH LP quote at the January 29, 2026 close, with reason
  `provider_quote_inconsistent_with_historical_pool_state`.
- Eight quotes for Ethereum asset `0x3b3ac5386837dc563660fb6a0937dfaa5924333b`
  (chain 1), at each daily UTC close from June 2 through June 9, 2026, with reason
  `provider_quote_inconsistent_with_historical_pool_reserves`.

Historical supply, reserves, virtual prices and adjacent observations are retained
as evidence. No later price or fabricated zero replaces them. These are additional
asset-days beyond the yBUSD observations in yearn/yearn-prices issue #61; canonical
upstream quote repair remains necessary.

The repaired publication recalculates accounting and nesting deductions for those
dates and reuses unaffected immutable chart rows. December 2023's average known
external TVL is about $329M, December 2024's is about $268M (no comparable outlier),
and January 2026's is about $562M. The publication preserves missing asset values
as unavailable while serving the other known observations.

June 2026 is also affected: its monthly denominator must use the repaired daily
external-TVL observations for June 2–9, including recomputed nesting deductions.
The rejected asset values remain unavailable; other known values still contribute
to the monthly average. A publication prepared before those eight rejections must
be repaired and its analytics prepared again before using that denominator. No
verified June aggregate is quoted here; the three historical monthly figures above
do not establish June coverage or justify retaining a pre-repair June denominator.
