# Recover jobs and publications

Recovery starts with the saved receipt, selected release, exact code/configuration
and the identity of every possible writer. Preserve logs and retained evidence.
Preserve pending state and satisfy each integrity check before continuing.

## Native refresh and publication

Use [refresh recovery](refresh.md#publication-and-recovery) for `work-deferred`,
failed stages, changed checkpoints and `selected-cleanup-failed`. Selection and
canonical cleanup are separate commits: the newly selected release remains active after a
cleanup error. Inspect `/api/publication` before acting.
Recovery requires retained datasets or a restorable backup; see
[retention](../reference/retention.md).

After a manual publication, complete the [nightly baseline
handoff](nightly.md#required-handoff-after-a-manual-publication).
Adoption requires a selected, consolidated native receipt covering the configured
scope and matching both the live database and hosted API. A publication without
that receipt needs a supervised native run. Pending wrapper work must be
recovered first; `--adopt-baseline` requires idle state.

## Legacy analysis and TVL run markers

`lifetime-yield`, `vault-fees`, `vault-volume`, `fee-usd` and `canonical-fees`
share one lifecycle and commit a `running` marker before
calculating outputs. A handled Python exception or interruption now rolls back
the partial output transaction, commits a `failed` marker with `completed_at`,
and re-raises the error. A successful job commits its outputs and completed
marker together. Retry creates a new run; only successful output is eligible for publication.

This applies to the supported Postgres runtime. Use the current Postgres lifecycle described
here to address the recovery requirement recorded in issue #48.

## Process crashes and unavailable database connections

SIGKILL, host failure, or a lost database connection can prevent terminal-state
cleanup. A surviving `running` marker requires process, host and receipt checks to establish
liveness. Establish abandonment from execution evidence and preserve canonical-history’s
active-writer guard. Consolidation checks both `analysis_runs` and `tvl_runs`;
resolve each table’s marker independently. TVL collection uses the separate lifecycle below.

1. Inspect running markers in both tables. For `analysis_runs`, record the explicit
   ID, job name, start time and parameters. For `tvl_runs`, record the explicit
   ID, start time, chain/date scope and parameters. Match each to its operator
   receipt, process/service logs and execution host; verify each table’s job identity
   independently.
2. Check that the process and any resuming worker have stopped, including other
   hosts. Inspect `pg_stat_activity` and locks with an appropriately privileged
   operator connection. Confirm application-worker termination on its execution host as well
   as database inactivity.
3. Stop or fence all possible owners before recovery. If ownership cannot be
   established, leave the marker untouched and investigate.
4. With ownership ruled out, mark only the confirmed abandoned run failed in a
   transaction. The conditional update below must return exactly one row. Select only the
   IDs whose abandonment has been verified.

```sql
BEGIN;
UPDATE analysis_runs
SET status = 'failed', completed_at = EXTRACT(EPOCH FROM NOW())::bigint
WHERE id = <confirmed_abandoned_run_id> AND status = 'running'
RETURNING id, name, status, completed_at;
COMMIT;
```

For a confirmed abandoned TVL collection, use the same ownership/fencing checks
and conditional transaction, but target only its explicit `tvl_runs` ID:

```sql
BEGIN;
UPDATE tvl_runs
SET status = 'failed', completed_at = EXTRACT(EPOCH FROM NOW())::bigint
WHERE id = <confirmed_abandoned_tvl_run_id> AND status = 'running'
RETURNING id, status, completed_at, params_json;
COMMIT;
```

Require exactly one returned row. Run only the example appropriate to the verified lifecycle
and preserve the active-writer guard. Archive the TVL run record and its retained
`tvl_snapshots` / `tvl_positions` before retrying collection for its intended
scope. TVL publication inputs require successful collection runs. Recheck both tables
before a separately authorized consolidation; a remaining live or unexplained
marker must continue to block it.

Before retrying or publishing, export the failed run's `analysis_runs` record,
any retained `analysis_outputs`, and relevant process logs to the incident's
access-controlled diagnostic archive. Record that archive location, the recovered
run ID and supporting evidence in the operational receipt. Publication runs
canonical consolidation, which removes outputs outside its retained run set;
export partial outputs to retained evidence before consolidation.

Readers must select complete runs. Retry the original analysis with the same
intended cutoff and source policy, inspect its completed output, and only then
perform any separately authorized publication. This code change performs no
production recovery or publication automatically.

## Inspect zero-decimal valuation impact

Explicit asset decimals of zero mean one raw unit is one token. For example,
raw amount 7 at USD 2 must value at USD 14. The shared analysis amount helper
previously substituted 18 for zero. Missing decimals still leave nonzero USD
amounts unavailable; genuine raw zero remains zero without a quote or scale.

## Existing analysis impact

This fix changes calculation behavior using the existing stored data. Completed
`lifetime-yield`,
legacy `vault-fees`, and `vault-volume` analyses that valued nonzero amounts
with zero decimals and an accepted quote are candidates for regeneration.
Their stored outputs retain the old calculation until explicitly regenerated.
Amounts with 6 or 18 decimals, missing decimals, or genuine raw zero are unchanged.
Canonical fee and TVL valuation use their own scaling paths.

No production records were inspected to measure affected runs. Before choosing
a repair scope, inspect retained zero-decimal inputs using a read-only connection:

```sql
SELECT 'strategy_reports' AS source, chain_id, asset, MIN(block_timestamp) AS first_timestamp,
       MAX(block_timestamp) AS last_timestamp, COUNT(*) AS candidate_rows
FROM strategy_reports
WHERE asset_decimals = 0 AND (gain_raw <> '0' OR loss_raw <> '0' OR net_raw <> '0'
    OR COALESCE(protocol_fees_raw, '0') <> '0' OR COALESCE(total_fees_raw, '0') <> '0'
    OR COALESCE(total_refunds_raw, '0') <> '0')
GROUP BY chain_id, asset
UNION ALL
SELECT 'vault_fee_events', chain_id, asset, MIN(block_timestamp), MAX(block_timestamp), COUNT(*)
FROM vault_fee_events WHERE asset_decimals = 0 AND fee_raw <> '0' GROUP BY chain_id, asset
UNION ALL
SELECT 'vault_flows', chain_id, asset, MIN(block_timestamp), MAX(block_timestamp), COUNT(*)
FROM vault_flows WHERE asset_decimals = 0 AND assets_raw <> '0' GROUP BY chain_id, asset
UNION ALL
SELECT 'strategy_debt_flows', chain_id, asset, MIN(block_timestamp), MAX(block_timestamp), COUNT(*)
FROM strategy_debt_flows WHERE asset_decimals = 0 AND debt_delta_raw <> '0' GROUP BY chain_id, asset;
```

These counts identify candidate inputs. Inspect a saved run’s captured evidence to establish
quote use. Match retained analysis outputs by event identity, source policy and cutoff
to establish the affected runs. Review candidate regenerated outputs before any
Redis or paired publication update. Preserve raw history and select changed publications
only through an explicit reviewed publication.

[Documentation index](../README.md).
