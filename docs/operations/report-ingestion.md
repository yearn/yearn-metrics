# Fetch reports for a selected block range

Use `catch-up` to fetch reports for one chain.
Set the first and last block explicitly. Both blocks are included.

## Select the source

| Chains | Default source |
| --- | --- |
| Ethereum, Polygon, Base, Arbitrum, Optimism, Katana | Envio |
| Fantom | RPC |

Set the RPC endpoint for the selected chain.
The command uses RPC to check block details, even when reports come from Envio.
For Envio requests, also set `YEARN_ENVIO_GRAPHQL_URL`.

Use `--source rpc` to select RPC explicitly.
Use `--source envio` only on a chain with configured Envio coverage.
The command does not switch sources after a provider error.

Optimism uses Envio for both reports and registry inventory discovery. Historical
Optimism RPC log queries have returned inconsistent empty successes for known
events; changing filters or reducing the range does not establish completeness.
Use Envio for historical collection. Changing sources does not invalidate saved
coverage. Add `--revalidate-rpc-coverage` to recheck Optimism RPC coverage through
Envio within the explicit `--from-block` / `--to-block` window. Reports are checked
for retained Yearn vaults; add `--discover` to include registry inventory and newly
discovered vaults. This option rejects other chains and `--source rpc`.

Revalidation reuses existing Envio coverage, but ignores RPC coverage when
planning the selected scans. It preserves existing events and unrelated ranges.
An identical RPC coverage row is replaced atomically only after the Envio read
and event writes succeed. Wider overlapping RPC records remain as historical
evidence. After a failure, repeat the same command with the flag; successful
Envio ranges are reused and incomplete ranges remain pending. A count of zero
inserted rows does not mean the read found no events.

```bash
yearn-data --db staging catch-up --chain op --source envio \
  --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK" \
  --discover --revalidate-rpc-coverage
```

Do not delete existing event observations to force a rescan. Revalidation fetches
reports and inventory only; it does not reprice, recompute financial outputs, or
publish a dataset.

## Run the command

Set `FROM_BLOCK` and `TO_BLOCK` before you run this example:

```bash
yearn-data --db neon catch-up --chain op \
  --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK" --discover
```

Keep these block limits in your calling script.
To resume, run the command again with the same limits.
The command does not use a saved scheduler run.

## Select the finality policy

By default, `TO_BLOCK` must be at or below the RPC `finalized` block.
Envio must also have processed the requested range when it is the selected source.

If the RPC finalized block is unavailable or stale, you can select a confirmation buffer:

```bash
yearn-data --db neon catch-up --chain kat \
  --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK" --confirmations 1000
```

This permits blocks up to the latest block minus 1,000 blocks.
It does not claim that the chain protocol has finalized those blocks.

The JSON result records the selected block number, hash, timestamp, and finality policy.
Keep this result with your acquisition records.

## Select the vaults

The command includes all stored Yearn vaults on the selected chain.
This includes inactive vaults and vaults absent from the current registry.

`--discover` also checks configured registries and role managers within the requested range.
It adds newly found vaults to the stored vault selection.
Use `--include-experimental-v2` to include experimental V2 registrations in this search.
Previously stored experimental vaults remain available on later runs.

Discovery also restores cached non-Yearn classifications when the selected
registry or role-manager facts prove membership. Factory creation alone does not
qualify. Registration does not supply a deployment block; existing deployment
evidence and other cached metadata are preserved.

For V3 vaults, the stored active flag follows the latest recorded role-manager
action, including actions after the requested window.

This search does not prove that every historical vault has been found.
A newly found vault can have reports before `FROM_BLOCK`.
Fetch an earlier range if you need those reports.

The command asks Kong for missing asset details.
If necessary, it then requests historical details through RPC at the selected block.
It stops if required details remain missing or the vault release is unsupported.
When the RPC fallback confirms no contract code at `TO_BLOCK`, the command lists
that vault in `skipped_vaults` with its address, block and reason, then scans the
other vaults. It does not certify report coverage for the skipped vault. An RPC
failure is not proof of absence; it stops with the vault and block in the error.

## Resume after a failure

The command saves each successful range and its observations in one database transaction.
This also applies to ranges with no events.
A failed range has no success record.

Completed ranges can be reused, even if you change `--chunk-size`.
The command checks saved block hashes before it reuses coverage.
Matching event identities are not added again.
Conflicting amounts or hashes cause an error.
New transaction hashes use lowercase. Replay also recognizes legacy mixed-case
hashes without renaming keys that other tables may reference. If existing rows
already duplicate one identity under different spellings, the command stops and
requests explicit reconciliation; it does not choose or delete those rows.

