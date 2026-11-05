# Historical TVL and nested positions

The pipeline stores a dated asset quantity for every vault in the selected inventory,
prices it, and removes known nested ownership when combining vaults. It includes
retired vaults. Collection is separate from fee and transaction-event ingestion.

For example, a parent with $100 invested in a child with $100 has $200 of gross
vault TVL but $100 of combined TVL. A third nested layer is deducted again at that
layer. The child retains any capital supplied by unrelated depositors.

## Run it

Start from an empty database with independent discovery. The command combines
Kong V2/V3 inventory, the bundled 28-address Yearn V1 registry, paginated Morpho
owner/creator/curator queries, factory creation events, and explicitly listed
curated products. It requires no old TVL service checkout or database.

```bash
yearn-data --db staging --env .env tvl discover

# One daily UTC-close sample: 2026-10-03 23:59:59 UTC.
yearn-data --db staging --env .env tvl collect \
  --from-timestamp 1791071999 --to-timestamp 1791071999

yearn-data --db staging tvl export --out exports/tvl
```

Discovery supports `--sources kong v1 curation` and `--chain-ids 1 8453`.
Omitting those filters selects all sources and their configured scopes. Factory
and historical adapter scans resume from successful stored coverage. Their first
run can take longer. `--from-block`, `--to-block`, and `--chunk-size` bound those
scans; API identities and metadata still describe current state. Use a chain
filter when setting block bounds, because block numbers differ across chains.

The final JSON summary reports each selected source, metadata failures, mapping
failures, and scan scope. Complete discovery exits 0; incomplete discovery exits
2 and retains candidates so the same command can be retried. Discovery summaries
are also stored in `tvl_discovery_runs`. A failed source does not prevent the
other selected sources from completing. Discovery does not call a price provider.

`YEARN_MORPHO_GRAPHQL_URL` optionally overrides the public Morpho API endpoint.
RPCs must provide the selected historical logs; enriching old adapter contracts
can also require archive state. Missing access is reported as incomplete.

The one-time legacy SQLite catalog importer is retired. Fresh inventories come
from the supported discovery sources above; existing Postgres history is reused.

Without filters, collection requests **every vault in the database**, across all
its chains and categories. Use `--chain-ids 1 8453`, `--vaults <addresses>`, or a
shorter date range for bounded work. Daily intervals are the default; use
`--interval 604800` for weekly history. Yearn Prices requires UTC-close timestamps
and whole-day intervals so quantities and prices represent the same date.
`--price-source defillama` allows explicit timestamps and uses the existing
DefiLlama historical-price client.

