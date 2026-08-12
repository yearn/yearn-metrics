# yearn-data

Reusable Python tooling for indexing Yearn vault data and running research jobs on top of normalized onchain data.

The primary `lifetime-yield` pipeline reads Yearn V2/V3 decoded `StrategyReported` events from the Envio indexer, prices report-time vault asset gains/losses, and exports aggregate yield totals. The legacy RPC backfill remains available for recovery.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

yearn-data init-db
yearn-data discover
yearn-data index-events
yearn-data price
yearn-data analyze lifetime-yield
yearn-data export lifetime-yield
```

By default the CLI reads decoded events from Envio and loads its endpoint from `.env`:

```bash
YEARN_ENVIO_GRAPHQL_URL=
```

The Envio importer reads 1,000 event rows at a time over 1,000,000-block windows. Set `YEARN_ENVIO_PAGE_SIZE` or `YEARN_ENVIO_WINDOW_BLOCKS` only to accommodate an endpoint limit.

Use `YEARN_DATA_EVENT_SOURCE=rpc` only when recovering from the legacy `eth_getLogs` backfill. The default Envio path resolves new vault and underlying-token metadata, including `asset.decimals`, from Yearn Kong; it does not require an RPC endpoint for metadata. `--no-onchain-fallbacks` keeps report pricing off-chain-only. `index-fees` reads V2 harvest share transfers from Envio, then requires an Ethereum archive RPC only for batched historical `pricePerShare()` / `getPricePerFullShare()` valuation calls.

```bash
# Optional override; defaults to https://kong.yearn.fi/api/gql.
YEARN_KONG_GRAPHQL_URL=

# Required only when YEARN_DATA_EVENT_SOURCE=rpc.
ETH_RPC_URL=
POLYGON_RPC_URL=
BASE_RPC_URL=
ARB_RPC_URL=
KAT_RPC_URL=
ETHERSCAN_API_KEY=
```
`ETHERSCAN_API_KEY` is optional for most indexing runs, but useful for V2 source/ABI review workflows.

## Useful Options

```bash
yearn-data --db data/yearn.sqlite discover --chains eth arb kat
yearn-data index-events --to-block 25400000 --chunk-size 5000
yearn-data run lifetime-yield
yearn-data run vault-volume
```

The SQLite database stores raw event rows, normalized strategy reports, prices, resumable cursors, and analysis outputs so later research jobs can reuse the same indexed data.

## Lifetime Yield Outputs

The `lifetime-yield` aggregate columns `gross_gain_usd`, `loss_usd`, and `net_yield_usd` are economic totals adjusted for known Yearn public incident disclosures where vault reports emitted paper losses or compensating phantom profits. The raw report-time accounting values remain available as `raw_gross_gain_usd`, `raw_loss_usd`, and `raw_net_yield_usd`.

Rows changed by an incident adjustment are marked in `reports.csv` with `is_adjusted`, `incident_id`, `incident_classification`, `incident_description`, and `incident_disclosure_url`. Raw indexed `strategy_reports` rows in SQLite are not modified.

### Redis Headline

After a completed `lifetime-yield` analysis run, publish the odometer headline payload with:

```bash
REDIS_URL='rediss://...' yearn-data publish lifetime-yield --ttl 259200
```

The publisher atomically replaces `lifetime_yield:headline` with one JSON blob containing `net_yield_usd`, `as_of_ms`, `rate_usd_per_sec`, previous anchor fields, and `run_id`. Use `--dry-run` to print the payload without touching Redis.

## Vault Volume Outputs

The `vault-volume` job indexes user `Deposit`/`Withdraw` events and strategy debt movement. V2 strategy allocation volume is derived from `StrategyReported.debtAdded` and `debtPaid`; V3 allocation volume is indexed from `DebtUpdated` events.

The headline volume metric is `gross_total_volume_usd`, defined as deposits + withdrawals + allocations + deallocations. Exports also include net user flow, net strategy flow, per-chain/vault/strategy/token/month rollups, and raw `vault_flows.csv` / `strategy_debt_flows.csv` event files.

## Pricing

The `price` command supports three modes:

```bash
yearn-data price --source defillama
yearn-data price-volume --source defillama
yearn-data price --source defillama --no-onchain-fallbacks
```

DefiLlama is the only offchain pricing source. When DefiLlama cannot price a token directly, the default pricing path can apply local deterministic fallbacks for canonical stablecoins, canonical wrapped/native equivalents, Curve/CRV derivative tokens, exchange-rate wrappers, historical Yearn V1 share-price wrappers, and Aave aTokens. Use `--no-onchain-fallbacks` to record only direct DefiLlama results. All source/status rows are stored in SQLite.

On-chain fallback valuation is protocol-specific and fail-closed. Every fallback prices a token from exact on-chain state at the report's `block_number` and leaves the row unpriced when the full valuation cannot be proven:

- **Curve LP tokens** (Ethereum, Polygon, Base, Arbitrum): resolve the pool via `minter()`/registry, read every non-zero reserve and the LP supply (batched through Multicall3 when available), and value each reserve recursively. If any non-zero reserve cannot be priced, the LP stays unpriced and `raw_json` records the missing reserve. Partial reserve sums and `get_virtual_price × mean(underlying prices)` are never used.
- **ERC-4626 / fixed-rate wrappers** (any chain, e.g. Ether.fi weETH, Curve Lend cvTokens): historical assets-per-share rate (`convertToAssets`, `getRate`) times the priced underlying. Pendle principal tokens are excluded because their redemption rate is a maturity value, not a market price.
- **Balancer BPTs**: `getPoolId()` + Vault `getPoolTokens` reserves over BPT supply, skipping the pool's own phantom BPT balance.
- **Constant-product pair LPs** (Uniswap-V2 forks, Aerodrome): historical pair reserves over LP supply.

Nested structures resolve recursively with a depth budget, so cycles terminate. RPC endpoints accept a comma-separated rotation list (`ETH_RPC_URL=url1,url2`); transient failures rotate endpoints with backoff before a row is marked missing.

## Backfill Efficiency

Envio ingestion uses resumable, forward-cursored GraphQL block windows. It replaces RPC `eth_getLogs` scans and per-block timestamp reads for reports, deposits, withdrawals, and V3 debt movements. The legacy RPC source uses resumable `eth_getLogs` chunks and cached block timestamps; select it with `YEARN_DATA_EVENT_SOURCE=rpc`.
