# Operate the read-only hosted API

The hosted API serves a coherent selected release from Postgres: fees, TVL history
and matching analytics. Requests never acquire data, fetch prices or publish.
The database does not host HTTP. Use an explicitly selected logical database and
separate read-only serving credentials; collectors/publishers need writer access.
See [API contracts](../reference/api.md) for consumer parameters and values.

## Routine publication

Use the [native refresh worker](refresh.md) for maintained acquisition/preparation
and explicit publication. The [nightly adapter](nightly.md) preserves its scope,
cutoff and pending job across retries. Historical repair and research exports are
separate deliberate operations. Never infer success from an enabled timer alone.

## Manual publication from prepared inputs

The following writer operation is for a separately reviewed manual publication.
It is not read-only startup. Coordinate writers and finish the nightly baseline
handoff afterwards on installations with a timer.

### Publication flow

The explicit writer command validates the selected financial analysis, requires
prepared TVL rows/totals, and checks that analytics reference those same two datasets.
It stores financial and TVL manifests in `api_manifests`, a coherent set of IDs in
`api_releases`, and atomically updates `api_selection`. It then performs verified
[canonical-history consolidation](../reference/retention.md): current historical
coverage remains complete, while superseded outputs and publications are retired.
Conflicting content for an existing identity is rejected.

```bash
PYTHONPATH=src python -m yearn_data.cli --db staging publish-hosted \
  --publication data/staging/fees --tvl-publication data/staging/tvl \
  --analytics-publication-id <prepared-analytics-id>
```

Use the database that actually owns those publications. Source manifests must
reference that same logical database. Existing analytics preparation is a separate
step. The writer uses write-capable credentials; the deployed API should receive
separate read-only credentials. API code only opens read-only connections and never
creates schema, collects data, refreshes prices, or publishes a new selection.

Every request reads the small selected release record. Object caches are keyed by
immutable dataset IDs; selecting another release does not require a restart.
`GET /api/publication` returns the selected release and fee/TVL/analytics IDs.
Existing Powerglove discovery routes and URLs pinned to the current dataset work.
A superseded pin returns HTTP 410; reload the selection and retry related views.

## Local entry point

Set `YEARN_DATA_DB` to the logical database name, and supply the matching database
URL in the process environment. The HTTP module does not implicitly load `.env`.

```bash
YEARN_DATA_DB=staging PYTHONPATH=src python -m yearn_data.hosted_api --port 3494
```

`api/index.py` exports the same handler for Vercel. This uses Vercel's supported
file-based `BaseHTTPRequestHandler` interface and reuses the existing HTTP style
without adding a framework dependency. `vercel.json` routes `/api/*` to that handler.
The bundle exclusions retain runtime inventories but exclude local databases,
environment files, generated artifacts and development files.

Reference: https://vercel.com/docs/functions/runtimes/python/api-directory

For local staging, supply `YEARN_DATA_DB=staging` and the exported
`STAGING_DB_URL` before starting the module; it does not implicitly load dotenv.
It needs an already prepared staging publication, not an empty setup database.
For deployment, configure `YEARN_DATA_DB` and the matching URL in the server's
private environment. Never put them in `VITE_*` variables or committed manifests.

## Deploy and verify

Review the actual target project, deployment environment, source revision and
read-only role before changing them. Do not copy historical personal-preview
commands or IDs as current defaults. Build/package validation must check API-only
routing, inventory inclusion and exclusion of credentials/data/artifacts. Verify
function region, runtime, memory/duration, compressed payload limits and representative
cold/warm consumer requests in that deployment; local timings do not prove hosted
performance. The current routing/build configuration is in `vercel.json`.

After a separately authorized deployment, verify `/api/publication`, pinned
fees/TVL/analytics and the Powerglove proxy's matching identities and cutoffs.
Check the deployment's actual authentication and cache behavior. Old 503/SSO
observations in the [implementation record](../archive/rollouts/hosted-api.md)
are historical, not assertions about the current alias.

Configure Powerglove's `VITE_PUBLIC_YEARN_DATA_API_URL` for its browser-accessible
API origin and `VITE_YEARN_DATA_API_TARGET` for the local server-side proxy target.
Active owned stats routes use Yearn Data; legacy fallback variables do not
establish that an old service is required. Rebuild the frontend after changing
public build variables and verify the actual consumer requests.

## Caching

Successful unpinned responses receive a 30-second Vercel CDN TTL. Explicitly pinned
responses receive a one-hour TTL when the pin determines the response body.
For `/api/comparison`, `publicationId` pins the analytics body; `datasetId` alone
only checks TVL compatibility and retains the 30-second TTL because analytics can
be republished against the same TVL dataset. The cache key must retain the full query string,
including the publication identity and all filters. Browser responses revalidate;
failed requests use `no-store`. CORS allows public read access without credentials.
A publication change can take up to the unpinned TTL to become visible through the CDN.

Reference: https://vercel.com/docs/caching/cache-control-headers

## Recovery and reader permissions

Failures before hosted selection leave the previous hosted release active;
completed intermediate preparations may remain stored for reuse on retry. If
consolidation fails **after selection**, the new coherent release was already
published. The CLI exits with status 1 and prints JSON containing its `releaseId`,
`published: true`, `consolidated: false`, and a recovery message. A consolidation
transaction failure rolls back cleanup, not the earlier release selection. Inspect
`/api/publication` before recovery, resolve the cleanup failure and retry
consolidation, or select a still-retained coherent release:

```bash
PYTHONPATH=src .venv/bin/python -m yearn_data.cli --db neon select-hosted-release \
  --release-id <still-retained-release-id>
```

This command checks that referenced prepared datasets and matching analytics exist
before changing hosted selection; it cannot restore a release retired by successful
consolidation and does not independently change the legacy analytics selection.
API processes read selection on every request; no restart is needed. CDN TTLs
still apply after deployment. Verify the selected release after recovery.

Use separate read-only credentials for the API. A dedicated reader role (for example, `powerglove_api_reader`)
needs SELECT on serving tables only: `api_manifests`, `api_releases`,
`api_selection`, `api_tvl_dates`, `api_fee_rows`, `api_fee_datasets`, `analytics_publications`,
`tvl_snapshots`, `tvl_positions`, `tvl_chart_rows`, `tvl_chart_totals`,
`tvl_chart_publications`. It does not receive access to raw analysis outputs or
write privileges. Keep credentials in private environment files, never manifests
or committed documentation. Additional serving tables require explicit grants.
An existing nightly installation must finish the
[manual-publication handoff](nightly.md#required-handoff-after-a-manual-publication)
after any separate selection. A publication with no matching native receipt
requires a supervised native baseline run. Never fabricate a receipt, erase
pending state or automatically trust an arbitrary selected ID.

## Supported local file-backed server

`serve-pairing` reads completed publications referenced by local manifest files.
It is distinct from the fileless hosted registry reader. To serve already
prepared staging publications:

```bash
yearn-data --db staging serve-pairing \
  --publication data/staging/fees --tvl-publication data/staging/tvl \
  --current-bridge-policy retired-registry --analytics \
  --host 127.0.0.1 --port 3490
```

Prepare the matching analytics first. `--analytics` rejects bridge policy `none`.
File-backed TVL may refresh its local publication pointer when finished source
observations change; this does not acquire data and is not the hosted release
selection mechanism. Hosted requests only read explicitly selected database
releases. Both modes are read-only at request-data boundaries, but file-backed
startup/publication preparation can write prepared artifacts: use the documented
mode deliberately. See the API reference for pin retirement differences.

[Documentation index](../README.md).
