# Malformed receipt matrices: October 26, 2026

All four malformed StrategyReported payload lengths (0, 191, 193 and 224 bytes)
remain covered for yvUSD, yBOLD and yETH. Twelve pure tests call each product's
accounting boundary and require the specific encoding rejection. Existing
historical-accounting tests continue to cover valid 192-byte receipts.

Six Postgres cases retain online ingestion and offline replay for each product.
They use 193-byte payloads, checking that malformed evidence becomes unresolved
with no accounting output. Replay cases first ingest valid evidence, then corrupt
the stored receipt and verify that recomputation marks it unavailable.

| Focused selection | Before | After |
| --- | ---: | ---: |
| Pure cases | 0 | 12 |
| Postgres cases | 24 | 6 |
| Passed | 24 | 18 |
| Pytest elapsed | 9.60 s | 3.00 s |

This saves 6.60 seconds in the measured pair: 69% less elapsed time, or 3.2×
faster. It is not a whole-suite speedup measurement. Total Postgres cases drop
from 761 to 743; the total collected count drops by six because pure decoding
need not be repeated for online and offline execution.

## Coverage check

In a temporary source copy, each product's exact-length guard was changed from
`len(data) != 192` to `len(data) < 192`, allowing trailing bytes. All six retained
integration cases failed: ingestion incorrectly stored `ok`, and replay failed
to count the receipt as unavailable. All six pure overlong-payload cases also
failed because accounting did not raise. The six truncated-payload cases passed.
No production source in the repository was modified.

## Validation and measurement limits

- Focused after selection: **18 passed in 3.00 s**.
- Complete yvUSD, yBOLD and yETH accounting tests, both review files, and the new
  pure matrix: **102 passed in 16.32 s**.
- Full non-Postgres selection with the task-owned database stopped and test URL
  unset: **544 passed, 7 skipped, 743 deselected in 8.03 s**.
- Focused Ruff and `git diff --check` passed.
- CI workflows, production code and dependency lockfile remain unchanged.

Before and after used CPython 3.12.3, the same locked dependencies and local
Postgres 18 on port 55439. The baseline is the working tree after the earlier
fee-completeness change. Container startup is excluded. These are single
sequential successful runs on a shared host, with cache effects possible; the
full Postgres suite was not rerun. Runs emit the existing websockets legacy
warning. The disposable database was removed after validation.

Reproduce after configuring the disposable database per `docs/postgres.md`:

```bash
python -m pytest -q tests/test_yvusd_review.py tests/test_distribution_review.py tests/test_malformed_receipts.py -k malformed --durations=5
python -m pytest -q tests/test_yvusd_review.py tests/test_distribution_review.py tests/test_yvusd_fees.py tests/test_ybold_fees.py tests/test_yeth_fees.py tests/test_malformed_receipts.py
python -m pytest -q -m 'not postgres'
```

For the before measurement, the first command omitted the new
`tests/test_malformed_receipts.py` file.
