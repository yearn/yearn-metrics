# yearn-data

Reusable Python tooling for indexing Yearn vault data and running research jobs on top of normalized onchain data.

The first analysis job is `lifetime-yield`, which backfills Yearn V2/V3 `StrategyReported` events, prices report-time vault asset gains/losses, and exports aggregate yield totals.

For pricing policy, configuration and Yearn-only commands, see [historical earnings pricing](docs/earnings-pricing.md).

## Quick Start

Copy `.env.example` to `.env` and configure `YEARN_PRICE_PROD_KEY` before running
the Yearn Prices commands below, along with the ingestion/RPC settings you need.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

yearn-data init-db
yearn-data discover
yearn-data index-events
yearn-data price --source yearn-prices --no-provider-fallback --no-onchain-fallbacks
yearn-data analyze lifetime-yield --price-source yearn-prices --no-provider-fallback
yearn-data export lifetime-yield
```

By default the CLI loads RPC and API values from `.env` in this repo. Required RPC variables depend on the chains you run:

```bash
ETH_RPC_URL=
POLYGON_RPC_URL=
BASE_RPC_URL=
ARB_RPC_URL=
KAT_RPC_URL=
OP_RPC_URL=
FTM_RPC_URL=
YEARN_ENVIO_GRAPHQL_URL=
YEARN_PRICE_PROD_KEY=
ETHERSCAN_API_KEY=
```

`ETHERSCAN_API_KEY` is optional for most indexing runs, but useful for V2 source/ABI review workflows.

## Development checks

Install the development dependencies with `python -m pip install -e '.[dev]'`,
then run the same lint command as CI from the repository root:

```bash
python -m ruff check .
```

Ruff is pinned in `pyproject.toml`. The enabled `E9` and `F` rules check syntax,
undefined names, unused imports and variables, and other Python correctness
errors. They preserve the existing formatting style. The independent `lint`
CI job needs no database or credentials. Both CI jobs use `uv sync --locked --extra dev`
to validate and install the committed lockfile before running checks. Lint and
tests use the interpreter pinned in `.python-version`; tests also run on the
Python 3.10 minimum declared in `pyproject.toml`. uv uses the interpreter supplied
by CI, with managed interpreter downloads disabled. See the [Postgres test guide](docs/postgres.md#tests)
to run the test suite, and the [testing policy](docs/testing.md) for retention
rules and selecting tests without Postgres.

Fixtures defined in `tests/conftest.py` are available automatically; request them
as test arguments without importing them. Fixtures defined in other test modules
use explicit aliases, such as `from test_canonical_fees import db as db`, so Ruff
recognizes their intentional use by pytest. Keep those imports while the tests
use their fixtures. Ruff does not detect every unused function; disconnected test
helpers still need review when their callers or monkeypatches are removed.

## Useful Options

```bash
yearn-data --db neon discover --chains eth arb kat
yearn-data index-events --to-block 25400000 --chunk-size 5000
# Requires YEARN_PRICE_PROD_KEY in .env; use --price-source defillama without it.
yearn-data run lifetime-yield
yearn-data run vault-volume
```

The database stores raw event rows, normalized strategy reports, prices, resumable cursors, and analysis outputs so later research jobs can reuse the same indexed data. Postgres is the only supported database, and Neon is the default logical selection. See the [Postgres operator guide](docs/postgres.md) for hosted configuration and isolated local testing.

## Lifetime Yield Outputs

Lifetime earnings include stored history from both active and retired vaults. Retirement does not remove earlier reports. Unpriced reports remain counted with unavailable USD values. For the Powerglove integration scope and the different treatment of nested fees and earnings, see the [accounting contract](docs/powerglove-accounting-contract.md).

For selecting completed results and serving the Powerglove fee/earnings views, see the [pairing operator guide](docs/powerglove-pairing.md).

The `lifetime-yield` aggregate columns `gross_gain_usd`, `loss_usd`, and `net_yield_usd` are economic totals adjusted for known Yearn public incident disclosures where vault reports emitted paper losses or compensating phantom profits. The raw report-time accounting values remain available as `raw_gross_gain_usd`, `raw_loss_usd`, and `raw_net_yield_usd`.

Rows changed by an incident adjustment are marked in `reports.csv` with `is_adjusted`, `incident_id`, `incident_classification`, `incident_description`, and `incident_disclosure_url`. Raw indexed `strategy_reports` rows in Postgres are not modified.

### Redis Headline

After a completed `lifetime-yield` analysis run, publish the odometer headline payload with:

```bash
REDIS_URL='rediss://...' yearn-data publish lifetime-yield --ttl 259200
```

The publisher atomically replaces `lifetime_yield:headline` with one JSON blob containing `net_yield_usd`, `as_of_ms`, `rate_usd_per_sec`, previous anchor fields, and `run_id`. Use `--dry-run` to print the payload without touching Redis.

Publishing requires explicit complete valuation counts and retained report rows.
Incomplete or legacy summaries with unknown completeness fail before replacing
Redis, including in dry-run mode; an existing valid anchor is preserved. A
complete genuine zero remains publishable. Payloads add completeness and hashed
coverage/policy metadata. Extrapolation continues only when the previous anchor
is verified complete and its historical report rows match the current run.
The rate divides new yield by the advance in covered chain time, independent of
how quickly consecutive anchors are published. Retained coverage is checked in
one bounded-memory streaming pass. New reports after that history can contribute yield; backfills, changed historical
valuations, source-policy changes and legacy anchors reset the rate to zero.
Anchor timestamps remain monotonic across a reset. Existing Redis publications
are not changed until this command is explicitly invoked.

## Vault Volume Outputs

The `vault-volume` job indexes user `Deposit`/`Withdraw` events and strategy debt movement. V2 strategy allocation volume is derived from `StrategyReported.debtAdded` and `debtPaid`; V3 allocation volume is indexed from `DebtUpdated` events.

The headline volume metric is `gross_total_volume_usd`, defined as deposits + withdrawals + allocations + deallocations. Exports also include net user flow, net strategy flow, per-chain/vault/strategy/token/month rollups, and raw `vault_flows.csv` / `strategy_debt_flows.csv` event files.

## Pricing

Earnings default to Yearn Prices with optional DefiLlama fallback. For the
Yearn-only delivery workflow, use the explicit flags in the quick start. Volume
pricing retains its DefiLlama path:

```bash
yearn-data price --source defillama
yearn-data price-volume --source defillama
yearn-data price --source defillama --no-onchain-fallbacks
```

Yearn Prices is the default earnings pricing source, with optional DefiLlama fallback; see [historical earnings pricing](docs/earnings-pricing.md). Volume and legacy fee analyses retain their DefiLlama policy. When DefiLlama cannot price a token directly, its pricing path can apply local deterministic fallbacks for canonical stablecoins, canonical wrapped/native equivalents, selected Curve/CRV derivative tokens, exchange-rate wrappers, and Aave aTokens. Use `--source defillama --no-onchain-fallbacks` to record only direct DefiLlama results. All source/status rows are stored in Postgres.

## Backfill Efficiency

Event indexing uses resumable `eth_getLogs` block chunks and dedupes logs by `(chain_id, tx_hash, log_index)`. The default chunk size is `50,000` blocks, intended for Tenderly-style archive RPCs; lower it if an RPC returns block range errors. Block timestamps are cached in the local database after first lookup.

## Historical report ingestion

See [the Envio ingestion guide](docs/historical-envio-ingestion.md) for retired vault discovery, metadata and bounded replay.

For explicit per-chain RPC/Envio windows, including Optimism and Fantom, see [bounded report catch-up](docs/bounded-report-catchup.md).

For the complete scriptable workflow and reproducible CSV exports, see
[Refresh and export earnings and fees](docs/earnings-and-fees.md).


## Historical TVL

Store dated TVL for the complete vault inventory, including retired and curated
vaults, and export nested capital positions with overlap deductions. Start from an empty database with
`tvl discover`, collect an explicit date range with `tvl collect`, and use
`tvl export` for CSV/JSON outputs. See [Historical TVL and nested positions](docs/tvl-history.md)
for the accounting rules, mapping, and coverage boundaries.

Use one Postgres database with separate TVL and fee/earnings tables. See
[Shared database](docs/shared-database.md) for the storage boundary.

Serve the accumulated observations to Powerglove with the [TVL pairing API](docs/powerglove-tvl-api.md).
