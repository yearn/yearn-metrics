# Documentation migration map

The cleanup starts at commit `ef03f16fe1c90299ff5342c931563a1e8ffdf26a`. This index accounts for old source
paths, current topic owners and retained historical sections. Git retains original
content; the historical copies below have date/revision notices and rebased links.
Raw JSON receipts and the upstream patch keep their exact bytes and SHA-256.
This is a navigation/retention record, not a procedure for restoring old live data.

| Previous path | Current owner or archive | Additional historical record |
| --- | --- | --- |
| `docs/accounting/effective-fee-products.md` | [docs/accounting/effective-fee-products.md](accounting/effective-fee-products.md) | [effective-fee-products](archive/rollouts/effective-fee-products.md) |
| `docs/accounting/yeth-recovery-fees.md` | [docs/accounting/yeth.md](accounting/yeth.md) | [yeth-recovery-fees](archive/rollouts/yeth-recovery-fees.md) |
| `docs/accounting/yvusd-product.md` | [docs/accounting/yvusd.md](accounting/yvusd.md) | [yvusd-product](archive/rollouts/yvusd-product.md) |
| `docs/analysis-recovery.md` | [docs/operations/recovery.md](operations/recovery.md) | — |
| `docs/bounded-report-catchup.md` | [docs/operations/report-ingestion.md](operations/report-ingestion.md) | — |
| `docs/canonical-history.md` | [docs/reference/retention.md](reference/retention.md) | — |
| `docs/canonical-tvl-ownership.md` | [docs/reference/tvl-accounting.md](reference/tvl-accounting.md) | — |
| `docs/cumulative-powerglove-performance-2026-10-26.json` | [benchmarks/results/2026-10/cumulative-powerglove-performance-2026-10-26.json](../benchmarks/results/2026-10/cumulative-powerglove-performance-2026-10-26.json) | — |
| `docs/cumulative-powerglove-performance-2026-10-26.md` | [docs/archive/measurements/2026-10/cumulative-powerglove-performance-2026-10-26.md](archive/measurements/2026-10/cumulative-powerglove-performance-2026-10-26.md) | — |
| `docs/database-refresh.md` | [docs/operations/refresh.md](operations/refresh.md) | — |
| `docs/earnings-and-fees.md` | [docs/operations/research-exports.md](operations/research-exports.md) | — |
| `docs/earnings-pricing.md` | [docs/reference/pricing.md](reference/pricing.md) | — |
| `docs/fantom-prestate-replay.md` | [docs/development/fantom-replay.md](development/fantom-replay.md) | — |
| `docs/fee-accounting.md` | [docs/reference/fee-accounting.md](reference/fee-accounting.md) | — |
| `docs/fee-completeness-test-measurement-2026-10-26.md` | [docs/archive/measurements/2026-10/fee-completeness-test-measurement-2026-10-26.md](archive/measurements/2026-10/fee-completeness-test-measurement-2026-10-26.md) | — |
| `docs/fee-rowloading-performance-2026-10-26.json` | [benchmarks/results/2026-10/fee-rowloading-performance-2026-10-26.json](../benchmarks/results/2026-10/fee-rowloading-performance-2026-10-26.json) | — |
| `docs/fee-rowloading-performance-2026-10-26.md` | [docs/archive/measurements/2026-10/fee-rowloading-performance-2026-10-26.md](archive/measurements/2026-10/fee-rowloading-performance-2026-10-26.md) | — |
| `docs/fee-serving-performance-2026-10-26.json` | [benchmarks/results/2026-10/fee-serving-performance-2026-10-26.json](../benchmarks/results/2026-10/fee-serving-performance-2026-10-26.json) | — |
| `docs/fee-serving-performance-2026-10-26.md` | [docs/archive/measurements/2026-10/fee-serving-performance-2026-10-26.md](archive/measurements/2026-10/fee-serving-performance-2026-10-26.md) | — |
| `docs/fee-summary-performance-2026-10-26.json` | [benchmarks/results/2026-10/fee-summary-performance-2026-10-26.json](../benchmarks/results/2026-10/fee-summary-performance-2026-10-26.json) | — |
| `docs/fee-summary-performance-2026-10-26.md` | [docs/archive/measurements/2026-10/fee-summary-performance-2026-10-26.md](archive/measurements/2026-10/fee-summary-performance-2026-10-26.md) | — |
| `docs/historical-envio-ingestion.md` | [docs/operations/report-ingestion.md](operations/report-ingestion.md) | — |
| `docs/hosted-api-test-measurement-2026-10-26.md` | [docs/archive/measurements/2026-10/hosted-api-test-measurement-2026-10-26.md](archive/measurements/2026-10/hosted-api-test-measurement-2026-10-26.md) | — |
| `docs/hosted-api.md` | [docs/operations/hosted-api.md](operations/hosted-api.md) | [hosted-api](archive/rollouts/hosted-api.md) |
| `docs/incremental-tvl-history.md` | [docs/reference/tvl-history-preparation.md](reference/tvl-history-preparation.md) | [incremental-tvl-history](archive/measurements/2026-10/incremental-tvl-history.md) |
| `docs/legacy-service-retirement.md` | [docs/archive/rollouts/2026-10-13-service-retirement.md](archive/rollouts/2026-10-13-service-retirement.md) | — |
| `docs/malformed-receipt-test-measurement-2026-10-26.md` | [docs/archive/measurements/2026-10/malformed-receipt-test-measurement-2026-10-26.md](archive/measurements/2026-10/malformed-receipt-test-measurement-2026-10-26.md) | — |
| `docs/nightly-refresh.md` | [docs/operations/nightly.md](operations/nightly.md) | — |
| `docs/parity-policy-coverage.json` | [benchmarks/results/2026-10/parity-policy-coverage.json](../benchmarks/results/2026-10/parity-policy-coverage.json) | — |
| `docs/parity-price-policy.md` | [docs/reference/pricing.md](reference/pricing.md) | [parity-price-policy](archive/measurements/2026-10/parity-price-policy.md) |
| `docs/postgres.md` | [docs/operations/postgres.md](operations/postgres.md) | — |
| `docs/powerglove-accounting-contract.md` | [docs/reference/accounting.md](reference/accounting.md) | [powerglove-accounting-contract](archive/rollouts/powerglove-accounting-contract.md) |
| `docs/powerglove-analytics.md` | [docs/reference/analytics.md](reference/analytics.md) | [2026-10-analytics-cutover](archive/rollouts/2026-10-analytics-cutover.md) |
| `docs/powerglove-branch-stack.md` | [docs/archive/design/2026-10-branch-stack.md](archive/design/2026-10-branch-stack.md) | — |
| `docs/powerglove-pairing-requirements.md` | [docs/archive/design/powerglove-pairing-requirements.md](archive/design/powerglove-pairing-requirements.md) | — |
| `docs/powerglove-pairing.md` | [docs/reference/api.md](reference/api.md) | [powerglove-pairing](archive/rollouts/powerglove-pairing.md) |
| `docs/powerglove-remaining-cutover-requirements.md` | [docs/archive/design/powerglove-remaining-cutover-requirements.md](archive/design/powerglove-remaining-cutover-requirements.md) | — |
| `docs/powerglove-tvl-api.md` | [docs/reference/api.md](reference/api.md) | — |
| `docs/prices-backfill-refresh-2026-10-15.md` | [docs/archive/rollouts/2026-10-15-pricing.md](archive/rollouts/2026-10-15-pricing.md) | — |
| `docs/pricing-clients.md` | [docs/reference/pricing.md](reference/pricing.md) | — |
| `docs/refresh-benchmark-2026-10-27.json` | [benchmarks/results/2026-10/refresh-benchmark-2026-10-27.json](../benchmarks/results/2026-10/refresh-benchmark-2026-10-27.json) | — |
| `docs/refresh-performance.md` | [docs/archive/measurements/2026-10/refresh-performance.md](archive/measurements/2026-10/refresh-performance.md) | — |
| `docs/report-coverage-benchmark-2026-10-27.json` | [benchmarks/results/2026-10/report-coverage-benchmark-2026-10-27.json](../benchmarks/results/2026-10/report-coverage-benchmark-2026-10-27.json) | — |
| `docs/report-coverage-performance.md` | [docs/archive/measurements/2026-10/report-coverage-performance.md](archive/measurements/2026-10/report-coverage-performance.md) | — |
| `docs/shared-database.md` | [docs/operations/postgres.md](operations/postgres.md) | — |
| `docs/testing-coverage-review-2026-10-26.md` | [docs/archive/measurements/2026-10/testing-coverage-review-2026-10-26.md](archive/measurements/2026-10/testing-coverage-review-2026-10-26.md) | — |
| `docs/testing-measurement-2026-10-26.md` | [docs/archive/measurements/2026-10/testing-measurement-2026-10-26.md](archive/measurements/2026-10/testing-measurement-2026-10-26.md) | — |
| `docs/testing.md` | [docs/development/testing.md](development/testing.md) | — |
| `docs/tvl-comparison.md` | [docs/operations/tvl-comparison.md](operations/tvl-comparison.md) | — |
| `docs/tvl-curation-corrections.md` | [docs/reference/tvl-accounting.md](reference/tvl-accounting.md) | [tvl-curation-corrections](archive/rollouts/tvl-curation-corrections.md) |
| `docs/tvl-curation-indexing.md` | [docs/reference/curation-indexing.md](reference/curation-indexing.md) | — |
| `docs/tvl-history.md` | [docs/operations/tvl.md](operations/tvl.md) | [2026-10-tvl-backfill](archive/rollouts/2026-10-tvl-backfill.md) |
| `docs/tvl-incremental-benchmark-2026-10-27.json` | [benchmarks/results/2026-10/tvl-incremental-benchmark-2026-10-27.json](../benchmarks/results/2026-10/tvl-incremental-benchmark-2026-10-27.json) | — |
| `docs/tvl-overlaps.md` | [docs/reference/api.md](reference/api.md) | [tvl-overlaps](archive/rollouts/tvl-overlaps.md) |
| `docs/upstream/defillama-yearn-usdc.patch` | [docs/archive/upstream/defillama-yearn-usdc.patch](archive/upstream/defillama-yearn-usdc.patch) | — |
| `docs/ybold-fee-accounting.md` | [docs/accounting/ybold.md](accounting/ybold.md) | [ybold-fee-accounting](archive/rollouts/ybold-fee-accounting.md) |
| `docs/yvusd-effective-fees.md` | [docs/accounting/effective-fee-products.md](accounting/effective-fee-products.md) | — |
| `docs/yvusd-fee-accounting.md` | [docs/accounting/yvusd.md](accounting/yvusd.md) | [yvusd-fee-accounting](archive/rollouts/yvusd-fee-accounting.md) |
| `docs/zero-decimal-valuation.md` | [docs/operations/recovery.md](operations/recovery.md) | — |


The upstream patch's SHA-256 is `919f276d6fe3061a46f387ae27e421bf88c47d9161f273076f3c2463043e7d80`.
Its current upstream disposition is unverified; preserving it avoids deleting
potentially unique evidence based only on the lack of old incoming links.

[Documentation index](README.md).
