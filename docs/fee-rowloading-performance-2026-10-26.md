# Fee row loading performance — October 26, 2026

The measured candidate packs ordered stored JSON rows into bounded groups before
decoding, reducing Python driver and JSON parser calls. The exact final candidate
and paired measurements are recorded below.

## Measurement boundary

The frozen baseline is commit `6a0437d`, after the existing summary aggregation
and grouped-history changes. The opt-in `scripts/benchmark_fee_summary.py`
separates connection setup, SQL execution and libpq transfer, cursor fetch and
Python row conversion, custom `Row` construction, and JSON decoding. `row_wrap_ms`
is nested within `fetch_ms`; these columns must not be added together. Server
execution and result transfer are measured together, not as separate SQL and
network timings. Per-row diagnostic timers perturb latency; latency comparisons
use `--no-profile`.

The fixture contains 32 monthly periods, 1,000 vault identities across four
chains, 32,000 fee rows and 32,000 earnings rows. Decimal strings, unavailable
values and genuine zero values are retained. Synthetic vault identities do not
model registered multi-contract products; existing domain tests cover those
semantics. Every run prepares and drops its own unique schema in an explicitly
provisioned disposable localhost Postgres. Fixture preparation is untimed.

Every sample constructs a fresh application object. Database and operating-system
caches may be warm. The separate second query measures the existing same-object
process cache. Neither measurement includes HTTP, CDN lookup, TLS, hosted dataset
selection, browser work, or whole-page latency. No production database,
publication, release selection or CI policy is changed.

## Baseline diagnosis

Three diagnostic samples per view and scope ran while the root and other agents
held CPU-intensive checks. Summary contract/product row-loading medians were
574.60/564.82 ms. The corresponding JSON decoding medians were 236.86/232.29 ms,
SQL execution plus transfer 215.31/211.79 ms, cursor fetch 81.58/78.84 ms, and
custom Row construction 19.82/19.24 ms within fetch. Removing the custom wrapper
alone therefore has a substantially smaller plausible benefit than reducing
per-row JSON decoder calls and transfer/driver overhead.

Frozen source SHA-256 digests:

- `pairing.py`: `9214c3c01bba478f3f1cb473e5640ba622cc646095fb18845dde72ac98b7db4b`
- `hosted_fees.py`: `dc6034a29cabe30ea1f93fcd7a9ee57b925d079b7f04047257de41c2a32981c8`
- `postgres.py`: `d920b1d20d17c2a693d5a14feff22cd77cfe4ccb25dcb89cca86dbf273f311f6`

## Final protected candidate

`PublishedFees.view_rows` now validates each stored value using PostgreSQL `json`
and preserves its text representation before concatenating at most 2,000 ordinal
positions into each array. This reduces driver rows and Python decoder calls
without changing financial arithmetic, JSON numeric conversion, source order,
filters, or the existing process cache. PostgreSQL `json`, rather than `jsonb`,
avoids normalizing numeric text or object keys. Empty, whitespace-only, multiple
or truncated stored JSON values fail instead of disappearing or creating extra
financial rows. The publication writer already rejects nonstandard NaN/Infinity.

The chosen source SHA-256 is
`d661faf5ef41b20e991602b0de7ee0a0cce1f7ba9442c348f61dd44b4e6fde1c`.
Baseline and candidate use the same frozen pairing algorithm. Five fresh objects
per view and scope were measured with `--no-profile` during an explicitly quiet
window: root held tests/builds, the reviewer remained read-only and the backend
agent held probes. Baseline preceded candidate. Fixture setup is excluded.

Medians in milliseconds; component medians need not add to the total.

| View / scope | Baseline total | Protected total | Baseline row loading | Protected row loading | Total reduction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Summary / contract | 879.78 | 586.87 | 559.36 | 344.39 | 33.3% |
| Summary / product | 836.21 | 658.63 | 507.36 | 359.27 | 21.2% |
| Grouped history / contract | 1,004.37 | 853.34 | 508.13 | 349.79 | 15.0% |
| Grouped history / product | 1,057.44 | 918.56 | 406.38 | 321.40 | 13.1% |

Full total-time ranges, baseline then candidate, were 836–1,112 / 573–738 ms,
801–972 / 587–705 ms, 958–1,067 / 772–906 ms and 1,000–1,140 / 874–938 ms
respectively. These are small, fixed-order samples on one shared host. Earlier
baseline and candidate runs show material drift; an earlier unprotected candidate
had a product-summary median regression despite lower medians in other views.
Those samples remain in the raw bundle and are not silently excluded as
outliers. The final protected sample supports a local improvement in every view,
not a precise production speedup or a confidence interval.

All five baseline and candidate responses per view/scope match complete
serialized JSON hashes, including metadata and every financial field:

