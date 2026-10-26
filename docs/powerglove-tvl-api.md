# Powerglove TVL cutover

The read-only pairing server can serve TVL alongside fees and reported earnings from
Postgres (`neon` or `staging`). Powerglove's `codex/stats-page` uses this source for current TVL,
history, constant-price history, curation products and the vault audit tree.
With `--analytics`, prepared fee-stack, profitability and DefiLlama
membership/comparison results also come from Postgres.
See [owned Powerglove analytics](powerglove-analytics.md) for the complete cutover.
Unused supplementary graph routes retain their fallback configuration.

## Start the API

Keep the existing fees/earnings publication selected, then run:

```bash
yearn-data serve-pairing --publication data/powerglove \
  --tvl-publication data/powerglove-tvl \
  --current-bridge-policy retired-registry \
  --analytics \
  --host 127.0.0.1 --port 3492
```

Prepared analytics supply stored DefiLlama references from the same owned
Postgres publication. No legacy reference database is required. The API makes no RPC or
pricing requests and opens its source databases read-only. Configure a reverse
proxy for production; set `--cors-origin` when Powerglove uses a different origin.

Powerglove configuration:

```text
VITE_YEARN_DATA_API_TARGET=http://127.0.0.1:3492
# Production, when the API is on a separate browser-accessible origin:
VITE_PUBLIC_YEARN_DATA_API_URL=https://your-data-api.example
```

Existing TVL and fee service settings continue to serve supplementary analytics.
Without a Yearn Data target, the TVL proxy retains its existing TVL-service fallback.

## Hosted database connection

The Postgres-only runtime supports these API contracts with a hosted database. Keep `NEON_DB_URL` in Yearn Data's ignored environment file;
never put this credential into a `VITE_*` variable or the browser bundle. The
Powerglove consumer talks to the API, which opens Neon read-only.

Publish the existing completed Postgres analyses, then start the API:

```bash
python -m yearn_data.cli --env /path/to/yearn-data/.env --db neon select-pairing \
  --earnings-run-id 13 --fees-run-id 12 --out data/powerglove-neon

python -m yearn_data.cli --env /path/to/yearn-data/.env serve-pairing \
  --publication data/powerglove-neon --tvl-publication data/powerglove-tvl-neon \
  --current-bridge-policy retired-registry \
  --analytics \
  --host 127.0.0.1 --port 3491
```

The example run IDs match the imported current financial publication. Fee dataset
identity is preserved when the imported outputs are identical. The TVL dataset ID
changes because its publication records the logical database reference `neon`.
Restart the consumer when switching the target so it obtains the new TVL selection;
do not reuse an old file-backed TVL dataset ID against a Neon publication.

Set Powerglove's ignored `.env.local` to
`VITE_YEARN_DATA_API_TARGET=http://127.0.0.1:3491` and restart its local server or
preview. For a remotely deployed consumer, configure the public **API** URL using
`VITE_PUBLIC_YEARN_DATA_API_URL`; a hosted database alone does not deploy an API.

Use the collection/preparation commands in [owned analytics](powerglove-analytics.md)
before enabling `--analytics`. Keep Postgres recovery backups and validate TVL,
core fees, weekly history and comparison results before changing consumers.
SQLite-backed serving and rollback are retired; historical archives remain untouched.

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
a TVL pairing route. See [owned analytics comparison](powerglove-analytics.md)
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
See [TVL overlap conditions](tvl-overlaps.md) for its flow-versus-deduction fields,
router and wrapper corrections, cross-chain verification, and review procedure.
New publications include curated vaults in the same canonical ownership ledger:
Yearn-owned deposits reduce the receiving vault's external TVL, including holdings
routed through strategies. The manifest pins this as `ownershipAccounting:
all-yearn-v1`; older publications retain their separate curation-layer convention.
See [canonical ownership](canonical-tvl-ownership.md) for availability and rollout.
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

## Collection operations

Collect or repair source data through the existing TVL commands, then allow the
server's next refresh to publish it. Check finalized head freshness on the actual
RPC before interpreting a failed close as a chain-level finalization problem.
Katana's configured dRPC endpoint returned a stale finalized head during cutover;
healthy public endpoints supplied the requested historical close and the Katana-only
repair completed. RPC endpoint configuration is local and contains no committed
credentials. Refresh the existing fee/earnings analyses separately when their
source data changes; TVL collection does not regenerate those analyses.

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
