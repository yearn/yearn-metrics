# Testing policy

Keep a test when it protects a distinct, supported failure mode at the cheapest
boundary that can faithfully detect it. Decide separately how often it runs.

## Writing and retaining tests

- Use as many exploratory tests as useful during TDD. Before delivery,
  consolidate them into readable permanent coverage. Retain debugging checks when they
  protect supported behavior.
- Name the failure being prevented: incorrect amounts, lost data, an invalid
  publication, a broken consumer contract, faulty recovery, or a meaningful
  performance regression. Explain the risk in the test name or a short comment.
  Choose tests by the supported behavior and plausible faults they protect.
- Use pure tests for calculations and parsing; real disposable Postgres for
  persistence, SQL, transactions, and locking; HTTP tests for routing, headers,
  and serialization. Keep representative end-to-end contracts to verify wiring.
- Prefer independently calculated values, observed receipts, and explicit
  expected states. Derive expected results independently from domain facts or controlled
  examples. Equivalence tests such as cached versus uncached output need
  accompanying known-result coverage.
- Keep meaningful boundaries and equivalence classes. A Cartesian product at an
  expensive integration boundary needs a plausible interaction; broad cheap
  pure matrices can be useful. Preserve boolean/numeric, unavailable/zero and pricing-policy
  distinctions when consolidating coverage.
- Give each behavior an owning test. Avoid copies added only for a PR review or
  backend migration. Different layers may retain similar assertions when they
  protect different wiring or persistence failures. Keep tests independently runnable; share
  small domain fixture builders where needed.
- Keep normal regression tests independent of live providers and production
  credentials. Preserve unique test schemas and close connections before cleanup.
  Request infrastructure explicitly through the tests and fixtures that need it.
- When removing a test, identify its surviving coverage or the supported
  behavior being retired. For substantial financial coverage reductions,
  demonstrate that a plausible fault still fails a surviving test; use a small temporary
  mutation when it helps demonstrate that coverage.
- Keep tests runnable and owned. Moving a slow test to another execution tier
  must include a documented command and a job or explicit validation step.
  Agree on scope before introducing test skips or new CI tiers, and give temporary skips an
  explicit resolution path.
- Measure setup, execution, teardown, flakiness, and maintenance cost. Use coverage to
  identify behavioral gaps and select tests by the faults they protect. Broaden or repeat
  validation when new changes, failures or unresolved risks justify it.

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

See [Postgres test setup](../operations/postgres.md#tests) for the disposable database. The
non-Postgres group includes pure tests and local HTTP/TLS boundary tests; those boundary
tests require local sockets. Frozen
Anvil execution remains explicitly enabled with `RUN_LOCAL_EVM_REPLAY=1`.

Database tests request `postgres_database`, directly or through fixtures such
as `pg`, `new_pg`, or a domain's `db` fixture. Collection automatically applies
the `postgres` marker from that fixture dependency, including imported fixtures.
Request `postgres_database` for database provisioning; collection applies the marker for
test selection.

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
peer test deliberately restores the driver for its local handshake only; ordinary database
access continues to require the database fixture and guard.
Database fixtures also reject toolkit connections to another database URL or
to a schema outside their created set, even if a command reloads environment settings.

CI remains unchanged: every PR runs the full suite on pinned and minimum Python.
Use focused tests during editing, then the appropriate broader checks once a
change is coherent. Any later CI selection policy requires its own review and
measurement; the current CI checks remain required.

The [October 26
measurement](../archive/measurements/2026-10/testing-measurement-2026-10-26.md) records the
first
cleanup's before/after results, the cumulative full-suite result after all
follow-ups, retained coverage, and measurement limits.

The [fee-completeness
follow-up](../archive/measurements/2026-10/fee-completeness-test-measurement-2026-10-26.md)
shows how to move a calculation matrix to pure tests while retaining all
component/state combinations in fewer database scenarios.

The [malformed-receipt
follow-up](../archive/measurements/2026-10/malformed-receipt-test-measurement-2026-10-26.md)
keeps payload-length boundaries pure and retains ingestion/replay checks for
each product.

The [hosted API fixture
follow-up](../archive/measurements/2026-10/hosted-api-test-measurement-2026-10-26.md)
separates request validation, registry persistence, financial serving, and full
publication setup without reducing the hosted API case count.

[Documentation index](../README.md).
