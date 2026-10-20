from types import SimpleNamespace
import json

import pytest
from yearn_data.analytics import (SCHEMA, AnalyticsStore, analytics_response, capital_period,
    comparable, fee_stack, profitability, put_input, DAY, YEAR)
from yearn_data.analytics_collect import rules
from yearn_data.storage import connect

A='0x'+'11'*20;B='0x'+'22'*20;C='0x'+'33'*20

def rows():
    return [{'chain_id':1,'vault':A,'name':'parent','tvl_usd':'100','external_tvl_usd':'100','category':'v3'},
            {'chain_id':1,'vault':B,'name':'child','tvl_usd':'60','external_tvl_usd':'0','category':'v3'}]

def configs():
    return {'vaults':[{'chainId':1,'address':A,'performanceFee':1000,'managementFee':100},
                      {'chainId':1,'address':B,'performanceFee':1000,'managementFee':0}]}

def test_stack_rates_capital_and_unknown():
    positions=[{'chain_id':1,'parent':A,'strategy':C,'child':B,'debt_usd':'60','status':'ok'}]
    result=fee_stack(rows(),positions,configs())
    assert result['chains'][0]['effectivePerfFee']==pytest.approx(1540)
    assert result['chains'][0]['effectiveMgmtFee']==100
    assert result['chains'][0]['root']['children'][0]['viaStrategy']==C
    assert result['totalStackedCapital']==100
    unknown=configs();unknown['vaults'][1]['performanceFee']=None
    assert fee_stack(rows(),positions,unknown)['chains'][0]['effectivePerfFee'] is None
    zero=configs();zero['vaults'][1]['performanceFee']=0
    assert fee_stack(rows(),positions,zero)['chains'][0]['effectivePerfFee']==pytest.approx(1000)


def test_cycle_is_not_complete():
    positions=[{'chain_id':1,'parent':a,'strategy':b,'child':b,'debt_usd':'50','status':'ok'} for a,b in [(A,B),(B,A)]]
    result=fee_stack(rows(),positions,configs())
    assert all(c['effectivePerfFee'] is None for c in result['chains'])
    assert result['availability']['state']=='partial'


def test_fee_stack_follows_cross_chain_child_identity():
    points = rows()
    points[1]['chain_id'] = 747474
    configuration = configs()
    configuration['vaults'][1]['chainId'] = 747474
    positions = [{'chain_id': 1, 'parent': A, 'strategy': C, 'child': B,
                  'child_chain_id': 747474, 'debt_usd': '60', 'status': 'ok'}]
    result = fee_stack(points, positions, configuration)
    assert len(result['chains']) == 1
    chain = result['chains'][0]
    assert chain['root']['children'][0]['vault']['chainId'] == 747474
    assert chain['effectivePerfFee'] == pytest.approx(1540)


def test_known_child_without_selected_fee_evidence_stays_unresolved():
    # A known nested fee layer must not disappear just because its row is absent.
    points = rows()
    positions = [{'chain_id': 1, 'parent': A, 'strategy': C, 'child': C,
                  'child_chain_id': 747474, 'debt_usd': '60', 'owned_usd': None,
                  'status': 'unavailable', 'mapping_status': 'child_outside_selection',
                  'reason': 'child_outside_selection'}]
    result = fee_stack(points, positions, configs())
    parent = next(entry for entry in result['entries'] if entry['vault']['address'] == A)
    assert parent['roots'][0]['perfFee'] == 1000
    assert parent['roots'][0]['mgmtFee'] == 100
    assert parent['performanceFee'] is None
    assert parent['managementFee'] is None
    other = next(entry for entry in result['entries'] if entry['vault']['address'] == B)
    assert other['performanceFee'] == 1000
    assert other['managementFee'] == 0
    assert result['availability']['unresolvedAllocations'] == [
        {'parent': [1, A], 'strategy': C, 'reason': 'unavailable allocation'}]


