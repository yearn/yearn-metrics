> Historical reference captured from `docs/legacy-service-retirement.md` at `ef03f16`.
> This record captures a past design, measurement or rollout. Use the current
> guides and live checks for ports, IDs, branch state and recovery options.
> Current guidance: [docs/operations/hosted-api.md](../../operations/hosted-api.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/legacy-service-retirement.md).

# Legacy TVL and fees service retirement

The local `yearn-tvl-service` and `yearn-fees-service` APIs were retired on
October 13, 2026. Current Powerglove stats use the Yearn Data hosted API and its
selected database-backed publication. No Vercel deployment or accounting change
was part of this retirement.

## Comparison ownership

`analytics_collect.collect()` fetches DefiLlama protocol observations directly
and stores their timestamps, payload hashes and source URLs in `analytics_inputs`.
`analytics.prepare()` embeds the selected references into prepared comparison
results. HTTP requests read prepared publications. Explicit writer operations own collection
and publication.

The old preview on port 3491 used `--comparison-db` to read DefiLlama observations
from the legacy TVL database. That preview and its 3492 socket forwarding bridge
are retired. The `--comparison-db` option has been removed; use `--analytics`
to serve comparisons from the selected prepared publication. Their service/socket
definitions were removed from `ops/`, and the installed user units are masked. No
replacement external SQLite database is needed.

Independent live reference acquisition and storage were verified against the
current source in an isolated database with an empty local catalog. This check
fetched both protocols and their provenance without touching the legacy databases
or changing the selected Neon publication. It did not repeat full-catalog RPC
membership/fee acquisition.

## Retired local infrastructure

- QTOV and old pairing frontend previews on ports 4178 and 4598.
- Legacy TVL and fees APIs on ports 3460 and 3482, including leftover module-load
  processes holding SQLite handles.
- Old Neon preview on 3491 and socket forwarding on 3492.
- The four private routes that pointed to these services.
- The two stale frontend worktree registrations, with branches and local files
  preserved. A tiny dependency symlink at the old QTOV path redirects a remaining
  unrelated preview's esbuild path to identical dependencies in the main checkout.

The maintained owned analytics API on 3493, hosted APIs on 3494/3495 and current
Powerglove previews remain running. The compressed historical TVL evidence preview
also remains available.

## Database recovery

Both legacy database paths are now notice directories to prevent old commands
silently recreating empty databases. Verified archives are retained locally at:

- `data/legacy-service-retirement/yearn-tvl.db.zst`
- `data/legacy-service-retirement/yearn-fees.db.zst`

Snapshots used SQLite's backup API so committed WAL records were included. Every
table's ordered row hash and count matched the source; full decompression matched
the snapshot SHA-256; restored integrity checks passed and foreign-key results
matched. Only unchanged source database/WAL files were removed. WAL files were
never discarded independently.

Restore to a **new**, isolated destination:

```bash
zstd -d data/legacy-service-retirement/yearn-fees.db.zst \
  -o /tmp/restored-legacy-fees.db
sha256sum /tmp/restored-legacy-fees.db
```

Compare against `consistent_snapshot_sha256` in
`artifacts/storage-cleanup-20261013/retirement/database-retirement-execution.json`.
That record also retains schema, row counts, table digests and foreign-key results.

Private local unit definitions and worktree file archives are retained in the same
retirement evidence directory. Keep these recovery artifacts private and available to the
recovery procedure.
Restarting retired infrastructure requires deliberately unmasking/restoring its
units and restoring databases to new paths; retain the retirement notices throughout recovery.

## Validation

Focused analytics, hosted API and comparison tests passed (48 passed, 2 skipped).
Frontend routing/fetch tests passed (10 tests), focused formatting passed and the
Powerglove production build passed. Current TVL, fees, comparison and audit routes
returned HTTP 200 after legacy shutdown. Final runtime checks and recovery hashes
are retained with the retirement evidence.
