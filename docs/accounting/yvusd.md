# yvUSD fee classification

The Ethereum base USD yVault (`0x696d02Db93291651ED510704c9b286841d506987`)
reports locker yield redistribution inside `StrategyReported.total_fees`.
That redistribution is classified as locker yield.

The current `fees.METHOD_VERSION` (`canonical-fees-9`) binds each report to the preceding
`FeesReported` event
from its historical accountant, using the complete transaction receipt and a
block-hash-pinned `accountant()` call. A split cannot be reused across reports.
This rule was introduced in `canonical-fees-7`.
Both historical accountants are supported; unknown implementations remain
unresolved until verified. Zero deductions without a split event are known zero.

The two allowed accountants serve **only this yvUSD vault**. Their verified
`report(address,uint256,uint256)` implementations require the caller to equal
`address(asset)`; `asset` is immutable in `BaseStrategy`, and both constructor
arguments bind it to `0x696d02Db93291651ED510704c9b286841d506987`:

- [Accountant 0xaaafea48 verified source and
constructor](https://eth.blockscout.com/address/0xaaafea48472f77563961cdb53291dedfb46f9040?tab=contract).
- [Accountant 0xab9018a6 verified source and
constructor](https://eth.blockscout.com/address/0xab9018a699003a777d690c156045dfc4a7ef3a96?tab=contract).

Each has one `FeesReported` emission, inside that guarded report function.
Consequently, a permitted accountant cannot emit a fee split for another vault;
consuming pending splits at each yvUSD report is sufficient. Other emitters are
ignored. This invariant must be reverified before adding another accountant.

For an accepted split:

- `total_fees_paid_raw` / `totalFeesPaidUsd` include actual management and performance fees.
- `manager_fee_raw` excludes locker distributions.
- `locker_bonus_raw` / `lockerBonusUsd` retain the redistribution separately.
- `reported_total_fees_raw` / `reportedTotalFeesUsd` retain the original deduction.

The additional fields are populated for this verified yvUSD classification;
other contracts retain their own component applicability and availability states. API
aggregates use
the same known-amount summation policy as existing component fields.

Acceptance requires the split sum to equal the final vault deduction exactly,
with zero protocol fees. Capped, rounded, protocol-charged, missing, or ambiguous
splits remain unresolved until exact reconciliation is supported.
The [historical classification reconciliation](../archive/rollouts/yvusd-fee-accounting.md)
records raw totals and the receipt cohort for its captured publication.

## Refresh and publication

Normal `index-fees --canonical --version v3` upgrades old yvUSD canonical rows and acquires
evidence for new reports. Receipts and historical accountant evidence persist in
Neon. `recompute-fees formulas --version v3` replays retained evidence
without RPC access. A fee USD analysis must then be regenerated using the same
cutoff and deferred-event manifest as the paired earnings analysis, selected
with `select-pairing`, and followed by `prepare-analytics` for profitability.
Preserve completed analyses and generate a new dataset identity for each changed result.

Strategy gains/losses, incident adjustments, prices and Tokenized Strategy accounting retain
their existing policies. The frontend consumes the
corrected canonical metric without an address exclusion.

Public receipt fixtures in `tests/fixtures/fees/yvusd.json` retain the relevant
vault/accountant logs for regression tests; live ingestion retains complete
receipts. January's fixture preserves genuine management and performance fees;
March and October's fixtures contain only locker yield. Operator evidence and
before/after reconciliation are retained in ignored `artifacts/yvusd-fees/`.

## Review and unavailable values

A known zero fee remains zero even when its positive locker distribution cannot
be valued. Such a row reports `zero_fee_unpriced_distribution` (or
`metadata_unavailable` for missing asset metadata). Grouped output counts
`unpriced_distribution_events` and leaves pricing/fee-USD completeness false
until those distributions are valued. Optional component fields follow each contract’s
supported classification; evaluate distribution coverage within that scope.

Canonical CSV exports include `locker_bonus_raw` and `reported_total_fees_raw`
regardless of row order, with blank values for contracts outside their scope.
Malformed report payloads remain unresolved in both indexing and offline replay.
Receipt acquisition failures retain `receipt_unavailable_or_invalid`; normal V3
reports in the same transaction can still use their own report inputs. An explicit
`--refresh-evidence` also reacquires the historical accountant at the receipt block
hash, while ordinary retries can reuse retained evidence.

The run IDs, dataset ID and USD reconciliation above are operator receipts for the
historical publication. Inspect the current selection for today’s publication. The review
replays the
438 retained report/receipt pairs offline for raw reconciliation; the committed
public fixtures and regression tests cover classification, valuation, export,
query scoping and pairing without needing the operator database.

Offline replay distinguishes `cached_distribution_evidence_missing_or_invalid`
(missing or unreconciled accountant evidence) from
`cached_allocator_inputs_missing_or_changed` (changed source report inputs).
The former requires acquiring valid receipt/accountant evidence through indexing;
indexing supplies that evidence for subsequent offline replay.

A retained accountant outside the verified allowlist records
`rejected_yvusd_accountant`. Ordinary `--retry-unresolved` retains that pinned
evidence. Use `index-fees --canonical --version v3 --retry-unresolved
--refresh-evidence` with a reliable historical provider to reacquire it. If the
same address remains, verify the new implementation before expanding the
allowlist; acceptance requires implementation verification after acquisition.

## Product membership and earnings

The `scope=product` fee API names the product **yvUSD** and groups these Ethereum contracts:

- Unlocked yvUSD: `0x696d02db93291651ed510704c9b286841d506987` (product address).
- Locked yvUSD: `0xaaafea48472f77563961cdb53291dedfb46f9040`.
- Earlier locked yvUSD: `0xab9018a699003a777d690c156045dfc4a7ef3a96`.

All members' actual fees count toward the product. Earnings come from the underlying
unlocked vault: they already include returns on locked holders' capital. The locked
contracts hold yvUSD shares, and their additional reported gains include locker-bonus
redistributions. Summing their gains again would double count product earnings.
The locker bonus remains separate from actual fees. Contract-scope records and names
are preserved; product charts use the API’s `yvUSD` name.

Both locked contracts are retained in the maintained Tokenized Strategy inventory,
so bounded updater runs collect their fee reports, including any future actual fees.
The earlier contract's verified deployment is block 24271898; the current contract's
is block 24329199. Both expose API version 3.0.4 and use yvUSD as their asset.
Sources are Sourcify exact-match `src/LockedyvUSD.sol:LockedyvUSD` at each address.

The selected reporting scope covers full onchain history. Preserve
the four underlying reports with real fees on January 26–28, 2026 (UTC):
$0.533052320825365730302 management and $0.9690390576925535207695 performance fees.
Configured downstream rates use the separate [effective-rate policy](effective-fee-products.md).
Historical reconciliation totals and old selected IDs are retained in the
[product rollout record](../archive/rollouts/yvusd-product.md); inspect the live selection
and retained data to establish current publication and recovery options.

[Documentation index](../README.md).