def test_rules_extract_cross_routes_and_integer_converter_decimals():
    from yearn_data.analytics_collect import FINANCE_CHAINS, CURATION_CHAINS
    from yearn_data.finance_membership import capped_debt
    from web3 import Web3

    parent_asset, child_asset = '0x' + 'ab' * 20, '0x' + 'cd' * 20

    finance = """buildNestedDebtByVault subtractNestedDebt get_default_queue withdrawalQueue
list/vaults/${api.chainId}?origin=yearn
const v1Vaults = [];
const blacklist = [];
""" + 'const chains = [' + ','.join(repr(name) for name in FINANCE_CHAINS.values()) + '];\n'
    finance += f"""const routes = [
      {{ chainId: 1, parent: '{A.upper()}', strategy: '{B}', child: '{C}', getter: 'vault' }},
      {{ chainId: 10, parent: '{A}', strategy: '{C}', child: '{B}', getter: 'vault',
         parentAsset: '{Web3.to_checksum_address(parent_asset)}', childAsset: '{Web3.to_checksum_address(child_asset)}', parentDecimals: 6, childDecimals: 18 }},
    ];"""
    registry = 'export default {\n  "yearn-curating": { chains: {\n' + ''.join(
        f'    {name}: {{}},\n' for name in CURATION_CHAINS.values()) + '  }\n  },\n}'
    result = rules(finance, registry)
    assert result['cross'] == {(1, A, B): C, (10, A, C): B}
    assert result['routes'][(1, A, B)]['getter'] == 'vault'
    route = result['routes'][(10, A, C)]
    assert route == {'parent': A, 'strategy': C, 'child': B, 'getter': 'vault',
                     'parentAsset': parent_asset, 'childAsset': child_asset, 'parentDecimals': 6, 'childDecimals': 18}
    assert capped_debt(100 * 10**6, 200 * 10**18, 1, 4, route, parent_asset, child_asset) == 50 * 10**6
    assert type(route['parentDecimals']) is int
    assert type(route['childDecimals']) is int
    assert all(not owners for owners in result['owners'].values())
    assert all(not vaults for vaults in result['explicit'].values())
    with pytest.raises(ValueError, match='curation.*ethereum'):
        rules(finance, registry.replace('ethereum:', 'ethereum_v2:'))


@pytest.mark.parametrize('snapshot', ['legacy', 'current'])
def test_captured_curator_registry_preserves_verified_hyperevm_scope(snapshot):
    from pathlib import Path
    import hashlib
    from yearn_data.analytics_collect import FINANCE_CHAINS

    fixture = json.loads((Path(__file__).parent / 'fixtures/finance/curator-registry-snapshots.json').read_text())
    capture = fixture['snapshots'][snapshot]
    assert capture['sourceSha256'] == {
        'legacy': '7f23eeb5c4c7e58c158efb12a5ed1afb7a2df305f9d2c0a4e35794b7b3dd291c',
        'current': 'ba341896a16f189846fd315983b052c22bb19c0d65693bedd63634238bc1b67c',
    }[snapshot]
    assert hashlib.sha256(capture['yearnEntry'].encode()).hexdigest() == capture['yearnEntrySha256']
    finance = """buildNestedDebtByVault subtractNestedDebt get_default_queue withdrawalQueue
list/vaults/${api.chainId}?origin=yearn
const v1Vaults = [];
const blacklist = [];
""" + 'const chains = [' + ','.join(repr(name) for name in FINANCE_CHAINS.values()) + '];'
    result = rules(finance, capture['yearnEntry'])
    common = {'0xfc5f89d29ccaa86e5410a7ad9d9d280d4455c12b',
              '0x50b75d586929ab2f75dc15f07e1b921b7c4ba8fa',
              '0x75a1253432356f90611546a487b5350cef08780d'}
    assert result['owners'][999] == result['owners'][8453] == result['owners'][42161] == common
    assert result['owners'][747474] == common | {'0x518c21dc88d9780c0a1be566433c571461a70149'}
    assert result['owners'][1] == common | ({'0x0dca0fdc170baa4ca9c1dcd37ffe01f97bcfd504'} if snapshot == 'current' else set())
    admitted = {r['address'] for r in fixture['verifiedHyperEvm'] if r['owner'] in result['owners'][999]}
    assert admitted == {'0x0fb7e41a0a85eb0bca55172b73942cc6685e2b2e',
                        '0x7bdb0f32cee4847407bfd391594e19b23f5519d0'}


def test_membership_unknown_is_neither_missing_nor_included():
    r=rows();r[1]['external_tvl_usd']='60'
    membership={'vaults':[{'chainId':1,'address':A.upper(),'included':False}]}
    result=comparable(r,membership)
    assert result['diff']['missingFromDefillama'][0]['countedTvlUsd']==100
    assert result['includedVaults']==[]
    assert result['availability']['unknownVaults'][0]['vaultAddress']==B
    assert result['availability']['state']=='partial'


def test_capital_time_weighted_and_gaps():
    points=[(i*DAY+DAY-1,100 if i<180 else 300) for i in range(360)]
    assert capital_period(points,0,360*DAY)['average']==200
    assert capital_period(points[:80],0,360*DAY)['eligible'] is False
    assert capital_period([(t,None) for t,v in points],0,360*DAY)['average'] is None


