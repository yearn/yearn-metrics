# Nightly VM refresh

The repository supplies a scheduler adapter and systemd templates. An operator installs the
service, configures user lingering, tests a supervised publication and enables the timer
through the steps below.

## Scope and runtime

`ops/nightly-refresh.json` preserves the October 30 production acquisition scope:
reports/fees and TVL on Ethereum, Optimism, Polygon, Fantom, Base, Arbitrum and
Katana; TVL-only discovery/collection on Gnosis (100), HyperEVM (999) and Yeer
(4663). Fantom uses 64 confirmations. Other report chains retain native source
and finality policies. The TVL scope includes the seven report chains and the three TVL-only
chains.
Financial preparation continues to include all retained history through the cutoff.

The configured `initial_baseline_job` supplies a historical bootstrap candidate whose
receipt must match the live selection. Before acquisition the adapter reads its
native receipt, requires completed selection/consolidation and checks that its
release still matches the database. Verify a current receipt before installing.
A publication without a matching native receipt requires a supervised native
baseline run; preserve authentic receipts and reviewed historical acquisition bounds.

`initial_baseline_job` only seeds an installation with no `state.json`. Once that
file exists, `state.json:lastJob` is authoritative. After a manual publication, use explicit
adoption below to change an existing baseline. Preserve pending state and recover its job
before adoption.
For a fresh installation whose configured candidate has been superseded, either
set the bootstrap job to a reviewed current selected native receipt or initialize
state with `--adopt-baseline REVIEWED_JOB_ID`. Both initialization paths verify that the
reviewed receipt matches the live selection.

Use a dedicated environment to avoid the existing `.venv` editable installation
pointing at a different checkout. From the repository root:

```bash
UV_PROJECT_ENVIRONMENT=.scratch/nightly-venv uv sync --locked --extra dev
PYTHONPATH="$PWD/src" .scratch/nightly-venv/bin/python -m yearn_data.nightly \
  --config ops/nightly-refresh.json --env .env --check
```

`--check` validates local configuration and prints the source-module path. Its checks use
local files and configuration only.
Check that its package path belongs to this checkout. Python follows the repo's
`.python-version`. The service sets an explicit source path and uses this dedicated
environment; deploy code and dependencies explicitly into that environment.

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
source evidence and unavailable prices remain recorded as warnings. An attempt with
unavailable units completes with a retained warning. Historical repair stays
separate, just as in the native refresh worker.

Transient RPC transport/HTTP failures and lagging finalized heads on TVL-only
chains record sanitized `extraTvl.unavailableChains` warnings and allow the report
chains to proceed. A missing catalog on an unavailable chain retains an
unavailable-inventory status. Wrong-chain endpoints, authentication/configuration
failures, TLS failures and unclassified errors still stop the run.

Next it calls `run_refresh(..., mode='publish')` with `--since` semantics. Native
budgets, report acquisition, accounting, preparation, pin verification, atomic
selection and consolidation remain owned by that worker. Its first progress event durably
saves the
job ID. Budget pauses and unfinished cleanup resume the same job and original
cutoff. It permits twelve passes per service invocation, returning exit 3 if work
or HTTP verification is still pending. Other failures return exit 2.

The worker emits `refresh-job-ready` immediately after obtaining its durable
job. Proven read-only preflight failures (`RefreshNotStarted`) and writer-lock
contention (`RefreshBusy`) permit a later retry without a new cutoff. An unknown
connection/commit outcome during job allocation requires inspection of `refresh_jobs` to
establish the allocation outcome before retrying.

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
Preserve local state while recovering an interrupted publication.

Local locking prevents overlapping wrapper runs; extra collection and native
refresh each use the existing database writer lock. There is a lock handoff
between those stages. Independent manual acquisition/publication writers must
remain coordinated, as described in [Database refresh](refresh.md).

Code/configuration changes can invalidate native checkpoints. Retain the saved job ID and
inspect the invalidated checkpoint before recovery. If interruption occurs before any job ID
could be saved, the adapter stops for deliberate recovery: inspect `refresh_jobs`, identify
the matching context and recover the state. Transient failures after a saved job ID, and
proven pre-job failures,
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
Adoption updates local baseline state after verifying an already completed publication.
Coordinate other writers while adopting. If work is pending, recover that work
first; adoption requires an idle wrapper with recovered pending work.

### Required handoff after a manual publication

Complete a manual publication’s operational handoff by reconciling the nightly baseline. A
selected API release and `state.json:lastJob` must refer
to the same completed native refresh. Otherwise the next activation deliberately
stops with `baseline-selection-drift` before acquisition.

Use the deployed checkout and its dedicated interpreter for this procedure:

1. Stop `yearn-data-nightly.timer` before starting another acquisition or
   publication writer. An already running service continues after the timer stops: inspect
   `yearn-data-nightly.service` and finish or recover active
   work first. Coordinate every independent writer, including direct publication
   tools, whose locking behavior must be checked separately.
2. Finish the supervised native publication and consolidation. Record its job
   ID, cutoff and selected release ID. Preserve any pending wrapper job. Use the native job
   ID, authentic receipt and preserved wrapper state.
3. If the publication was made outside the native worker and has no matching
   selected receipt, run a supervised native refresh first. A reviewed previous
   receipt can supply acquisition bounds with `--since`; the worker rechecks
   its chain pins and prepares all retained financial history. This creates a new job using
   the reviewed acquisition bounds. Include the configured
   extra TVL chains in the supervised acquisition. Inspect warnings and readiness
   before accepting the result; use the resulting authentic native receipt for adoption.
4. Run `--adopt-baseline REVIEWED_JOB_ID` as shown above. Adoption must succeed
   against the live database and hosted API. Existing installations take their baseline from
   `state.json:lastJob`. Retain the verification evidence.
5. Test the installed service after local `--check` validation:

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
   confirm successful publication through the completed receipt and hosted cutoff.

Record incident-specific IDs, deployed revisions and verification in ignored operational
receipts. Deploy merged wrapper code explicitly to the VM’s service checkout and dedicated
environment.

Native `refresh --since` rejections retain trusted baseline-validation reasons in
CLI output and receipts through the `RefreshNotStarted` signal. Provider/database errors
expose their class; raw messages and endpoints remain private.

The service allows ten hours per invocation, then requests graceful shutdown;
systemd may terminate it after another 90 seconds. It retries failed invocations
after five minutes, with at most three starts in twelve hours. The next nightly
activation can resume unfinished work. These limits bound retry work. Source outages can
continue beyond the nightly window. Inspect journal failures and receipt
warnings; no separate overdue-publication alert has been installed.

## New installation: review, test, then install disabled

Validate configuration and behavior offline:

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
canonical consolidation retires superseded releases, so recovery requires verified retained
datasets or a restorable backup.

A supervised `--run` **writes to production, publishes, consolidates and may update
the existing approved GitHub warning issue**. Schedule that acceptance run separately
after reviewing these files. Only after it succeeds should an operator explicitly
enable/start the timer. Installation, acceptance testing and timer activation are explicit
operator steps.

For inspection after installation:

```bash
systemctl --user status yearn-data-nightly.service yearn-data-nightly.timer
journalctl --user -u yearn-data-nightly.service --since yesterday
```

Restrict GitHub reports to approved public summary fields; keep credentials, private preview
URLs and raw provider exceptions in private operational evidence. See [publication
recovery](refresh.md#publication-and-recovery)
for native selected-cleanup-failed and changed-code recovery.

[Documentation index](../README.md).