| View / scope | SHA-256 |
| --- | --- |
| Summary / contract | `f1c23b9dbdb2e3b3269f361177ce91e707cdea43e03d7e75e848bf27aab91b3d` |
| Summary / product | `ab9a2fe458c2732ef8dda867f9c27a6b27e00ff3550565ddb9ee8643a10cadb9` |
| Grouped history / contract | `b17e49fd08da264418d77257e7b5f0e9d11ff90c21e51bee4c11301d2e577206` |
| Grouped history / product | `31aa9cd67b5487ed217194b15205a92f3ee1f1ceff410562ce4333e858cdf04e` |

## Final diagnostic attribution

Separate three-sample profiled runs show the final protected summary loader's
contract/product cursor fetch medians at 5.45/4.41 ms, including only 0.061/0.053 ms
of custom Row construction, compared with baseline 81.58/78.84 ms fetch including
19.82/19.24 ms wrapping. JSON decoder medians fell from 236.86/232.29 ms to
135.20/127.71 ms. Metadata plus row decoder calls fell from 64,001 to 33, and
custom Row constructions from 64,001 to 33. SQL execution plus transfer was
235.64/214.22 ms compared with baseline 215.31/211.79 ms; this does not establish
a SQL execution improvement. Reduced per-row driver and decoder overhead is the
supported attribution. The final protected diagnostic also checks all grouped
history hashes. Profile totals are not used for the latency table.

All raw samples, source hashes, response hashes and the earlier noisy runs are
retained in [the measurement bundle](fee-rowloading-performance-2026-10-26.json).
Its `final_baseline` and `final_protected_candidate` entries own the final table;
`baseline_diagnostic` and `final_protected_diagnostic` own phase attribution.

## Alternatives and validation cost

Exploratory three-sample summary medians (contract/product) were:

| Candidate | Total ms | Row loading ms | Disposition |
| --- | --- | --- | --- |
| Existing loader | 787 / 799 | 506 / 491 | Baseline |
| Tuple driver rows | 783 / 847 | 494 / 480 | No useful total improvement |
| Client-side array concatenation | 691 / 733 | 441 / 469 | Smaller improvement; still transfers every row |
| SQL array concatenation, unvalidated | 554 / 586 | 307 / 313 | Rejected: corrupt values can split or disappear |
| Nested envelopes with count guard | 671 / 760 | 374 / 408 | Rejected: compensating corrupt fragments can bypass guards |
| Per-row SQL JSON validation | 691 / 794 | 431 / 445 | Selected: validates stored value boundaries |

All these exploratory summary hashes matched the baseline on valid fixtures.
They are not the final latency result and were measured at different times. SQL
validation adds work: the exploratory validated SQL version loaded rows around
124/132 ms slower than unvalidated SQL concatenation. This is a rough validation
cost comparison under host drift, not an isolated controlled SQL timing. The
faster unsafe candidate was deliberately not delivered.

The 2,000-row bound limits each individual array/decode by row count, not bytes.
The query still materializes its result in libpq and the view retains all decoded
financial rows, as before. This is not streaming or a measured memory reduction;
no production row-size or database-load claim follows from this synthetic fixture.

## Reproduce

Export `YEARN_TEST_POSTGRES_URL` for an explicitly provisioned disposable
localhost database. The benchmark rejects remote hosts and never starts Postgres
implicitly. Freeze baseline sources before editing:

```bash
git show 6a0437d:src/yearn_data/pairing.py > /tmp/row-before-pairing.py
git show 6a0437d:src/yearn_data/hosted_fees.py > /tmp/row-before-hosted.py
PYTHONPATH=src python scripts/benchmark_fee_summary.py --postgres \
  --months 32 --vaults 1000 --repeats 5 --no-profile --include-history \
  --pairing-source /tmp/row-before-pairing.py \
  --hosted-source /tmp/row-before-hosted.py --output /tmp/row-before.json
PYTHONPATH=src python scripts/benchmark_fee_summary.py --postgres \
  --months 32 --vaults 1000 --repeats 5 --no-profile --include-history \
  --compare-to /tmp/row-before.json --output /tmp/row-after.json
```

Omit `--no-profile` in separate diagnostic runs. `--compare-to` checks complete
serialized response hashes for summary and grouped vault history in both scopes,
including response metadata and all financial fields. A hash check establishes
parity with the frozen baseline; known-result domain tests remain necessary for
independent financial correctness.

## Regression validation

The full existing suite passed against disposable Postgres: **1,355 passed,
7 skipped**, one existing websockets deprecation warning, in **182.54 seconds**.
The three new database cases cover sparse legacy ordinal ordering and filters,
plus corrupted empty and multiple-value stored JSON. Existing known-result
financial, product-membership and publication-selection coverage remains intact.
Focused Ruff and `git diff --check` passed; independent review found no blockers.
CI configuration is unchanged. This test runtime is a validation result, not a
controlled test-suite performance comparison.

The previous summary changes were committed locally as backend `6a0437d` and
Powerglove `94487b8`. This row-loading pass remains uncommitted; no production
publication, application deployment, or remote push was performed.