def test_profitability_period_chain_retired_and_no_nested_yield_sum():
    cutoff=YEAR
    history={(1,A):[(i*DAY+DAY-1,100 if i<180 else 300) for i in range(365)]}
    financial=SimpleNamespace(fees=[{'chain_id':1,'vault_address':A,'block_timestamp':100*DAY,
                                   'total_fees_paid_usd':'20','price_usd':1}],
                              earnings=[{'chain_id':1,'vault_address':A,'block_timestamp':100*DAY,
                                         'gross_gain_usd':'100','version':'v3','price_usd':1}],vault_names={})
    result=profitability(financial,rows(),history,configs(),cutoff,1)
    parent=next(v for v in result['vaults'] if v['address']==A)
    assert parent['feeYield']==pytest.approx(20/(100*180/365+300*185/365))
    assert parent['timeWeightedTvlUsd']!=parent['tvlUsd']
    assert parent['trend']=='insufficient_data'
    assert parent['totalGainUsd']==100
    assert parent['feeCapture']==0.2
    assert result['period']['endTimestamp']==cutoff
    assert 'gross contract' in result['capitalBasis']
    unavailable=next(v for v in result['vaults'] if v['address']==B)
    assert unavailable['quadrant'] is None
    assert unavailable['annualizedFeeRevenue'] is None
    assert profitability(financial,rows(),history,configs(),cutoff,10)['vaults']==[]


def test_publications_pinned_filters_and_atomic_selection(tmp_path):
    database=tmp_path/'data.sqlite';conn=connect(database);conn.executescript(SCHEMA)
    identity='a'*64
    meta={'publicationId':identity,'tvlDatasetId':'b'*64}
    data={'meta':meta,'stack':{},'comparison':{},'comparable':{'diff':{'missingFromDefillama':[]}},'profitability':{'None':{},'1':{}}}
    with conn:
        conn.execute('INSERT INTO analytics_publications VALUES (?,?,?)',(identity,1,json.dumps(data)))
        conn.execute('INSERT INTO analytics_selection VALUES (?,?)',('powerglove',identity))
    store=AnalyticsStore(database)
    assert analytics_response(store,'/api/comparison/defillama-comparable?includeVaultBreakdown=false')[0]==200
    assert analytics_response(store,'/api/profitability?chainId=1')[0]==200
    assert analytics_response(store,'/api/profitability?chainId=999')[0]==400
    assert analytics_response(store,'/api/profitability?chainId=1&chainId=1')[0]==400
    assert analytics_response(store,'/api/fees/stack?publicationId=wrong')[0]==400
    assert analytics_response(store,'/api/analytics/publication')[1]==meta
    with conn:conn.execute("DELETE FROM analytics_selection")
    assert analytics_response(store,'/api/fees/stack?publicationId='+identity)[0]==200


def test_input_zero_stored_and_bad_adapter_fails(tmp_path):
    conn=connect(tmp_path/'data.sqlite');conn.executescript(SCHEMA)
    identity=put_input(conn,'fee-config',configs(),observed_at=1)
    assert identity==put_input(conn,'fee-config',configs(),observed_at=1)
    with pytest.raises(ValueError,match='unsupported finance adapter'):rules('garbage','garbage')


def test_failed_retry_preserves_known_zero_and_membership(tmp_path):
    from yearn_data.analytics import selected_input
    conn=connect(tmp_path/'data.sqlite');conn.executescript(SCHEMA)
    put_input(conn,'fee-config',{'vaults':[{'chainId':1,'address':A,'performanceFee':0,'managementFee':0,'observedAt':1}]},observed_at=1)
    put_input(conn,'fee-config',{'vaults':[{'chainId':1,'address':A,'performanceFee':None,'managementFee':None}]},status='partial',observed_at=2)
    latest=selected_input(conn,'fee-config')['body']['vaults'][0]
    assert latest['performanceFee']==0
    assert latest['retainedObservedAt']==1
    put_input(conn,'membership',{'vaults':[{'chainId':1,'address':A,'included':True}]},observed_at=1)
    put_input(conn,'membership',{'vaults':[{'chainId':1,'address':A,'included':None}]},status='partial',observed_at=2)
    assert selected_input(conn,'membership')['body']['vaults'][0]['included'] is True


def test_stack_unsupported_configuration_is_not_fixed_rate():
    c=configs();c['vaults'][0]['fixedRateSupported']=False
    result=fee_stack(rows(),[{'chain_id':1,'parent':A,'strategy':B,'child':B,'debt_usd':'60','status':'ok'}],c)
    assert result['chains'][0]['effectivePerfFee'] is None


