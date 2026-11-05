# Yearn Data

Python tooling for Yearn report/fee ingestion, historical TVL, accounting,
research exports and a read-only published API. Postgres is the supported store.
The maintained refresh worker acquires data and prepares matching fee, TVL and
analytics datasets; publication is an explicit writer operation.

Start with [local development setup](docs/development/setup.md). It uses the
pinned interpreter/lockfile, verifies this checkout's imports and selects a
separate local `staging` database before writes. Most CLI commands otherwise
default to the configured `YEARN_DATA_DB` or `neon`; `refresh` requires an explicit
logical database. Keep credentials in ignored environment files.

## Choose a task

| Task | Guide |
| --- | --- |
| Find all maintained guides and specialist tools | [Documentation index](docs/README.md) |
| Prepare/publish a maintained multi-chain refresh | [Refresh operations](docs/operations/refresh.md) |
| Install, operate or recover the 02:00 UTC adapter | [Nightly operations](docs/operations/nightly.md) |
| Finish a separate manual publication safely | [Required nightly handoff](docs/operations/nightly.md#required-handoff-after-a-manual-publication) |
| Collect explicit history and export earnings/fees | [Research exports](docs/operations/research-exports.md) |
| Collect or repair historical TVL | [TVL operations](docs/operations/tvl.md) |
| Deploy/serve already published data | [Hosted API operations](docs/operations/hosted-api.md) and [consumer contract](docs/reference/api.md) |
| Interpret fees, reported earnings and external TVL | [Accounting contract](docs/reference/accounting.md) |
| Run local checks | [Testing policy](docs/development/testing.md) |

Readers must preserve genuine zero, unavailable, incomplete, retryable, retained
and estimated evidence as distinct states. Reported P&L is not consolidated
underlying yield; fees charged at multiple layers are not automatically treasury
revenue. The owning guides define pricing, coverage and product scope.

Old plans, rollout IDs and measurements are in the [historical archive](docs/archive/README.md).
They are evidence for their captured revisions, not operating instructions or
rollback guarantees. Merging code does not deploy it; an enabled timer does not
prove a completed publication.
