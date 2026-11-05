# Published API contracts

## Publication identity and availability

`GET /api/publication` identifies the hosted `releaseId`, `feesDatasetId`,
`tvlDatasetId` and `analyticsPublicationId`. Hosted selection binds these inputs
coherently. Select once, pin related requests, validate their returned IDs and
cutoffs, and reload the selection together on HTTP 410. An obsolete ID is not a
backup or a request to mix old financial values with new TVL/analytics.

Amounts are exact decimal strings or null unless a particular field states
otherwise. Available subtotals, genuine zero, unavailable evidence, retained
observations and estimates remain distinct. Parameter units, scope and half-open
UTC cutoffs are part of the contract. The API does not fetch current chain state.

## Core fees and reported earnings

| Path | Result |
| --- | --- |
| `/api/fees` | Fee and incident-adjusted reported-earnings amounts, with fee family breakdown. |
| `/api/fees/history` | Ordered monthly fee/earnings buckets. |
| `/api/fees/vaults` | Chain/address-keyed vault rows and their amounts. |

Filters are `since`, exclusive `until`, `chainId` (one ID or a comma-separated list), and `vaultAddress`. History supports `interval=monthly` (the unchanged default) or `interval=weekly`. Monetary values are decimal strings or null. A wholly unavailable amount is not converted to zero. Unknown chains and invalid parameters produce normal request errors.

`vaultAddress` selects one contract and requires exactly one `chainId`. Addresses are validated as 20-byte hex values and matched case-insensitively. The same selector applies before aggregation to all three views. A contract absent from the selected dataset on that chain produces HTTP 400; a known contract with no reports in the requested range returns empty history and unavailable totals. Missing names do not prevent selection.

Weekly buckets use Monday 00:00 UTC through the following Monday, with an exclusive end. Their `period` is the week-start date (`YYYY-MM-DD`), and `startTimestamp` / `endTimestamp` give those calendar boundaries in Unix seconds. They are sorted by week start. Existing monthly buckets retain their `YYYY-MM` periods and response shape.

Amounts are filtered by `[since, until)` and the selected dataset cutoff before grouping. A first or last weekly bucket can therefore contain only part of its calendar week; its boundary fields describe the calendar week, not additional observations. Empty calendar weeks are not fabricated. Weekly earnings remain reports recognized during the period rather than continuously accrued returns.

For example, these requests describe one vault's amounts and weekly history:

```text
/api/fees?chainId=1&vaultAddress=0x...&since=START&until=END
/api/fees/history?chainId=1&vaultAddress=0x...&interval=weekly&since=START&until=END&datasetId=SELECTED_ID
/api/fees/vaults?chainId=1&vaultAddress=0x...&since=START&until=END&datasetId=SELECTED_ID
```

Replace the placeholders with a full address, Unix timestamps and the summary's dataset ID. Vault filtering counts that contract's own accepted fee charges and incident-adjusted reported P&L. It does not consolidate children into the parent or attribute downstream charges to historical ownership. Tokenized Strategy fees remain available independently of main vault earnings.

Each response carries an opaque `datasetId`. Powerglove uses the summary's ID in subsequent history/vault requests, so a manual publication change cannot mix datasets. That identifier is not displayed. Source reasons, price provenance, coverage counts and reporting cutoffs remain in developer publication records and the existing analysis exports.

If the production frontend calls a separate API origin, pass its exact origin with `--cors-origin`. Otherwise use a same-origin reverse proxy. No Yearn Prices or RPC credentials belong in the display contract.

### Product scope

