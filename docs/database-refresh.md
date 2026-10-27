# Maintained database refresh

`yearn-data refresh` coordinates the native collectors, accounting and publication
interfaces. Run it manually or from a scheduler; the worker does not configure a
scheduler. Acquisition and preparation write the explicitly selected database.
Preparation is the default; selecting a serving release requires `--mode publish`.

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

These ancient fixture bounds illustrate the syntax, not recommended production
bounds. Without `--from-block`, each chain starts at its oldest retained history
coverage boundary. An empty chain requires an explicit initial bound. Without
`--first-close`, the first retained TVL observation determines the daily window;
empty TVL history requires an explicit initial day end. Inactive vaults remain
in historical scope. Automatic report source routing uses the existing chain
policy; `--source rpc` or `envio` explicitly selects a supported source. The worker
checks chain identity, finality, the selected source watermark and pinned block
hashes. It does not silently replace an unavailable source with a different one.
A finality head before the cutoff permits inspection but prevents publication.

`--confirmations CHAIN:N` replaces that chain's finalized-head policy with
`latest-minus-N` (for example `--confirmations eth:64`). Each count must be
positive. The selected policy is stored in Tokenized range evidence; coverage
acquired under a different finality policy is not reused. Confirmation settings
are part of the checkpoint identity, so changing them requires a new job.

`--deferred-manifest PATH` passes an explicitly reviewed fee-deferral manifest
to the `fee-usd` analysis. Its entries determine which matching events retain a
deferred-evidence disposition in the prepared/published fee outputs; they are
not treated as accepted fees or invented zero values. The manifest content is
hashed into checkpoint configuration and analysis provenance. Changing its
contents requires a new job rather than resuming an older financial scope.

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

Inspection is read-only and does not take the writer lock. Progress is newline
JSON on stderr; stdout is a JSON receipt. `--receipt` also writes the receipt
atomically to a file. One refresh holds a dedicated database advisory lock for
the invocation. Other tools do not acquire this lock: do not run independent
acquisition or publication writers concurrently. Completed stage inputs and
outputs are checked again before selection; detected changes rerun dependent
stages and a repeatedly changing database fails before selection.

## Work and gaps

The stages acquire reports, discover the TVL catalog and fill maintained Tokenized
Strategy fee ranges for each chain. They then project canonical fees, price
reports and fees, fill missing daily TVL closes, refresh owned analytics inputs,
run earnings and fee accounting, and prepare matched fee, TVL, history and
analytics datasets. Global financial preparation includes retained database
history through the cutoff; `--chains` limits acquisition, rather than deleting
other chains from the reporting dataset. Future raw TVL observations are retained
while the reporting cutoff hides them from this publication.

`--max-targets` defaults to 10000 shared Tokenized chunk, canonical-fee and pricing
targets per invocation; native projection/pricing pages contain at most 500
items. `--max-dates` defaults to 30 chain/day acquisitions. Exhausting a budget
returns `work-deferred`; resume the same job to continue after committed work.
Unavailable pricing targets are attempted once per traversal so one missing
quote cannot starve later targets. Accepted quotes are preserved. A new job can
retry unavailable work. Reports include provider coverage gaps and unresolved
statuses, and fee targets with missing metadata remain explicitly unpriceable.

Daily collection fills missing asset-unit observations, including genuine zero.
Known units with unavailable prices or ownership evidence are reported as
`deferredTvlRepairs`; they do not silently become zero. To reacquire all daily
closes in the selected window, start a job with `--repair-history`; the date
budget still applies. Acquisition failures and incomplete catalog/source stages
prevent publication readiness. Native financial availability rules still apply:
`publishReady` does not mean every price or ownership observation is known.
Inspect `gaps`, source availability and dataset diagnostics before publishing.

Owned analytics collectors run before analytics preparation. Receipts show input
IDs, observation times, ages and retained-row ages, including unavailable or
partially refreshed inputs. Comparison evidence has no maintained automatic
collector and remains explicitly identified as retained manual evidence.

## Publication and recovery

`--mode acquire` stops before financial preparation; `--mode prepare` writes
inspectable staged datasets under `ROOT/refresh/JOB_ID` without changing the
serving release. To publish an inspected job, repeat its options with
`--mode publish --resume JOB_ID`. The worker verifies pinned bounds again and
requires matching fee, TVL and analytics dataset IDs and cutoff before calling
the existing atomic hosted-publication interface. This documentation does not
itself authorize publication or production writes.

A failure before selection retains the previous serving release. After repairing
the cause, resume to revalidate persisted stages. Changed cutoff, chain scope,
code, configuration or options reject the checkpoint: start a new job rather
than editing persisted identities. The receipt distinguishes successful
selection followed by failed/unknown consolidation as `selected-cleanup-failed`.
Resume that job in publish mode to retry only native canonical-history cleanup;
it does not reacquire inputs or select the release again. A durable expected
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
provider exception text is not persisted.

Offline regressions use explicitly provisioned disposable Postgres and public
provider boundaries. They run the actual accounting, dataset preparation and
atomic publication/consolidation functions; no live provider or production
publication is needed:

```bash
python -m pytest -q tests/test_refresh_*.py
```

TVL manifests include an `observationRevision` digest of the captured bounded
inputs. An in-place correction therefore creates a new dataset identity and
prepared history instead of reusing the earlier immutable rows. Re-reading an
existing manifest returns its stored close and diagnostics. A previous failed
publication does not count as unfinished preparation; repaired jobs can prepare
and publish again, while incomplete acquisition or preparation still blocks
selection. Tokenized acquisition honors `--chunk-size` and shares finality and
end-header validation within each bounded work batch.
