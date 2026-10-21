"""Regressions for analytics collection, publication coverage and CLI cutover."""
from types import SimpleNamespace

import pytest

from yearn_data.analytics import (SCHEMA, DAY, YEAR, comparable, fee_stack,
                                  owned_comparison, profitability, put_input, selected_input)
from yearn_data.storage import connect

A='0x'+'11'*20
B='0x'+'22'*20
C='0x'+'33'*20
ZERO='0x'+'00'*20


def test_cli_prepare_accepts_same_file_and_rejects_other_database(tmp_path, monkeypatch, capsys):
    from yearn_data import analytics, cli, pairing, tvl_api
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, 'load_environment', lambda path: None)
    monkeypatch.delenv('YEARN_DATA_DB', raising=False)
    monkeypatch.setattr(cli, 'DEFAULT_DB_PATH', 'data/metrics.sqlite')
    database=tmp_path/'data/metrics.sqlite'
    database.parent.mkdir();database.touch()
    alias=tmp_path/'alias.sqlite';alias.symlink_to(database)
    financial=SimpleNamespace(database=database)
    monkeypatch.setattr(pairing,'PairingStore',lambda path:SimpleNamespace(get=lambda:financial))
    monkeypatch.setattr(tvl_api,'TvlStore',lambda *a,**kw:SimpleNamespace(get=lambda:object()))
    monkeypatch.setattr(analytics,'prepare',lambda db,f,t:'a'*64)
    command=['prepare-analytics','--publication','fees','--tvl-publication','tvl']
    for args in ([],['--db','./data/metrics.sqlite'],['--db',str(database)],['--db',str(alias)]):
        assert cli.main(args+command)==0
    with pytest.raises(ValueError,match='database must match'):
        cli.main(['--db','other.sqlite']+command)
    financial.database='staging'
    assert cli.main(['--db','staging']+command)==0
    with pytest.raises(ValueError,match='database must match'):
        cli.main(['--db','neon']+command)


def test_analytics_serving_rejects_mixed_bridge_policy_before_startup():
    from yearn_data.pairing import serve_pairing
    with pytest.raises(ValueError,match='bridge policy mismatch.*retired-registry'):
        serve_pairing('unused',analytics=True,current_bridge_policy='none')


def comparison_store():
    return SimpleNamespace(id='b'*64,catalog={(1,A):{'active':False},(10,B):{'active':False}},
        summary=lambda:{'retiredVaultTvl':30,'overlapExcluded':0,'vaultBridgeExcluded':0,
                        'tvlByChain':{'Ethereum':10,'Optimism':20}})


def test_retained_external_snapshot_preserves_age_and_marks_partial(tmp_path):
    conn=connect(tmp_path/'analytics.sqlite');conn.executescript(SCHEMA)
    refs=[{'protocol':p,'timestamp':1,'total':10,'byChain':{}} for p in ('yearn-finance','yearn-curating')]
    put_input(conn,'defillama',{'snapshots':refs},observed_at=1)
    put_input(conn,'defillama',{'snapshots':[dict(refs[0],timestamp=2,total=20)]},status='partial',observed_at=2)
    retained=selected_input(conn,'defillama')['body']['snapshots']
    result=owned_comparison(comparison_store(),[],retained)
    assert result['defillamaTotal']==30
    assert result['availability']['state']=='partial'
    assert retained[1]['timestamp']==retained[1]['retainedObservedAt']==1
    put_input(conn,'defillama',{'snapshots':[]},status='partial',observed_at=3)
    assert selected_input(conn,'defillama')['body']['snapshots'][1]['retainedObservedAt']==1
    put_input(conn,'defillama',{'snapshots':refs},observed_at=4)
    assert owned_comparison(comparison_store(),[],selected_input(conn,'defillama')['body']['snapshots'])['availability']['state']=='available'


def test_unpriced_current_vault_remains_in_comparable_scope():
    rows=[{'chain_id':1,'vault':A,'external_tvl_usd':None},
          {'chain_id':1,'vault':B,'external_tvl_usd':'0'},
          {'chain_id':1,'vault':C,'external_tvl_usd':'10'}]
    result=comparable(rows,{'vaults':[{'chainId':1,'address':C,'included':True}]})
    assert result['availability']['state']=='partial'
    assert result['availability']['expected']==2
    assert result['availability']['known']==1
    assert result['availability']['unavailableTvlVaults'][0]['vaultAddress']==A
    assert result['availability']['unavailableTvlVaults'][0]['countedTvlUsd'] is None