def test_prepare_freezes_inputs_and_retains_old_selection(tmp_path):
    from yearn_data.analytics import prepare
    db=tmp_path/'data.sqlite';conn=connect(db);conn.executescript(SCHEMA)
    put_input(conn,'fee-config',configs(),observed_at=1)
    put_input(conn,'membership',{'vaults':[{'chainId':1,'address':A,'included':True}]},observed_at=1)
    financial=SimpleNamespace(id='f'*64,cutoff=YEAR,fees=[],earnings=[],vault_names={})
    tvl=SimpleNamespace(id='b'*64,as_of=lambda:YEAR-1,frames=lambda dates:[(YEAR-1,rows(),[],{})],
                        dates=lambda:[i*DAY+DAY-1 for i in range(365)],history_prepared=lambda:False,
                        history_rows=lambda dates,chain:[(t,tuple(rows())) for t in dates],
                        summary=lambda:{'retiredVaultTvl':0,'overlapExcluded':60,'vaultBridgeExcluded':0})
    old=prepare(db,financial,tvl)
    store=AnalyticsStore(db)
    assert store.get()['meta']['publicationId']==old
    put_input(conn,'membership',{'vaults':[{'chainId':1,'address':A,'included':False}]},observed_at=2)
    new=prepare(db,financial,tvl,select=False)
    assert store.get()['meta']['publicationId']==old
    assert new!=old
    assert store.get(old)['comparable']['includedVaults'][0]['vaultAddress']==A
    assert store.get(new)['comparable']['diff']['missingFromDefillama'][0]['vaultAddress']==A
    assert prepare(db,financial,tvl)==new
    assert analytics_response(store,'/api/comparison?datasetId='+tvl.id)[0]==200


def test_quadrants_use_displayed_capital_and_zero_yield_is_low():
    points={};current=[];fee_rows=[]
    for i,(tvl,fee) in enumerate([(20000,0),(30000,50),(200000,0),(300000,500),(0,0)]):
        address='0x'+f'{i+1:040x}'
        current.append({'chain_id':1,'vault':address,'name':address,'category':'v3','tvl_usd':str(tvl)})
        points[(1,address)]=[(d*DAY+DAY-1,max(tvl,100)) for d in range(365)]
        fee_rows.append({'chain_id':1,'vault_address':address,'block_timestamp':100*DAY,'total_fees_paid_usd':str(fee)})
    financial=SimpleNamespace(id='f'*64,fees=fee_rows,earnings=[],vault_names={})
    result=profitability(financial,current,points,{},YEAR)
    assert result['quadrantThresholds']['cohortSize']==4
    assert all(len(vaults)==1 for vaults in result['quadrants'].values())
    assert result['vaults'][-1]['quadrant'] is None


def test_no_fee_observations_do_not_become_known_zero_yield():
    f=SimpleNamespace(fees=[],earnings=[],vault_names={})
    history={(1,A):[(i*DAY+DAY-1,100000) for i in range(365)]}
    current=[dict(rows()[0],tvl_usd='100000')]
    result=profitability(f,current,history,configs(),YEAR)['vaults'][0]
    assert result['totalFeeRevenue'] is None
    assert result['feeYield'] is None
    assert result['quadrant'] is None
    assert result['availability']['fees']['state']=='unavailable'


def test_current_curator_registry_is_scoped_to_yearn():
    finance="""
    buildNestedDebtByVault subtractNestedDebt get_default_queue withdrawalQueue
    list/vaults/${api.chainId}?origin=yearn
    const chains = ['ethereum','optimism','polygon','fantom','base','arbitrum','katana']
    const v1Vaults = []
    const blacklist = []
    """
    owner='0x0Dca0FDC170baA4CA9c1dCd37Ffe01f97bCfD504'
    registry='''const configs = {
  "other": {
    config: { blockchains: { ethereum: {
      morphoVaultOwners: ["OTHER"],
    } } }
  },
  "yearn-curating": {
    config: { blockchains: {
      ethereum: {
        morphoVaultOwners: ["OWNER"],
        // turtleclub_erc4626: ["OTHER"],
        morpho: ["VAULT"],
      },
      base: {
        morphoVaultOwners: ["BASE"],
      },
      katana: {},
      arbitrum: {},
      hyperliquid: {},
    } }
  },
}'''.replace('OTHER',A).replace('OWNER',owner).replace('VAULT',B).replace('BASE',C)
    policy=rules(finance,registry)
    assert policy['owners'][1]=={owner.lower()}
    assert policy['owners'][8453]=={C}
    assert policy['explicit'][1]=={B}
    assert policy['explicit'][747474]==set()
    with pytest.raises(ValueError,match='missing yearn-curating'):
        rules(finance,registry.replace('yearn-curating','unrelated'))
