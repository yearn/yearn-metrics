# Price historical earnings

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
yearn-data --db data/yearn.sqlite price --source yearn-prices \
  --no-provider-fallback --no-onchain-fallbacks --limit 500
yearn-data --db data/yearn.sqlite analyze lifetime-yield \
  --price-source yearn-prices --no-provider-fallback
yearn-data --db data/yearn.sqlite export lifetime-yield --out exports/earnings
```

`--no-provider-fallback` prevents the use of DefiLlama prices.
This also prevents analysis from using an existing DefiLlama price in the database.
`--no-onchain-fallbacks` disables local price calculations that use onchain data.
The local onchain adapters also depend on DefiLlama quotes. When acquiring Yearn
Prices without provider fallback, both flags are required; enabling the local
onchain adapters in that mode is rejected before any price requests.
The combined `run lifetime-yield` command checks this policy before discovery or
event indexing starts.

These commands do not fetch onchain events again.
Volume and legacy fee analyses keep their existing DefiLlama policy.

## Understand price requests

The requested price time is 23:59:59 UTC on the report day.
The client checks the asset, timestamp, and positive price value.

If a successful batch response omits a target, the client makes an exact historical request for that target.
Network failures remain eligible for retry.
An authentication failure stops the run.
Malformed or duplicate batch points are omitted and checked through exact
requests, while valid points in the same batch remain usable. A malformed exact
response is saved as `invalid`, remains eligible for
`--retry-missing`, and can use the configured provider fallback.

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
They also include amounts adjusted for known incidents, including the yRecoverer recovery accounting loss.

| Result | Meaning |
| --- | --- |
| Known zero amount | No price is required to value that amount as zero. |
| `null` for a nonzero amount | A USD value is unavailable. |
| Known USD subtotal | The sum of values that could be calculated. It can be incomplete. |

Summaries include priced and unpriced report counts.
A report counts as priced only when all its raw and adjusted gain, loss, and net
amounts have USD values. A known zero net does not make unknown gains or losses
priced. A nonzero amount with a price but unknown asset decimals stays unpriced
and has `valuation_status: missing_decimals`.
A completed analysis is a saved snapshot.
Run the analysis again after you change relevant reports or prices.
