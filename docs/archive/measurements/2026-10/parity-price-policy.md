> Historical reference captured from `docs/parity-price-policy.md` at `ef03f16`.
> This records a past design, measurement or rollout; its ports, IDs, branch
> states and rollback availability are not current operating instructions.
> Current guidance: [docs/reference/pricing.md](../../../reference/pricing.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/parity-price-policy.md).

## Measured controlled coverage impact

[The saved fixture receipt](../../../../benchmarks/results/2026-10/parity-policy-coverage.json) compares the original
analysis code at `5adc4b0f3eb8e7e50aa961864239d7746327a51d` with this policy,
against the same guarded disposable Postgres inputs. These are synthetic quotes,
not asset-price or production-coverage claims.

| Same six reports and six deposits | Before | After |
| --- | ---: | ---: |
| Priced reports / volume events | 6 | 3 |
| Unpriced reports / volume events | 0 | 3 |
| Known net yield / gross volume subtotal, USD | 21,050.75 | 35.00 |

The removed numeric contribution comes from three deliberately seeded parity
estimates, including a derived quote whose underlying price was a parity guess.
A matched observation, a supported contract valuation and genuine zero remain
valued. The remaining 35 is a known subtotal with explicit gaps, not a claim
that total economic yield/volume fell to 35.

The one-off before/after probe was consolidated into this receipt. Surviving
regressions in `test_parity_policy.py` protect the acquisition, cache, classified
CSV, valid-secondary, fee/refund and TVL boundaries. The two old retry tests that
expected Polygon/Arbitrum parity success now assert rejection while retaining
their retry-selection coverage. No supported regression was removed.
