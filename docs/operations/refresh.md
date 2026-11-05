# Maintained database refresh

`yearn-data refresh` coordinates the native collectors, accounting and publication
interfaces. Run it manually or configure a scheduler separately to invoke it. Acquisition
and preparation write the explicitly selected database.
Preparation is the default; selecting a serving release requires `--mode publish`.

For the optional VM scheduler adapter and disabled systemd templates, see
[Nightly VM refresh](nightly.md).
On a VM with that scheduler installed, every manual publication must finish the
[nightly baseline handoff](nightly.md#required-handoff-after-a-manual-publication)
before scheduling is restored. A publication without a matching native receipt
requires a supervised native baseline run first.

## Scope and invocation

Choose a named database, chain scope, an exclusive closed UTC midnight cutoff,
and a local staging root. For example, cutoff `172800` includes observations
before that timestamp and daily TVL closes through `172799`:

```bash
yearn-data --db staging refresh --chains eth op --cutoff 172800 \
  --publication-root ./artifacts/publication --mode prepare \
  --from-block eth:0 --from-block op:0 --first-close 86399 \
  --receipt ./artifacts/refresh-receipt.json
```

These historical fixture bounds illustrate syntax. Choose production bounds from the
reviewed baseline and current finalized chain evidence. Normal acquisition is bounded to the
last seven closed UTC days
(`--window-days N` changes that window). Report bounds start at the window’s first block;
each vault's retained range
coverage still determines which recent ranges need fetching. A chain with no
retained inventory/coverage requires an explicit initial `--from-block`.
TVL planning applies its lower bound in SQL. Older gaps are left for explicit
repair, and all retained financial history still participates in preparation.
If a vault's retained report coverage has unscanned blocks before the window,
the receipt records them in a `report-coverage-gap` warning. The developer
GitHub report lists the affected vaults and block ranges. The scheduled run keeps its
bounded scope and can publish; recover those reports through a separate catch-up.
Use `--from-block CHAIN:N` and `--first-close TIMESTAMP` for deliberate wider
acquisition; `--repair-history` retains the older retained-history repair behavior.
Inactive vaults remain eligible.

For a regular sync, continue after an accepted baseline:

```bash
yearn-data --db staging refresh --chains eth op --cutoff UTC_MIDNIGHT \
  --publication-root ./artifacts/publication --since PREPARED_JOB_ID \
  --receipt ./artifacts/refresh-receipt.json
```

`--since` requires a publish-ready prepared or selected receipt in the same
database covering the requested chains. It uses that receipt's per-chain end
blocks plus one and its cutoff as the start of the next daily TVL window. It
therefore covers missed weeks as well. The baseline block pins are reverified
before acquisition, so an old-chain reorg cannot be skipped by advancing the
window. It cannot be combined with explicit
from-block/first-close bounds or repair-history. Repeat the same flags on resume.
Older gaps and late-discovered vault history before the accepted baseline require explicit
historical repair.

Report fetches use four concurrent workers by default (`--report-workers 1`
restores serial fetching; maximum 32). Compatible vaults with the same missing
block interval are fetched in groups of up to 50 addresses. Envio retains both
producer address spellings and complete cursor pagination; RPC queries use an
address list and the appropriate event ABI. Active and inactive vaults remain
in scope, and coverage is still recorded individually for each vault.

Registry inventory scans also use the bounded fetch pool before membership is
materialized. Free workers are refilled as requests finish, allowing unrelated fetches to
proceed while earlier requests remain pending. Database commits remain on the caller thread,
with fresh canonical-hash verification. Completed independent ranges can be
retained after another request fails; failed and uncommitted ranges are retried.
A later metadata-enrichment failure retains already completed eligible work.

Default report planning windows are 1,000,000 blocks for Envio and 50,000 for
RPC. `--chunk-size N` overrides that default; `--chain-chunk-size op:N` overrides
one chain. Inside each planning window, batched reads start at up to 250,000
Envio blocks or 10,000 RPC blocks, grow on fast sparse responses and shrink after
slow responses. Explicit provider range/result-size limits split further;
timeouts allow at most two additional split levels along a failing path.
Address-limit errors split address batches. Source changes, invalid evidence, rate limits
and other failures retain incomplete coverage with their failure status.
Coverage commits only after a member's entire planned interval was fetched.
These settings are part of checkpoint identity. Catalog and Tokenized collectors
retain their 50,000-block default unless the global chunk size is explicitly set.

Automatic report source routing uses the existing chain
policy; `--source rpc` or `envio` explicitly selects a supported source. The worker
checks chain identity, finality, the selected source watermark and pinned block
hashes. An unavailable source retains its source identity and failure status.
A finality head before the cutoff permits inspection but prevents publication.

`--confirmations CHAIN:N` replaces that chain's finalized-head policy with
`latest-minus-N` (for example `--confirmations eth:64`). Each count must be
positive. The selected policy is stored in Tokenized range evidence; reuse requires a
matching finality policy. Confirmation settings
are part of the checkpoint identity, so changing them requires a new job.

`--deferred-manifest PATH` passes an explicitly reviewed fee-deferral manifest
to the `fee-usd` analysis. Its entries determine which matching events retain a
deferred-evidence disposition in the prepared/published fee outputs; they retain their
deferred status and unavailable amounts. The manifest content is
hashed into checkpoint configuration and analysis provenance. Changing its
contents requires a new job with a new financial scope.

The receipt records fixed per-chain bounds, code/inventory and configuration
hashes. It excludes configuration values, endpoints and private filesystem paths.
Use the same scope, root, flags and environment when resuming:

```bash
yearn-data --db staging refresh --chains eth op --cutoff 172800 \
  --publication-root ./artifacts/publication --mode prepare \
  --from-block eth:0 --from-block op:0 --first-close 86399 \
  --resume JOB_ID --receipt ./artifacts/refresh-receipt.json

yearn-data --db staging refresh --inspect JOB_ID
```

Inspection uses read-only access independently of the writer lock. Progress is newline
JSON on stderr; stdout is a JSON receipt. `--receipt` also writes the receipt
atomically to a file. One refresh holds a dedicated database advisory lock for
the invocation. Coordinate independent acquisition and publication tools sequentially; their
lock behavior is separate from the native worker’s lock. Completed stage inputs and
outputs are checked again before selection; detected changes rerun dependent
stages and a repeatedly changing database fails before selection.

## Work and gaps

The stages acquire reports, discover the TVL catalog and fill maintained Tokenized
Strategy fee ranges for each chain. They then project canonical fees, price
reports and fees, fill missing daily TVL closes, refresh owned analytics inputs,
run earnings and fee accounting with pipelined database output writes, and prepare matched
fee, TVL, history and
analytics datasets. Global financial preparation includes retained database
history through the cutoff; `--chains` defines the acquisition scope; retained reporting
history includes other chains. Future raw TVL observations are retained
while the reporting cutoff hides them from this publication.

`--max-targets` defaults to 10000 shared Tokenized chunk, canonical-fee and pricing
targets per invocation; native projection/pricing pages contain at most 500
items. `--max-dates` defaults to 30 chain/day acquisitions. Exhausting a budget
returns `work-deferred`; resume the same job to continue after committed work.
Normal refreshes acquire new report/fee price targets and preserve existing
quotes and explicit missing/retryable states. Retry the historical missing-price queue
through a separate repair operation. Report prices for previously open days
can still transition from pending after the UTC day closes.

Historical report and fee price repair is an explicit, separately budgeted
operation. Run one writer at a time, inspect results, and repeat only as needed:

```bash
yearn-data --db staging price --source yearn-prices --retry-missing \
  --no-provider-fallback --no-onchain-fallbacks --limit 500
yearn-data --db staging price-fees --retry-missing --before-timestamp UTC_MIDNIGHT --limit 500
```

These commands update price evidence without publishing a release. Run a fresh
refresh preparation afterward to capture corrections. Schedule historical repairs as
separate deliberate operations. Reports include provider coverage gaps and unresolved
statuses, and fee targets with missing metadata remain explicitly unpriceable.

Daily collection fills missing asset-unit observations, including genuine zero.
The coordinator and backfill share a batched archive reader: concurrent Multicall
requests with verified JSON-RPC batch fallbacks, batched code probes, retained
UTC-close block lookups, and persisted exact-day prices scoped to endpoint and
price-evidence policy. Readers are reused across chain/day checkpoints; values
remain keyed by historical block. Missing cached TVL quotes remain unavailable;
`--repair-history` explicitly retries them while reacquiring the selected closes.
Authentication/configuration failures stop acquisition explicitly.
Known units with unavailable prices or ownership evidence are reported as
`deferredTvlRepairs`; their amounts retain the supported availability state. To reacquire
all daily
closes in the selected window, start a job with `--repair-history`; the date
budget still applies. Incomplete catalog/source stages and explicit availability gaps
produce publication warnings; readiness follows the integrity checks below. This applies
across chains,
including aging networks and unreleased products; the worker records upstream catalog and
optional discovery API availability explicitly. Native financial
availability rules still apply: missing evidence stays unavailable.
`publishReady` means publication integrity checks passed. Source, price and ownership
availability are reported separately.

Every acquisition/preparation result includes `warnings`, `warningCount` and
`incompleteStages`. Catalog warnings include source failure reasons and affected
addresses. Pricing, fee evidence, TVL coverage, retained analytics evidence and
unavailable history are summarized as warnings too. A `refresh-findings` progress
event surfaces them on stderr; `ROOT/refresh/JOB_ID/warnings.json` and
`warnings.html` provide a durable machine report and a standalone review page.
Warnings stay in the job receipt after publication and can be inspected with
`refresh --inspect JOB_ID`. Source stages retain their truthful `incomplete`
status. Automatic developer GitHub reporting is described below; dashboard
responses are unchanged.

`publicationBlockers` distinguishes integrity failures from source warnings:
unexecuted stages and mismatched prepared dataset identities or reporting
cutoffs still block publication. A finalized chain head older than the requested
cutoff is a visible freshness warning; the pinned interval is still verified,
and prepared financial datasets must still agree on the requested reporting
cutoff. Exceptions that prevent preparation from completing still stop the run. Reorg/pin
verification, code and
configuration checks, immutable history completion and atomic release validation
remain enforced. Fixing source evidence later triggers normal revalidation and
new immutable preparation with its own financial values.

Owned analytics collectors run before analytics preparation. Receipts show input
IDs, observation times, ages and retained-row ages, including unavailable or
partially refreshed inputs. Comparison evidence has no maintained automatic
collector and remains explicitly identified as retained manual evidence.

## Automatic developer GitHub warning report

After a run commits its acquired/prepared/selected outcome, the worker updates
one existing GitHub issue. It updates the configured existing issue with the current
approved report.
The default target is `yearn/yearn-metrics#92`; override it with
`YEARN_REFRESH_GITHUB_REPOSITORY` and `YEARN_REFRESH_GITHUB_ISSUE` when needed.
`refresh --inspect` provides read-only receipt inspection. Deferred work-budget
runs wait until a terminal outcome before reporting. Selected-job resumes retry
notification without selecting the financial release again.

The reusable body is `.github/weekly-sync-warning-report.md`. This template was
approved on October 29, 2026, and automatic reporting is enabled in the local
worker configuration for `yearn/yearn-metrics#92`. The approved digest is also
recorded in `.env.example` for future worker setup. `gh` must be authenticated
with issue-write access.

`YEARN_REFRESH_GITHUB_TEMPLATE_SHA256` must match the approved content digest.
To verify the template digest:

```bash
PYTHONPATH=src python -c 'from yearn_data.github_warning_report import template_digest; print(template_digest())'
```

If that value is missing or no longer matches, no GitHub request is made and the receipt records
`githubWarningReport.status = awaiting-template-approval`. Changing the template
requires a new approval digest. Set `YEARN_REFRESH_GITHUB_REPORTING=off` to
explicitly disable notification. Review and approve a changed template before updating its
configured digest. The template and reporting
configuration are included in refresh checkpoint identity; start a new job
after changing them.

The Markdown file is a layout framework, with room for a run summary,
a finding table and focused explanations of affected products. Reports describe the event,
its evidence and its operational meaning, with measurements and concrete follow-up where
supported. Use human-readable product and
chain names, group repeated provider failures, and distinguish an unknown cause
from a confirmed diagnosis. Catalog acquisition captures affected vault names,
retained token metadata and source coverage counts for this purpose. Reports use that run’s
captured evidence and catalog.

The report contains only the latest run's environment, acquisition window and
scope, outcome, current findings and publication integrity results. Counts
distinguish retained historical gaps from a weekly window. Keep issue content focused on the
approved operational summary; retain implementation notes, provider bodies, credentials and
private URLs in private working evidence. Affected-vault details are capped at
25, with remaining evidence available through the run receipt. Missing warning
measurements from an interrupted run are marked unavailable.

The existing issue's title/body are replaced; issue comments, labels and assignees
are preserved. An unchanged report is a no-op. An older reporting cutoff or,
for the same cutoff, older run creation time cannot overwrite a newer managed
report. Reports use the same issue even if it was closed: current warnings reopen
it; an all-clear report replaces stale findings and preserves the issue's state.
A per-issue host lock prevents concurrent staging/production reporters on this
host from racing; a busy reporter defers to a later attempt.
Operators on multiple hosts should use one reporting writer for the target issue.

Runtime transport uses the already authenticated `gh` executable (`gh api`,
GitHub.com). It requires read/write access to the configured issue. Each request
has a 15-second timeout, with no immediate retry. A missing executable, invalid
configuration, permission error, timeout, render error or notification-receipt
write failure leaves financial publication committed and records a separate notification
failure. Financial status
is committed first; notification uses a separate database connection and records
its outcome under `githubWarningReport`, plus a `github-warning-report` progress
event. Persist only the sanitized provider/CLI error classification. If notification-result
persistence itself fails, the returned receipt/progress event still identifies
that failure. Subsequent completed syncs or selected-job resumes can retry.
The issue updater owns operational reporting. Dashboard API deployment and scheduler
installation use their own workflows.

## Incremental preparation and independent validation

The worker uses retained coverage and changed-date evidence while preserving
pin, accounting, readiness and publication checks. See the
[history preparation contract](../reference/tvl-history-preparation.md) for
invalidation and explicit repair, and [performance recipes](../development/performance.md)
for independent full-versus-incremental comparisons. Historical repair remains a separate
task under both nightly and weekly schedules.

## Publication and recovery

`--mode acquire` stops before financial preparation; `--mode prepare` writes
inspectable staged datasets under `ROOT/refresh/JOB_ID` without changing the
serving release. To publish an inspected job, repeat its options with
`--mode publish --resume JOB_ID`. The worker verifies pinned bounds again and
requires matching fee, TVL and analytics dataset IDs and cutoff before calling
the existing atomic hosted-publication interface. Obtain explicit authorization for
production writes and publication before running the writer workflow.

A failure before selection retains the previous serving release. After repairing
the cause, resume to revalidate persisted stages. Changed cutoff, chain scope,
code, configuration or options reject the checkpoint: start a new job with the changed
context and preserve persisted identities. This check occurs before any stage is reused,
including a saved TVL manifest: a changed-code `--resume` raises the explicit
checkpoint error instructing the operator to start a new job. Start the same
command without `--resume`; the new job publishes a manifest under the current
financial revision while reusing retained acquisition coverage and observations.
Preserve the old manifest’s `accountingRevision` and prepare a new compatible manifest.
The receipt distinguishes successful
selection followed by failed/unknown consolidation as `selected-cleanup-failed`.
Resume that job in publish mode to retry only native canonical-history cleanup;
the retry uses the already acquired inputs and committed release selection. A durable expected
release ID also recovers interruption after selection but before saving success.
If another release has since been selected, recovery refuses to clean up under
the old job. Inspect the current selection first. After a code upgrade rejects
an old checkpoint, use the documented standalone canonical-history inspection
and consolidation commands against the current selected release.

Exit status is 0 for acquisition/preparation/selection or inspection, 3 for a
work budget pause, 4 for selection with unfinished cleanup, and 2 for a rejected
or failed invocation. Always inspect the JSON status and `publishReady`, because
preparation can succeed with explicit availability gaps. Receipts include stage
completion/progress, counts, bounds, gap summaries and dataset/release IDs;
provider errors retain sanitized classifications.

Offline regressions use explicitly provisioned disposable Postgres and public
provider boundaries. They run the actual accounting, dataset preparation and
atomic publication/consolidation functions; no live provider or production
publication is needed:

```bash
python -m pytest -q tests/test_refresh_*.py
```

TVL manifests include an `observationRevision` digest of the captured bounded
inputs. An in-place correction therefore creates a new dataset identity and its own prepared
history. Re-reading an
existing manifest returns its stored close and diagnostics. Repaired jobs can prepare and
publish again after a previous publication failure. Incomplete source outcomes remain
visible warnings; unfinished
preparation and publication integrity failures still block selection. Tokenized acquisition
honors `--chunk-size` and shares finality and
end-header validation within each bounded work batch.

## Intermediate reports and previews

Keep maintained operating documentation in `docs/`. Generated review pages,
HTML reports and other intermediate agent output belong in the ignored
`.scratch/` folder, normally `.scratch/reports/`. Operational run evidence can
continue to use ignored `artifacts/`. Reusable GitHub report templates live in
`.github/`. See [the documentation directory instructions](../AGENTS.md).

Installed wheels place the canonical GitHub template under
`share/yearn-data/.github/` in the Python environment; checkout runs read it
directly from the repository's `.github/` folder. The template is included in
both wheel and source distribution builds.

Warning-issue locks use a verified account-private directory with mode 0700 and
reject symbolic links or files with unsafe ownership or permissions. Deferred
notifications record `consecutiveDeferredRuns` and `persistentContention` so
repeated contention across completed jobs is distinguishable from one concurrent
reporter. The run count tracks distinct jobs; acquiring the
lock resets it. Reporting failures remain isolated from publication.

[Documentation index](../README.md).
