# Curation inventory and indexed events

`src/yearn_data/inventories/tvl-curation.json` owns Yearn discovery scope:
owner/creator/curator identities, factory deployments, explicit products and a
known-vault bootstrap. The expanded catalog lives in `tvl_vaults`; acquisition
provenance lives in `tvl_catalog_evidence`. Candidates include historical and
unnamed deployments, not just currently marketed products.

All four configured identities apply on all chains, including
`0x518C21DC88D9780c0A1Be566433c571461A70149`. API reconciliation covers V1 owner,
creator and curator and V2 owner and curator. Factory discovery retains matching
historical initial owners. Explicit seeds repair gaps even when an API omits a
vault. Optimism's V1 factory and Robinhood's V2 factory are included. HyperEVM
OUSD V2 and Robinhood Yearn Stonk USDG are included in the bootstrap.

## Source selection

Curation factory and adapter history prefer Envio automatically.
`YEARN_CURATION_EVENT_SOURCE=rpc` forces direct RPC. Other vault discovery and
metadata reads retain their existing behavior.

The producer indexes `CurationFactoryEvent` and dynamically registers factory-created
vaults. Existing `MorphoGovernanceEvent` rows carry V2 `AddAdapter` history.
`CurationVault` identifies dynamic registrations. Attribution remains in metrics:
indexing a contract does not endorse it or classify its TVL as Yearn's.

Before accepting even an empty indexed range, metrics requires:

- A `CurationIndexingState` covering the exact bundled scope hash and range.
- The matching static contract start block or a verified dynamic registration.
- A processed chain watermark covering the range.
- An indexed raw-event anchor at or beyond the range, matching current RPC's
  canonical block hash. Quiet chains without such an anchor use RPC.

The anchor hash is checked again on every range, including reused anchors. Each
returned event must match chain, emitter, family, block range, canonical block
hash/timestamp, ABI fields and unique log identity. Pagination continues to a
terminal empty page, even after short pages. A failed page discards all partial
indexed results and retries the range through RPC. Coverage and observations
commit atomically only after rechecking the range's canonical end block.

Verified Envio ranges may use larger planning windows. If their fetch falls back,
RPC divides those ranges into bounded windows. Failed acquisition never certifies
empty coverage. Receipts retain event source counts and public fallback reasons.

## Producer rollout and backfill

The matching producer scope is in yearn-envio's
`src/morpho/curation-scope.json`; metrics bundles the same JSON as
`inventories/envio-curation-scope.json`. Its SHA-256 is over the compact JSON
payload excluding `scopeHash`, preserving field order. Keep both copies identical.
Changing contract/event coverage requires a new scope and a historical backfill;
do not update the attestation manually to bypass that backfill.

The producer advances scope checkpoints only from the configured starting block
and through contiguous checkpoint intervals. A late deployment cannot claim that
it processed earlier history. Metrics safely uses RPC until the new schema,
complete scope and canonical anchor are available. No dashboard behavior changes.

HyperEVM factory history starts at 1,988,677. Robinhood factory history starts at
288. Existing unrelated subscriptions retain their previous start floors. A
production database reset is not implicit in installing these changes: prepare a
separate deployment/backfill or review the specific migration/reset operation
before replacing a serving indexer. Verify all seven curation chains before
claiming that indexed acquisition is active.

`YEARN_CURATION_EVENT_SOURCE` accepts `auto` or `rpc`, ignoring case and
surrounding whitespace; other values fail explicitly. Fully paginated Envio
windows may contain 10,000 or more events. Only potentially capped RPC/custom
reader responses retain the result-cap rejection.
