# Nightly refresh

The repository supplies a scheduler adapter and systemd templates. An operator installs the
service, configures user lingering, tests a supervised publication and enables the timer
through the steps below.

## Configure an installation

Start with [development setup](../development/setup.md) and the environment/provider
settings in `.env.example`. Each operator chooses a database, report chains,
additional TVL-only chains, confirmation overrides and hosted API origin. The example
uses an Ethereum-only staging scope. Financial preparation includes all retained
history within the chosen scope through the cutoff.

Keep installation settings and machine notes in ignored `.deployment/nightly/`.
Keep credentials in the ignored `.env`; the runtime checkpoint and publication data
use the directories selected in the local config.

```bash
install -d -m 700 .deployment/nightly
install -m 600 ops/nightly-refresh.example.json .deployment/nightly/config.json
```

Edit the copied config. Supply a reviewed `initial_baseline_job` and the HTTPS
`api_origin` for the selected database. Configure that database's credentials in `.env`.
A new database needs a supervised [native baseline publication](refresh.md)
before scheduling. The example's required baseline and origin fields are blank.

| Field | Meaning |
| --- | --- |
| `database` | Explicit named database: `staging` or `neon`. |
| `chains` | Native report-chain names used for reports, fees and TVL. |
| `extra_tvl_chains` | Additional TVL-only chain IDs outside the report scope. |
| `confirmations` | Explicit per-chain finality overrides, such as `{"ftm": 64}` when Fantom is included. |
| `initial_baseline_job` | Reviewed selected native receipt used to seed a fresh installation. |
| `api_origin` | HTTPS origin serving the matching selected publication. |
| `publication_root` | Publication files directory, resolved relative to this JSON. |
| `state_dir` | Wrapper checkpoint directory, resolved relative to this JSON. |
| `max_invocations`, `retry_seconds` | Native invocation budget and verification retry interval. |

The example paths are relative to `.deployment/nightly/config.json` and resolve to
`artifacts/nightly/` under the checkout. Choose persistent locations deliberately.
For an existing installation, preserve its resolved publication/state directories and
accepted checkpoint when relocating configuration.

The operator supplies `initial_baseline_job` from a reviewed selected native receipt.
Its receipt must match the live selection. Before acquisition the adapter reads its
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

Use a dedicated environment for each service checkout. From the repository root:

```bash
UV_PROJECT_ENVIRONMENT=.deployment/nightly/venv uv sync --locked --extra dev
PYTHONPATH="$PWD/src" .deployment/nightly/venv/bin/python -m yearn_data.nightly \
  --config .deployment/nightly/config.json --env .env --check
```

`--check` validates local configuration and prints the source-module path. Its checks use
local files and configuration only.
Check that its package path belongs to this checkout. Python follows the repo's
`.python-version`. The service sets an explicit source path and uses this dedicated
environment; deploy code and dependencies explicitly into that environment.

The ignored `.env` supplies writer credentials and price/indexer/provider settings
for the selected scope. Additional TVL chains use `ARCHIVE_RPC_URL_<CHAIN_ID>`;
`.env.example` lists supported overrides. Existing refresh GitHub warning reporting
uses its approved template and environment configuration.

Record the chosen checkout, Python executable, config/environment locations, schedule,
compatibility links and operational verification references in
`.deployment/nightly/README.md`. General procedures remain in this guide.

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
PYTHONPATH="$PWD/src" .deployment/nightly/venv/bin/python -m yearn_data.nightly \
  --config .deployment/nightly/config.json --env .env --adopt-baseline REVIEWED_JOB_ID
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
continue beyond the nightly window. Inspect journal failures, receipt warnings and
the verified publication cutoff. Record each installation's monitoring in its local runbook.

## Render, test and install disabled

From the repository root, render service files with the actual checkout and runtime.
The helper validates configuration with the selected Python's offline `--check`, checks
its source-module identity, and writes the two unit files into the ignored output
directory. Installation and activation are explicit subsequent steps.

