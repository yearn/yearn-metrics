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

`initial_baseline_job` only seeds an installation with no `state.json`. Once that
file exists, `state.json:lastJob` is authoritative. Editing the bootstrap setting
does not replace an existing baseline. Use explicit adoption below after a manual
publication, and never delete pending state to make a different baseline fit.
For a fresh installation whose configured candidate has been superseded, either
set the bootstrap job to a reviewed current selected native receipt or initialize
state with `--adopt-baseline REVIEWED_JOB_ID`. The shipped historical candidate is
not an instruction to trust a stale release; both paths enforce the live match.

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

Transient RPC transport/HTTP failures and lagging finalized heads on TVL-only
chains record sanitized `extraTvl.unavailableChains` warnings and allow the report
chains to proceed. A missing catalog on such an unavailable chain is not treated
as an empty vault inventory. Wrong-chain endpoints, authentication/configuration
failures, TLS failures and unclassified errors still stop the run.

Next it calls `run_refresh(..., mode='publish')` with `--since` semantics. Native
budgets, report acquisition, accounting, preparation, pin verification, atomic
selection and consolidation remain owned by that worker. The wrapper never calls
the older single-chain update script. Its first progress event durably saves the
job ID. Budget pauses and unfinished cleanup resume the same job and original
cutoff. It permits twelve passes per service invocation, returning exit 3 if work
or HTTP verification is still pending. Other failures return exit 2.

The worker emits `refresh-job-ready` immediately after obtaining its durable
job. Proven read-only preflight failures (`RefreshNotStarted`) and writer-lock
contention (`RefreshBusy`) permit a later retry without a new cutoff. An unknown
connection/commit outcome during job allocation remains ambiguous and requires
inspection; it must not be treated as proof that no job exists.

Successful selection is persisted before HTTP verification. The wrapper verifies
the hosted release, fee/TVL/analytics IDs, analytics cutoff and latest TVL close,
including pinned fee and TVL consumer responses. The selection is checked again
after those reads. Failures retry verification without republishing. After recovery
of an older cycle, it starts a new cycle through today's cutoff if the invocation
budget allows. A verified baseline advances only after HTTP checks succeed.

Failed verification records a stable reason code, exception class and any HTTP
status in pending state and a `hosted-verification-failed` journal event. Integrity
mismatches, HTTP failures and invalid responses remain distinguishable without
storing provider exception text, URLs, credentials or response bodies. API origins
with any userinfo are rejected by local configuration checks.

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
deliberately. Transient failures after a saved job ID, and proven pre-job failures,
can resume without replacing state. The journal gives owned stop conditions stable
codes such as `unknown-job-id`, `baseline-selection-drift`, `scope-changed`,
`writer-busy` and `checkpoint-rejected`; arbitrary provider/DB messages stay hidden.

After a separately reviewed manual publication, use this explicit operation to
adopt its selected native refresh receipt as the existing installation's baseline:

```bash
PYTHONPATH="$PWD/src" .scratch/nightly-venv/bin/python -m yearn_data.nightly \
  --config ops/nightly-refresh.json --env .env --adopt-baseline REVIEWED_JOB_ID
```

Adoption takes the local wrapper lock, initializes missing state or updates idle
state, and refuses changed-scope or pending state. It requires a selected,
consolidated, publish-ready receipt covering the
configured chains, matches it to the live database selection, and verifies the
hosted API. Only then does it atomically update local `lastJob` and `lastVerified`.
It does not acquire data, change the database selection or publish a release.
Coordinate other writers while adopting. If work is pending, recover that work
first; adoption is not an escape hatch for an interrupted job.

### Required handoff after a manual publication

A manual publication is not operationally complete until the nightly baseline
has been reconciled. A selected API release and `state.json:lastJob` must refer
to the same completed native refresh. Otherwise the next activation deliberately
stops with `baseline-selection-drift` before acquisition.

Use the deployed checkout and its dedicated interpreter for this procedure:

1. Stop `yearn-data-nightly.timer` before starting another acquisition or
   publication writer. Stopping the timer does not stop an already running
   service: inspect `yearn-data-nightly.service` and finish or recover active
   work first. Coordinate every independent writer, including direct publication
   tools, which do not necessarily take the native advisory lock.
2. Finish the supervised native publication and consolidation. Record its job
   ID, cutoff and selected release ID. Preserve any pending wrapper job. Do not
   substitute a release ID for a job ID, edit an old receipt, or delete state.
3. If the publication was made outside the native worker and has no matching
   selected receipt, run a supervised native refresh first. A reviewed previous
   receipt can supply acquisition bounds with `--since`; the worker rechecks
   its chain pins and prepares all retained financial history. This is a new
   job, not a resume of a superseded selected publication. Include the configured
   extra TVL chains in the supervised acquisition. Inspect warnings and readiness
   before accepting the result; never fabricate a receipt for the external release.
4. Run `--adopt-baseline REVIEWED_JOB_ID` as shown above. Adoption must succeed
   against the live database and hosted API. Changing `initial_baseline_job`
   does not update an existing installation. Retain the verification evidence.
5. Test the installed service, rather than only running `--check`:

   ```bash
   systemctl --user reset-failed yearn-data-nightly.service
   systemctl --user start yearn-data-nightly.service
   systemctl --user show yearn-data-nightly.service -p Result -p ExecMainStatus
   journalctl --user -u yearn-data-nightly.service --since today
   ```

   Confirm `Result=success`, `ExecMainStatus=0`, no pending state, and the saved
   baseline matches the current selected release. A same-day run can correctly
   do no acquisition when its verified cutoff is already current. If another
   writer changes selection during this handoff, inspect and reconcile again.
6. Restore scheduling only after those checks pass:

   ```bash
   systemctl --user enable --now yearn-data-nightly.timer
   systemctl --user list-timers yearn-data-nightly.timer
   systemctl --user is-enabled yearn-data-nightly.timer
   systemctl --user is-active yearn-data-nightly.timer
   ```

   Verify the next activation is 02:00 UTC. Record deployed source revision,
   native receipt, hosted verification, service result and next activation in
   the operational handoff. Check the next morning's journal and verified cutoff;
   an enabled timer alone is not proof of successful publication.

The November 5, 2026 activation demonstrated this failure: an independent
publication superseded the saved November 4 baseline without a matching native
receipt or adoption. The service stopped before acquisition, retried twice and
hit its start limit. The baseline guard worked; the missing operational step was
the manual-publication handoff above. Deployment must also update the actual
service checkout: merging a wrapper fix does not change the VM's files.

Native `refresh --since` rejections retain trusted baseline-validation reasons in
CLI output and receipts through the `RefreshNotStarted` signal. Provider/database
errors expose their class rather than raw messages or endpoints.

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
