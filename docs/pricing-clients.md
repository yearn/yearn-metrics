# Pricing boundaries

Provider transport, local valuation and job storage have separate owners:

| Module | Responsibility |
| --- | --- |
| `pricing_clients.py` | Yearn/DefiLlama HTTP, authentication/configuration errors, response validation, normalized request targets and provider evidence |
| `pricing_adapters.py` | Contract valuation policy, RPC calls, adapter caches and public `fetch_underlying_price` quote dependency |
| `pricing.py` | Earnings/volume target selection, batching/exact recovery policy, retry eligibility and conditional price writes |
| `fee_valuation.py` | Yearn-only closed-day requests, fee-specific endpoint/policy cache identity and fee valuation |
| `tvl_sources.py` | TVL-specific source selection, error handling and dated observation storage |

Clients contain no SQL, database access or contract valuation. The fee consumer
uses public client batch utilities rather than private earnings helpers. TVL uses
the Yearn client and the existing DefiLlama adapter policy explicitly. Public
fetch imports in `pricing.py` remain available for existing callers.

## Results

`PriceResult` retains the three-item `(value, status, provenance)` tuple interface.
It also exposes `value`, `status`, `reason`, `provider`, `requested_timestamp`,
`evidence_timestamp` and `provenance` by name. A missing USD value is `None`, never
a guessed zero. Evidence timestamps identify accepted observations and remain
unavailable when no observation exists. Yearn requests use normalized UTC EOD;
the jobs retain original report timestamps separately.

`PriceBatch.target_results` includes every requested target, including unresolved
Yearn targets that require exact verification. The ordinary mapping preserves
the prior sparse Yearn interface, so missing or invalid batch points still go
through exact recovery. An HTTP/batch acquisition exception is distinct from a
successful response with unresolved targets. Authentication and configuration
errors remain fatal rather than becoming unavailable evidence.

The raw DefiLlama client accepts provider observations only. The separate
[parity policy](parity-price-policy.md) retires local fixed-parity aliases;
contract valuations retain explicit `local-adapter` provenance. The earlier
client extraction preserved the prior policy; its follow-up changes acceptance
without mixing that decision into transport or retry budgets.

## Preserved behavior

HTTP attempts/backoff, batch sizes, exact fallback fan-out, fee outage handling,
pending-day retries, source defaults and existing formulas are unchanged. A
failed acquisition cannot replace accepted stored quotes; explicit rejected
evidence retains its existing handling. Fee cache policy and endpoint isolation
remain in the fee consumer, without schema consolidation. Adapter caches retain
their prior boundaries. No backfill, data publication or deployment is performed
by extracting these modules.
