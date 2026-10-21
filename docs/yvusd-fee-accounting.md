# yvUSD fee classification

The Ethereum base USD yVault (`0x696d02Db93291651ED510704c9b286841d506987`)
reports locker yield redistribution inside `StrategyReported.total_fees`.
That redistribution is not fee revenue.

`canonical-fees-7` binds each report to the preceding `FeesReported` event
from its historical accountant, using the complete transaction receipt and a
block-hash-pinned `accountant()` call. A split cannot be reused across reports.
Both historical accountants are supported; unknown implementations remain
unresolved until verified. Zero deductions without a split event are known zero.

For an accepted split:

- `total_fees_paid_raw` / `totalFeesPaidUsd` include actual management and performance fees.
- `manager_fee_raw` excludes locker distributions.
- `locker_bonus_raw` / `lockerBonusUsd` retain the redistribution separately.
- `reported_total_fees_raw` / `reportedTotalFeesUsd` retain the original deduction.

The additional fields are populated for this verified yvUSD classification;
absence on other contracts does not establish a known zero. API aggregates use
the same known-amount summation policy as existing component fields.

Acceptance requires the split sum to equal the final vault deduction exactly,
with zero protocol fees. Capped, rounded, protocol-charged, missing, or ambiguous
splits remain unresolved instead of receiving a guessed proportional allocation.
The retained October 8, 2026 reconciliation contains 438 reports that pass this rule: 395 positive deductions
and 43 zero deductions. There are two historical accountant contracts.
The raw totals are 1.502736 USDC actual fees and 30,213.781411 USDC locker yield.

## Refresh and publication

Normal `index-fees --canonical --version v3` upgrades old yvUSD canonical rows and acquires
evidence for new reports. Receipts and historical accountant evidence persist in
Neon. `recompute-fees formulas --version v3` replays retained evidence
without RPC access. A fee USD analysis must then be regenerated using the same
cutoff and deferred-event manifest as the paired earnings analysis, selected
with `select-pairing`, and followed by `prepare-analytics` for profitability.
Never rewrite completed analysis outputs or reuse an old dataset identity.

The change does not modify strategy gains/losses, incident adjustments, prices,
or the separate Tokenized Strategy accounting layer. The frontend consumes the
corrected canonical metric without an address exclusion.

Public receipt fixtures in `tests/fixtures/fees/yvusd.json` retain the relevant
vault/accountant logs for regression tests; live ingestion retains complete
receipts. January's fixture preserves genuine management and performance fees;
March and October's fixtures contain only locker yield. Operator evidence and
before/after reconciliation are retained in ignored `artifacts/yvusd-fees/`.

Published fee run: **18**, paired with unchanged earnings run **15**.
Dataset: `7ac5e43aa6ee095e6fea8991cf1ebb1c6c57a2feffa074da75b0aa776bef83f2`.
The exact USD totals are **1.5020913785179192510715** actual fees and
**30208.3417791514335400091614** locker distributions, reconciling to the
previous **30209.8438705299514592602329** deduction total. All non-yvUSD
fee-event fields were compared against run 16 and remain unchanged.


## Review and unavailable values

A known zero fee remains zero even when its positive locker distribution cannot
be valued. Such a row reports `zero_fee_unpriced_distribution` (or
`metadata_unavailable` for missing asset metadata). Grouped output counts
`unpriced_distribution_events` and leaves pricing/fee-USD completeness false
until those distributions are valued. Optional component fields absent from
unrelated contracts do not imply missing positive distributions.

Canonical CSV exports include `locker_bonus_raw` and `reported_total_fees_raw`
regardless of row order, with blank values for contracts where they do not apply.
Malformed report payloads remain unresolved in both indexing and offline replay.
Receipt acquisition failures retain `receipt_unavailable_or_invalid`; normal V3
reports in the same transaction can still use their own report inputs. An explicit
`--refresh-evidence` also reacquires the historical accountant at the receipt block
hash, while ordinary retries can reuse retained evidence.

The run IDs, dataset ID and USD reconciliation above are historical operator
receipts, not a claim about today's selected publication. The review replays the
438 retained report/receipt pairs offline for raw reconciliation; the committed
public fixtures and regression tests cover classification, valuation, export,
query scoping and pairing without needing the operator database.
