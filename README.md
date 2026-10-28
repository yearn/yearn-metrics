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

A ticking estimate needs bounded analyses, not just `run lifetime-yield`, which
produces unbounded totals. After indexing and pricing, analyze through an explicit
closed UTC midnight and publish:

```bash
CUTOFF=$(date -u -d '00:00 today' +%s)
yearn-data analyze lifetime-yield --before-timestamp "$CUTOFF"
REDIS_URL='rediss://...' yearn-data publish lifetime-yield --ttl 259200
```

The publisher atomically replaces `lifetime_yield:headline` with one JSON blob containing `net_yield_usd`, `as_of_ms`, `rate_usd_per_sec`, previous anchor fields, and `run_id`. Use `--dry-run` to inspect without writing Redis. With a Redis URL configured,
it reads the existing anchor and makes the same extrapolation decision as a
publish. Without Redis it reports `anchor-not-read` rather than predicting a
reset. Unbounded analyses always report `unbounded-analysis`.

The first bounded publication seeds a zero-rate anchor. Repeat at a later cutoff
to derive a rate from a comparable covered interval. `net_yield_usd` is the stored
value through `coverage_through_timestamp`, while `as_of_ms` is publication time.
For a `coverage-verified` estimate, consumers tick from the coverage timestamp:
`net_yield_usd + rate_usd_per_sec * max(0, now_ms / 1000 - coverage_through_timestamp)`.
This includes any delay between the close and publication; it remains an estimate.
Do not tick from `as_of_ms`. On resets or unbounded runs the rate is zero.
The `prev_*` fields retain the actual previous publication even when no rate can
be inferred; they do not fabricate a replacement anchor on resets.

Publishing requires explicit complete valuation counts and retained report rows.
Incomplete or legacy summaries with unknown completeness fail before replacing
Redis, including in dry-run mode. Rejection leaves an existing anchor unchanged,
but does not extend its TTL: it remains available only until its existing expiry.
With the example TTL above, the key can disappear after 72 hours without a
successful publication. A
complete genuine zero remains publishable. Payloads add completeness and hashed
coverage/policy metadata. Extrapolation continues only when the previous anchor
is verified complete and its historical report rows match the current run.
For analyses with an explicit `--before-timestamp`, the rate divides new yield
by the advance in covered chain time, independent of publication frequency.
Cutoff-less analyses (including `run lifetime-yield`) publish complete totals
with a zero rate, `coverage_through_timestamp: null` and
`extrapolation_status: "unbounded-analysis"`; analysis start time never stands in
for closed chain coverage. Bounded anchors report `coverage-verified` or
`coverage-reset` according to the historical comparability check. Retained coverage is checked in
one bounded-memory streaming pass; unbounded runs validate rows without sorting
or computing unusable coverage digests. New reports after that history can contribute yield; backfills, changed historical
valuations, source-policy changes and legacy anchors reset the rate to zero.
Only the publication timestamp `as_of_ms` remains monotonic across a reset.
Selecting an earlier bounded analysis can reduce `coverage_through_timestamp`
and the covered value; selecting an unbounded analysis sets coverage to `null`.
Consumers must read the new coverage and status rather than assuming forward
financial coverage across resets. Existing Redis publications
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

Yearn Prices is the default earnings pricing source, with optional DefiLlama fallback; see [historical earnings pricing](docs/earnings-pricing.md). Volume and legacy fee analyses retain their DefiLlama source. Optional contract adapters use historical conversion/reserve evidence and accepted underlying quotes. Local fixed-parity estimates are retired: a missing provider quote does not become USD 1 or another token's price. Use `--source defillama --no-onchain-fallbacks` for direct DefiLlama results. See the [parity policy and coverage comparison](docs/parity-price-policy.md) for classifications and legacy-cache handling. All source/status rows are stored in Postgres.

## Backfill Efficiency

Event indexing uses resumable `eth_getLogs` block chunks and dedupes logs by `(chain_id, tx_hash, log_index)`. The default chunk size is `50,000` blocks, intended for Tenderly-style archive RPCs; lower it if an RPC returns block range errors. Block timestamps are cached in the local database after first lookup.

## Historical report ingestion

See [the Envio ingestion guide](docs/historical-envio-ingestion.md) for retired vault discovery, metadata and bounded replay.

For explicit per-chain RPC/Envio windows, including Optimism and Fantom, see [bounded report catch-up](docs/bounded-report-catchup.md).
The [coverage performance receipt](docs/report-coverage-performance.md) documents
batched coverage reads, active/inactive progress, and benchmark limits.

For maintained multi-chain acquisition, missing daily TVL collection, durable
resume and explicitly selected publication, see [Database refresh](docs/database-refresh.md).

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
