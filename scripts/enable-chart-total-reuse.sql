-- Run after enable-chart-day-reuse.sql with the publisher stopped.
BEGIN;
SET LOCAL lock_timeout='5s';
LOCK TABLE tvl_chart_days, tvl_chart_publications, tvl_chart_totals IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tvl_chart_publications WHERE version NOT LIKE '%+totals') THEN
        RAISE EXCEPTION 'finish pending chart totals before converting their storage';
    END IF;
END
$$;

CREATE TEMP TABLE prior_chart_totals ON COMMIT DROP AS SELECT * FROM tvl_chart_totals;
ALTER TABLE tvl_chart_totals RENAME TO tvl_chart_total_values;
ALTER TABLE tvl_chart_total_values RENAME COLUMN dataset_id TO owner_id;

-- Existing rows already at their physical owner keep their heap and index entries.
-- An installation that already shares chart rows may have several copies per owner.
DELETE FROM tvl_chart_total_values t WHERE NOT EXISTS (
    SELECT 1 FROM tvl_chart_days d WHERE (d.owner_id,d.timestamp)=(t.owner_id,t.timestamp));
INSERT INTO tvl_chart_total_values
SELECT DISTINCT d.owner_id,t.timestamp,t.group_by,t.mode,t.series,t.value
FROM prior_chart_totals t JOIN tvl_chart_days d USING(dataset_id,timestamp)
WHERE d.owner_id<>t.dataset_id
ON CONFLICT DO NOTHING;

CREATE VIEW tvl_chart_totals WITH (security_invoker=true) AS
SELECT d.dataset_id,t.timestamp,t.group_by,t.mode,t.series,t.value
FROM tvl_chart_days d JOIN tvl_chart_total_values t USING(owner_id,timestamp)
JOIN tvl_chart_publications p ON p.dataset_id=d.dataset_id
WHERE p.version LIKE '%+totals';

-- Include both directions: shared days must agree even on missing groups or days.
DO $$
DECLARE reader record;
BEGIN
    IF EXISTS (SELECT * FROM prior_chart_totals EXCEPT ALL SELECT * FROM tvl_chart_totals)
       OR EXISTS (SELECT * FROM tvl_chart_totals EXCEPT ALL SELECT * FROM prior_chart_totals) THEN
        RAISE EXCEPTION 'chart totals differ across releases for a shared day';
    END IF;
    FOR reader IN
        SELECT CASE WHEN grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(grantee)) END AS role_sql,
               bool_or(is_grantable) AS may_grant
        FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE c.oid='tvl_chart_total_values'::regclass AND privilege_type='SELECT' AND grantee<>relowner
        GROUP BY grantee
    LOOP
        EXECUTE format('GRANT SELECT ON tvl_chart_totals TO %s%s',
                       reader.role_sql, CASE WHEN reader.may_grant THEN ' WITH GRANT OPTION' ELSE '' END);
        EXECUTE format('GRANT SELECT (dataset_id,owner_id,timestamp) ON tvl_chart_days TO %s',reader.role_sql);
        EXECUTE format('GRANT SELECT (dataset_id,version) ON tvl_chart_publications TO %s',reader.role_sql);
    END LOOP;
END
$$;
ANALYZE tvl_chart_total_values;
COMMIT;
