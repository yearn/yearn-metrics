# Performance recipes and measurement boundaries

Performance commands provide opt-in diagnostics. Validate correctness with behavioral checks
and apply the existing CI policy. Use the [development environment](setup.md) and explicit
disposable
Postgres fixtures from [Postgres tests](../operations/postgres.md#tests). Write new
JSON, browser traces, screenshots and review pages to ignored `.scratch/`.
Run benchmarks against an isolated development or staging database.

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
browser results. Exclude profiler overhead from latency trials. Nested timing fields
overlap; use the end-to-end measurement for total duration. The retained JSON-payload budget
measures serialized cache contents. Measure process RSS and concurrent-request memory
separately. Appended raw trials need a new output file
or explicit accounting for prior samples. The intentionally old query-budget failure serves
as a baseline for comparison with the current implementation.

## Ongoing contracts extracted from the coverage work

### Coverage reuse and bounded reads

The bounded catch-up command already subtracts committed successful ranges. Coverage
requires committed range evidence for the selected vault and event scope. A fixed 120-vault
benchmark confirms that a fully covered repeat
makes zero report fetches. The historical baseline read coverage twice per vault, once for
saved-hash
validation and once for gap planning; the current bounded read combines them.

Catch-up now fetches both intervals and hashes in one query per 100 retained
report targets. Gap planning uses the same interval-subtraction primitive as
other collectors. Each saved scope still has its own policy, each reused end
hash is verified, and each newly fetched range is freshly hash-checked before
its rows and coverage commit atomically. Discovery ranges use one query per
scope. Provider batching, concurrency, historical inclusion and publication scheduling
retain their separately defined policies.

The bounded read includes all policies for the selected targets. Metadata
enrichment can change the chosen policy after the read; only the resulting exact
policy is reusable. Successful replay still deletes superseded metadata-policy
coverage. Reuse is restricted to matching targets, chains, families and windows. At most 100
vaults' relevant range rows are retained at once; each vault can supply its full historical
range set.

### Progress and recovery

`catch-up` writes JSON-line progress to stderr and its final summary to stdout.
Events name the `report-acquisition` stage and show planned pending ranges,
started ranges, committed row counts, failures and completion. A successfully
scanned empty range emits a commit with zero observed rows. A failed request
emits `range-failed`, exits without claiming coverage for that range, and omits
the completion event. Failure events contain public bounds and target coordinates; raw
provider errors and endpoints remain private.

The final summary includes `coverage_read_queries`, overall post-pin `seconds`,
and `report_work` with guaranteed `active` and `inactive` keys, even when a
class has no vaults. Empty classes contain zero for every counter and processing
seconds. Each class reports vault count, reused ranges,
reused/scanned block counts, completed ranges and processing seconds. Per-vault
processing seconds include metadata enrichment but exclude shared batch reads
and the initial batched metadata lookup; use them to diagnose per-vault work and measure
end-to-end duration separately. Range counts identify collector fetch units. Provider
implementations own their internal HTTP pagination and transport attempts.

Provider failures return to the caller for deliberate retry. Retry/rate-limit failures stop the
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
reorg, source, readiness and publication checks. Run a full comparison as an explicit
validation exercise. The nightly schedule follows its configured refresh stages.

## Frozen results

[Historical measurement index](../archive/README.md#measurements) links each
narrative to its raw evidence and original harness commands. The archived trial
numbers and test counts describe their recorded revisions and apparatus only.
Raw results are byte-preserved under `benchmarks/results/2026-10/`.

[Documentation index](../README.md).
