# Historical reference

These records preserve captured intent, reconciliations, apparatus limits and
recovery evidence. Their run/release IDs, local ports, branch status and assertions
about retained releases apply only to the recorded revision/time. Use the
[current task index](../README.md) for operations. Review archived commands against the
current environment and obtain authorization before production execution.

## Design

- [2026-10-branch-stack](design/2026-10-branch-stack.md).
- [powerglove-pairing-requirements](design/powerglove-pairing-requirements.md).
-
[powerglove-remaining-cutover-requirements](design/powerglove-remaining-cutover-requirements.md).
## Rollouts

- [2026-10-13-service-retirement](rollouts/2026-10-13-service-retirement.md).
- [2026-10-15-pricing](rollouts/2026-10-15-pricing.md).
- [2026-10-analytics-cutover](rollouts/2026-10-analytics-cutover.md).
- [2026-10-tvl-backfill](rollouts/2026-10-tvl-backfill.md).
- [effective-fee-products](rollouts/effective-fee-products.md).
- [hosted-api](rollouts/hosted-api.md).
- [powerglove-accounting-contract](rollouts/powerglove-accounting-contract.md).
- [powerglove-pairing](rollouts/powerglove-pairing.md).
- [tvl-curation-corrections](rollouts/tvl-curation-corrections.md).
- [tvl-overlaps](rollouts/tvl-overlaps.md).
- [ybold-fee-accounting](rollouts/ybold-fee-accounting.md).
- [yeth-recovery-fees](rollouts/yeth-recovery-fees.md).
- [yvusd-fee-accounting](rollouts/yvusd-fee-accounting.md).
- [yvusd-product](rollouts/yvusd-product.md).
## Measurements

-
[cumulative-powerglove-performance-2026-10-26](measurements/2026-10/cumulative-powerglove-performance-2026-10-26.md).
-
[fee-completeness-test-measurement-2026-10-26](measurements/2026-10/fee-completeness-test-measurement-2026-10-26.md).
-
[fee-rowloading-performance-2026-10-26](measurements/2026-10/fee-rowloading-performance-2026-10-26.md).
-
[fee-serving-performance-2026-10-26](measurements/2026-10/fee-serving-performance-2026-10-26.md).
-
[fee-summary-performance-2026-10-26](measurements/2026-10/fee-summary-performance-2026-10-26.md).
-
[hosted-api-test-measurement-2026-10-26](measurements/2026-10/hosted-api-test-measurement-2026-10-26.md).
- [incremental-tvl-history](measurements/2026-10/incremental-tvl-history.md).
-
[malformed-receipt-test-measurement-2026-10-26](measurements/2026-10/malformed-receipt-test-measurement-2026-10-26.md).
- [parity-price-policy](measurements/2026-10/parity-price-policy.md).
- [refresh-performance](measurements/2026-10/refresh-performance.md).
- [report-coverage-performance](measurements/2026-10/report-coverage-performance.md).
-
[testing-coverage-review-2026-10-26](measurements/2026-10/testing-coverage-review-2026-10-26.md).
- [testing-measurement-2026-10-26](measurements/2026-10/testing-measurement-2026-10-26.md).

## Upstream evidence

[The saved DefiLlama Yearn-USDC patch](upstream/defillama-yearn-usdc.patch) is
retained because its submission/adoption status was not verified during cleanup.
It records a proposed registry change. Verify current upstream membership against the
current registry.
Apply it only after reviewing current upstream status and compatibility. Its original path
and SHA-256 are in the
[migration map](../migration.md).

## Frozen raw measurements

[Results index](../../benchmarks/results/2026-10/README.md) lists byte-preserved
JSON receipts, source paths, hashes and narrative owners. New measurements belong
in ignored `.scratch/`; promoting durable reference evidence requires review.