def test_retry_retains_coherent_fee_support_but_rejects_changed_accountant(tmp_path):
    conn=connect(tmp_path/'analytics.sqlite');conn.executescript(SCHEMA)
    good={'chainId':1,'address':A,'performanceFee':1000,'managementFee':0,
          'fixedRateSupported':True,'availability':'available','observedAt':1,
          'accountant':C,'contractFamily':'v3_allocator'}
    child=dict(good,address=B,performanceFee=0)
    put_input(conn,'fee-config',{'vaults':[good,child]},observed_at=1)
    failed=dict(good,performanceFee=None,managementFee=None,fixedRateSupported=False,availability='unavailable',observedAt=2)
    put_input(conn,'fee-config',{'vaults':[failed]},status='partial',observed_at=2)
    body=selected_input(conn,'fee-config')['body']
    assert body['vaults'][0]['fixedRateSupported'] is True
    assert body['vaults'][0]['observedAt']==body['vaults'][0]['retainedObservedAt']==1
    assert body['vaults'][0]['unavailableRetry']['observedAt']==2
    rows=[{'chain_id':1,'vault':a,'tvl_usd':100,'external_tvl_usd':100 if a==A else 0} for a in (A,B)]
    positions=[{'chain_id':1,'parent':A,'strategy':B,'child':B,'debt_usd':100,'status':'ok'}]
    assert fee_stack(rows,positions,body)['chains'][0]['effectivePerfFee']==pytest.approx(1000)
    for i,change in zip((20,40),({'accountant':B},{'failureKind':'verification_rejected'})):
        put_input(conn,'fee-config',{'vaults':[failed|change]},status='partial',observed_at=i)
        assert selected_input(conn,'fee-config')['body']['vaults'][0]['performanceFee'] is None
        put_input(conn,'fee-config',{'vaults':[good,child]},observed_at=i+1)


def test_capture_uses_complete_observations_and_discloses_subset():
    rows=[{'chain_id':1,'vault':a,'tvl_usd':100} for a in (A,B,C)]
    financial=SimpleNamespace(fees=[{'chain_id':1,'vault_address':A,'block_timestamp':DAY,'total_fees_paid_usd':20},
        {'chain_id':1,'vault_address':B,'block_timestamp':DAY,'total_fees_paid_usd':None}],
        earnings=[{'chain_id':1,'vault_address':A,'block_timestamp':DAY,'gross_gain_usd':100}],vault_names={})
    result=profitability(financial,rows,{}, {},YEAR)
    assert result['feeCaptureRate']==0.2
    assert result['feeCaptureCoverage']['contributingVaults']==1
    assert result['feeCaptureCoverage']['expectedVaults']==3
    assert result['feeCaptureCoverage']['state']=='partial'
    financial.fees[0]['total_fees_paid_usd']=0
    assert profitability(financial,rows,{}, {},YEAR)['feeCaptureRate']==0
    financial.fees[0]['total_fees_paid_usd']=None
    assert profitability(financial,rows,{}, {},YEAR)['feeCaptureRate'] is None


def test_owned_comparison_keeps_retired_tvl_by_chain():
    rows=[{'chain_id':chain,'vault':a,'tvl_usd':value,'external_tvl_usd':value}
          for chain,a,value in [(1,A,10),(10,B,20),(1,C,50)]]
    assert owned_comparison(comparison_store(),rows,[])['retiredTvlByChain']=={'Ethereum':10,'Optimism':20}


def test_fee_collector_unpacks_families_and_units_offline(monkeypatch):
    from yearn_data import analytics_collect as collect
    addresses=['0x'+f'{i:040x}' for i in range(1,7)]
    local=[{'address':a,'version':version,'api_version':api} for a,version,api in zip(addresses,
           ['v2','v3','v3','morpho-v1','morpho-v2','v3'],['0.4.6','3.0.4','3.0.4','','','3.0.4'])]
    class Reader:
        block={'number':100,'timestamp':200}
        def __init__(self,chain):pass
        def call(self,calls):
            if not calls:return []
            if calls[0][1]=='getVaultConfig(address)':
                assert calls[0][3]==[addresses[1]]
                return [(100,1500,0,5000,10000,10000,False)]
            assert len(calls)==24
            return [(1200,),(200,),None,None, None,None,None,(C,),
                    (900,),None,None,None, None,None,(10**17,),None,
                    (2*10**17,),(10**16,),None,None, None,None,None,(ZERO,)]
    monkeypatch.setattr(collect,'Reader',Reader)
    configs=collect.observed_configs(1,local,[])
    assert [(c['performanceFee'],c['managementFee']) for c in configs]==[(1200,200),(1500,100),(900,0),(1000,0),(2000,100),(0,0)]
    assert [c['contractFamily'] for c in configs]==['v2','v3_allocator','tokenized_strategy','morpho-v1','morpho-v2','v3_allocator']
    rows=[{'chain_id':1,'vault':a,'version':'v3','tvl_usd':100,'external_tvl_usd':100} for a in addresses[1:3]]
    result=fee_stack(rows,[{'chain_id':1,'parent':addresses[1],'child':addresses[2],'strategy':addresses[2],'status':'ok','debt_usd':100}],{'vaults':configs})
    root=result['chains'][0]['root']
    assert root['vault']['contractFamily']=='v3_allocator'
    assert root['children'][0]['vault']['contractFamily']=='tokenized_strategy'