Set `YEARN_PRICE_PROD_KEY` for Yearn Prices. TVL RPC selection uses, in order,
`ARCHIVE_RPC_URI_FOR_<chainId>`, `ARCHIVE_RPC_URL_<chainId>`,
`RPC_URI_FOR_<chainId>`, and the existing chain's named RPC variable. This also
supports inventory chains outside the fee indexer's default chain list.
Each chain is sampled at its last block at or before the requested timestamp,
within the pinned finalized head. Ethereum-style RPCs use the `finalized` tag;
Fantom Opera uses its published main-chain head, whose blocks already have
[consensus finality](https://docs.fantom.foundation/technology/faq). Both policies
pin a single block before historical lookups. Missing archive access remains unavailable.

## Map additional nested positions

Direct allocator-to-tokenized-strategy relations are identified by address.
The migrated registry covers known intermediary strategies and retired V2 routers.
Vaults whose underlying asset is another vault's share token are detected automatically.

To find additional intermediaries, scan their child share balances at an explicit
date. This scans all known strategy and vault holders against the chain's vaults,
so run it deliberately rather than on every daily collection:

```bash
yearn-data --db staging --env .env tvl map-holders \
  --chain-ids 1 --timestamp 1791071999
```

Repeat at older dates to find historical routes that no longer hold shares today.
Locally indexed strategy reports, debt flows, and strategy-change events also add
historical candidates. Rerun `tvl discover` to refresh V1/curated inventory and discover adapter paths.
Specific Morpho families are retained separately from product category. All
registered adapters are checked, and previously added adapters remain candidates
through their `AddAdapter` events. Unknown child mappings are retained with
unresolved diagnostics. Positive debt in an unresolved adapter makes a collected
aggregate incomplete; a verified zero debt does not require an overlap deduction.
Children outside the configured Yearn catalog remain visible as relations, without
adding their full TVL to Yearn's inventory.

## Accounting and availability

[tvl-accounting](../reference/tvl-accounting.md) owns the dated deduction ledger.
The default export includes curation in unified ownership accounting;
`--include-curation` retains that default and `--no-include-curation` explicitly
selects the separate-product export convention. Export switches do not change a
publication's captured policy. Gross, overlap and external TVL remain distinct;
missing evidence must not become a zero deduction or a complete total.

## Stored data and outputs

| Table/output | Contents |
|---|---|
| `tvl_discovery_runs` / `tvl_catalog_evidence` | Source outcomes, metadata provenance, and unresolved mapping diagnostics |
| `tvl_runs` | Explicit date range, selection, pricing policy, acquisition status |
| `tvl_snapshots` / `vaults.csv` | Raw integer assets/supply, normalized quantity, historical block, price provenance, gross and external TVL |
| `tvl_positions` / `positions.csv` | Parent, strategy/holder, child, debt, share ownership, mapping method, deduction |
| `history.csv` | Combined gross TVL, known overlap, external TVL, valuation coverage |
| `tvl.json` | All outputs and run metadata for consumers |

Each collection creates a new immutable run. Recollect an unavailable date after
fixing RPC/pricing access; the earlier run stays available via `--run-id`.
An interrupted collection is marked failed, and default export selects finished
runs. A genuine zero is stored as zero; an unavailable value is null. Incomplete
aggregates have null headline totals and separately named `known_*` subtotals.

Positions describe capital allocated at a point in time. Deposits, withdrawals,
and debt movements remain in the existing `vault-volume` workflow; TVL changes
also include yield, losses, and price changes and are not pure deposit flows.
Raw quantities are retained for later price-neutral analysis.

## Deliberate historical backfill

`scripts/backfill_tvl.py` collects daily UTC closes in restartable monthly batches,
newest first, using the native collector and exporter. It writes checkpoints and
export evidence beside the outputs. Run from this checkout with an explicit
logical database, reviewed finalized-head manifest and deliberate date range:

```bash
PYTHONPATH=src python scripts/backfill_tvl.py \
  --db staging --heads /path/to/verified-heads.json --env /path/to/.env \
  --out artifacts/tvl-backfill --from-date 2026-01-01 --to-date 2026-01-31
```

This writes source observations and exports, not a selected hosted release.
Coordinate all writers; do not assume its local process lock fences every native
or independent publisher. Interrupted collections create a new run; interrupted
exports reuse their saved collection. A repeated command reuses completed monthly
checkpoints, including completed attempts with explicitly incomplete source data.
Inspect those gaps separately. Current exports use the exporter's unified-curation
default; per-chain exports do not establish a combined cross-chain headline.
Old collection runs remain inspectable only while retained by canonical history.

Archive settings serve ordinary reads too. `ARCHIVE_RPC_URI_FOR_<id>`,
`ARCHIVE_RPC_URL_<id>`, and `RPC_URI_FOR_<id>` precede named chain endpoints.
Missing prices/ownership, unavailable RPCs and unverified historical discovery
remain explicit. Sonic and Berachain are outside the configured TVL scope;
stored source records are not deleted merely because they are excluded.
Historical receiver/router evidence must be read at the collected block.

Prepare matching history and analytics before [hosted publication](hosted-api.md).
See [history preparation](../reference/tvl-history-preparation.md),
[curation indexing](../reference/curation-indexing.md) and
[comparison review](tvl-comparison.md). The original backfill's fixed dates,
SQLite path and measured coverage are in a [dated record](../archive/rollouts/2026-10-tvl-backfill.md).

[Documentation index](../README.md).
