# yETH recovery distributions

The Ethereum Yield Vault `0xd7a540ba3626c0aa66e7db4088971d0cd64695b6`
uses the Recovery Vault `0xe5387cd454dcc542421c069c009d915ab9efaafd` as its
accountant. The accountant returns the smaller of the report gain and the
remaining recovery target as the fee operand. Once the target is satisfied,
it returns zero. These deductions are classified as creditor recovery distributions.

Source: [Sourcify exact-match recovery
contract](https://sourcify.dev/server/v2/contract/1/0xE5387cd454Dcc542421c069C009D915Ab9EFaaFd?fields=all),
`src/Strategy.sol:yETHRecoveryVault`, deployed at block 24522098.
Verified runtime hash: `0x48ca6275895571ea285bae62f082392412ccc3ac035877c881a05ade9cf0653e`.

## Accounting

This rule was introduced in `canonical-fees-9`, the current
`fees.METHOD_VERSION`.

- Preserve raw StrategyReported deductions as `reported_total_fees_raw`.
- Classify the deduction less actual protocol fees as `recovery_distribution_raw`;
  expose its valuation as `recoveryDistributionUsd`.
- Actual protocol fees remain fee revenue; classify each observed split using its report
  evidence.
- Require receipt identity, report amounts, the historical accountant, its yield
  vault, and its exact runtime hash to match before reclassification. Retain
  evidence for offline replay. Unknown configurations remain unresolved.
- Index the Recovery Vault as a Tokenized Strategy and retain its observed fees,
  including any future nonzero charges. Its 21 reports through the publication
  cutoff charged zero protocol and performance fees.
- `scope=product` groups both contracts under **yETH Recovery**. Fees include both
  members; investment earnings come from the Yield Vault only. Contract scope preserves the
  original raw records and labels.

Recovery reports can recognize both forwarded investment yield and donations.
Product investment earnings count the forwarded yield once at the Yield Vault. Normal
deposits are capital;
yield subsequently earned on that capital is investment earnings. Direct donations
are classified as recovery contributions.
Product earnings use the Yield Vault’s gains. A separate treasury/donor transfer
reconciliation is required to quantify donations.

## Validation and publication

Validate receipt/configuration rejection, real protocol fees, zero/partial
distributions at the target, offline replay, valuation and product earnings
before a coherent publication. Preserve unrelated fee events exactly. See
[fee accounting](../reference/fee-accounting.md) and the
[historical reconciliation](../archive/rollouts/yeth-recovery-fees.md).
Use the current selected publication for operating IDs and counts.

[Documentation index](../README.md).
