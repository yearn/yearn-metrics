> Historical reference captured from `docs/tvl-history.md` at `ef03f16`.
> This records a past design, measurement or rollout; its ports, IDs, branch
> states and rollback availability are not current operating instructions.
> Current guidance: [docs/operations/tvl.md](../../operations/tvl.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/tvl-history.md).

## Running the historical backfill

`scripts/backfill_tvl.py` collects daily UTC closes in independent monthly runs,
newest month first. It calls the same `collect_tvl` accounting code as the CLI;
batched RPC requests only change acquisition speed. Deployment boundaries are
verified against archive code, and successful prices and dated blocks are cached
in the selected database. It never imports historical TVL values.

The initial October 2026 local job used a separate `data/tvl-backfill.sqlite`.
TVL now owns namespaced tables in the shared database; see
[One database for TVL, fees and earnings](../../operations/postgres.md) for consolidation.
Use the same database path as fee/earnings jobs for future collections. The
catalog comes from independent discovery,
with deployment hints and historical strategy identities from this project's
existing event database. Its finalized-head manifest records the RPC checks.

```bash
PYTHONPATH=src python scripts/backfill_tvl.py \
  --db neon \
  --heads artifacts/tvl-review/backfill/heads.json \
  --env /path/to/.env \
  --out artifacts/tvl-review/backfill \
  --to-date 2026-10-05
```

Restarting the same command reuses saved monthly collections, including those
marked incomplete because of missing data. Interrupted collections are collected
again as a new run; interrupted exports reuse their saved collection. Earlier
runs stay in the database for inspection. The job
writes `status.json` and one export folder per chain/month. A process lock prevents
two backfills from writing to the same database at once.

The current scope starts with the earliest verified vault deployment, no earlier
than January 2020, and ends at October 5, 2026's UTC close. Chains without a usable
finalized archive RPC remain pending. Missing prices or reads remain unknown.
Factory discovery receipts and known candidate mappings define catalog coverage;
this job does not certify that every historical holder or bridge transition has
been discovered. Curation remains separate in the monthly exports. Per-chain
exports do not claim a combined cross-chain headline TVL.

Archive settings also serve ordinary full-node reads. `ARCHIVE_RPC_URI_FOR_<id>`,
`ARCHIVE_RPC_URL_<id>`, and `RPC_URI_FOR_<id>` take precedence over named settings
such as `ETH_RPC_URL`; one archive-capable endpoint is sufficient for both uses.
The local TVL worktree shares the main repository's ignored `.env` via a symlink.
Restart the backfill to load environment changes; saved monthly batches are kept.

Each monthly export gets an `export-complete.json` receipt only after all files
are written. On restart, missing files or a missing/mismatched receipt cause the
export to be regenerated from its saved run, without collecting that batch again.
Older exports without receipts are regenerated once to establish completion.

The October 6 RPC refresh enabled Gnosis, Fantom, Robinhood (4663), and HyperEVM
alongside the existing six chains. Katana discovery uses 50,000-block log ranges
because its RPC rejects larger historical requests. Sonic (146) and Berachain
(80094) are excluded from TVL discovery and collection by project scope. Yearn Prices
namespaces for Gnosis, Sonic, Berachain, and Robinhood are explicitly mapped;
HyperEVM balances can be collected, but its USD prices remain unavailable under
the currently configured Yearn Prices namespaces. These limitations are reported
in the progress preview rather than converted to zero.

Post-run validation found valid zero-padded ABI returns on early Ethereum Yearn
vaults. Scalar getters and strategy tuples now accept a declared ABI prefix with
zero trailing words, and continue to reject short or nonzero trailing data. The
legacy repair retains original runs and re-collects only monthly batches with
failed native reads; missing Yearn Prices quotes remain explicit. Live standard
ABI comparisons and regression checks are retained beside the backfill exports.

Empty V1 vaults are valued at zero under the share-supply TVL method without
calling an undefined price-per-share getter. Residual read retries retain their
original runs and use the same pinned historical blocks.

Sonic and Berachain are excluded even when returned by upstream catalogs or
retained in an existing database. They do not produce pending-RPC notices.
Existing records are retained; the TVL scope excludes them rather than deleting
shared catalog history.
