# Recover legacy analysis jobs

`lifetime-yield`, `vault-fees`, `vault-volume`, `fee-usd` and `canonical-fees`
share one lifecycle and commit a `running` marker before
calculating outputs. A handled Python exception or interruption now rolls back
the partial output transaction, commits a `failed` marker with `completed_at`,
and re-raises the error. A successful job commits its outputs and completed
marker together. Retry creates a new run; failed output is never promoted.

This applies to the supported Postgres runtime. SQLite is retired; it is not
restored to satisfy the older wording of issue #48.

## Process crashes and unavailable database connections

SIGKILL, host failure, or a lost database connection can prevent terminal-state
cleanup. A surviving `running` marker is not proof that a process is active or
abandoned. Do not expire it on age alone, and do not disable canonical-history's
active-writer guard.

1. Inspect `analysis_runs` by explicit ID, name, start time and saved parameters.
   Match it to the operator receipt, process/service logs and execution host.
2. Check that the process and any resuming worker have stopped, including other
   hosts. Inspect `pg_stat_activity` and locks with an appropriately privileged
   operator connection. Database inactivity alone does not prove that an idle
   application worker will not resume.
3. Stop or fence all possible owners before recovery. If ownership cannot be
   established, leave the marker untouched and investigate.
4. With ownership ruled out, mark only the confirmed abandoned run failed in a
   transaction. The conditional update below must return exactly one row. Do
   not select IDs by age or update all running jobs.

```sql
BEGIN;
UPDATE analysis_runs
SET status = 'failed', completed_at = EXTRACT(EPOCH FROM NOW())::bigint
WHERE id = <confirmed_abandoned_run_id> AND status = 'running'
RETURNING id, name, status, completed_at;
COMMIT;
```

Before retrying or publishing, export the failed run's `analysis_runs` record,
any retained `analysis_outputs`, and relevant process logs to the incident's
access-controlled diagnostic archive. Record that archive location, the recovered
run ID and supporting evidence in the operational receipt. Publication runs
canonical consolidation, which removes outputs outside its retained run set;
keeping partial outputs in the working database is not durable evidence.

Readers must select complete runs. Retry the original analysis with the same
intended cutoff and source policy, inspect its completed output, and only then
perform any separately authorized publication. This code change performs no
production recovery or publication automatically.
