-- Run once on an initialized chart database with the application writer stopped.
-- Existing chart readers continue to use tvl_chart_rows; the values keep their indexes.
BEGIN;
SET LOCAL lock_timeout='5s';
LOCK TABLE tvl_chart_rows IN ACCESS EXCLUSIVE MODE;
ALTER TABLE tvl_chart_rows RENAME TO tvl_chart_row_values;
ALTER TABLE tvl_chart_row_values RENAME COLUMN dataset_id TO owner_id;
-- Match the legacy-table upgrade performed by chart preparation on main.
ALTER TABLE tvl_chart_row_values ADD COLUMN IF NOT EXISTS ownership_incomplete BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE tvl_chart_days (
    dataset_id text NOT NULL, timestamp bigint NOT NULL, owner_id text NOT NULL,
    row_count bigint NOT NULL CHECK (row_count >= 0), PRIMARY KEY(dataset_id,timestamp)
);
INSERT INTO tvl_chart_days
SELECT owner_id,timestamp,owner_id,count(*) FROM tvl_chart_row_values GROUP BY owner_id,timestamp;
-- Some known input dates have no accounted rows.
INSERT INTO tvl_chart_days SELECT dataset_id,timestamp,dataset_id,0 FROM tvl_chart_inputs ON CONFLICT DO NOTHING;
CREATE VIEW tvl_chart_rows WITH (security_invoker=true) AS
SELECT d.dataset_id,v.timestamp,v.chain_id,v.vault,v.name,v.category,v.version,
       v.asset_units,v.tvl_usd,v.external_tvl_usd,v.ownership_incomplete
FROM tvl_chart_days d JOIN tvl_chart_row_values v USING(owner_id,timestamp);
-- A rename keeps grants on the physical rows. Existing readers also need the
-- release-to-day lookup and the view; execute as the original table owner.
DO $$
DECLARE reader record;
BEGIN
    FOR reader IN
        SELECT CASE WHEN grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(grantee)) END AS role_sql,
               bool_or(is_grantable) AS may_grant
        FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
        WHERE c.oid='tvl_chart_row_values'::regclass AND privilege_type='SELECT' AND grantee<>relowner
        GROUP BY grantee
    LOOP
        EXECUTE format('GRANT SELECT ON tvl_chart_rows,tvl_chart_days TO %s%s',
                       reader.role_sql, CASE WHEN reader.may_grant THEN ' WITH GRANT OPTION' ELSE '' END);
    END LOOP;
END
$$;
COMMIT;
