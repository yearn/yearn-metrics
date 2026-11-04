# Nightly VM refresh

The repository contains a scheduler adapter and systemd **templates only**.
Adding these files does not install, start or enable a service, change the VM's
linger setting, update the database, or publish a release.

## Scope and runtime

`ops/nightly-refresh.json` preserves the October 30 production acquisition scope:
reports/fees and TVL on Ethereum, Optimism, Polygon, Fantom, Base, Arbitrum and
Katana; TVL-only discovery/collection on Gnosis (100), HyperEVM (999) and Yeer
(4663). Fantom uses 64 confirmations. Other report chains retain native source
and finality policies. The seven report chains are not the whole TVL scope.
Financial preparation continues to include all retained history through the cutoff.

The initial baseline is the selected October 30 refresh job
`167c385affbf423c99388cbb7168bd86`. This is a bootstrap candidate, not an
assertion about today's live database. Before any acquisition the adapter reads
its authoritative receipt, requires successful selection/consolidation and checks
that its release still matches the database selection. A newer manual publication
therefore stops bootstrap until the configuration is deliberately updated to its
reviewed selected refresh job. A publication without such a receipt requires a
separate supervised baseline run; do not fabricate a receipt or reset history bounds.

Use a dedicated environment to avoid the existing `.venv` editable installation
pointing at a different checkout. From the repository root:

```bash
UV_PROJECT_ENVIRONMENT=.scratch/nightly-venv uv sync --locked --extra dev
PYTHONPATH="$PWD/src" .scratch/nightly-venv/bin/python -m yearn_data.nightly \
  --config ops/nightly-refresh.json --env .env --check
```

`--check` validates local configuration and prints the source-module path. It does
not load credentials, connect to Postgres, call providers, or write run state.
Check that its package path belongs to this checkout. Python follows the repo's
`.python-version`. The service sets an explicit source path and uses this dedicated
environment; it does not update code or dependencies automatically.

The ignored `.env` needs production writer credentials, the existing price/indexer
credentials and archive RPCs for all ten chains. TVL-only RPC overrides are
`ARCHIVE_RPC_URL_100`, `ARCHIVE_RPC_URL_999` and `ARCHIVE_RPC_URL_4663`, as listed
in `.env.example`. Existing refresh GitHub warning
reporting remains governed by its approved template and environment configuration;
the wrapper adds no new external notification destination.

## Execution and recovery

The timer requests 02:00 UTC every day, with one-minute accuracy and
`Persistent=true`. A missed schedule produces a catch-up activation when the user
manager returns. The worker cutoff is the activation day's closed UTC midnight,
independent of local daylight-saving time. A run at 02:00 UTC covers through
23:59:59 UTC on the preceding day.

The wrapper freezes a pending cutoff and baseline before work. It first acquires
TVL-only catalogs and missing closes using the existing collectors and missing-close
planner under the native database advisory lock. At most 30 extra chain/date
attempts run per pass. Completed catalog/day attempts are checkpointed; incomplete
source evidence and unavailable prices remain recorded as warnings. A completed
attempt with unavailable units does not retry forever. Historical repair stays
separate, just as in the native refresh worker.

Next it calls `run_refresh(..., mode='publish')` with `--since` semantics. Native
budgets, report acquisition, accounting, preparation, pin verification, atomic
selection and consolidation remain owned by that worker. The wrapper never calls
the older single-chain update script. Its first progress event durably saves the
job ID. Budget pauses and unfinished cleanup resume the same job and original
cutoff. It permits twelve passes per service invocation, returning exit 3 if work
or HTTP verification is still pending. Other failures return exit 2.

Successful selection is persisted before HTTP verification. The wrapper verifies
the hosted release, fee/TVL/analytics IDs, analytics cutoff and latest TVL close,
including pinned fee and TVL consumer responses. The selection is checked again
after those reads. Failures retry verification without republishing. After recovery
of an older cycle, it starts a new cycle through today's cutoff if the invocation
budget allows. A verified baseline advances only after HTTP checks succeed.

State and per-job receipts live under ignored `artifacts/nightly/`, with private
permissions and atomic, flushed writes. `state.json` distinguishes pending work
from `lastJob` and `lastVerified`; `JOB_ID-verification.json` retains TVL-only
warnings and verification evidence. Financial checkpoints remain in Postgres.
Do not delete local state to recover an interrupted publication.

Local locking prevents overlapping wrapper runs; extra collection and native
refresh each use the existing database writer lock. There is a lock handoff
between those stages. Independent manual acquisition/publication writers must
remain coordinated, as described in [Database refresh](database-refresh.md).

Code/configuration changes can invalidate native checkpoints. Stop and inspect
instead of dropping the saved job ID. If interruption occurs before any job ID
could be saved, the adapter stops rather than creating a potentially duplicate
job: inspect `refresh_jobs`, identify the matching context and recover the state
deliberately. A superseding manual publication also requires baseline review.
Ordinary transient failures can be resumed without replacing state.

The service allows ten hours per invocation, then requests graceful shutdown;
systemd may terminate it after another 90 seconds. It retries failed invocations
after five minutes, with at most three starts in twelve hours. The next nightly
activation can resume unfinished work. This is bounded retry, not a guarantee
that source outages resolve within a night. Inspect journal failures and receipt
warnings; no separate overdue-publication alert has been installed.

## Review, test, then install disabled

Offline validation does not contact production:

```bash
.scratch/nightly-venv/bin/python -m pytest -q tests/test_nightly.py
.scratch/nightly-venv/bin/python -m ruff check .
systemd-analyze --user verify ops/yearn-data-nightly.service ops/yearn-data-nightly.timer
systemd-analyze calendar '*-*-* 02:00:00 UTC'
```

Tests mock the native worker, collectors and HTTP boundaries to cover scheduler
recovery. The existing worker/Postgres suites continue to own SQL, accounting,
atomic publication and consolidation. CI policy is unchanged.

After review, install templates without enabling or starting them:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 644 ops/yearn-data-nightly.service ops/yearn-data-nightly.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user is-enabled yearn-data-nightly.timer
systemctl --user is-active yearn-data-nightly.timer
```

Expect `disabled` and `inactive` (their commands return nonzero for those states).
The templates assume `%h/raaaws/yearn-data`; change both the working directory
and absolute paths when deploying elsewhere. Configure user lingering only when
ready for unattended execution. Check that backups and recovery are adequate:
canonical consolidation retires superseded releases, so old IDs are not guaranteed
rollback points.

A supervised `--run` **writes to production, publishes, consolidates and may update
the existing approved GitHub warning issue**. Schedule that acceptance run separately
after reviewing these files. Only after it succeeds should an operator explicitly
enable/start the timer. This implementation does not perform those actions.

For inspection after installation:

```bash
systemctl --user status yearn-data-nightly.service yearn-data-nightly.timer
journalctl --user -u yearn-data-nightly.service --since yesterday
```

Never put credentials, private preview URLs or raw provider exceptions into GitHub
reports. See [publication recovery](database-refresh.md#publication-and-recovery)
for native selected-cleanup-failed and changed-code recovery.