Correcting a vault's asset or decimals requires a new coverage scan. When the
underlying report event matches, catch-up reconciles those two report metadata
fields with the selected vault and commits the corrected report and coverage
together. Coverage under obsolete metadata policies is retired in that same
transaction, so changing back to an earlier value also requires a rescan.
Raw event conflicts still stop the scan.

Envio imports name vaults and block ranges whose catalog metadata has resolved
while stored report metadata remains missing. Use those ranges with this command;
ordinary forward imports and `index-events` replay do not perform reconciliation.
After repair, rerun pricing, analysis, and export as described in the
[ingestion guide](#repair-stored-report-metadata).

If a request fails:

1. Read the error.
2. If the provider rejects the range size or returns its 10,000-log limit, reduce `--chunk-size`.
3. Run the same block range again.

Investigate conflicting evidence before you retry.
The command does not split failed requests or retry indefinitely.

## Understand the scope

Stored reports and older cursors do not prove that an unscanned range is complete.
The command leaves those records intact and does not advance the older forward cursor.
It leaves existing coordinator tables intact but does not use a coordinator run.

Run fee accounting, pricing, analysis, and export separately.
Use a separate command for Tokenized Strategy fees.
`catch-up` does not fetch volume or debt flows, change financial outputs, or copy the database.

## Grouped fetching and recovery

Native report adapters group up to 50 compatible vault addresses with identical
missing intervals. Each returned event is checked against the requested emitter
set and normalized with that vault's asset metadata. Envio pagination continues
through an empty terminal page, including when the server returns short pages.
RPC decodes the requested event signatures and rejects logs outside the emitter
set or block range.

Reads use bounded source-specific windows that grow on fast sparse results and
split on explicit provider limits or bounded timeouts. Provider address-count
limits fall back to smaller address groups. Coverage remains per vault and
interval, including verified empty results. Failed fetches certify no coverage.

Registry inventory scans also fetch concurrently, with membership materialized
after all required scans finish. Concurrent requests are processed in completion order. Already committed valid
ranges remain reusable if another range fails, even when they are later in block
order. Repeating the same command fetches only the remaining gaps. All database
writes and canonical end-hash checks stay on the coordinating thread.

Optimism Envio report and registry reads require the producer's
`chain_metadata.start_block` attestation, including reads that return no events.
A missing, invalid, or later start block rejects the range before observations
or replacement coverage commit. The existing upper watermark and fresh canonical
end-hash checks still apply. A chain start attestation does not by itself prove
that every contract subscription or dynamic registration is complete; producer
configuration and replay checks remain separate requirements.

[Documentation index](../README.md).

## Discovery and forward report import

The following discovery/index-events workflow is distinct from the explicit
`catch-up` block window above. Its forward cursors and bounded replay flags have
their own semantics; do not substitute one command for another during recovery.

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

The default chains are Ethereum, Polygon, Base, Arbitrum, Optimism, and Katana.
Optimism uses the same Envio report and registry inventory collection as the
other default chains. Fantom remains configured for explicit historical RPC
work and has no configured Envio source.
Polygon and Katana have no configured V2 discovery source. Discovery leaves
existing V2 membership flags unchanged on those chains; it does not establish
whether historical V2 deployments exist there.

## Import a block range

1. Select one chain.
2. Set `FROM_BLOCK` to the first block to import.
3. Set `TO_BLOCK` to the last block to import. Envio must have processed this block.
4. Run discovery, then import the reports:

```bash
yearn-data --db neon discover --chains eth --include-retired --to-block "$TO_BLOCK"
yearn-data --db neon index-events --chains eth --include-inactive --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK"
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
Membership from configured Yearn registries and role managers overrides a cached
non-Yearn classification. This includes retired members when requested. Factory
creation alone, events from other sources, and membership after a bounded
discovery range do not reclassify a vault. Promotion uses the latest recorded
trusted membership for its active flag, including cached events after that range.
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
yearn-data --db neon catch-up --chain eth \
  --from-block "$FROM_BLOCK" --to-block "$TO_BLOCK"
yearn-data --db neon price --source defillama --chains eth
yearn-data --db neon analyze lifetime-yield --price-source defillama
yearn-data --db neon export lifetime-yield --out exports
```

Configure the selected chain's RPC endpoint as well as Envio. Catch-up checks
block evidence through RPC even when reports come from Envio. It reconciles
asset/decimal metadata only when the raw report evidence agrees, commits metadata
and coverage together, and stops on conflicting raw amounts or block evidence.
Report identities and raw amounts are preserved. The commands above are an
operator-run repair; ordinary discovery and forward imports do not run it.
This repair example explicitly selects DefiLlama pricing, so it does not require
`YEARN_PRICE_PROD_KEY`. For authenticated Yearn Prices, use the commands and
configuration in [historical earnings pricing](../reference/pricing.md).

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
