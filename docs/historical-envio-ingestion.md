# Import historical reports from Envio

Use Envio to find Yearn vaults and import their V2 and V3 reports.
Use Kong to fill missing vault and asset details.

## Set the data source

Set `YEARN_ENVIO_GRAPHQL_URL` to the Envio endpoint.
Discovery and report imports use Envio by default.

To use the older RPC path, set `YEARN_DATA_EVENT_SOURCE=rpc`.
Volume and legacy fee indexing always use RPC.

Envio discovery and report imports cover Yearn-managed vaults only.
Non-Yearn factory vaults require the RPC workflow for both commands:

```bash
YEARN_DATA_EVENT_SOURCE=rpc yearn-data discover --chains eth --include-non-yearn
YEARN_DATA_EVENT_SOURCE=rpc yearn-data index-events --chains eth
```

Continue running both RPC commands to keep the non-Yearn reporting dimension
current. Switching report imports to Envio does not refresh those rows.
`discover --find-deployment` and `run lifetime-yield --find-deployment` require RPC.
`discover --to-block` requires Envio; unsupported flag/source combinations fail.

The default chains are Ethereum, Polygon, Base, Arbitrum, and Katana.
Optimism and Fantom are configured for explicit historical RPC work.
They do not have a configured Envio source.
Polygon and Katana have no configured V2 discovery source. Discovery leaves
existing V2 membership flags unchanged on those chains; it does not establish
whether historical V2 deployments exist there.

## Import a block range

1. Select one chain.
2. Set `FROM_BLOCK` to the first block to import.
3. Set `TO_BLOCK` to the last block to import. Envio must have processed this block.
4. Run discovery, then import the reports:

```bash
yearn-data --db data/yearn.sqlite discover --chains eth --include-retired --to-block "$TO_BLOCK"
yearn-data --db data/yearn.sqlite index-events --chains eth --include-inactive --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK"
```

Both commands reject an upper block that Envio has not processed. Rejected
bounds do not advance the report or inventory cursors.

An import for a specific range requires both block limits.
Use `--versions v2` to select V2 reports.
Use `--vaults ADDRESS ...` to select specific vaults.

These commands do not check whether the RPC considers the last block finalized.
Select the upper block limit before you run them.

## Include historical vaults

Discovery keeps vaults that previously belonged to Yearn.
It does not mark retired vaults as active again.
When newer membership events are already cached, discovery at an earlier block
preserves the existing active flag for those vaults, including later additions
and removals.
Use `discover --include-experimental-v2` to include experimental V2 registrations.

A registration event does not prove when a vault was deployed.
Factory creation and V2 experimental-vault creation events can fill a missing
deployment block, including for an already cached or subsequently endorsed vault.
Registry endorsement and role-manager membership alone leave deployment unknown.
The importer keeps reports that occurred before registration.
It does not assume token decimals when asset details are missing.
Inventory vaults absent from Kong are retained with nullable metadata. Progress
identifies each address with unresolved asset metadata. Their raw reports can
still be imported; missing decimals and the corresponding valuations stay
unknown. Refreshing catalog metadata does not rewrite previously stored reports.
When Kong omits an optional name, asset symbol, or API version, discovery keeps
the existing non-null cached value. A supplied non-null value can refresh it.

Earnings analysis can use stored reports from inactive vaults.
`run lifetime-yield` discovers retired vaults and includes their reports, so a
vault removed between runs still contributes its final harvests. Retired vaults
remain inactive. The separate discovery and report commands keep their explicit
`--include-retired` and bounded-import `--include-inactive` controls.

## Repair stored report metadata

When catalog metadata resolves but stored reports still lack an asset or decimal
scale, report imports name each affected vault and its stored block range.
Nonzero USD amounts stay unknown until that metadata is reconciled. Repeating
`index-events`, including a bounded replay, does not update existing report rows.

Use the explicit `catch-up` command provided by the bounded report catch-up layer
([PR #23](https://github.com/yearn/yearn-metrics/pull/23)). A checkout containing
only this ingestion layer does not yet provide that command. Once it is available,
set `FROM_BLOCK` and `TO_BLOCK` to cover the ranges named in the diagnostics:

```bash
yearn-data --db data/yearn.sqlite catch-up --chain eth \
  --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK"
yearn-data --db data/yearn.sqlite price --chains eth
yearn-data --db data/yearn.sqlite analyze lifetime-yield
yearn-data --db data/yearn.sqlite export lifetime-yield --out exports
```

Configure the selected chain's RPC endpoint as well as Envio. Catch-up checks
block evidence through RPC even when reports come from Envio. It reconciles
asset/decimal metadata only when the raw report evidence agrees, commits metadata
and coverage together, and stops on conflicting raw amounts or block evidence.
Report identities and raw amounts are preserved. The commands above are an
operator-run repair; ordinary discovery and forward imports do not run it.

## Repeat or resume an import

You can repeat the same block range. Existing report identities are not added again.
The reported count includes fetched reports that were already stored.

The importer saves each completed window before it requests the next window.
A failed request does not advance the saved position.
An import for a specific range leaves the normal forward cursor unchanged.
A cursor is the saved position for later imports.

Imports without a specific range use one forward cursor per chain and event entity.
They also save completed coverage per vault and event entity. A newly discovered
vault automatically catches up from block zero, then joins ordinary forward
imports. Registration is not used as a deployment bound. Catch-up progress names
each affected address, and completed windows survive retries.
An existing database with only shared cursors replays each vault's history once
to establish this coverage; existing report identities remain deduplicated.
Forward `index-events` imports always include retained inactive Yearn vaults,
matching `run lifetime-yield`. Active-only imports require an explicit block
range and leave the shared forward cursor unchanged.
Use a bounded `--include-inactive` replay for explicit historical repairs.
Bounded replay changes neither shared nor per-vault forward coverage.

## Handle invalid responses

The importer stops if a response has any of these problems:

- A required event entity is missing.
- A report has the wrong chain, address, or block range.
- A page does not advance beyond the previous page.

An invalid response is not treated as proof that a range has no events.
Address filters accept both lowercase and checksum address forms.
Permanent HTTP errors fail immediately. HTTP 429, server errors, and connection
failures have at most five attempts, with no sleep after the final attempt.
Pagination continues until an empty page. A nonempty short page can reflect a
server response cap rather than the end of the range; it does not complete the
window or advance its forward cursor on its own.

Run pricing, analysis, and export as separate commands after the import.
