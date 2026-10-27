# Zero-decimal valuation correction

Explicit asset decimals of zero mean one raw unit is one token. For example,
raw amount 7 at USD 2 must value at USD 14. The shared analysis amount helper
previously substituted 18 for zero. Missing decimals still leave nonzero USD
amounts unavailable; genuine raw zero remains zero without a quote or scale.

## Existing analysis impact

This is a calculation fix, not a data migration. Completed `lifetime-yield`,
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

These are candidate input counts, not proof that a particular saved run used a
quote. Match retained analysis outputs by event identity, source policy and cutoff
to establish the affected runs. Review candidate regenerated outputs before any
Redis or paired publication update. Do not rewrite raw history or automatically
replace selected publications as part of this fix.
