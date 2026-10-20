from yearn_data.tvl_comparison import discrepancy_details
from yearn_data.analytics import SCHEMA, put_input, selected_input
import sqlite3


def row(address, net='10', gross='10', category='v3'):
    return dict(chain_id=1, vault=address, name=address, category=category,
                external_tvl_usd=net, tvl_usd=gross)


def evidence(vaults=(), checks=()):
    return dict(tvlDatasetId='dataset', asOfTimestamp=123, vaults=list(vaults),
                overlapChecks=list(checks), coverage={}, sources={})


def external(address, amount='20', protocol='yearn-finance'):
    return dict(protocol=protocol, chainId=1, address=address, name=address,
                units='20', replayTvlUsd=amount)


def test_symmetric_scope_does_not_confuse_net_zero_with_exclusion():
    result=discrepancy_details([row('ours'),row('zero','0'),row('unpriced',None,None)],
        evidence([external('zero'),external('unpriced'),external('absent')]),'dataset')['categories'][0]
    assert [r['vaultAddress'] for r in result['oursOnly']]==['ours']
    assert {r['vaultAddress'] for r in result['theirsOnly']}=={'unpriced','absent'}
    assert next(r for r in result['theirsOnly'] if r['vaultAddress']=='unpriced')['oursUsd'] is None
    assert next(r for r in result['theirsOnly'] if r['vaultAddress']=='absent')['oursUsd']==0


def test_unverified_external_prices_are_not_reported_as_priced_discrepancies():
    result=discrepancy_details([row('unpriced',None,None)],evidence([external('unpriced',None)]),'dataset')
    assert not result['categories'][0]['theirsOnly']
    assert len(result['categories'][0]['unresolvedVaults'])==1


def test_unavailable_external_contribution_remains_in_unresolved_scope():
    observation = external('unpriced', None) | {'units': None}
    result = discrepancy_details([row('unpriced', None, None)],
        evidence([observation]), 'dataset')['categories'][0]
    assert result['theirsOnly'] == []
    assert [r['vaultAddress'] for r in result['unresolvedVaults']] == ['unpriced']


def test_unavailable_external_contribution_does_not_prove_ours_only():
    observation = external('priced', None) | {'units': None}
    result = discrepancy_details([row('priced')], evidence([observation]), 'dataset')['categories'][0]
    assert result['oursOnly'] == []
    assert result['theirsOnly'] == []


def test_saved_partial_replay_edges_cannot_mark_an_overlap_handled():
    from yearn_data.tvl_comparison import finance_overlap_status, finance_replay_routes

    position = {'chain_id': 1, 'parent': 'parent', 'strategy': 'sender', 'child': 'child', 'child_chain_id': 10}
    replay = {'chainId': 1, 'records': [{'address': 'parent', 'adjustedRaw': None},
                                       {'address': 'sender', 'adjustedRaw': '40'}],
              'edges': [{'parent': 'parent', 'contributionVault': 'sender', 'strategy': 'sender',
                         'child': 'child', 'childChainId': 10, 'method': 'cross-chain-shares'}]}
    assert finance_overlap_status(position, finance_replay_routes([replay])) is None
    replay['records'][0]['adjustedRaw'] = '0'
    assert finance_overlap_status(position, finance_replay_routes([replay])) == 'handled'
    replay['records'][1]['adjustedRaw'] = None
    assert finance_overlap_status(position, finance_replay_routes([replay])) is None


def test_checks_separate_missed_from_handled_and_unverified():
    checks=[dict(protocol='yearn-finance',status=s,ourDeductionUsd='10') for s in ['missed','handled','unverified']]
    result=discrepancy_details([],evidence(checks=checks),'dataset')['categories'][0]
    assert len(result['missedOverlaps'])==len(result['handledOverlaps'])==len(result['unverifiedOverlaps'])==1
    assert result['missedOverlaps'][0]['ourDeductionUsd']==10


def test_stale_evidence_fails_closed():
    assert discrepancy_details([],evidence(),'different')['state']=='unavailable'


def test_incomplete_factory_scan_does_not_establish_absence():
    e=evidence();e['coverage']['incompleteChains']=[1]
    r=discrepancy_details([row('curator',category='curation')],e,'dataset')['categories'][1]
    assert not r['oursOnly']
    assert len(r['unresolvedVaults'])==1


def test_comparison_inputs_replace_instead_of_retaining_stale_vaults():
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row;conn.executescript(SCHEMA)
    put_input(conn,'comparison-evidence',evidence([external('removed')]),observed_at=1)
    put_input(conn,'comparison-evidence',evidence([]),observed_at=2)
    assert selected_input(conn,'comparison-evidence')['body']['vaults']==[]


def test_prepared_publication_pins_evidence_without_rewriting_previous_result(tmp_path):
    from types import SimpleNamespace
    from yearn_data.analytics import prepare, AnalyticsStore
    from yearn_data.storage import connect
    database=tmp_path/'comparison.sqlite'
    financial=SimpleNamespace(id='fees',cutoff=86400,fees=[],earnings=[],vault_names={})
    tvl=SimpleNamespace(id='dataset',as_of=lambda:123,dates=lambda:(),
        frames=lambda dates:[(123,[row('ours')],[],{})],history_prepared=lambda:False,
        summary=lambda:{'retiredVaultTvl':0,'overlapExcluded':0,'vaultBridgeExcluded':0})
    with connect(database) as conn:
        conn.executescript(SCHEMA)
        put_input(conn,'comparison-evidence',evidence(),observed_at=1)
    first=prepare(database,financial,tvl)
    with connect(database) as conn:
        put_input(conn,'comparison-evidence',evidence([external('ours')]),observed_at=2)
    second=prepare(database,financial,tvl)
    assert first!=second
    store=AnalyticsStore(database)
    assert len(store.load(first)['comparable']['reconciliation']['categories'][0]['oursOnly'])==1
    assert store.load(second)['comparable']['reconciliation']['categories'][0]['oursOnly']==[]
