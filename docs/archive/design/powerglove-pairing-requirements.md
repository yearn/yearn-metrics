> Historical reference captured from `docs/powerglove-pairing-requirements.md` at `ef03f16`.
> This record captures a past design, measurement or rollout. Use the current
> guides and live checks for ports, IDs, branch state and recovery options.
> Current guidance: [docs/reference/accounting.md](../../reference/accounting.md).
> Original source: [frozen revision](https://github.com/yearn/yearn-metrics/blob/ef03f16fe1c90299ff5342c931563a1e8ffdf26a/docs/powerglove-pairing-requirements.md).

# Powerglove pairing requirements

Draft for implementation · October 5, 2026. Revised to keep data-quality diagnostics in
yearn-data.

This document defines the first usable pairing between yearn-data and Powerglove. The
[accounting contract](../../reference/accounting.md) supplies the underlying
fee-versus-earnings rules. This document specifies the consumer behavior, implementation
boundary and checks needed to deliver that pairing. Implementation and deployment require
separate delivery and authorization.

## Outcome and scope

Powerglove must show fee summaries, monthly history and per-vault amounts published by
yearn-data, including active and retired history. Yearn-data owns data quality: developers
must be able to identify unavailable accounting/prices, understand the reasons and repair
the underlying data. Powerglove users receive the published values through a simple
presentation contract.

Confirmed requirements from this work:

- Build on `ross/pr-08-fantom-replay`, retaining its existing accounting, pricing, cutoff
  and export behavior. The continuation checkout is `ross/powerglove-accounting`.
- Include active and retired vault reports together. Retain historical earnings across
  changes in current status.
- Count distinct accepted fee charges at every included layer once. Establish duplicate
  charges through bound event identity.
- Keep earnings recognition separate from fee charges. Underlying performance can be
  recognized by multiple contracts, including V2-to-V3 nesting.
- Retain missing accounting, missing prices, reasons and cutoff information in yearn-data
  for developer diagnosis and repair. Serve the amounts we have without adding public
  completeness notices throughout Powerglove. Preserve unknown source values as unavailable.

Recommended first-delivery boundary:

- Serve one explicitly selected set of completed earnings and fee results through the three
  core endpoint paths already requested by Powerglove.
- Offer gross fees across the included stack, retaining the family breakdown. Use
  incident-adjusted **reported P&L** for earnings; document its definition and the remaining
  consolidation work in yearn-data.
- Keep TVL, fee-stack and profitability behavior on their existing sources while migrating
  the three core views. The first delivery preserves those analytics through their existing
  sources. Rebuilding them in yearn-data has a separate scope.
- Provide a repeatable manual refresh and local preview. Automatic scheduling and production
  rollout are later steps.

These choices define proposed implementation scope. Consolidated lifetime yield requires the
historical evidence and implementation described below. If the intended first delivery must
instead show consolidated underlying yield, historical attribution becomes required
additional work; see the scope-expansion rule below.

## Verified starting point

| Component | Observed state | Implication |
| --- | --- | --- |
| yearn-data base | `ross/pr-08-fantom-replay@5fcea03f2803d6d4fed761aaea1d03ddb9d16965` | Already includes retired reports and native closed-day earnings cutoffs; retain the already integrated active-filter behavior. |
| Powerglove consumer | `codex/qtov-on-improved-fee-data@04a1a30586d17a3b3da86ccdd30ecf2560a2435a` | Core fee requests already exist; response types and displays need a coordinated adaptation. |
| Canonical data | `data/yearn.sqlite`; completed earnings run 13 and fee-USD run 12 | Both record exclusive cutoff `1791158400`, October 5, 2026 at 00:00 UTC. Use these as starting snapshots and select current retained runs for subsequent work. |
| Earnings snapshot | 96,881 reports; 30,723 retired; 4,541 unpriced | Inclusion is verified, but monetary totals remain known priced subtotals. |
| Fee snapshot | 117,248 stored events; 5,275 unpriced fee values; 181 accounting-unavailable events | Preserve accounting gaps independently from pricing gaps. Accounting-unavailable events retain their individual deferral reasons. |
| Existing outputs | Earnings report/summary CSVs and fee event/day/vault/asset/chain/family CSVs, plus run context | Reuse completed results and existing projection functions. |

These counts were read from the local canonical snapshots while preparing this document.
Historical discovery and deployment require their own coverage and operational verification.
Regenerated earnings and branch-validation evidence are retained under
`artifacts/lifetime-earnings-regenerated-20261005/`.

## Functional requirements

### R1. Select and serve coherent completed results

The serving layer must use an explicit earnings run ID and fee run ID, both complete and
belonging to the expected analyses. Their cutoffs and declared inclusion/pricing policies
must be compatible. Pin HTTP requests to one explicitly verified compatible publication.

Yearn-data must record the selected dataset, earnings/fee run IDs, exclusive UTC cutoff,
generation time, included families, discovery scope and price/methodology evidence. Keep
these details in the publication context and developer-facing records. The public response
needs the requested monetary views and only the identifiers needed for request/cache
consistency; diagnostic counts and reasons remain in developer-facing evidence. Dataset
context and cutoff support internal consistency and developer inspection.

For the initial pairing, select Yearn-only earnings with fallback disabled and the existing
Yearn EOD fee policy. Check the two caches' selected evidence for shared asset/day targets;
resolve price/policy discrepancies in yearn-data before serving the paired values. Preserve
the actual selected evidence for developer review. A shared provider name alone is
insufficient.

HTTP reads must be read-only and bounded to the selected results. HTTP requests serve
prepared data; explicit writer workflows own collection, fee reconstruction, pricing and
migrations. Refreshes must publish a complete selected dataset as one unit; an interrupted
refresh must leave the previous selected dataset usable.

Here, a completed dataset means the selected analysis runs and publication finished. Serve
known amounts with their supported availability state while retaining gaps in developer
evidence.

### R2. Supply the consumer's three core views

| Endpoint | Required behavior |
| --- | --- |
| `GET /api/fees` | Return published fee amounts, incident-adjusted reported earnings and the financial family breakdown for the selected filters. Keep data-quality diagnostics in yearn-data. |
| `GET /api/fees/history` | Return ascending UTC monthly buckets with published fee and earnings values. Preserve Powerglove's completed-month convention. |
| `GET /api/fees/vaults` | Return a count and vault rows keyed by `(chainId, lowercase address)`, with published fee amounts, reported earnings and available name/family metadata. Retain financial history for addresses with missing names. |

Support the filters used by the first delivery: `since`, exclusive `until`, `chainId`, and
`interval=monthly` for history. Use Unix seconds and `[since, until)` semantics. With no
`since`, include all stored history within the selected scope. Cap the effective end at the
dataset cutoff and record the applied range in yearn-data. Retain cutoff and range coverage
in developer-facing metadata.

Define and validate an explicit family scope for fees. Its initial combined view includes V2
vault, V3 allocator and Tokenized Strategy charges and retains their separate amounts. Main
reported earnings use the existing V2/allocator report universe; Tokenized P&L retains its
separate reporting scope. Additional family/vault selectors can be added only where the UI
actually uses them.

Apply time/chain filters before grouping. Day rollups grouped only by family cannot answer
chain-filtered monthly history; use completed event/report rows or a directly derived
equivalent. Invalid request parameters must produce a normal API error. Yearn-data must
distinguish unsupported or unscanned scope from genuine zero activity internally and avoid
manufacturing a successful zero total for a wholly unavailable scope.

The endpoint paths are retained to reuse the current consumer. Implement the routes and
fields consumed by the approved Powerglove views. Update producer and consumer types
together.

### R3. Preserve distinct fee charges and their acceptance

Only accepted accounting contributes to fee amounts. Conditional, rejected, deferred and
unindexed reports remain excluded from accepted fees and recorded in yearn-data diagnostics.
Preserve each retained reason where available; keep those reason codes and counts in
developer-facing evidence.

Deduplicate representations of one charge using the established chain/transaction/log/report
binding. Retain charges with distinct bound event identities, including equal amounts,
assets or timestamps across nested contracts. Preserve raw units, asset identity and
contract family.

Protocol/manager/performance components describe the charge already included in their total.
Recipient distributions describe allocation of an existing charge. Gross charges across the
included stack must be labeled as such; treasury/protocol attribution requires recipient
evidence. Unknown components stay unavailable even when the overall fee is known.

### R4. Preserve reported earnings independently of fee evidence

Include active and retired stored reports. Retain records with missing vault metadata.
Preserve raw gains/losses/net amounts, existing incident adjustments and their provenance.
USD values must follow the selected earnings policy and cutoff.

Retain a report’s supported earnings when its fee accounting is unavailable. Fee and
earnings known/unknown counts are independent developer diagnostics in yearn-data. Use the
full earnings report universe for gains and losses, independently of fee acceptance.

For this delivery, describe the main earnings series as incident-adjusted reported P&L. Its
net amount is adjusted reported gains minus losses. Net earnings across the full fee stack
require a separate consolidation policy. Label its aggregate as incident-adjusted reported
P&L. Keep contract/family identity available so later historical attribution can be added
without changing raw records.

Make the old consumer’s separate `tokenizedStrategyYield` field optional or unavailable when
unsupported; retain its separate economic scope. Keep its definition separate in the
backend. Handle a missing optional projection with ordinary unavailable-value behavior.

### R5. Keep data-quality diagnostics and repair work in yearn-data

Aggregate accepted available amounts with decimal arithmetic. Preserve unknown source values
as null internally, with prices, fee evidence and amounts tied to supported observations. A
known aggregate can be published as the amount currently available even when some source
events remain unresolved. This first pairing serves available values with internal
completeness metadata and ordinary public unavailable-value behavior.

Yearn-data must retain independent accounting, metadata, pricing and historical-coverage
diagnostics, including reasons, affected event/asset/day identities, counts, cutoff and
selected evidence. Developers need enough information to investigate and repair each
category. Reuse existing CSVs, run context and diagnostic artifacts for pairing diagnostics.

Keep diagnostics reconcilable to the selected publication and its filters. A price repair
must lead to a reproducible revaluation and new publication. Accounting-evidence repair
remains a separate operation. Existing missing-price targets and unresolved-fee records
remain developer work items even while known amounts are served.

Powerglove renders the published financial values; developer evidence retains reason codes,
coverage counts, confidence, discovery flags and cutoffs. A wholly unavailable aggregate may
remain null; use the usual missing-value treatment.

### R6. Display published values with normal request handling

Powerglove must adapt its response types/validation to the simpler display contract: decimal
monetary values, ordinary null handling, financial scope and the identifiers necessary for
stable requests. Render the values yearn-data publishes in the existing cards, tables and
charts.

Keep detailed coverage, missing-event, price-source and cutoff diagnostics in
developer-facing evidence. Remove the old Fees panel's developer-oriented canonical-coverage
presentation from the pairing contract. Keep diagnostic reasons in yearn-data.

Cumulative charts may sum the published known amounts. Cumulative series continue with
available accepted events; source gaps remain in developer-facing evidence. Preserve monthly
sorting, selected-timeframe accumulation and the completed-month convention. A wholly absent
monthly amount can use existing missing-value behavior; retain unavailable event amounts and
prices.

Retain normal loading, empty, HTTP error/retry and filter-change behavior. These request
states are different from diagnostics about individual missing source events. Apply
responses and cached data only to their matching selected filter and publication. Preserve
existing product refresh indicators; retain operational cutoffs in developer metadata.

### R7. Preserve the separate TVL connection

Use a separate core origin through `VITE_PUBLIC_YEARN_DATA_API_URL` in production and
`VITE_YEARN_DATA_API_TARGET` for the local proxy. Preserve the existing fee-origin variables
for fee-stack/profitability; core views can fall back to that existing source when the new
variables are unset. This split avoids moving existing analytics when the core source
changes. Verify the client-appended `/api/fees` paths in both modes. Configure the required
same-origin proxy or CORS without exposing Yearn Prices/RPC credentials to the browser.

TVL requests and their provider must continue working independently. An unused required
`tvlUsd` field in the old fee-row type must become optional and preserve unavailable status.
Preserve the existing fee-stack/profitability features on their current source while the
core views move to yearn-data. These requests currently share the fee API origin, so inspect
and separate their routing as needed; route each request to a service supporting its
contract. Verify the existing source before cutover. If it cannot be retained, record a
separate product/source decision before changing those features; preserve those features
until that separate decision is approved.

### R8. Make refresh and review reproducible

Document the selected source database, price policy, cutoff, completed run IDs and exact
commands used to regenerate and export the paired dataset. Use the existing native
`--before-timestamp`, `--no-provider-fallback` and export `--run-id` support. New runs
create immutable analysis snapshots and preserve raw events and accepted accounting.

Reconcile served views against the pinned outputs, retain the dataset context in developer
publication records, and provide a Powerglove preview using the new source. Establish
consumer correctness through response-value checks and actual browser interactions.

## Behavior to preserve

- Existing native fee arithmetic, acceptance decisions, reconstruction evidence, incident
  rules and price provenance.
- Active/retired inclusion and the requested base's cutoff/export functionality; the
  regression tests already added protect these.
- Existing explicit provider options for other workflows. Yearn-only applies to this
  pairing; the repository’s global fallback default retains its separate policy.
- Existing CSVs, completed analysis runs and raw tables. Consumer JSON is an additional
  projection.
- Powerglove's time/chain filtering, monthly chart convention and straightforward numeric
  presentation. Existing developer-quality panels may be removed from the public Fees tab as
  part of this contract adaptation.
- The current TVL, fee-analysis and profitability behavior and their existing data sources,
  plus unrelated local work.

## Existing fee-stack and profitability features

These existing Powerglove features form a separate integration scope from the three
historical fee/earnings views:

| Request | Visible feature | Additional data needed |
| --- | --- | --- |
| `GET /api/fees/stack` | **Fee Analysis**: nested vault/strategy tree, effective fee rates, fee capture and trend information. | Current allocation relationships, debt/capital values and fee configuration. Captured fees and trends also join the core/profitability responses in the UI. |
| `GET /api/profitability` | **TVL vs Fee Yield** scatter plot, classifications and profitability trends. | Fee history and suitable TVL/period inputs for yield and trend calculations. |

These endpoints require a serving projection over yearn-data’s existing historical exports.
The core migration should preserve their current source and separate routing where needed.
Preserve their existing behavior and plan reimplementation as a later scope.

## Expected change boundary

| Component | Necessary work | Boundary |
| --- | --- | --- |
| yearn-data projections/serving | Select completed paired runs; derive the three JSON views and filters; retain quality/provenance context internally. | Reuse existing results/functions. A thin read-only adapter is the recommended implementation; no duplicate fee engine or new ingestion pipeline. |
| yearn-data publication/operator | Explicit dataset selection, coherence checks, repeatable manual refresh, complete-dataset activation. | No scheduler, automatic broad backfill or new operational control plane. Serving framework and hosting provider remain implementation choices. |
| Powerglove fee types/helpers | Adapt to the display contract and calculate charts from published amounts. | Keep accounting and historical overlap calculations in yearn-data; React consumes the published results. |
| Powerglove Fees panel/routing | Connect core views; display published amounts; retain normal request handling and existing analytics routes. Remove public developer-quality indicators if present. | No missing-data badges, coverage counters or mandatory cutoff displays; keep existing analytics and TVL behavior. |
| Validation/handoff | Focused accounting/contract/UI checks and actual-source preview with exact source/run provenance. | Use retained evidence and controlled prices for offline acceptance fixtures. |

Recommendations guide implementation choices; validate the chosen approach against the
required behavior. In particular, a new database schema, JSON export format, web framework,
schema version bump or separate service deployment needs a concrete reason; none is required
merely to resemble the reference service.

## Acceptance checks

| Check | Observable result | Requirements |
| --- | --- | --- |
| A1. Dataset selection | Incomplete/wrong-analysis runs and incompatible cutoffs/policies are rejected. Developer publication records identify the runs used by all three views; an interrupted refresh retains the previous selection. | R1, R8 |
| A2. Filter reconciliation | Summary, sorted monthly buckets and per-vault rows match the same pinned event/report cohort. Inclusive start, exclusive end, current UTC day, chain isolation and 30D/90D/1Y/all-time behavior are exercised. History and headline ranges retain their documented differences. | R2 |
| A3. Fee identity | A nested strategy charge of $10 and allocator charge of $9 produce $19 gross charges in that defined scope, with a family breakdown. Count each bound charge once across paired evidence, components and distributions; equal distinct charges remain distinct. | R3 |
| A4. Earnings independence | Active and retired V2/V3 reports appear together; retired incident adjustments apply. A priced report with unresolved fee evidence contributes earnings while its fee remains unavailable. | R4 |
| A5. Layered recognition | The $100 strategy gain and $90 allocator recognition retain identifiable contract-level reported values totaling $190 of report recognition. Tokenized P&L retains its separate reporting scope and availability state. | R4 |
| A6. Developer diagnostics | Yearn-data retains missing accounting, metadata, prices, reasons, affected counts and repair targets. These remain distinct from proven zero and independently known earnings. Its published arithmetic uses available accepted values; unavailable source amounts retain their availability state. | R5 |
| A7. Simple rendering | Published summary/month/vault amounts render normally despite internally recorded gaps. No public completeness badges, gap counters, source-failure explanations, cutoff notices or partial-state propagation are required. A wholly absent display value uses ordinary missing-value behavior. | R5, R6 |
| A8. Requests and errors | Initial loading, ordinary HTTP failure/retry, empty results and filter changes render correctly. Older responses/caches cannot overwrite or mislabel the selected scope. Source-quality reasons stay in yearn-data. | R6 |
| A9. Existing analytics | Core views use yearn-data while fee-stack/profitability and TVL requests continue reaching their intended existing providers. The origin change preserves those features and routes them to supported endpoints. | R7 |
| A10. Actual pairing | Build Powerglove and inspect the published cards/charts/vault rows against the new adapter, including datasets with internally known gaps. Verify local proxy paths, production routing configuration, preserved analytics, source revision and developer run context. | R7, R8 |

Reuse existing accounting, retired-history, cutoff and export tests. Add only tests needed
to protect the new selection/filter/contract/rendering behavior. Run the applicable
repository checks when implementation changes land. Validate these acceptance checks during
implementation delivery.

## Non-goals and scope expansion

The first delivery excludes complete historical discovery, broad event/price backfill,
recovery of every accounting deferral, upstream yearn-prices fixes, global fallback-policy
changes, treasury attribution, historical yield consolidation, TVL migration,
reimplementation of fee-stack/profitability calculations, new chart families, a general
strategy catalog, automated scheduling and production deployment. Developer diagnostics
remain in yearn-data; building or extending a public data-quality dashboard is also
excluded.

Historical yield consolidation requires a separate economic policy, historical evidence and
implementation. The inspected TVL reference preserves parent/strategy/child identity and
historical ownership, including V2-to-V3 routers. Those patterns inform dated earnings
attribution. Today's allocation graph or the TVL stock-subtraction formula cannot establish
period-specific earnings overlap.

If consolidated yield becomes necessary for the requested headline, explain the overlapping
reports and supported history, define gross underlying versus vault-recognized versus
depositor-net earnings, and agree the smallest attribution scope before adding historical
relationship acquisition/reconstruction. Until then, retain the reported-P&L definition and
consolidation limitation in yearn-data. Powerglove uses ordinary unavailable-value behavior;
the backend labels its economic scope according to the verified methodology.

Optional improvements remain excluded unless tied to an observable requirement or a
demonstrated regression. If native outputs cannot support a required filter or completeness
field, identify the concrete gap and extend that projection only; keep the extension bounded
to that projection.

## Implementation sequence and completion

1. Finalize the coordinated JSON shape and selected-dataset context for the three core
   views. Record inclusion and pricing policies, and reconcile shared price evidence.
2. Implement the read-only adapter and manual publication using completed results. Validate
   filtering, fee identity and earnings independence, retaining source-quality diagnostics
   in yearn-data.
3. Adapt the Powerglove fee types, helpers and panel to published values. Retain TVL and
   existing fee-stack/profitability source routing; remove public developer-quality displays
   from the pairing contract.
4. Reconcile representative output against the canonical snapshots and review the actual
   paired preview. Document source revisions, run IDs, policy, cutoff, limitations and
   refresh commands.

The first pairing is complete when A1–A10 pass for the agreed scope and the consumer preview
demonstrates the new source with existing analytics preserved. Completion requires the
paired data, serving contract and consumer behavior to pass the acceptance checks together.
Missing historical prices/evidence may remain internally tracked while available amounts are
served. Developer records must describe the actual scope and retain a concrete route to
investigating and repairing the gaps.

## Source references

- Implementation and manual refresh: [Powerglove pairing operator
  guide](../../reference/api.md).
- yearn-data: `src/yearn_data/analysis.py` (`run_lifetime_yield`, cutoff and incident
  handling); `fee_valuation.py` (accepted fee rows, Yearn EOD policy, family/completeness
  outputs); `fees.py` (charge binding/acceptance); `exports.py` (completed-run selection and
  context).
- Maintained operators: [earnings and fees](../../operations/research-exports.md), [earnings
  pricing](../../reference/pricing.md), [fee accounting](../../reference/fee-accounting.md).
- Powerglove at the verified commit: `src/components/landing/native-stats/FeesPanel.tsx`,
  `canonical-fees.ts`, `fee-history.ts`, `hooks.tsx`, and `vite.config.ts` in
  `/home/dev/raaaws/worktrees/yearn-powerglove-qtov`.
- TVL reference and its verification limits: [accounting
  contract](../rollouts/powerglove-accounting-contract.md#what-to-reuse-from-yearn-tvl-service).
  Reference source identifies local code. Verify the deployed revision and historical
  relationship coverage separately.
