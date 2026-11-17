# Pricing policy and evidence

Local fixed-parity estimates are retired under `observed-or-contract-v1`.
Missing provider observations remain unavailable; optional contract conversions
require underlying quotes. See the [quote classification and cached-estimate treatment](#quote-classification-and-retired-estimates).

The `price` and `analyze lifetime-yield` commands use Yearn Prices first.
By default, they can also use DefiLlama when a Yearn price is unavailable.

## Configure Yearn Prices

| Setting | Purpose | Default |
| --- | --- | --- |
| `YEARN_PRICE_PROD_KEY` | API key | Required |
| `YEARN_PRICE_PROD_BASE_URL` | Service address | `https://prices.yearn.dev` |
| `YEARN_PRICE_BATCH_TOKENS` | Tokens per batch | 20; maximum 50 |

Each batch segment requests at most 90 days per token.
HTTP requests allow three attempts by default.

## Use only Yearn Prices

Use these commands to price and analyze reports already stored in the database:

```bash
yearn-data --db neon price --source yearn-prices \
  --no-provider-fallback --no-onchain-fallbacks --limit 500
yearn-data --db neon analyze lifetime-yield \
  --price-source yearn-prices --no-provider-fallback
yearn-data --db neon export lifetime-yield --out exports/earnings
```

`--no-provider-fallback` prevents the use of DefiLlama prices.
This also prevents analysis from using an existing DefiLlama price in the database.
`--no-onchain-fallbacks` disables local price calculations that use onchain data.
The local onchain adapters also depend on DefiLlama quotes. When acquiring Yearn
Prices without provider fallback, both flags are required; enabling the local
onchain adapters in that mode is rejected before any price requests.
The combined `run lifetime-yield` command checks this policy and required Yearn
Prices credential configuration before discovery or event indexing starts.

These commands price already stored onchain events.
Volume and legacy fee analyses keep their existing DefiLlama policy.
Legacy fee analysis uses only successful stored DefiLlama prices; an absent DefiLlama
valuation remains unavailable under that policy.

## Understand price requests

The requested price time is 23:59:59 UTC on the report day.
The client checks the asset, timestamp, and positive price value.
Reports from an open UTC day are saved as `pending` without requesting a future
end-of-day point. Once the day closes, an ordinary `price` or `run lifetime-yield`
invocation retries them automatically; this retry is part of ordinary invocation behavior. The
configured DefiLlama fallback can still supply an interim valuation.

If a successful batch response omits a target, the client makes an exact historical request
for that target.
Network failures remain eligible for retry.
An authentication failure stops the run.
Malformed or duplicate batch points are omitted and checked through exact
requests, while valid points in the same batch remain usable. A malformed exact
response is saved as `invalid`, remains eligible for
`--retry-missing`, and can use the configured provider fallback.
An unsuccessful DefiLlama fallback fetch preserves an existing successful price
and its evidence; it can still insert a new result or refresh an unsuccessful row.
An unsupported provider/chain combination is saved as missing with an
`unsupported-chain` reason in its evidence. Pricing continues for other chains; adapter
acquisition requires explicit chain configuration.

Saved price records include these details:

- The requested timestamp and the selected end-of-day timestamp.
- The upstream source and confidence value.
- The adapter used to obtain the price.

In report rows, `price_source` is the selected provider and `primary_price_source`
is the requested first choice. Summary rows use `primary_price_source` for the
requested policy; they omit `price_source` because the aggregate can use multiple
providers. `fallback_price_source` records the permitted fallback in both outputs.

An adapter is the code path that obtains or calculates a price.
Use `--retry-missing` to retry targets with unsuccessful saved results.

## Read the earnings results

Earnings include all stored reports, including reports from retired vaults.
Reports remain in scope when vault details are missing.

The outputs retain raw gain, loss, and net amounts.
They also include amounts adjusted for known incidents, including the yRecoverer recovery
accounting loss.

| Result | Meaning |
| --- | --- |
| Known zero amount | No price is required to value that amount as zero. |
| `null` for a nonzero amount | A USD value is unavailable. |
| Known USD subtotal | The sum of values that could be calculated. It can be incomplete. |

Summaries include priced and unpriced report counts.
A report counts as priced only when all its raw and adjusted gain, loss, and net
amounts have USD values. Gains and losses each retain their own pricing availability even
when their net is known zero. A nonzero amount with a price but unknown asset decimals stays
unpriced
and has `valuation_status: missing_decimals`.
A completed analysis is a saved snapshot.
Run the analysis again after you change relevant reports or prices.

## Optional DefiLlama and onchain prices

If you enable DefiLlama, the client selects the nearest valid price within 12 hours of the
report timestamp.
It rejects prices outside that window.

Historical requests use the batch endpoint.
HTTP 429 responses trigger a limited number of retries.
The client follows `Retry-After`, with a maximum wait of 30 seconds.

Local adapters can calculate prices from these sources:

- Historical Yearn V1 share prices.
- ERC-4626 conversions.
- Balancer pools.
- Additional Curve wrappers.

These calculations can require archive RPC calls.
Missing reserve prices or token decimals leave the asset unpriced.
A zero reserve balance has a known zero valuation.

Use `--no-onchain-fallbacks` to disable these calculations when you use DefiLlama.
The Yearn-only commands above use the configured Yearn Prices service exclusively.

### Refresh prices after an adapter correction

Run this command to replace saved adapter results:

```bash
yearn-data --db staging price --source defillama --refresh-onchain-fallbacks --limit 500
```

The flag selects successful saved local-adapter results by their recorded adapter source and
successful acquisition status. It works
with the default Yearn Prices source too: those refresh targets are read from
DefiLlama storage and refreshed through DefiLlama, while normal Yearn Prices
pending/retry selection remains unchanged. Provider alias quotes retain their provider
source identity even when their payload includes fallback metadata. The normal
chain filters and combined target limit apply to refresh and pending targets.
Yearn-only mode cannot use this flag; enable DefiLlama provider fallback first.

The `price-volume` command also accepts `--refresh-onchain-fallbacks`.

## Quote classification and retired estimates

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
unchanged; provider quotes retain their provider-quote classification.

## Evidence and outputs

Earnings, legacy fee and volume rows expose `price_classification`,
`price_accepted`, `price_status` and `price_valuation_policy` in stored outputs
and CSVs. The classifications are:

| Classification | Meaning |
| --- | --- |
| `observed` | Accepted matched provider-observation evidence is present |
| `provider-quote` | A configured provider supplied the quote; the upstream observation/estimation method remains unspecified |
| `derived` | A contract valuation uses conversion/reserve evidence and underlying quotes |
| `estimated` | Retained evidence identifies a retired local parity source; USD valuation is rejected |
| `legacy-unclassified` | A previously accepted quote has unknown classification and retains that status |
| `unavailable` | No usable quote is present |

A retired source anywhere in a derived quote's structured underlying evidence
rejects that quote too. A nonzero amount then retains unavailable USD, while raw
zero still values to zero without a quote. TVL collection applies the same
rejection and records its classification/policy in pricing evidence. Existing selected TVL
snapshots and immutable publications retain their captured values.

Yearn Prices may itself select upstream methods; `provider-quote` preserves that
distinction and the existing upstream-source metadata. Rejection applies to the explicitly
identified retired estimate; other provider and unclassified quotes retain their existing
acceptance rules.

## Existing cache repair

Known retired source labels are explicitly eligible for reacquisition in the
ordinary DefiLlama price/volume commands, even when a legacy row says `ok`:

```bash
yearn-data --db staging price --source defillama --no-provider-fallback --no-onchain-fallbacks --limit 500
yearn-data --db staging price-volume --source defillama --no-onchain-fallbacks --limit 500
```

A successful accepted quote can replace the estimate. If acquisition fails,
the retired estimate becomes audit-only evidence: its old numeric value
and original JSON are retained under the explicitly qualified `retired_estimate`
audit field, while the current quote is unavailable/retryable as appropriate.
Audit JSON retains opaque historical evidence. Subsequent selection and classification use
the current quote’s fields. Existing valid
observed/derived quotes still survive failed refreshes.

New analyses reject known cached estimates even before a cache repair is run,
and can select a valid permitted secondary quote instead. Analysis/export
parameters identify the new policy. Older saved analyses retain their captured
results until explicitly regenerated. Compare affected rows and coverage before
any separately authorized publication; this PR runs no production repair,
backfill, Redis update or publication.

## Implementation boundaries

Provider transport, local valuation and job storage have separate owners:

| Module | Responsibility |
| --- | --- |
| `pricing_clients.py` | Yearn/DefiLlama HTTP, authentication/configuration errors, response validation, normalized request targets and provider evidence |
| `pricing_adapters.py` | Contract valuation policy, RPC calls, adapter caches and public `fetch_underlying_price` quote dependency |
| `pricing.py` | Earnings/volume target selection, batching/exact recovery policy, retry eligibility and conditional price writes |
| `fee_valuation.py` | Yearn-only closed-day requests, fee-specific endpoint/policy cache identity and fee valuation |
| `tvl_sources.py` | TVL-specific source selection, error handling and dated observation storage |

Clients contain no SQL, database access or contract valuation. The fee consumer uses public
client batch utilities. TVL uses
the Yearn client and the existing DefiLlama adapter policy explicitly. Public
fetch imports in `pricing.py` remain call-compatible aliases for existing callers.
To replace acquisition in a test, patch the owning client or adapter module. The scalar
DefiLlama function
is owned by `pricing_clients.py`; the adapter module re-exports that same function.

## Results

`PriceResult` retains the three-item `(value, status, provenance)` tuple interface.
It also exposes `value`, `status`, `reason`, `provider`, `requested_timestamp`,
`evidence_timestamp` and `provenance` by name. A missing USD value is `None`. Evidence
timestamps identify accepted observations and remain
unavailable when no observation exists. Yearn requests use normalized UTC EOD;
earnings rows retain the original event time as `report_timestamp`, while
`requested_timestamp` consistently names the normalized request day-end in both
earnings and fee evidence. Existing stored rows retain their values; older earnings
rows use `normalized_timestamp` for day-end and `requested_timestamp` for event time.

`PriceBatch.target_results` includes every requested target, including unresolved
Yearn targets that require exact verification. The ordinary mapping preserves
the prior sparse Yearn interface, so missing or invalid batch points still go
through exact recovery. An HTTP/batch acquisition exception is distinct from a
successful response with unresolved targets. Authentication and configuration errors stop
acquisition explicitly.

The raw DefiLlama client accepts provider observations only. The separate
[parity policy](pricing.md) retires local fixed-parity aliases;
contract valuations retain explicit `local-adapter` provenance. The earlier
client extraction preserved the prior policy; its follow-up changes acceptance
without mixing that decision into transport or retry budgets.

## Scaling and recovery

Explicit asset decimals of zero mean one raw unit is one token: 7 raw units at
USD 2 is USD 14. Missing decimals remain unknown until supported by asset metadata. A
genuine raw zero remains zero
without a price or scale; a nonzero amount with unavailable scale remains unknown.
See [valuation impact recovery](../operations/recovery.md#inspect-zero-decimal-valuation-impact)
before regenerating affected completed analyses. Preserve raw history and publish changed
calculations through a new explicitly selected output.

The [controlled parity comparison](../archive/measurements/2026-10/parity-price-policy.md)
provides frozen synthetic evidence for its recorded inputs. Assess current production
coverage separately.

[Documentation index](../README.md).
