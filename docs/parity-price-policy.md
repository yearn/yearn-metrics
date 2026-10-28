# Retire local parity estimates

`observed-or-contract-v1` retires locally invented fixed-parity quotes. Missing
provider observations no longer become USD 1 for a stablecoin, another token's
price at a 1:1 ratio, or CRV parity when the yCRV pool quote is unavailable. Both
batch and single DefiLlama paths require an accepted observation for the requested
asset. No opt-in local parity-estimate mode remains.

The existing optional contract adapters remain: historical reserves, conversion
rates, supplies and accepted underlying quotes can produce a derived valuation.
Removing a fixed-parity guess is separate from removing a measured conversion
rate. `--no-onchain-fallbacks` disables those local calculations. Yearn Prices
source selection, canonical EOD acceptance and upstream pricing policy are
unchanged; a provider quote is not newly asserted to be a market observation.

## Evidence and outputs

Earnings, legacy fee and volume rows expose `price_classification`,
`price_accepted`, `price_status` and `price_valuation_policy` in stored outputs
and CSVs. The classifications are:

| Classification | Meaning |
| --- | --- |
| `observed` | Accepted matched provider-observation evidence is present |
| `provider-quote` | A configured provider supplied the quote; this does not assert its upstream observation/estimation method |
| `derived` | A contract valuation uses conversion/reserve evidence and underlying quotes |
| `estimated` | Retained evidence identifies a retired local parity source; USD valuation is rejected |
| `legacy-unclassified` | A previously accepted quote lacks classification metadata; it is not relabeled as an observation or guessed estimate |
| `unavailable` | No usable quote is present |

A retired source anywhere in a derived quote's structured underlying evidence
rejects that quote too. A nonzero amount then retains unavailable USD, while raw
zero still values to zero without a quote. TVL collection applies the same
rejection and records its classification/policy in pricing evidence. Existing
selected TVL snapshots and immutable publications are not rewritten.

Yearn Prices may itself select upstream methods; `provider-quote` preserves that
distinction and the existing upstream-source metadata. This change does not
silently reject all provider quotes or all older unclassified evidence.

## Existing cache repair

Known retired source labels are explicitly eligible for reacquisition in the
ordinary DefiLlama price/volume commands, even when a legacy row says `ok`:

```bash
yearn-data price --source defillama --no-provider-fallback --no-onchain-fallbacks --limit 500
yearn-data price-volume --source defillama --no-onchain-fallbacks --limit 500
```

A successful accepted quote can replace the estimate. If acquisition fails,
the retired estimate is not preserved as a usable price: its old numeric value
and original JSON are retained under the explicitly qualified `retired_estimate`
audit field, while the current quote is unavailable/retryable as appropriate.
Audit JSON is opaque evidence, so it does not repeatedly select an already
retired row or contaminate a later valid quote's classification. Existing valid
observed/derived quotes still survive failed refreshes.

New analyses reject known cached estimates even before a cache repair is run,
and can select a valid permitted secondary quote instead. Analysis/export
parameters identify the new policy. Older saved analyses retain their captured
results until explicitly regenerated. Compare affected rows and coverage before
any separately authorized publication; this PR runs no production repair,
backfill, Redis update or publication.

## Measured controlled coverage impact

[The saved fixture receipt](parity-policy-coverage.json) compares the original
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
