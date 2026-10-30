"""Curation event completeness and recovery at the source/persistence boundary."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from web3 import Web3
from yearn_data.curation_events import CurationEvents, CurationUnavailable, SCOPE
from yearn_data.storage import connect, init_db
from yearn_data.tvl_discovery import DiscoveryReader, FACTORY_EVENTS, CURATION_REGISTRY, scan_factory, owners_for_chain

CHAIN=8453
FACTORY='0xA9c3D3a366466Fa809d1Ae982Fb2c46E5fC41101'
START=52918927
END=53221326
OWNER='0x518C21DC88D9780c0A1Be566433c571461A70149'
VAULT='0x1111111111111111111111111111111111111111'
ASSET='0x2222222222222222222222222222222222222222'
HASH='0x'+'ab'*32
TX='0x'+'cd'*32
ABI=FACTORY_EVENTS['morpho-v1']


def header(number):
    return {'number':number,'hash':bytes.fromhex(HASH[2:]),'timestamp':12345}


def event(identity='a', index=1):
    return {'id':identity,'chainId':CHAIN,'factoryAddress':Web3.to_checksum_address(FACTORY),'vaultVersion':1,
            'blockNumber':START+1,'blockTimestamp':12345,'blockHash':HASH,'transactionHash':TX,'logIndex':index,
            'argsJson':json.dumps({'metaMorpho':VAULT,'caller':OWNER,'initialOwner':OWNER,'initialTimelock':str(2**100),
                                  'asset':ASSET,'name':'Yearn candidate','symbol':'yc','salt':'0x'+'00'*32})}


class Graphql:
    def __init__(self,pages=None):
        self.state={'chainId':CHAIN,'scopeHash':SCOPE['scopeHash'],'fromBlock':13978134,'throughBlock':END+1000}
        self.pages=pages if pages is not None else [[event()],[]]
        self.anchor={'chain_id':CHAIN,'block_number':END+500,'block_hash':HASH}
        self.calls=[]
    def __call__(self,q,v,**kwargs):
        self.calls.append((q,v))
        if 'CurationIndexingState_by_pk' in q:
            return {'CurationIndexingState_by_pk':self.state,'chain_metadata':[{'latest_processed_block':END+1000}]}
        if 'raw_events' in q:return {'raw_events':[self.anchor] if self.anchor else []}
        if 'CurationVault_by_pk' in q:return {'CurationVault_by_pk':{'chainId':CHAIN,'address':VAULT,'vaultVersion':2,'createdBlock':START}}
        result=self.pages.pop(0)
        if isinstance(result,Exception):raise result
        return {'CurationFactoryEvent' if 'CurationFactoryEvent(' in q else 'MorphoGovernanceEvent':result}


def reader(gql, rpc=None):
    r=object.__new__(DiscoveryReader)
    r.head={'number':END+2000}
    r.curation_events=CurationEvents(CHAIN,header,gql)
    r.block=header
    r.w3=SimpleNamespace(codec=Web3().codec,eth=SimpleNamespace(get_logs=rpc or (lambda args:[]),get_block=header))
    return r


def test_indexed_factory_paginates_short_pages_through_terminal_empty_without_rpc():
    gql=Graphql([[event('a',1)],[event('b',2)],[]])
    source=CurationEvents(CHAIN,header,gql)
    rows=source.logs(FACTORY,ABI,START,END)
    assert [r['logIndex'] for r in rows]==[1,2]
    assert rows[0]['args']['initialTimelock']==2**100
    pages=[v for q,v in gql.calls if 'CurationFactoryEvent(' in q]
    assert [p['cursor'] for p in pages]==['','a','b']
    assert pages[0]['addresses']==[Web3.to_checksum_address(FACTORY),FACTORY.lower()]
    assert source.sources=={'envio':1,'rpc':0}


@pytest.mark.parametrize('fault', ['missing','scope','lag','anchor','contract','duplicate','reorg','wrong-chain','partial','bad-amount'])
def test_incomplete_or_invalid_indexed_results_fall_back_without_certifying_partial_data(fault):
    gql=Graphql();address=FACTORY;row=event()
    if fault=='missing':gql.state=None
    elif fault=='scope':gql.state['scopeHash']='stale'
    elif fault=='lag':gql.state['throughBlock']=END-1
    elif fault=='anchor':gql.anchor=None
    elif fault=='contract':address=VAULT
    elif fault=='duplicate':gql.pages=[[row,event('b',1)],[]]
    elif fault=='reorg':row['blockHash']='0x'+'ee'*32;gql.pages=[[row],[]]
    elif fault=='wrong-chain':row['chainId']=1;gql.pages=[[row],[]]
    elif fault=='partial':gql.pages=[[row],RuntimeError('secret provider URL')]
    elif fault=='bad-amount':
        args=json.loads(row['argsJson']);args['initialTimelock']=True;row['argsJson']=json.dumps(args);gql.pages=[[row],[]]
    calls=[]
    def rpc(args):calls.append(args);return []
    r=reader(gql,rpc)
    assert r.logs(address,ABI,START,END)==[]
    assert calls[0]['fromBlock']==START and calls[-1]['toBlock']==END
    assert sum(x['toBlock']-x['fromBlock']+1 for x in calls)==END-START+1
    assert r.curation_events.sources=={'envio':0,'rpc':1}
    assert 'secret' not in json.dumps(r.curation_events.failures)


def test_dynamic_vault_adapter_empty_range_requires_registered_creation_and_complete_coverage():
    gql=Graphql([[]]);source=CurationEvents(CHAIN,header,gql)
    from yearn_data.tvl_discovery import ADD_ADAPTER_EVENT
    assert source.logs(VAULT,ADD_ADAPTER_EVENT,START,END)==[]
    assert any('CurationVault_by_pk' in q for q,v in gql.calls)
    gql=Graphql([[]]);gql.anchor['block_hash']='0x'+'ef'*32
    with pytest.raises(CurationUnavailable,match='canonical'):
        CurationEvents(CHAIN,header,gql).logs(VAULT,ADD_ADAPTER_EVENT,START,END)
    # Reuse of a previously checked anchor must still detect a later fork.
    gql=Graphql([[],[],[]]);gql.anchor['block_number']=str(END+500)
    source=CurationEvents(CHAIN,header,gql)
    assert source.logs(VAULT,ADD_ADAPTER_EVENT,START,END)==[]
    assert source.logs(VAULT,ADD_ADAPTER_EVENT,START,END)==[]
    assert len([q for q,v in gql.calls if 'raw_events' in q])==1
    source.anchor_block=lambda n:dict(header(n),hash=bytes.fromhex('ef'*32))
    with pytest.raises(CurationUnavailable,match='canonical'):
        source.logs(VAULT,ADD_ADAPTER_EVENT,START,END)


def test_forced_rpc_reads_are_distinct_from_no_reads_in_source_receipt(monkeypatch):
    from yearn_data.tvl_discovery import event_read_summary
    monkeypatch.setenv('YEARN_CURATION_EVENT_SOURCE','rpc')
    gql=Graphql();calls=[];r=reader(gql,lambda args:calls.append(args) or [])
    assert event_read_summary({CHAIN:r})['event_sources']=={'envio':0,'rpc':0}
    assert r.logs(FACTORY,ABI,START,START+1)==[]
    summary=event_read_summary({CHAIN:r})
    assert summary=={'event_sources':{'envio':0,'rpc':1},'event_fallbacks':{}}
    assert len(calls)==1 and gql.calls==[]


def test_scope_manifest_inventory_and_global_owner_include_verified_missing_products():
    payload={k:v for k,v in SCOPE.items() if k!='scopeHash'}
    assert hashlib.sha256(json.dumps(payload,separators=(',',':')).encode()).hexdigest()==SCOPE['scopeHash']
    known={(v['chain_id'],v['address'].lower()) for v in CURATION_REGISTRY['known_vaults']}
    assert (999,'0xe90959cbe7e56b5ebff9ad12de611a4976f2d2b1') in known
    assert (4663,'0x8c258f25fc6fbe3f01ccc828e2c3840a46a4a9a2') in known
    assert all(OWNER.lower() in owners_for_chain(chain) for chain in [1,10,999,4663,8453,42161,747474,137])
    assert {f['chain_id'] for f in CURATION_REGISTRY['factories']}=={1,10,999,4663,8453,42161,747474}


def test_indexed_events_and_rpc_recovery_preserve_atomic_factory_coverage(postgres_database):
    with connect('staging') as c:init_db(c)
    c=connect('staging')
    try:
        factory={'chain_id':CHAIN,'address':FACTORY,'family':'morpho-v1','from_block':START}
        gql=Graphql();r=reader(gql)
        found=scan_factory(c,factory,r,from_block=START,to_block=END,chunk_size=END-START+1)
        assert [(v['address'].lower(),v['deployment_block']) for v in found]==[(VAULT,START+1)]
        coverage=c.execute('SELECT row_count,source FROM tvl_history_coverage').fetchone()
        assert tuple(coverage)==(1,'envio')
        assert scan_factory(c,factory,r,from_block=START,to_block=END,chunk_size=END-START+1)==found
        assert len([q for q,v in gql.calls if 'CurationFactoryEvent(' in q])==2
        # Neither a partial indexed page nor failed fallback may advance coverage.
        c.execute('DELETE FROM tvl_history_coverage');c.execute('DELETE FROM tvl_inventory_events');c.commit()
        gql=Graphql([[event()],RuntimeError('provider unavailable')])
        def broken(args):raise RuntimeError('RPC unavailable')
        with pytest.raises(RuntimeError,match='RPC unavailable'):
            scan_factory(c,factory,reader(gql,broken),from_block=START,to_block=END,chunk_size=END-START+1)
        assert c.execute('SELECT count(*) FROM tvl_history_coverage').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM tvl_inventory_events').fetchone()[0]==0
        # Reorg between acquisition and atomic commit is also retryable.
        r=reader(Graphql())
        r.verify_block=lambda number,expected:(_ for _ in ()).throw(ValueError('changed before commit'))
        with pytest.raises(ValueError,match='changed before commit'):
            scan_factory(c,factory,r,from_block=START,to_block=END,chunk_size=END-START+1)
        assert c.execute('SELECT count(*) FROM tvl_history_coverage').fetchone()[0]==0
        # Old/missing Envio deployment must still ingest positive RPC events.
        from eth_abi import encode
        from eth_utils import event_abi_to_log_topic
        raw={'address':Web3.to_checksum_address(FACTORY),'blockNumber':START+1,
             'blockHash':bytes.fromhex(HASH[2:]),'transactionHash':bytes.fromhex(TX[2:]),
             'transactionIndex':0,'logIndex':1,
             'topics':[event_abi_to_log_topic(ABI),encode(['address'],[VAULT]),
                       encode(['address'],[OWNER]),encode(['address'],[ASSET])],
             'data':encode(['address','uint256','string','string','bytes32'],
                           [OWNER,2**100,'Yearn candidate','yc',bytes(32)])}
        def rpc(args):return [raw] if args['fromBlock']<=START+1<=args['toBlock'] else []
        gql=Graphql();gql.state=None
        r=reader(gql,rpc)
        assert scan_factory(c,factory,r,from_block=START,to_block=END,chunk_size=END-START+1)
        assert tuple(c.execute('SELECT row_count,source FROM tvl_history_coverage').fetchone())==(1,'rpc')
    finally:c.close()


@pytest.mark.parametrize('mode',['rpc','RPC','rpc '])
def test_rpc_override_normalizes_case_and_whitespace(monkeypatch,mode):
    monkeypatch.setenv('YEARN_CURATION_EVENT_SOURCE',mode)
    r=reader(Graphql([AssertionError('indexer must not be queried')]))
    assert r.event_chunk_size(FACTORY,ABI,START,END,100)==100
    assert r.logs(FACTORY,ABI,START,END)==[]
    assert r.last_event_source=='rpc'


def test_invalid_source_override_fails_before_query(monkeypatch):
    monkeypatch.setenv('YEARN_CURATION_EVENT_SOURCE','rpcc')
    r=reader(Graphql([AssertionError('indexer must not be queried')]))
    with pytest.raises(ValueError,match='must be auto or rpc'):
        r.event_chunk_size(FACTORY,ABI,START,END,100)
    with pytest.raises(ValueError,match='must be auto or rpc'):
        r.logs(FACTORY,ABI,START,END)


def test_complete_busy_indexed_factory_window_is_not_a_truncated_rpc_result(postgres_database):
    from contextlib import closing
    pages=[]
    for start in range(0,10000,500):
        page=[]
        for i in range(start,start+500):
            row=event(f'{i:05d}',i)
            args=json.loads(row['argsJson']);args['initialOwner']='0x'+'99'*20
            row['argsJson']=json.dumps(args);page.append(row)
        pages.append(page)
    pages.append([])
    gql=Graphql(pages);r=reader(gql)
    factory=dict(chain_id=CHAIN,address=FACTORY,family='morpho-v1',from_block=START)
    with closing(connect('staging')) as conn:
        init_db(conn)
        assert scan_factory(conn,factory,r,from_block=START,to_block=END)==[]
        assert tuple(conn.execute('SELECT source,row_count FROM tvl_history_coverage').fetchone())==('envio',0)
    assert sum('CurationFactoryEvent(' in q for q,v in gql.calls)==21