def test_membership_multicall_offsets_keep_zero_and_unavailable(monkeypatch):
    from yearn_data import analytics_collect as collect
    class Reader:
        block={'number':100,'timestamp':200}
        def __init__(self,chain):pass
        def call(self,calls):
            if not calls:return []
            if all(c[1] in ('token()','asset()') for c in calls):
                return [(C,) for _ in calls]
            assert all(c[1]=='totalAssets()' for c in calls)
            return [(100,),(0,),None]
    monkeypatch.setattr(collect,'Reader',Reader)
    candidates=[{'chainId':10,'address':a,'asset':{'address':C},'strategiesCount':0} for a in (A,B,C)]
    policy={'blacklist':set(),'v1':set(),'cross':{},'owners':{},'explicit':{},'factories':[]}
    result=collect.membership_chain(10,[{'address':a} for a in (A,B,C)],candidates,policy,{})
    assert [r['included'] for r in result]==[True,False,None]


def test_analytics_requires_prepared_selection_before_http_start(tmp_path, monkeypatch):
    from yearn_data import pairing
    database=tmp_path/'empty.sqlite';connect(database).close()
    monkeypatch.setattr(pairing,'PairingStore',lambda path:SimpleNamespace(get=lambda:SimpleNamespace(database=database)))
    with pytest.raises(ValueError,match='analytics have not been published'):
        pairing.serve_pairing('unused',analytics=True)


def test_comparison_unknown_chain_reconciles_and_matches_retired_label():
    chain=123456
    rows=[{'chain_id':chain,'vault':A,'tvl_usd':40,'external_tvl_usd':30},
          {'chain_id':1,'vault':B,'tvl_usd':20,'external_tvl_usd':20}]
    tvl=SimpleNamespace(id='a'*64,catalog={(chain,A):{'active':False}},
        summary=lambda:{'retiredVaultTvl':40,'overlapExcluded':10,'vaultBridgeExcluded':0,
                        'tvlByChain':{'Chain 123456':40,'Ethereum':20}})
    result=owned_comparison(tvl,rows,[])
    assert sum(r['ours'] for r in result['byChain'])==result['ourTotal']==50
    assert next(r for r in result['byChain'] if r['chain']=='Chain 123456')['ours']==30
    assert result['retiredTvlByChain']['Chain 123456']==40


def test_trend_requires_capital_history_for_each_periods_events():
    events=[{'chain_id':1,'vault_address':A,'block_timestamp':day*DAY,
             'total_fees_paid_usd':10,'gross_gain_usd':100} for day in (40,120,200,300)]
    financial=SimpleNamespace(fees=events,earnings=events,vault_names={})
    current=[{'chain_id':1,'vault':A,'tvl_usd':10000}]
    # Enough annual coverage remains, but the recent half lacks capital at day 200.
    incomplete={(1,A):[(d*DAY+DAY-1,10000) for d in range(365) if d<183 or d>=253]}
    vault=profitability(financial,current,incomplete,{},YEAR)['vaults'][0]
    assert vault['feeYield'] is not None
    assert vault['previousPeriodFeeYield'] is not None
    assert vault['currentPeriodFeeYield'] is None
    assert vault['trend']=='insufficient_data' and vault['trendDelta'] is None
    complete={(1,A):[(d*DAY+DAY-1,10000) for d in range(365)]}
    vault=profitability(financial,current,complete,{},YEAR)['vaults'][0]
    assert vault['previousPeriodFeeYield'] is not None and vault['currentPeriodFeeYield'] is not None
    assert vault['trend']=='stable'
    # The same guard applies to the first half, and to gain evidence as well.
    financial.fees=events[1:]
    first_half_gap={(1,A):[(d*DAY+DAY-1,10000) for d in range(70,365)]}
    assert profitability(financial,current,first_half_gap,{},YEAR)['vaults'][0]['previousPeriodFeeYield'] is None


def test_explicit_none_chain_filter_is_rejected():
    from yearn_data.analytics import analytics_response
    store=SimpleNamespace(get=lambda identity:{'meta':{'publicationId':'a'*64},'profitability':{'None':{'scope':'all'},'1':{'scope':'ethereum'}}})
    assert analytics_response(store,'/api/profitability')[1]['scope']=='all'
    assert analytics_response(store,'/api/profitability?chainId=1')[1]['scope']=='ethereum'
    for value in ('None','null','','0','-1'):
        assert analytics_response(store,'/api/profitability?chainId='+value)[0]==400
