"""Offline adapter accounting and metadata coverage acceptance fixtures."""
from pathlib import Path

import pytest

from yearn_data import analytics_collect as collect
from yearn_data.analytics import comparable

A='0x'+'11'*20;B='0x'+'22'*20;C='0x'+'33'*20
D='0x'+'44'*20;E='0x'+'55'*20;ZERO='0x'+'00'*20


def policy():
    return {'v1':set(),'blacklist':set(),'cross':{},'owners':{},'explicit':{},'factories':[]}


@pytest.mark.parametrize('count,expected', [('missing',None),(None,None),(-1,None),('invalid',None),(0,True),('0',True)])
def test_missing_strategy_count_is_not_an_observed_zero(monkeypatch,count,expected):
    class Reader:
        block={'number':1,'timestamp':2}
        def __init__(self,chain):pass
        def call(self,calls):
            results=[]
            for address,signature,*_ in calls:
                if signature=='totalAssets()':results.append((100,))
                elif signature in ('asset()','token()'):results.append((D,))
                else:raise AssertionError('No queue read should be inferred: '+signature)
            return results
    monkeypatch.setattr(collect,'Reader',Reader)
    candidate={'chainId':10,'address':A,'asset':{'address':D},'apiVersion':'3.0.4'}
    if count!='missing':candidate['strategiesCount']=count
    result=collect.membership_chain(10,[{'address':A}],[candidate],policy(),{})[0]
    assert result['included'] is expected
    if expected is None:assert result['reason']=='strategy count unavailable'
    else:assert result['evidence']['strategyDebts']==[]


@pytest.mark.parametrize('version', ['3.0.4','0.4.6'])
@pytest.mark.parametrize('cross_asset', [False,True])
def test_adapter_queues_decode_and_deduct_nested_debt(monkeypatch,version,cross_asset):
    v3=version.startswith('3');strategy=C if cross_asset else B
    calls_seen=[]
    class Reader:
        block={'number':1,'timestamp':2}
        def __init__(self,chain):pass
        def call(self,calls):
            calls_seen.extend(calls);result=[]
            for address,signature,types,args,outputs in calls:
                if signature=='totalAssets()':value=(100,)
                elif signature in ('asset()','token()'):value=(E if address==B and cross_asset else D,)
                elif signature=='get_default_queue()':
                    assert v3 and address==A;value=([strategy],)
                elif signature=='withdrawalQueue(uint256)':
                    assert not v3 and address==A;value=(strategy if args[0]==0 else ZERO,)
                elif signature=='strategies(address)':
                    assert address==A and args==[strategy]
                    fields=[7]*(4 if v3 else 9);fields[2 if v3 else 6]=100;value=tuple(fields)
                else:raise AssertionError(signature)
                result.append(value)
            return result
    monkeypatch.setattr(collect,'Reader',Reader)
    candidates=[{'chainId':10,'address':a,'asset':{'address':E if a==B and cross_asset else D},
                 'apiVersion':version,'strategiesCount':2 if a==A else 0} for a in (A,B)]
    rules=policy()
    if cross_asset:rules['cross'][(10,A,C)]=B
    result=collect.membership_chain(10,[{'address':A}],candidates,rules,{})[0]
    assert result['included'] is False
    assert result['evidence']['strategyDebts']==[(strategy,100)]
    queues=[c for c in calls_seen if c[1] in ('get_default_queue()','withdrawalQueue(uint256)')]
    assert len(queues)==(1 if v3 else 2)


@pytest.mark.parametrize('supply,pps,expected', [(1,1,True),(0,1,False),(1,0,False),(None,1,None)])
def test_v1_supply_and_pps_membership(monkeypatch,supply,pps,expected):
    class Reader:
        block={'number':1,'timestamp':2}
        def __init__(self,chain):pass
        def call(self,calls):
            values={'totalSupply()':None if supply is None else (supply,),
                    'getPricePerFullShare()':(pps,)}
            return [values[c[1]] for c in calls]
    monkeypatch.setattr(collect,'Reader',Reader)
    rules=policy();rules['v1']={A};rules['explicit']={1:set()}
    result=collect.membership_chain(1,[{'address':A,'version':'v1'}],[],rules,{})[0]
    assert result['included'] is expected
    assert result['reason']=='v1 onchain supply and PPS'


def test_membership_identity_includes_chain(monkeypatch):
    class Reader:
        block={'number':1,'timestamp':2}
        def __init__(self,chain):pass
        def call(self,calls):return [(1,) for _ in calls]
    monkeypatch.setattr(collect,'Reader',Reader)
    rules=policy();rules['v1']={A};rules['explicit']={1:set()}
    observed=[]
    for chain in (1,10):observed+=collect.membership_chain(chain,[{'address':A,'version':'v1'}],[],rules,{})
    rows=[{'chain_id':chain,'vault':A,'external_tvl_usd':100} for chain in (1,10)]
    result=comparable(rows,{'vaults':observed})
    assert [v['chainId'] for v in result['includedVaults']]==[1]
    assert [v['chainId'] for v in result['diff']['missingFromDefillama']]==[10]


def test_factory_source_parser_fails_explicitly_on_unsupported_shape():
    source=(Path(__file__).parent/'fixtures/analytics/curation-factories.js').read_text()
    factories=collect.curator_factories(source)
    assert len(factories)==12
    assert {r['chain_id'] for r in factories}==set(collect.CURATION_CHAINS)
    assert {r['family'] for r in factories}=={'morpho-v1','morpho-v2'}
    for broken in (source.replace('MorphoConfigs','OtherConfigs'),
                   source.replace('hyperliquid:', 'missing_chain:'),
                   source.replace('\n  ', '\n    '),
                   source.replace('fromBlock:', 'startBlock:'),
                   source.replace('vaultFactories:', 'vaultFactories: load([')):
        with pytest.raises(ValueError,match='unsupported curation factory'):
            collect.curator_factories(broken)
