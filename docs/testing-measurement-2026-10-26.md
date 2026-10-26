# Test suite measurement — October 26, 2026

Branch: `ross/test-suite-policy`. Baseline: PR #69 tip
`c1a1c9c4eb860d20ad78fcbfa2ddfa0563ea2086` (includes PR #68).

## Delivery validation against the requested PR base

Rebased onto `ross/pr-08-fantom-replay` at
`49b25982e76a99142b02968128f56713970714f6`. The base added production fixes and
55 test cases after the original measurement. Its new direct price-repair test
now explicitly requests `postgres_database` (four parameterized cases).

- Full rebased suite: **1342 passed, 7 skipped in 388.26s**.
- With the disposable database removed and URL unset: **567 passed, 7 skipped,
  775 deselected in 7.87s**.
- Current selection: 775 Postgres and 574 non-Postgres cases, 1349 total.
- Repository-wide Ruff and `git diff --check` passed.
- Production source, CI workflows and `uv.lock` are unchanged relative to this
  base. The disposable database was removed after validation.

The rebased full run used the same Python 3.12.3 environment, temporary profiler
and a fresh local Postgres 18 service. It validates the delivery tree; it is not
an equivalent-workload speedup comparison with the original baseline below.
The seven skips remain opt-in Anvil replays, and the existing websockets legacy
warning remains. Local execution covers Python 3.12; unchanged CI also checks
Python 3.10. See [the coverage review](testing-coverage-review-2026-10-26.md).

## Cumulative result after all cleanups

The full suite on the original PR #69 base passed: **1287 passed, 7 skipped in 345.63s**.
CI continues to run the complete existing two-version matrix; production code
and the dependency lockfile are unchanged.

| Full suite | Passed | Skipped | Pytest time |
| --- | ---: | ---: | ---: |
| Original PR #69 baseline | 1290 | 7 | 416.14s |
| Initial duplicate removal and fixture split | 1288 | 7 | 399.30s |
| All cleanups, original PR #69 base | 1287 | 7 | 345.63s |

Cumulative elapsed-time reduction: **70.51s (16.9%)**
against the original baseline. Since the initial cleanup, the reduction is
**53.67s (13.4%)**. These are measured
full-suite differences, not a sum of separately timed focused runs.

| Full-suite phase totals | Original | Initial cleanup | All cleanups |
| --- | ---: | ---: | ---: |
| Setup | 169.57s | 162.33s | 127.85s |
| Test calls | 117.97s | 113.86s | 109.70s |
| Teardown | 124.07s | 121.29s | 106.38s |

Phase totals exclude collection and some runner overhead. Setup includes domain
fixtures, not only schema creation.

That measured collection has **1294 cases: 734 Postgres and 560 non-Postgres**. The
latest database-free run was **553 passed, 7 skipped in 7.96s**, performed after
the hosted API refactor with the disposable database stopped and URL unset.
Seven opt-in local Anvil replays remain skipped. The net count change reflects
four duplicate removals, two isolation regressions, five retained fee-matrix
integration scenarios, and six fewer malformed-receipt cases after removing the
online/offline duplication from pure decoding. Hosted API case count is unchanged.

Included work:

- Initial duplicate removal, explicit database fixtures and agent testing policy.
- [Fee-completeness matrix](fee-completeness-test-measurement-2026-10-26.md):
  60 pure cases plus five integration scenarios preserving all components/states.
- [Malformed receipts](malformed-receipt-test-measurement-2026-10-26.md):
  12 pure cases plus ingestion and replay for each of three products.
- [Hosted API fixtures](hosted-api-test-measurement-2026-10-26.md):
  21 cases use cheaper setup while all 70 cases remain.

### Cumulative run method and limits

The cumulative run used the same CPython 3.12.3 interpreter, locked dependencies,
host, and temporary profiling plugin as the original measurements, with
`PYTHONPATH=src:/tmp`. The profile confirms imports from this checkout and exit
status zero. A fresh task-owned Postgres 18 service used local port 55439;
container startup is excluded. No other validation ran concurrently in this
chat. The original run had limited overlapping classification checks, as noted
below. Host load and database cache state can vary, so this is a single-run
comparison, not a statistically established speedup or a prediction of CI time.
The only warning is the existing websockets legacy deprecation.

The profiler is temporary and does not change repository tests or CI. The
ordinary full-suite command remains `python -m pytest -q --durations=10` with the
disposable database configured. `git diff --check` passes, and production code,
CI workflows and `uv.lock` have no diff.

## Initial cleanup result

| Run | Passed | Skipped | Pytest time |
| --- | ---: | ---: | ---: |
| Before, full suite | 1290 | 7 | 416.14s |
| After, full suite | 1288 | 7 | 399.30s |
| After, without Postgres | 472 | 7 | 7.71s |

Full-suite time changed by 16.84s (4.0% faster in this pair of runs).
This is one before/after pair on a shared host, not a statistically established
speedup or a prediction of GitHub CI time. The useful new capability is the
7.71s non-Postgres selection without a database service or URL.

At this initial step, the suite collected 1295 cases: 816 request Postgres and 479 do
not. Seven local Anvil replays remain opt-in and skipped in these runs. Four
duplicate cases were removed and two database-isolation regressions added,
so the collected count changes from 1,297 to 1295.

| Full-suite phase totals | Before | After |
| --- | ---: | ---: |
| Setup | 169.57s | 162.33s |
| Test calls | 117.97s | 113.86s |
| Teardown | 124.07s | 121.29s |

Phase totals exclude collection and some runner overhead. Setup includes domain
fixtures, not only schema creation.

## Method and limits

- Same CPython 3.12.3 interpreter, committed `uv.lock` dependencies, host, and
  disposable Postgres 18 container for both full runs. No live providers.
  The baseline started on a fresh service; the after run reused the warmed
  service, so cache effects are another reason not to generalize the difference.
- Baseline ran from an isolated `git archive` of the exact baseline commit.
  The after run used this working tree. Both explicitly selected their own
  `src` through `PYTHONPATH`; the existing checkout venv points at an older
  worktree and was not used for these measurements.
- Full runs were sequential. Brief classification/isolation checks overlapped
  the baseline while implementation was prepared; timings are indicative.
- A temporary profiling plugin recorded pytest setup/call/teardown durations
  and loaded source paths. It was enabled for both full runs and is not part
  of the permanent test suite.
- The final non-Postgres run had `YEARN_TEST_POSTGRES_URL` unset and the
  task-owned disposable database stopped. Local HTTP/TLS tests still need
  socket access; non-Postgres does not mean exclusively pure functions.
- The full after timing preceded a fixture-wrapper argument rename preserving
  `Connection(url=...)` compatibility. The two isolation regressions were rerun
  afterward, including the added keyword-call assertion; the ordinary
  positional connection path is unchanged.
- All runs report the existing `websockets.legacy` deprecation warning.
- No workflows, production code, dependencies, or broad test matrices changed.

Reproduce the selections with the environment from the README and the disposable
service from [the Postgres guide](postgres.md#tests):

```bash
python -m pytest -q --durations=10
python -m pytest -q -m postgres
# Stop the disposable test service and unset its URL before this isolation check.
python -m pytest -q -m 'not postgres' --durations=10
```

## Removed cases and surviving coverage

| Removed case | Surviving coverage |
| --- | --- |
| Non-pipeline lifetime-yield wrapper | `test_analysis.py::test_lifetime_yield_uses_net_gain_minus_loss`; pipeline transport remains separately tested using the same scenario helper |
| Canonical parameter of Postgres export rollback wrapper | `test_canonical_fees.py::test_failed_canonical_export_rolls_back_partial_outputs`; USD injection remains in `test_postgres.py` |
| Postgres failed-quote refresh wrapper | `test_fee_valuation.py::test_failed_fee_refresh_preserves_accepted_quote_and_usd` |
| Standalone Gnosis price namespace assertion | Gnosis row of `test_rpc_config.py::test_catalog_prices_use_canonical_namespaces` |

The added tests protect the new fixture boundary: unmarked access fails before
transport, and database tests reject another database URL or an unowned schema.
A missing-URL probe also verified an explicit setup failure, not a silent skip.
Ruff and `git diff --check` pass. CI remains the existing full two-version matrix.
See [the testing policy](testing.md) for agent rules and fixture usage.
