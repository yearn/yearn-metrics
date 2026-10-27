# October 15 Yearn Prices refresh

Production reads verified the changes merged in yearn-prices PRs #64, #66 and
#67. A successful deployment and closed issues do not establish complete
historical coverage; the dated API results below define the verified outcome.

| Upstream issue | Verified production result | Remaining coverage |
| --- | --- | --- |
| [#61](https://github.com/yearn/yearn-prices/issues/61) | All eight invalid yBUSD LP quotes are absent. | All eight replacement asset-days remain missing. |
| [#62](https://github.com/yearn/yearn-prices/issues/62) | All 20 invalid Curve/yETH quotes are absent. | The 19 December Curve replacements remain missing; the delete-only yETH target is unavailable. |
| [#63](https://github.com/yearn/yearn-prices/issues/63) | Chain 999 uses `hyperliquid`; unsupported namespaces return HTTP 400. | USDC and USD₮0 are missing at the October 8 and 14 closes; none of the 213 cached HyperEVM targets returned a quote. |
| [#65](https://github.com/yearn/yearn-prices/issues/65) | All six cited Optimism LPs return dated derived prices through exact, batch and range routes. | The broader recovered cohort does not certify every LP or historical day. |

The incident checks included default and source-filtered batch/range reads and
requests with the upstream public cache revision `2026-10-quarantine-v1`.
Rejected quotes remain unavailable; adjacent-day values and assumed pegs are not
used as replacements. The exact-quote rejection inventory is retained as defense
against previously cached bad observations.

The consumer now maps chain 999 to the producer's canonical `hyperliquid`
namespace. A batch regression verifies request encoding and response parsing
without requiring an RPC. This mapping supports future collections; it cannot
create missing upstream history.

## Database refresh

The affected cache inventory includes the 28 incident targets, all stored
HyperEVM targets, missing Optimism targets from September 4 onward, and missing
all-chain targets from September 23 onward. It contains 5,105 distinct asset-days.
Yearn Prices returned 2,088 available quotes, including 1,944 on Optimism. Batch
omissions were left unchanged except for the incident targets independently
confirmed missing through exact reads.

The refresh updates 2,101 TVL price cache rows (2,073 recovered plus 28
quarantined), 151 report-price rows (150 recovered plus one quarantined), and 147
fee daily prices. It appends TVL run **493**, containing 2,226 revalued snapshots
and 2,586 dated positions across 40 dates. Original snapshots, quantities,
supplies and block numbers remain intact. Nested-share valuation and position
valuation use the existing collection functions; missing ownership evidence is
read at the recorded historical block. All affected positions valued successfully.

Completed analyses **26** (`lifetime-yield`) and **27** (`fee-usd`) use the
existing exclusive UTC cutoff `1792022400`, Yearn-only EOD pricing and the same
validated accounting-deferral manifest. No event discovery, fee methodology or
incident policy is changed. Known latest-close external TVL increases from
$205,293,474.86 to $208,172,781.17. Remaining missing values keep their unavailable
semantics.

Chart preparation, dataset-specific comparison evidence, matching analytics and
compact financial serving rows are prepared before atomically selecting the
coherent hosted release. Original observations and the previous release are
retained; this refresh does not run canonical-history deletion or storage cleanup.

## Evidence

The ignored local bundle `artifacts/prices-update-2026-10-15/` retains issue and
merged-PR snapshots, live route responses, affected cache before-values, fetched
quotes, revaluation inputs/outputs, analysis IDs, history validation, publication
receipts and an HTML review. It also contains the incident-specific operational
scripts; these are not a general scheduled refresh pipeline.

The upstream checkout's configured database is an older snapshot (latest quote
August 2). It is not used as evidence of production backfill completeness.
Production claims above use the authenticated deployed service.

The activated hosted release is
`079172452b73baaab6cca104a63984e82825ff9d605838c57fea78a9c961c353`.
All eight checked routes through Powerglove's configured API alias returned HTTP
200 and matched the local database responses. The full chart comparison retains
1,158,332 rows and unchanged quantities; its 2,226 changed rows are confined to
the 40 repaired dates. The preceding coherent release remains available for
operational rollback.

Repository validation: **660 passed, 23 skipped**. HTTP tests require localhost
socket access. Local source changes are not evidence of a deployed consumer code
change; database publication and code deployment remain separate operations.
