# Hosted API fixtures: October 26, 2026

The 70 hosted API cases remain intact. Twenty-one now request less setup:

| Cases | Fixture | Boundary preserved |
| --- | --- | --- |
| 7 invalid comparison queries, 2 stale pins | `selected_api` | Real HostedAPI validation with an in-memory selection; any publication-data load fails explicitly |
| 1 atomic-selection case, 2 metadata-immutability cases | `registered` | Real Postgres registry writes, reads, transactions and immutable manifests, without generating histories |
| 9 compact financial routes | `financial_publication` | Actual financial snapshots, pairing manifest, compact SQL rows and registry selection; source analysis outputs are deleted before serving |

The shared `published` fixture is unchanged by this step. Full publication remains
in tests covering fileless serving, read-only request connections, selected TVL
and analytics revisions, publication validation, rollback, failed cleanup,
prepared history, date indexes, serving bounds, and unavailable evidence.
Cache headers and HTTP compression already had cheap fixtures and are unchanged.
No test cases were deleted, and production code and CI remain unchanged.

## Measurement

| Hosted API file | Before | After |
| --- | ---: | ---: |
| Passed | 70 | 70 |
| Postgres cases | 53 | 44 |
| Non-Postgres cases | 17 | 26 |
| Pytest elapsed | 59.26 s | 45.54 s |

This pair saves **13.72 seconds (23% less elapsed time)**. Whole-suite database
cases drop from 743 to 734. The full Postgres suite was not timed again.

Both runs used CPython 3.12.3, the same locked dependencies and task-owned local
Postgres 18 on port 55439. The baseline is the working tree after the malformed
receipt change. Runs were sequential; container startup is excluded. These are
single successful runs on a shared host, with possible cache effects, not a
statistically established speedup or a CI-time prediction.

The complete non-Postgres selection passed with the task-owned database removed
and test URL unset: **553 passed, 7 skipped, 734 deselected in 7.96 s**.
Focused Ruff and `git diff --check` passed. Production source, CI workflows and
the dependency lockfile have no diff. Runs report the existing websockets legacy
deprecation warning.

## Coverage checks

Temporary source copies disable comparison-query validation and compact-row
chain filtering. These experiments verify that request validation and actual
SQL filtering remain exercised by the cheaper fixtures. Repository production
source is never mutated. The mutated run produced eight expected failures:
six invalid-query cases and two financial routes returned incorrect behavior.
The query cases caught either an incorrect 410 or an attempted publication-data
load; the financial routes caught incorrect response values after chain filtering
was removed. Eight other selected cases passed; no fixture setup errors occurred.

## Reproduction

After configuring the disposable database per `docs/postgres.md`:

```bash
python -m pytest -q tests/test_hosted_api.py --durations=10
python -m pytest -q -m 'not postgres'
```
