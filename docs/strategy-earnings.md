# Strategy earnings accounting

The optional `report-time-strategy-ownership-v1` policy adds tokenized strategy
report gains and losses attributable to non-overlapping holders. It leaves
`totalFeesPaidUsd` and every other fee field unchanged. A parent-owned fraction
is excluded because parent earnings remain in the existing accounting. yBOLD
staking, yvUSD lockers, and yETH recovery retain their designated product earnings
leg; their redistributed yield is not added again.

## Materiality

An unresolved path is conservatively omitted only when its **annual gross gains
plus losses are strictly below $1,000**. Gains and losses cannot cancel to pass
this test. Activity is combined across reports and strategies for each stable
`chainId:beneficiaryAddress` path. A wholly missing report uses its strategy
address as the unresolved path. An unpriced unresolved amount or a path at/above
$1,000 rejects selection instead of becoming zero or being assumed external.

The response's `strategyEarningsAccounting.coveragePeriods` contains all omitted
paths and their combined gross activity. This is an annual disclosure, independent
of request filters, not a claim that each filtered month omitted the annual sum.
Review the combined omission before activating a publication; passing the
per-path gate does not by itself establish that the combined omission is small.

Known overlap is normal accounting, not a missing-data warning. Small unresolved
omissions remain identifiable in publication metadata rather than being described
as observed zero. Raw strategy gains/losses remain in `lifetimeEarnings.raw*`;
the existing gains/losses/net fields contain the counted portion.

## Evidence and selection

Supply a reviewed ownership ledger with `--strategy-ownership`:

```sh
yearn-data --db neon select-pairing \
  --earnings-run-id 33 --fees-run-id 34 \
  --strategy-ownership /path/to/ownership-ledger.json \
  --out /path/to/candidate-publication
```

Use a separate candidate directory when reviewing a change. Selection does not
modify source analyses. Existing selections without this option keep their exact
legacy IDs and behavior. The ledger is embedded in the selected manifest, hashed
into the dataset identity, revalidated on reload, and retained in hosted response
metadata after consolidation. A failed validation does not replace `current.json`.

Ledger fields:

- `policy`: `report-time-strategy-ownership-v1`.
- `sourceFeeRowsSha256`: `strategy_earnings.digest(fee_rows)`, binding the entire
  ordered fee analysis, including valuations and report identities.
- `periods`: ordered, nonoverlapping full UTC years, each with `since` and `until`.
- `reports`: event identity (`chainId`, `txHash`, `logIndex`), `vaultAddress`,
  `blockTimestamp`, and `fractions`.
- Each fraction has `classification` (`included`, `parent-overlap`, `unresolved`),
  a stable `pathId`, and positive integer-string `numerator` / `denominator`.
  Fractions must sum exactly to one. Fractions must describe pre-report ownership,
  before that report's fee-share mints and profit locking.

Queries make no RPC calls. Evidence acquisition and classification remain a
separate, reviewed preparation step; contract names or current balances are not
substitutes for historical ownership evidence.

## Coverage and timing

This is report-time ownership accounting, not time-weighted accrual accounting.
Strategy earnings use the strategy report date. Parent reports keep their original
recognition dates. Do not describe this policy as reconciling profit-unlocking or
recognition timing between parent and child reports.

The initial candidate uses the audited **October 1, 2025–October 1, 2026 exclusive
UTC** period. Reports outside reviewed periods are not added; the metadata
explicitly reports `outsideCoverageReportCount`. The $1,000 rule does **not** justify
omitting unaudited history outside those periods. Full-history strategy earnings
require additional annual evidence coverage before a full-history rollout.

The implementation and candidate do not activate a live publication. Preview the
candidate totals and coverage disclosures before consolidation/activation.
