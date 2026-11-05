# Yearn Data

Yearn Data turns onchain activity and provider evidence into a consistent record
of Yearn vaults, fees, reported earnings and historical capital. It supplies the
Powerglove stats views, a published HTTP API, and research exports for analysis
and reconciliation.

The project is Python tooling backed by Postgres. Collectors gather evidence;
accounting and pricing turn it into financial records; preparation builds the
responses and charts that consumers read. A publication selects matching fees,
TVL and analytics as one release. This page explains that complete lifecycle and
the meaning of its main outputs.

## From source evidence to published data

| Stage | What happens | Result |
| --- | --- | --- |
| Collect | Discover vaults and acquire reports, fee events, transaction evidence, dated balances and ownership relationships. Envio and RPC supply chain evidence; external services supply metadata, prices and comparison inputs. | Stored observations with identities, block bounds and acquisition coverage. |
| Account and price | Calculate supported fees, apply documented incident adjustments to reported earnings, value amounts, and resolve ownership between vaults. | Financial records with explicit evidence and availability states. |
| Prepare | Build earnings and fee analyses, daily TVL history, charts and matching analytics from captured inputs through a chosen cutoff. | Prepared datasets with immutable identities and a common reporting context. |
| Publish | Validate that the datasets belong together, select a coherent release, and consolidate retained history. | One selected release for readers, plus a receipt of the work and its warnings. |
| Serve | Read the selected prepared data through the HTTP API or export completed analyses as CSVs. | Consistent values for Powerglove, other API consumers and researchers. |

Postgres holds source evidence, coverage, prices, accounting results and prepared
serving data. Collectors and publishers use writer credentials. The hosted API
uses separate read-only credentials to serve prepared results. Each published
release identifies its contributing datasets and cutoff, so related requests
can stay on the same revision.

Publication and application deployment are separate steps. Publication updates
which prepared data readers receive; deployment updates the code running the
worker or API. Operators verify each through its own receipt, selected release
and serving host.

## What the numbers mean

| Output | Meaning |
| --- | --- |
| Reported earnings | Gains minus losses recognized in stored reports, with documented incident adjustments. Active and retired vault history remains included within the discovered inventory. Each reporting contract retains its recognition scope. |
| Actual fees | Supported charges established from report, receipt or execution evidence. Distinct charges at strategy and allocator layers each contribute once to a scoped gross-fee total. Components and distributions retain their relationship to the original charge. |
| Treasury revenue | Receipts attributed to a defined treasury or protocol recipient universe. Establishing this metric requires recipient and transfer reconciliation. |
| Gross TVL | The observed asset value at a vault before internal ownership deductions. |
| External TVL | Capital attributed to outside holders after dated, capped deductions for ownership by other counted Yearn vaults. Headline TVL and historical charts use the publication's captured ownership policy. |
| Effective fee rates | Observed configured rates composed across supported allocation paths and weighted by attributable capital. Actual fees depend on execution, caps, refunds and realization timing. |
| Profitability and comparisons | Prepared views relating fees to supported capital history, and comparing Yearn Data's counted scope with independently checked DefiLlama references and membership. Each view carries its methodology and coverage. |

These metrics use aggregation rules suited to their economic meaning. A deposit
can pass through an allocator into a strategy: ownership accounting counts the
capital once, while fee accounting retains each distinct charge. Consolidating
underlying investment yield additionally requires historical ownership and a
policy for recognizing that yield once across reporting layers.

Product views group related contracts under a product identity. For example,
yvUSD locker redistribution, yBOLD staking allocations and yETH recovery
contributions have explicit classifications. Each product designates the
contract supplying its investment earnings and retains genuine fees from its
members. Contract views remain available for inspecting the charging and
reporting layers.

Pricing records its provider, date and evidence policy. Fee USD valuation uses
Yearn Prices; earnings workflows have a configurable provider policy, including
a supported DefiLlama fallback. A paired publication aligns the chosen valuation
policy across its financial views. Historical TVL uses dated observations and
its own captured price evidence.

## How to read availability

Every amount has an evidence boundary. The stored states make that boundary
visible to accounting, preparation and consumers:

| State | How to interpret it |
| --- | --- |
| Genuine zero | Evidence supports an amount of zero. |
| Unavailable | Evidence is insufficient to establish the amount; financial API values remain null. |
| Incomplete | Some expected evidence is missing. Available amounts can contribute to explicitly scoped known subtotals. |
| Retryable | Acquisition can be attempted again under the supported retry policy. |
| Retained | A compatible earlier observation remains in use with its original date and the latest acquisition outcome. |
| Estimated | A value uses an explicitly identified estimation method and its stated assumptions. |

These states apply independently where needed. A report can have known earnings
and unresolved fee evidence; a raw asset amount can be known while its dollar
valuation is unavailable. A publication can pass integrity checks and serve useful
known values while recording source or pricing gaps as warnings. Its receipt
and developer metadata explain those gaps.