`scope=contract` is the default. `scope=product` groups the declared members once,
retains `memberAddresses` and `earningsAddress`, and preserves the actual charging
contract families. Filtering a member selects its complete product under that
scope. Product membership does not imply every member supplies an additional
underlying earnings contribution. See [product accounting](accounting.md#product-policies).

## Grouped fee vault histories

`GET /api/fees/vaults/history` accepts the existing fee filters: `datasetId`,
`scope=contract|product`, `chainId`, `vaultAddress`, `since`, `until`, and
`interval=monthly|weekly`. It returns the selected `datasetId`,
`aggregationScope`, `interval`, `dataCutoffTimestamp`, and ordered `buckets`.
Each bucket contains `period`, `startTimestamp`, `endTimestamp`, `count`, and
`vaults`. Each vault retains `chainId`, `address`, `name`, and `contractFamily`,
plus `productId`, `memberAddresses`, and `earningsAddress` when applicable. Its
financial fields are only `totalFeesPaidUsd` and
`lifetimeEarnings: {netYieldUsd}`; each value remains an exact decimal string or
null. This compact shape avoids transferring unused fee components, earnings
components, and contract-family subtotals for every period. The existing
`/api/fees/vaults` response keeps its full row shape.
The broad requests used by the charts and monthly table read published source
rows once, replacing a separate vault query for each period. A product request
filtered to a member address retains the existing per-member expansion reads.

Buckets use UTC calendar months or Monday-based weeks. Explicit `since` and
`until` clip the first and last bucket bounds, and event inclusion retains the
existing half-open range capped at the immutable publication cutoff. With no
`since`, the first bucket starts at the earliest matching event; with no `until`,
the last bucket ends at the cutoff. Empty buckets within that range contain
`count: 0` and `vaults: []`, rather than fabricated numeric amounts. Requests
starting at or after the cutoff still fail validation. An explicit `until`
beyond the cutoff preserves the final publication bucket's calendar identity,
but emits no later calendar buckets. Even an arbitrarily large `until` cannot
create an unbounded future calendar. `dataCutoffTimestamp` distinguishes a
partially observed final bucket from a complete period; consumers must not
interpret wholly unobserved requested periods as genuine zero.

Amounts retain the existing Decimal precision and source order within each
bucket. Product membership, designated earnings legs, and unavailable versus
genuine-zero values retain the existing policies. Compact rows calculate only
the two amounts consumed by these views; they do not calculate unused subtotals. No
monthly totals are recombined to produce financial sums. This endpoint requires
no schema migration, publication backfill, or additional database permissions.
It uses the existing stale-pin and mid-request publication-change HTTP 410
checks and pinned-response cache policy.

Release the backend route before enabling a consumer that uses it. A consumer
must validate the dataset, aggregation scope, interval, and exact requested
bucket bounds; missing or malformed buckets are unavailable data. Reload the
selection on HTTP 410. Older backends return 404 for this new route.

## Routes and accounting

| Route | Stored result |
| --- | --- |
| `/api/tvl` | Current summary, chains, categories, retired vaults and deductions |
| `/api/tvl/history/runs/latest` | Sampled historical TVL |
| `/api/tvl/history/runs/latest/constant-price` | Actual and constant-price history |
| `/api/tvl/curation-products` | Morpho and positively identified V3 allocator products |
| `/api/tvl/graph` | Dated vault/holder/child flows with the same TVL accounting |
| `/api/audit/tree` | Dated vault/strategy positions and current bridge exclusions |

`/api/comparison` belongs to the prepared `--analytics` surface; it is not
a TVL pairing route. See [owned analytics comparison](analytics.md)
for its response contract and `publicationId` pin. Without `--analytics`, this
server returns 404 for comparison requests.

Filters include `datasetId`, `chainId`, inclusive `from`/`to`, `groupBy`
(`chain`, `category`, `type`, `vault`), `mode` (`external`, `raw`) and `interval`
(`daily`, `3day`, `weekly`). History retains the last observed stock in each bucket;
it does not sum daily TVL. Empty dates and artificial zero baselines are not added.
The legacy `includeCurrent` parameter is accepted; current means the latest stored
close, never a live RPC snapshot.

Public history requests are limited to 50,000 series/date points and 8 MiB of
serialized JSON, including reference metadata. Vault-grouped
requests are also limited to 366 sampled dates. Exceeding either limit returns
HTTP 400 with guidance to narrow the time range or grouping, regardless of full
or chart format or `top`. These are serving limits; offline dataset preparation
and analytics can still process complete history.

For vaults within the publication reporting scope, dated bridge exclusion
membership is retained even when valuation is unavailable.
Summary, audit and graph bridge totals return null when exclusions exist but none
can be valued, and zero when there are no exclusions. Mixed scopes sum known
values, matching other TVL aggregates; audit and graph lists retain unvalued
excluded vaults with null `tvlUsd`.

Native accounting caps verified nested holdings by both parent and child TVL.
The graph accepts `chainId`, `category`, `includeRetired`, and `datasetId`.
See [TVL overlap conditions](api.md) for its flow-versus-deduction fields,
router and wrapper corrections, cross-chain verification, and review procedure.
New publications include curated vaults in the same canonical ownership ledger:
Yearn-owned deposits reduce the receiving vault's external TVL, including holdings
routed through strategies. The manifest pins this as `ownershipAccounting:
all-yearn-v1`; older publications retain their separate curation-layer convention.
See [canonical ownership](tvl-accounting.md) for availability and rollout.
Powerglove uses external TVL directly rather than scaling raw historical values to
match today's headline. Constant-price history freezes each vault's asset-unit
price at a reference inside the requested window, using the existing first-seven
observation outlier rule. It applies the dated external fraction to those values.
It measures asset-unit change at frozen prices, not independently reconstructed
net deposit flows; share-token assets retain their stored share-unit definition.

The four Ethereum Katana pre-deposit vaults (USDC, WETH, USDT and WBTC) are
excluded across all history in new publications, including before their June 30,
2025 migration. This is an explicit reporting-scope decision, not a claim that
pre-migration assets were already held on Katana. Katana vault balances remain
included on their own dates; no balances are moved or backfilled onto Katana.

The all-history rule is frozen in `vaultPolicies` and applies to actual and
constant-price charts, chain/version/vault breakdowns, and reporting totals.
Raw-mode API charts also respect this reporting scope; stored observations remain
unchanged in the source database for audit. Older dataset IDs retain the original
dated exclusion.

All-history exclusions are removed before dated bridge accounting. These four
vaults therefore contribute nothing to `vaultBridgeExcluded`,
`crossChainOverlapByChain`, `crossChainOverlapByCategory`, or the graph
`totalBridgeExcludedTvlUsd`. With no other bridge exclusions, those totals are
zero, audit `crossChainVaults` and graph `bridgeExclusions` are empty, and frame
diagnostics contain no bridge-excluded balance. These fields describe dated
bridge deductions within reporting scope, not the balances removed by policy.

There is no public API route for the policy-excluded balances, including raw-mode
history. Auditors need authorized source database access: query `tvl_snapshots`
by `chain_id = 1`, the vault address, and the publication manifest
`runs[].id` values as `run_id`; `timestamp` and `data_json` retain the original
dated valuations.
The manifest `vaultPolicies` identifies the excluded addresses. Do not sum all
matching observations: select one row per `(chain_id, vault, timestamp)`, preferring
non-null `tvl_usd`, then non-null `asset_units`, then unavailable rows. Within each
quality tier, the greatest `run_id` wins. Apply the manifest's frozen
`priceRejections` to the selected row: an exact chain/asset/timestamp/price match
makes its price and TVL unavailable, without falling back to an older quote.
`TvlDataset.selected_rows(conn, 'tvl_snapshots', timestamps, chain_id=1)` implements
both steps before the reporting-policy filter; filter its output to the excluded
addresses. See [Publication and automatic selection](#publication-and-automatic-selection).
These selected source valuations are not part of the reported TVL totals; reproducing
an older publication's external or constant-price series also requires that
publication's dated ownership fractions and reference-price rules.
Deploying the inventory alone does not rewrite an existing publication: prepare
and select a new publication to make the change visible to Powerglove.

The registry stores the migration timestamp, Ethereum block 22817424, destination
network ID 20 and transaction
`0x6896487aac1fe132614719e7758719d55d11036bb163d1a959ed9500d8cd30f0`.
This transaction set all four deposit limits to zero and bridged their USDC, USDT,
WBTC and ETH positions to Katana. The publication freezes this evidence under
`bridgeMigrations`; older dataset IDs retain their original accounting context
while available, subject to the HTTP 410 retirement contract above.
Dated bridge-excluded parent positions may remain in diagnostics but no longer
deduct child TVL; this does not preserve policy-excluded vault balances in the API.

Undated bridge registry entries retain the optional latest-close retirement
fallback. It is never projected backward into history. Verified migration dates
take precedence over that fallback and over today's active flag. Fee charges at different layers
remain separate charges; reported earnings remain contract-level P&L rather than
consolidated nested economic yield.

## TVL graph

`GET /api/tvl/graph` serves Powerglove's existing node/edge contract from the
selected Yearn Data TVL publication. It supports `chainId`, `category`
(`v1`, `v2`, `v3`, `curation`), `includeRetired=true|false` (default true), and
`datasetId`. Both the pairing server and hosted API expose it. Hosted requests
for a retired dataset follow the existing HTTP 410 / reload contract.

The response contains `snapshotAt`, `asOfTimestamp`, `datasetId`,
`totalRawTvlUsd`, `totalOverlapTvlUsd`, `totalBridgeExcludedTvlUsd`, `totalTvlUsd`, `nodes`, `edges`, and
`bridgeExclusions`. The timestamp is the publication's stored close, not a
current RPC read. No request performs discovery, RPC collection, or pricing.

## Accounting and display conditions

- Ownership is accounted across the full network before display filters.
  `includeRetired=false` hides retired nodes and their displayed edges without
  changing the accounting amounts of remaining vaults. Category and chain filters
  select headline contributions and graph roots, retaining connected context
  among visible nodes.
- `totalOverlapTvlUsd` counts ownership deductions. Bridge exclusions are reported
  separately in `totalBridgeExcludedTvlUsd` and `bridgeExclusions`, matching the
  summary route's `overlapExcluded` and `vaultBridgeExcluded` distinction.
- Recorded debt, shared ownership, and parent/child TVL caps use `tvl.account`.
  Synthetic intermediary nodes explain allocations but never add to aggregate TVL.
- Each holder's ownership is split across parents and children once. The graph
  merges a shared holder's child edge while retaining distinct parent allocations.
- `tvlUsd` on an edge describes flow. `deductedTvlUsd` describes the contribution
  to overlap accounting. They intentionally differ for ordinary strategies and
  curation flows. Do not sum all displayed edges to derive headline overlap;
  multi-hop context and filtered categories make that incorrect.
- Retained strategy debts can exceed a V2 parent's assets. Verified downstream
  flows receive their accounted budget first; the remaining parent TVL is split
  among other debts. Such allocation edges have
  `valueSource=parent-tvl-capped-debt-estimate`; `recordedDebtUsd` retains the
  uncapped observation. Ordinary verified debt uses `dated-strategy-debt`.
- Unpriced vaults are omitted from graph nodes and retained under
  `diagnostics.unavailableNodes`, rather than shown as zero-value vaults.
  Graph totals follow the existing API's available-amount convention;
  `diagnostics.status` identifies incomplete accounting.
- Current publications use unified canonical ownership, including curated vaults.
  An explicitly retained older dataset may encode the earlier separate-product policy.

## Historical query performance

Chain, version and constant-price charts reuse compact, dated valuation rows
within a dataset. Chain drilldowns filter those cached all-chain valuations and
reference prices before grouping by vault or version, rather than rescanning the
database. The shared row cache is bounded to four windows of at most 400
observed dates; longer histories keep streaming. Reference prices are cached
separately for eight daily windows, independent of chart grouping. Explicit
all-time first/last bounds share the same response cache as omitted bounds.
Response caching is limited to 8 MiB of serialized JSON and 32 entries per dataset;
oversized results are not retained. Public requests stream source rows rather than
filling the shared row cache with attacker-selected date windows.
Concurrent requests for identical source work join that calculation, while
current summaries and unrelated reads remain available. The reference-price SQL
sorts observation identities rather than full snapshot payloads.

A first uncached history or reference window still performs a stored-data scan;
subsequent group changes reuse those observations. These caches are in memory
and are rebuilt after restart or a new dataset selection. No historical valuation,
reference-price rule or reporting cutoff changes as a result of caching.

Curation product totals carry a `completeness` object with `scope` set to
`network-products` and `totals` set to `known-subtotals`. Its status, valued-vault
count and unresolved-position count describe the network product universe even
for a chain-filtered display. Incomplete prices or ownership also produce an
explicit limitation. Product ownership is accounted across chains before display
filtering; potential V2 pass-through is attributed to the V2 parent's chain.

Quote rejection also invalidates explicitly derived nested-share valuations.
On rejected dates, wrappers are recalculated from compatible selected child
observations; unknown or incompatible children leave them unavailable. Previously
stored USD ownership is unavailable when that valuation basis changes, until
compatible ownership is recollected. References use the same dated valuation.
New publications use selection policy 9 and prepared accounting version 2; old
prepared rows must be rebuilt before they can serve this calculation.

## Hosted versus local TVL publication

The following auto-selection behavior belongs to the supported local file-backed
server. Hosted requests use the explicit registry selection described above.
Do not assume a new collection automatically changes the hosted release.

## Publication and automatic selection

The first startup publishes accumulated finished TVL runs. Subsequent unpinned
requests check for new finished observations at most every 30 seconds. There is
no manual collection-run selection. A newer old-date backfill repairs that date
without replacing recent observations or discarding other history. Known valuations
win over unavailable retries; successful later observations win ties.

The current close is the latest stored date with observed quantities on every
collected chain. A whole-chain RPC failure leaves the preceding common close
selected until a repair supplies that chain. Pricing gaps and unresolved positions
remain in developer diagnostics. Verified invalid provider quotes are rejected by exact
chain/asset/day/price matches in `inventories/tvl-price-rejections.json`, frozen into
the publication. Historical reserve evidence is retained there. Rejected values
remain unavailable until a corrected canonical quote is collected; no fallback
USD price is substituted. Reference candidates are filtered before selecting the
first seven valid daily observations, so rejected quotes cannot exhaust that
window or set its constant price. HTTP responses serve available stored amounts;
unavailable amounts remain null rather than fabricated zero.

`datasets/<dataset-id>.json` freezes finished source-run membership, the vault
catalogue, positively identified V3 allocators, selection policies and current-close
diagnostics. `current.json` activates it atomically after validation. Failed
publication retains the preceding selection. Completed observations are immutable,
but canonical consolidation can retire superseded observations and dataset pins.

The TVL summary returns `datasetId` and `asOfTimestamp`. Powerglove pins its history,
constant-price chart and chain drilldown to this identity. A stored dataset remains
addressable after a subsequent collection publishes another one, until canonical
consolidation retires its backing observations. The file-backed API returns HTTP
410 with an `error` when a pin lacks the retained canonical run union. Retry
without `datasetId` to obtain the current summary, then pin all related requests
to its new ID. The local current pointer must reference an available publication.
The hosted API rejects any superseded pin with HTTP 410 and the current
`releaseId`; reload `/api/publication` and retry the related views together.
Neither path returns a successful partial history for a retired pin.

## Owned analytics

`/api/analytics/publication`, `/api/comparison`,
`/api/comparison/defillama-comparable`, `/api/fees/stack` and `/api/profitability`
read prepared analytics with `publicationId` pinning. Comparison accepts matching
`datasetId`; `includeVaultBreakdown=false` must retain the confirmed missing list.
Profitability accepts optional `chainId`. Unknown membership is not exclusion or
inclusion; missing rate/denominator evidence is not zero. See
[analytics methodology](analytics.md) and [effective configured rates](../accounting/effective-fee-products.md).

The original [pairing requirements](../archive/design/powerglove-pairing-requirements.md)
and [cutover requirements](../archive/design/powerglove-remaining-cutover-requirements.md)
retain detailed acceptance criteria. General consolidated historical yield,
treasury attribution and arbitrary historical fee-rate reconstruction remain
outside these implemented routes.

[Documentation index](../README.md).
