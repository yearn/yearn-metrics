# Performance recipes and measurement boundaries

Performance commands are opt-in diagnostics, not correctness substitutes or CI
thresholds. Use the [development environment](setup.md) and explicit disposable
Postgres fixtures from [Postgres tests](../operations/postgres.md#tests). Write new
JSON, browser traces, screenshots and review pages to ignored `.scratch/`.
Never run a benchmark against a production writer.

| Tool | Supported measurement |
| --- | --- |
| `scripts/benchmark_fee_serving.py` | Synthetic fee-view/grouped-history calculation; optional guarded localhost Postgres with `--postgres`; `--pairing-source` selects a frozen implementation. |
| `scripts/benchmark_fee_summary.py` | Ordered summary and row-loading costs; frozen `--pairing-source` and `--hosted-source`; `--no-profile` for latency and `--include-history` for full grouped-result parity. |
| `tests/benchmark_refresh.py` | Actual native worker with deterministic provider boundaries and per-trial guarded disposable schemas; compare explicit before/after catch-up module revisions. |
| `tests/test_catchup_performance.py` | Guarded report-coverage benchmark/regressions with fixed inputs and explicit optional latency model; archived reproduction distinguishes historical expected-budget failures. |
| `tests/test_tvl_incremental_benchmark.py` | Opt-in incremental/full history comparison on controlled disposable Postgres inputs. |
| `scripts/benchmark_powerglove_browser.mjs` | Actual browser navigation; before/after URLs stay environment-only and output redacts origins. |
| `scripts/compare_powerglove_browser.cjs` | Compare captured financial responses without additional database queries. |

Check each tool's header/`--help` and its frozen measurement record for required
fixture sizes, baseline sources and environment variables. Freeze implementation
modules before editing. Keep baseline/candidate inputs, cutoffs, source order and
provider results identical. Retain independent expected amounts and financial
response parity; cache equality alone cannot prove financial correctness.

Separate fresh-process, warm-server, database-cold, modeled network delay and
browser results. Exclude profiler overhead from latency trials. Nested timing
fields are not additive. A retained JSON-payload budget is not a process RSS or
concurrent-request memory guarantee. Appended raw trials need a new output file
or explicit accounting for prior samples. Reproduction of an intentionally old
query-budget failure is not a failure of the current implementation.

## Ongoing contracts extracted from the coverage work

### Coverage reuse and bounded reads

The bounded catch-up command already subtracts committed successful ranges. It
does not infer coverage from recent event activity, a legacy cursor, or a vault's
active flag. A fixed 120-vault benchmark confirms that a fully covered repeat
makes zero report fetches. The historical baseline read coverage twice per vault, once for saved-hash
validation and once for gap planning; the current bounded read combines them.

Catch-up now fetches both intervals and hashes in one query per 100 retained
report targets. Gap planning uses the same interval-subtraction primitive as
other collectors. Each saved scope still has its own policy, each reused end
hash is verified, and each newly fetched range is freshly hash-checked before
its rows and coverage commit atomically. Discovery ranges use one query per
scope. This coverage-read optimization does not define provider batching, concurrency,
historical inclusion or publication scheduling policy.

The bounded read includes all policies for the selected targets. Metadata
enrichment can change the chosen policy after the read; only the resulting exact
policy is reusable. Successful replay still deletes superseded metadata-policy
coverage. Unrelated targets, chains, families and out-of-window scans are not
reused. At most 100 vaults' relevant range rows are retained at once; the number
of historical range rows per vault is not capped.

### Progress and recovery

`catch-up` writes JSON-line progress to stderr and its final summary to stdout.
Events name the `report-acquisition` stage and show planned pending ranges,
started ranges, committed row counts, failures and completion. A successfully
scanned empty range emits a commit with zero observed rows. A failed request
emits `range-failed`, exits without claiming coverage for that range, and omits
the completion event. Failure events contain public bounds/target coordinates,
not provider exception text or endpoints.

The final summary includes `coverage_read_queries`, overall post-pin `seconds`,
and `report_work` with guaranteed `active` and `inactive` keys, even when a
class has no vaults. Empty classes contain zero for every counter and processing
seconds. Each class reports vault count, reused ranges,
reused/scanned block counts, completed ranges and processing seconds. Per-vault
processing seconds include metadata enrichment but exclude shared batch reads
and the initial batched metadata lookup; they are diagnostics, not additive
end-to-end timings. Range counts identify collector fetch units, not HTTP pages
or transport attempts hidden inside a provider implementation.

The collector does not add automatic retries. Retry/rate-limit failures stop the
stage; the pending range is explicit in progress. Repeating the same command
reuses committed ranges and fetches the remainder. Existing regressions cover a
provider failure after earlier empty/nonempty commits, changed chunk sizes on
resume, newly discovered and retained inactive vaults, corrupted saved hashes,
conflicting observations, and policy changes after metadata enrichment. A
successful zero-event scan and an uncompleted scan remain distinct.

## History preparation

The [history preparation contract](../reference/tvl-history-preparation.md)
owns changed-date invalidation, explicit rebuilds, checkpoint recovery and
independent full-versus-incremental validation. The native worker keeps its
reorg, source, readiness and publication checks. A full comparison is an explicit
validation exercise, not an extra nightly stage or an installed audit schedule.

## Frozen results

[Historical measurement index](../archive/README.md#measurements) links each
narrative to its raw evidence and original harness commands. The archived trial
numbers and test counts describe their recorded revisions and apparatus only.
Raw results are byte-preserved under `benchmarks/results/2026-10/`.

[Documentation index](../README.md).
