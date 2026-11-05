# Documentation

Current guides describe supported code and reusable procedures. Check the actual
service checkout, environment, receipt and selected publication before operating
an installed deployment. Static documentation is not a live deployment inventory.

## Start and operate

| Task | Authoritative guide |
| --- | --- |
| Set up an isolated local environment | [Development setup](development/setup.md) |
| Configure Postgres, TLS, roles or staging | [Postgres operations](operations/postgres.md) |
| Routine multi-chain acquisition and explicit publication | [Native refresh](operations/refresh.md) |
| Schedule/install/recover the nightly wrapper | [Nightly operations](operations/nightly.md) |
| Complete a manual-publication handoff | [Baseline adoption, installed-service test and timer verification](operations/nightly.md#required-handoff-after-a-manual-publication) |
| Diagnose native, analysis or TVL jobs | [Recovery](operations/recovery.md) |
| Import bounded reports or replay discovery/metadata | [Report ingestion](operations/report-ingestion.md) |
| Run deliberate research and CSV exports | [Research exports](operations/research-exports.md) |
| Discover/collect/export/backfill TVL | [TVL operations](operations/tvl.md) |
| Review independent TVL/adapter differences | [TVL comparison](operations/tvl-comparison.md) |
| Publish/serve a hosted or local read-only API | [Hosted API operations](operations/hosted-api.md) |

## Understand data and contracts

| Topic | Authoritative guide |
| --- | --- |
| Reported P&L, distinct fees, treasury and capital boundaries | [Accounting](reference/accounting.md) |
| Allocator/Tokenized fee evidence and offline replay | [Fee accounting](reference/fee-accounting.md) |
| Provider/EOD/fallback policies, scale, provenance and cache repair | [Pricing](reference/pricing.md) |
| Dated unified ownership, caps and unavailable external TVL | [TVL accounting](reference/tvl-accounting.md) |
| Immutable history, invalidation and explicit rebuilds | [History preparation](reference/tvl-history-preparation.md) |
| Curation discovery, scope and producer attestations | [Curation indexing](reference/curation-indexing.md) |
| Membership, effective rates and profitability methodology | [Analytics](reference/analytics.md) |
| Response/filter/pin/time-window contracts | [API](reference/api.md) |
| Canonical retention, cleanup and obsolete IDs | [Retention](reference/retention.md) |
| Optional estimated ticking headline | [Redis headline](reference/redis-headline.md) |

## Product accounting

[Actual yvUSD fees and locker redistribution](accounting/yvusd.md),
[yBOLD allocator/staking fees](accounting/ybold.md), and
[yETH creditor recovery](accounting/yeth.md) define product membership and
one designated earnings leg. [Effective configured rates](accounting/effective-fee-products.md)
are a separate policy from actual paid fees.

## Develop and investigate

- [Testing policy](development/testing.md): pure/HTTP/Postgres boundaries and explicit fixtures.
- [Performance recipes](development/performance.md): supported tools, parity and measurement limits.
- [Optional Fantom replay](development/fantom-replay.md): saved prestate and strict execution evidence.
- [Fee fixture provenance](../tests/fixtures/fees/README.md) and [analytics fixture provenance](../tests/fixtures/analytics/README.md).
- [Agent/documentation policy](AGENTS.md) and [root agent instructions](../AGENTS.md).

Use `yearn-data --help` and each subcommand's help for exact syntax. Routine work
uses `refresh` or the nightly adapter. `scripts/update_postgres.py` is a specialized
bounded manual publisher; `scripts/backfill_tvl.py` is deliberate historical work.
Comparison/review scripts and benchmark/browser tools are opt-in diagnostics,
not alternate scheduled refresh paths. Their owning guides above describe input
contracts, write effects and evidence limits.

## Historical reference

[Archive index](archive/README.md) contains original requirements, branch history,
dated deployments/corrections and measurements. [Migration map](migration.md)
accounts for the previous paths and split sections. Archives must not be used as
current environment, service-port, release-retention or branch-state instructions.
