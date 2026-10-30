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
[ingestion guide](historical-envio-ingestion.md#repair-stored-report-metadata).

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