## Nightly updates and historical repair

The maintained `refresh` worker coordinates collection, accounting, pricing and
preparation. Its default mode prepares data for inspection. Publish mode also
selects and consolidates a coherent hosted release.

The VM adapter invokes that worker through a systemd timer configured for
**02:00 UTC daily**. It uses the activation day's closed UTC midnight as its
cutoff: a run at 02:00 covers through 23:59:59 UTC on the preceding day. This
schedule stays fixed in UTC across daylight-saving changes.

The repository's nightly configuration covers reports, fees and TVL on Ethereum,
Optimism, Polygon, Fantom, Base, Arbitrum and Katana. It also collects TVL on
Gnosis, HyperEVM and Yeer. Financial preparation includes retained history through
the cutoff. The installed service and its receipts establish actual runtime
state and freshness.

Each cycle continues from a verified prior job, acquiring the next report ranges
and missing daily TVL closes. It refreshes analytics inputs such as external
references, membership and fee configurations, then prepares and publishes the
matching datasets. New fee and earnings amounts appear as source reports and
fee events become available; daily balances and prices provide new TVL closes.

A weekly run uses the same pipeline and can acquire the intervening days from
its accepted baseline. Daily scheduling makes those observations available
sooner and reduces the delay between a source event and its publication. Weekly
chart buckets group the observations into calendar weeks; their display interval
is independent of the acquisition schedule.

Older coverage gaps, historical price repairs and history for late-discovered
vaults require deliberately scoped repair work. Routine updates preserve those
gaps in their evidence and warnings. Repair results enter a new preparation and
publication after review.

## Reliability and recovery

A refresh records its cutoff, scope, inputs and progress in a durable job receipt.
The nightly wrapper also saves pending work and hosted verification locally.
Bounded retries resume compatible work from its checkpoints. After selection,
the wrapper checks the hosted release, matching dataset identities and published
cutoffs before advancing its verified baseline.

Failures before release selection leave the previous release serving. A cleanup
failure after selection leaves the new coherent release selected and reports
unfinished consolidation for recovery. Source gaps retain their availability
states; integrity failures stop the affected publication or verification step.
Operators inspect receipts and service logs to determine the required action.

A separate manual publication requires a nightly handoff: coordinate writers,
finish publication and consolidation, adopt the reviewed native job as the
wrapper's baseline, test the installed service, and verify the next timer
activation. This keeps the live selection and the next scheduled job aligned.

Canonical retention preserves accepted historical identities and source evidence
while retiring superseded derived outputs and releases. Publication IDs identify
revisions. Recovery uses verified retained datasets or a tested backup. A reader
whose pinned revision has retired reloads the current selection and retries its
related views together.

## Development, staging and production

| Environment | Purpose and setup |
| --- | --- |
| Development | Use this checkout's pinned Python environment and an explicitly selected isolated Postgres database. Local work can exercise individual collectors, analyses and serving paths. |
| Tests | Use pure or local boundary tests for suitable behavior, and a disposable Postgres database for persistence, transactions and locking. Database fixtures own isolated schemas. |
| Staging | Use a separate database, role and storage for rehearsing collection, preparation, publication and API behavior. Seed data through a reviewed acquisition or dump/restore. |
| Production | Deploy the worker and API deliberately, keep credentials private, coordinate writers and verify the selected hosted release. The VM scheduler requires its own installation, acceptance test and activation. |

Select the intended logical database explicitly before writing. Most CLI commands
otherwise default to `YEARN_DATA_DB` or `neon`; `refresh` requires an explicit
`--db`. Keep credentials in ignored environment files and use separate read-only
credentials for serving.

## Guides for carrying out a task

The overview above explains the system as a whole. The following guides provide
commands, configuration and detailed policies for specific work.

| Task | Guide |
| --- | --- |
| Set up a local checkout | [Development setup](docs/development/setup.md) |
| Find all maintained guides and specialist tools | [Documentation index](docs/README.md) |
| Prepare/publish a maintained multi-chain refresh | [Refresh operations](docs/operations/refresh.md) |
| Install, operate or recover the 02:00 UTC adapter | [Nightly operations](docs/operations/nightly.md) |
| Finish a separate manual publication safely | [Required nightly handoff](docs/operations/nightly.md#required-handoff-after-a-manual-publication) |
| Collect explicit history and export earnings/fees | [Research exports](docs/operations/research-exports.md) |
| Collect or repair historical TVL | [TVL operations](docs/operations/tvl.md) |
| Deploy/serve already published data | [Hosted API operations](docs/operations/hosted-api.md) and [consumer contract](docs/reference/api.md) |
| Interpret fees, reported earnings and external TVL | [Accounting contract](docs/reference/accounting.md) |
| Run local checks | [Testing policy](docs/development/testing.md) |

The [historical archive](docs/archive/README.md) preserves dated designs,
measurements and rollout evidence. Use current operating guides and live checks
to establish deployment state, selected releases and recovery options.