```bash
.deployment/nightly/venv/bin/python scripts/install_nightly.py \
  --python "$PWD/.deployment/nightly/venv/bin/python" \
  --config "$PWD/.deployment/nightly/config.json" --env "$PWD/.env"
systemd-analyze --user verify \
  .deployment/nightly/units/yearn-data-nightly.service \
  .deployment/nightly/units/yearn-data-nightly.timer
systemd-analyze calendar '*-*-* 02:00:00 UTC'
```

Use `--repo` for another checkout, `--output-dir` for a separate rendered directory,
and `--python` for a dedicated existing virtual environment. The helper uses its bundled
templates and validates the selected checkout's wrapper. The renderer quotes paths,
escapes systemd specifiers and preserves the selected virtual-environment executable.
The tracked `.service.in` contains placeholders; the rendered `.service` contains the
installation's paths.

Validate the adapter and installer behavior offline:

```bash
.deployment/nightly/venv/bin/python -m pytest -q tests/test_nightly.py tests/test_install_nightly.py
.deployment/nightly/venv/bin/python -m ruff check .
```

Scheduler tests mock the native worker, collectors and HTTP boundaries. Installer tests
exercise portable path rendering, offline checks and invalid-input handling. The existing
worker/Postgres suites own SQL, accounting, atomic publication and consolidation.

For a new installation, copy the rendered units and reload the user service manager:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 644 .deployment/nightly/units/yearn-data-nightly.service \
  .deployment/nightly/units/yearn-data-nightly.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user is-enabled yearn-data-nightly.timer
systemctl --user is-active yearn-data-nightly.timer
```

Expect `disabled` and `inactive` (their commands return nonzero for those states).
For an existing installation, stop the timer and coordinate active work before replacing
its units. Preserve its checkpoint and financial scope, inspect the rendered paths, and
restore its reviewed schedule after verification. Configure user lingering when ready for
unattended execution. Recovery requires retained datasets or a restorable backup.

A supervised `--run` writes to the selected database, publishes, consolidates and may
update the existing approved GitHub warning issue. Complete that acceptance run after
reviewing the installation, then explicitly enable/start the timer. Record the service
result, native receipt, hosted identities and next activation in the local runbook.

For inspection after installation:

```bash
systemctl --user status yearn-data-nightly.service yearn-data-nightly.timer
journalctl --user -u yearn-data-nightly.service --since yesterday
```

Restrict GitHub reports to approved public summary fields; keep credentials, private preview
URLs and raw provider exceptions in private operational evidence. See [publication
recovery](refresh.md#publication-and-recovery)
for native selected-cleanup-failed and changed-code recovery.

## Repository renames and path reconciliation

A checkout move changes service paths and can leave saved receipts using an earlier
path. Record the old and new destinations in the installation runbook. The package name
`yearn_data` and service/timer names `yearn-data-nightly` remain stable across checkout
names.

Nightly state compares resolved publication-directory paths, so a compatibility
symlink to the same directory preserves the existing baseline. The next saved
checkpoint records the canonical path. A missing alias, a different destination,
or a change to chains, confirmations, database or API origin requires operator
reconciliation through the existing scope guard.

For a checkout rename:

1. Stop the timer and coordinate any running service before changing paths.
2. Preserve the saved state, publication directory, dedicated runtime and environment.
   Keep a compatibility symlink when retained receipts still refer to the old path.
3. Render the service with the new checkout and its selected Python, config and
   environment paths. Install the reviewed units and reload the user service manager.
4. Run configuration validation and compare the saved baseline with the selected
   database release and hosted publication. Preserve pending jobs and historical bounds.
5. Start the installed service under supervision. Require successful exit, hosted
   verification and a fresh cutoff before calling the update operational. Restore
   the timer and confirm its next 02:00 UTC activation.

Check the verified cutoff and service result after the next overnight run. Timer
activation alone confirms scheduling; publication freshness confirms delivery.

[Documentation index](../README.md).
