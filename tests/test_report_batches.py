import json
"""Native batching preserves emitters, monetary fields and durable coverage."""
from contextlib import closing
from threading import Event, Lock, get_ident

import pytest
import requests
from hexbytes import HexBytes
from web3 import Web3

from yearn_data import catchup
from yearn_data.coverage import commit_range
from yearn_data.history_sources import HistoricalSource, report_scope
from yearn_data.report_windows import read_windows
from yearn_data.storage import connect, init_db
from test_catchup import Source, seed, VAULT, NEW, ASSET, HASH
from test_history_sources import client, TX
from test_catchup_regressions import report_log


def test_batched_reports_match_single_vault_rows_and_partial_empty_coverage(postgres_database):
    class Batch(Source):
        batch_reports_supported=True
        def __init__(self):super().__init__();self.groups=[]
        def reports_many(self,members,lo,hi,source):
            self.groups.append(([v['address'] for v in members],lo,hi))
            return [r for v in members for r in self.reports(v,lo,hi,source)]
    with closing(connect('staging')) as conn:
        init_db(conn)
        for address in (VAULT,NEW,'0x'+'44'*20):seed(conn,address)
        results=[]
        for adapter in (Source(),Batch()):
            with conn:
                for table in ('history_coverage','strategy_reports','events_raw'):conn.execute(f'DELETE FROM {table}')
            vault=dict(conn.execute('SELECT * FROM vaults WHERE address=?',(VAULT,)).fetchone())
            commit_range(conn,report_scope(vault),10,19,source='rpc',end_hash=HASH,run_id=None,write=lambda c:0)
            result=catchup.catch_up(conn,'op',10,39,source='rpc',chunk_size=10,client=adapter)
            assert result['inserted_rows']==3 and result['completed_ranges']==8
            assert {r[0] for r in conn.execute('SELECT gain_raw FROM strategy_reports')}=={'12'}
            results.append(([tuple(r) for r in conn.execute('SELECT * FROM strategy_reports ORDER BY tx_hash')],
                [tuple(r) for r in conn.execute('SELECT target,policy,from_block,to_block,end_hash,row_count FROM history_coverage ORDER BY target,from_block')]))
            assert catchup.catch_up(conn,'op',10,39,source='rpc',chunk_size=10,client=adapter)['completed_ranges']==0
        assert results[0]==results[1]
        assert {lo:len(members) for members,lo,hi in adapter.groups}=={10:2,20:3,30:3}


def test_failed_or_invalid_batch_cannot_certify_any_member(postgres_database):
    class Batch(Source):
        batch_reports_supported=True
        mode='failed'
        changed=False
        def reports_many(self,members,lo,hi,source):
            rows=self.reports(members[0],lo,hi,source)
            if self.mode=='failed':raise RuntimeError('provider unavailable after first member')
            if self.mode=='invalid':return [tuple([999]+list(rows[0][1:]))]
            self.changed=True
            return rows
        def verify_pin(self,pin):
            if self.changed:raise ValueError('pinned block hash changed')
            return super().verify_pin(pin)
    with closing(connect('staging')) as conn:
        init_db(conn);seed(conn);seed(conn,NEW)
        adapter=Batch()
        for mode in ('failed','invalid','reorg'):
            adapter.mode=mode
            progress=[]
            with pytest.raises((RuntimeError,ValueError)):
                catchup.catch_up(conn,'op',20,29,client=adapter,progress=progress.append)
            for event in ('range-started','range-failed'):
                records=[json.loads(p) for p in progress if json.loads(p)['event']==event]
                targets={p['target'] for p in records}
                assert targets==({VAULT} if mode=='reorg' and event=='range-failed' else {VAULT,NEW})
                assert all(p['from_block']==20 and p['to_block']==29 for p in records)
            assert conn.execute('SELECT count(*) FROM history_coverage').fetchone()[0]==0
            assert conn.execute('SELECT count(*) FROM strategy_reports').fetchone()[0]==0


def test_envio_batch_paginates_mixed_case_emitters_and_keeps_per_vault_assets():
    addresses=[Web3.to_checksum_address('0x'+b*20) for b in ('ab','cd')]
    assets=[ASSET,'0x'+'55'*20]
    nodes=[dict(chainId=1,vaultAddress=a if i else a.lower(),blockNumber=20,logIndex=i,
        blockHash=HASH,blockTimestamp=1000,transactionHash=TX,strategy=ASSET,
        gain='12',loss='0',current_debt='100',protocol_fees='1',total_fees='2',total_refunds='0') for i,a in enumerate(addresses)]
    calls=[]
    def gql(query,variables):
        calls.append(variables)
        remaining=[n for n in nodes if (20,n['logIndex'])>(variables['lb'],variables['li'])]
        return {'StrategyReported':remaining[:1]}
    rpc=client(1)
    rpc.eth.get_block=lambda n:dict(number=n,hash=HexBytes(HASH),timestamp=1000)
    source=HistoricalSource('eth',rpc,gql)
    members=[dict(address=a,asset=asset,asset_decimals=6,version='v3') for a,asset in zip(addresses,assets)]
    rows=source.reports_many(members,20,20,'envio')
    assert len(calls)==3 and len(rows)==2
    assert all(set(c['addresses'])==set(addresses+[a.lower() for a in addresses]) for c in calls)
    assert [(r[2],r[8],r[10]) for r in rows]==[(a,Web3.to_checksum_address(asset),'12') for a,asset in zip(addresses,assets)]
    nodes[1]['vaultAddress']='0x'+'77'*20
    with pytest.raises(ValueError,match='scope mismatch'):source.reports_many(members,20,20,'envio')


