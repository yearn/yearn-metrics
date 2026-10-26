# Testing policy

Keep a test when it protects a distinct, supported failure mode at the cheapest
boundary that can faithfully detect it. Decide separately how often it runs.

## Writing and retaining tests

- Use as many exploratory tests as useful during TDD. Before delivery,
  consolidate them into readable permanent coverage. One-off debugging checks
  do not automatically belong in the suite.
- Name the failure being prevented: incorrect amounts, lost data, an invalid
  publication, a broken consumer contract, faulty recovery, or a meaningful
  performance regression. Explain the risk in the test name or a short comment.
  Adding a test for every function or increasing coverage is not itself a goal.
- Use pure tests for calculations and parsing; real disposable Postgres for
  persistence, SQL, transactions, and locking; HTTP tests for routing, headers,
  and serialization. Keep representative end-to-end contracts to verify wiring.
- Prefer independently calculated values, observed receipts, and explicit
  expected states. Do not reproduce the production algorithm to calculate the
  expected result. Equivalence tests such as cached versus uncached output need
  accompanying known-result coverage.
- Keep meaningful boundaries and equivalence classes. A Cartesian product at an
  expensive integration boundary needs a plausible interaction; broad cheap
  pure matrices can be useful. Do not erase boolean/numeric, unavailable/zero,
  or pricing-policy distinctions just to reduce counts.
- Give each behavior an owning test. Avoid copies added only for a PR review or
  backend migration. Different layers may retain similar assertions when they
  protect different wiring or persistence failures. Do not call test functions
  from other tests; share small domain fixture builders where needed.
- Keep normal regression tests independent of live providers and production
  credentials. Preserve unique test schemas and close connections before cleanup.
  Request infrastructure explicitly; never make database setup global merely
  to accommodate a new test.
- When removing a test, identify its surviving coverage or the supported
  behavior being retired. For substantial financial coverage reductions,
  demonstrate that a plausible fault still fails a surviving test; use a small
  temporary mutation when useful, not a mandatory repository-wide campaign.
- Keep tests runnable and owned. Moving a slow test to another execution tier
  must include a documented command and a job or explicit validation step.
  Do not silently skip tests indefinitely or introduce new CI tiers without
  agreement on their scope.
- Measure setup, execution, teardown, flakiness, and maintenance cost. Coverage
  can reveal gaps, but neither a coverage percentage nor a deletion quota is a
  target. Broaden or repeat validation for new changes, failures, or unresolved
  risks rather than rerunning everything after every edit.

For this repository, preserve fee precision, historical inclusion, exact-day
pricing, unavailable versus genuine-zero values, retry and reorg recovery,
event identity/conflicts, atomic publication/consolidation, TLS/read-only
boundaries, and ownership deductions. Lifetime-yield provider fallback and
Yearn-only fee valuation are different policies and require separate coverage.

## Selecting tests locally

Run commands from this checkout with the interpreter and dependencies described
in the README. `pyproject.toml` points pytest at this checkout's `src` directory.

```bash
# Pure calculations and offline boundary tests: no Postgres service or URL needed.
python -m pytest -q -m 'not postgres'

# Real database contracts, with the disposable service configured first.
python -m pytest -q -m postgres

# Full existing suite, as CI still runs it.
python -m pytest -q
```

See [Postgres test setup](postgres.md#tests) for the disposable database. The
non-Postgres group also contains local HTTP/TLS boundary tests; it is not a
promise that every test is a pure function or needs no local sockets. Frozen
Anvil execution remains explicitly enabled with `RUN_LOCAL_EVM_REPLAY=1`.

Database tests request `postgres_database`, directly or through fixtures such
as `pg`, `new_pg`, or a domain's `db` fixture. Collection automatically applies
the `postgres` marker from that fixture dependency, including imported fixtures.
Do not add only a marker: it selects tests but does not provision a database.

```python
def test_persists_result(postgres_database):
    # connect('staging') now uses an isolated schema in the disposable database.
    ...

@pytest.fixture
def db(postgres_database):
    # Domain-specific connection/setup belongs here.
    ...
```

Unmarked tests receive placeholder database settings and a connection guard.
They must mock the boundary if they only check configuration. The isolated TLS
peer test deliberately restores the driver for its local handshake only; this
is not permission to bypass the guard for ordinary database access.
Database fixtures also reject toolkit connections to another database URL or
to a schema they did not create, even if a command reloads environment settings.

CI remains unchanged: every PR runs the full suite on pinned and minimum Python.
Use focused tests during editing, then the appropriate broader checks once a
change is coherent. Any later CI selection policy requires its own review and
measurement; this local split does not authorize skipping checks in CI.

The [October 26 measurement](testing-measurement-2026-10-26.md) records the first
cleanup's before/after results, the cumulative full-suite result after all
follow-ups, retained coverage, and measurement limits.

The [fee-completeness follow-up](fee-completeness-test-measurement-2026-10-26.md)
shows how to move a calculation matrix to pure tests while retaining all
component/state combinations in fewer database scenarios.

The [malformed-receipt follow-up](malformed-receipt-test-measurement-2026-10-26.md)
keeps payload-length boundaries pure and retains ingestion/replay checks for
each product.

The [hosted API fixture follow-up](hosted-api-test-measurement-2026-10-26.md)
separates request validation, registry persistence, financial serving, and full
publication setup without reducing the hosted API case count.
