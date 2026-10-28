# Report coverage reads and refresh progress

## Diagnosis and scope

The bounded catch-up command already subtracts committed successful ranges. It
does not infer coverage from recent event activity, a legacy cursor, or a vault's
active flag. A fixed 120-vault benchmark confirms that a fully covered repeat
makes zero report fetches. The redundant work is reading the same coverage twice
per vault: once for saved-hash validation, once for gap planning.

Catch-up now fetches both intervals and hashes in one query per 100 retained
report targets. Gap planning uses the same interval-subtraction primitive as
other collectors. Each saved scope still has its own policy, each reused end
hash is verified, and each newly fetched range is freshly hash-checked before
its rows and coverage commit atomically. Discovery ranges use one query per
scope. No provider batching, concurrency, historical exclusion or publication
scheduling changes are included.

The bounded read includes all policies for the selected targets. Metadata
enrichment can change the chosen policy after the read; only the resulting exact
policy is reusable. Successful replay still deletes superseded metadata-policy
coverage. Unrelated targets, chains, families and out-of-window scans are not
reused. At most 100 vaults' relevant range rows are retained at once; the number
of historical range rows per vault is not capped.

## Measured results

[The machine-readable receipt](report-coverage-benchmark-2026-10-27.json) compares
integration commit `3db530831ce79bec597a43f5ab795d45d1ed8ab7` with this optimization.
Both runs use the same offline 120-vault inventory, blocks 10–30, 60 active and
60 inactive vaults. Forty vaults are fully covered, forty partially covered,
and forty initially uncovered. Seeded successful empty ranges remain covered.

| Measurement | Before | After |
| --- | ---: | ---: |
| Coverage SELECTs, extending coverage | 240 | 2 |
| Coverage SELECTs, fully covered repeat | 240 | 2 |
| Report fetches, extending coverage | 80 | 80 |
| Report fetches, fully covered repeat | 0 | 0 |
| Local extension, seconds | 0.720 | 0.458 |
| Local fully covered repeat, seconds | 0.107 | 0.003 |
| Extension with modeled 2 ms coverage-read latency, seconds | 1.238 | 0.513 |
| Covered repeat with modeled 2 ms coverage-read latency, seconds | 0.605 | 0.008 |

The final 120 report rows and 160 coverage rows have identical sorted digests
before and after. Each report has independently expected raw gain 12. Provider
boundary requests, including identity/header verification, are identical:
85 headers / 84 chain identities / 80 report fetches during extension, and
5 / 4 / 0 during the covered repeat. There is no time-threshold assertion in CI;
the regression protects bounded query count, known results and retained scope.
Single local timings vary; the request-count reduction is the stable evidence.
The separate latency model adds delay only to coverage SELECTs, not provider
calls or writes, and is not a claim about hosted Postgres latency.

These timings measure **catch-up's contribution to the TVL-start barrier**.
`scripts/update_postgres.py` waits for catch-up, fee acquisition, pricing and
analysis before dispatching TVL collection. This optimization reduces the first
stage's work; it does not make TVL run earlier in the dependency order. Supplementary full-worker measurements were subsequently added in
[PR #90](https://github.com/yearn/yearn-metrics/pull/90), with twelve trials through
actual TVL dispatch and complete preparation. Those measure the maintained
refresh worker and its documented stage order, rather than the old update script
end to end. PR #90 is merged into main at `ab9b3d7`; its
[full-worker performance document](refresh-performance.md) and
[raw receipt](refresh-benchmark-2026-10-27.json) are present both on main and in
this branch. This PR references #56 and keeps it open until this coverage-read
optimization also reaches the default branch. That is the remaining delivery
condition for closing #56. The refresh implementation was reviewed separately
in #90.
No production update, publication or provider-limit experiment was run.

## Progress and recovery

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

## Reproduce

Configure the explicit disposable Postgres service described in
[postgres.md](postgres.md#tests), then run from the checkout:

```bash
# Print the local receipt and validate the permanent query/result regression.
python -m pytest -q -s tests/test_catchup_performance.py

# Separate database round-trip model; default tests add no synthetic delay.
YEARN_BENCHMARK_ROUNDTRIP_MS=2 python -m pytest -q -s tests/test_catchup_performance.py

# Failure/resume, discovery, policy and source-evidence contracts.
python -m pytest -q tests/test_catchup.py tests/test_coverage.py tests/test_history_sources.py
```

The saved baseline was captured with this inventory/result harness against the
original catch-up implementation before editing it; the zero-delay measurement
was repeated by loading that exact original module after removing a zero-delay
scheduler yield from the measurement proxy. The interval-subtraction behavior is
shared by both measurements. Its final query-budget
assertion fails as expected (240 reads), after printing the receipt. The
optimized run passes (2 reads). Later assertions additionally protect the new
active/inactive diagnostic counters and CLI stderr routing; they do not alter
the measured inventory, cutoff, quotes or acquired rows.

To reproduce the historical baseline with this same committed harness:

```bash
git show 3db530831ce79bec597a43f5ab795d45d1ed8ab7:src/yearn_data/catchup.py > /tmp/yearn-coverage-baseline.py
YEARN_BENCHMARK_BASELINE_MODULE=/tmp/yearn-coverage-baseline.py \
python -m pytest -q -s tests/test_catchup_performance.py
```

This explicitly selected historical collector lacks activity diagnostics. Its
receipt prints first, then the expected query-budget assertion fails on 240
coverage reads. The default optimized run still requires and checks every
active/inactive diagnostic counter; baseline mode does not change CI selection.