def test_rpc_batch_uses_address_list_and_rejects_unrequested_emitters():
    logs=[report_log(),dict(report_log(),address=NEW,logIndex=1)]
    rpc=client(10,logs);requests_seen=[]
    rpc.eth.get_block=lambda n:dict(number=n,hash=HexBytes(HASH),timestamp=1000)
    rpc.eth.get_logs=lambda params:requests_seen.append(params) or logs
    source=HistoricalSource('op',rpc)
    members=[dict(address=a,asset=ASSET,asset_decimals=6,version='v2') for a in (VAULT,NEW)]
    rows=source.reports_many(members,20,29,'rpc')
    assert requests_seen[0]['address']==[VAULT,NEW]
    assert {r[2] for r in rows}=={VAULT,NEW}
    logs.append(dict(logs[0],address='0x'+'88'*20,logIndex=2))
    with pytest.raises(ValueError,match='scope mismatch'):source.reports_many(members,20,29,'rpc')
    fallback=[]
    def limited(params):
        address=params['address'];fallback.append(address)
        if isinstance(address,list):raise ValueError('too many addresses')
        return [log for log in logs if log['address']==address]
    rpc.eth.get_logs=limited
    assert len(source.reports_many(members,20,29,'rpc'))==2
    assert fallback==[[VAULT,NEW],VAULT,NEW]


def test_adaptive_windows_grow_split_limits_and_bound_timeouts(monkeypatch):
    calls=[]
    def read(first,last):calls.append((first,last));return []
    assert read_windows(read,0,99999,'rpc')==[]
    assert calls==[(0,9999),(10000,29999),(30000,69999),(70000,99999)]
    calls.clear()
    def limited(first,last):
        calls.append((first,last))
        if last-first>2:raise ValueError('too many results')
        return list(range(first,last+1))
    assert read_windows(limited,0,9,'rpc')==list(range(10))
    calls.clear()
    def timeout(first,last):calls.append((first,last));raise requests.Timeout('slow provider')
    with pytest.raises(requests.Timeout):read_windows(timeout,0,9999,'rpc')
    assert len(calls)==3
    calls.clear()
    def rejected(first,last):calls.append((first,last));raise RuntimeError('rate limit exceeded')
    with pytest.raises(RuntimeError):read_windows(rejected,0,9999,'rpc')
    assert len(calls)==1


def test_envio_gateway_retry_exhaustion_splits_with_bounded_timeout_depth(monkeypatch):
    from types import SimpleNamespace
    from yearn_data import envio
    responses=[];windows=[]
    monkeypatch.setenv('YEARN_ENVIO_GRAPHQL_URL','https://envio.invalid/graphql')
    monkeypatch.setattr(envio.time,'sleep',lambda _:None)
    def post(url,*,json,timeout):
        lo,hi=json['variables']['lo'],json['variables']['hi']
        responses.append((lo,hi))
        return SimpleNamespace(status_code=504 if hi-lo>=2 else 200,
            raise_for_status=lambda:None,json=lambda:{'data':{'rows':list(range(lo,hi+1))}})
    monkeypatch.setattr(envio.requests,'post',post)
    def fetch(lo,hi):
        windows.append((lo,hi))
        return envio._gql('query',{'lo':lo,'hi':hi},split_timeouts=True)['rows']
    assert read_windows(fetch,0,7,'envio')==list(range(8))
    assert windows==[(0,7),(0,3),(0,1),(2,3),(4,7),(4,5),(6,7)]
    assert responses.count((0,7))==5 and responses.count((0,3))==5
    windows.clear()
    with pytest.raises(RuntimeError,match='after retries'):
        read_windows(fetch,0,15,'envio')
    assert windows==[(0,15),(0,7),(0,3)]  # Two split levels, never unbounded retries.


def test_parallel_inventory_binds_each_registry_and_finishes_before_reports(postgres_database,monkeypatch):
    from yearn_data.history_sources import inventory_targets
    targets=list(inventory_targets('op'));third=Event();guard=Lock();finished=set();caller=get_ident()
    assert len(targets)>=3
    class Inventory(Source):
        def inventory(self,target,lo,hi,source):
            if target==targets[0]:assert third.wait(3),'inventory worker was not refilled'
            if target==targets[2]:third.set()
            result=super().inventory(target,lo,hi,source)
            with guard:finished.add(target.scope.key)
            return result
        def reports(self,*args):
            assert finished=={t.scope.key for t in targets}
            return super().reports(*args)
    with closing(connect('staging')) as conn:
        init_db(conn);seed(conn)
        execute=conn.execute
        def checked(*args,**kwargs):
            assert get_ident()==caller
            return execute(*args,**kwargs)
        monkeypatch.setattr(conn,'execute',checked)
        # The newly discovered vault has complete source metadata in this fixture.
        monkeypatch.setattr(catchup,'vault_metadata_from_kong',lambda *args:{Web3.to_checksum_address(NEW):dict(asset=ASSET,asset_decimals=6,api_version='0.4.6')})
        result=catchup.catch_up(conn,'op',10,30,workers=2,discover=True,client=Inventory())
        assert result['vaults']==2
        assert conn.execute('SELECT count(*) FROM strategy_reports').fetchone()[0]==2
        assert {tuple(r) for r in conn.execute("SELECT target,family FROM history_coverage WHERE family LIKE 'inventory:%'")}=={(t.scope.target.lower(),t.scope.family) for t in targets}
