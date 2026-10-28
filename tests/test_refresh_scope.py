"""Closed bounds and missing evidence cannot be inferred from activity or price zero."""
from contextlib import closing
from dataclasses import replace
import json
from types import SimpleNamespace

from hexbytes import HexBytes
import pytest

from yearn_data.config import CHAINS
from yearn_data.coverage import Scope,commit_range
from yearn_data.history_sources import HistoricalSource
from yearn_data.refresh_scope import RefreshOptions,context,resolve_bounds,verify_bounds,tvl_plan
from yearn_data.refresh_state import sql_digest
from yearn_data.storage import connect,init_db
from test_tvl_api import DAY,PARENT,run


@pytest.fixture
def db(postgres_database):
    with closing(connect('staging')) as conn:
        init_db(conn)
        yield conn


class Source(HistoricalSource):
    def __init__(self,chain='eth',head=1000,watermark=200):
        self.changed=False
        self.head=head
        self.watermark=watermark
        def block(number):
            number=self.head if number=='finalized' else number
            return dict(number=number,timestamp=number*1000,
                        hash=HexBytes('0x'+('ff' if self.changed else 'ab')*32))
        super().__init__(chain,SimpleNamespace(eth=SimpleNamespace(chain_id=CHAINS[chain].chain_id,
            block_number=1000,get_block=block)),lambda *a:{'chain_metadata':[{'latest_processed_block':self.watermark}]})


def test_resolve_retained_scope_strict_cutoff_watermark_and_changed_pin(db,tmp_path):
    commit_range(db,Scope(1,'vault','reports'),10,20,source='rpc',end_hash='known',run_id=None,write=lambda c:0)
    options=RefreshOptions('staging',('eth',),172800,tmp_path,source='envio')
    source=Source()
    bounds=resolve_bounds(db,options,'eth',source)
    assert (bounds['fromBlock'],bounds['toBlock'],bounds['startReason'])==(10,172,'retained-coverage')
    assert bounds['pin']['timestamp']==172000 and bounds['cutoffReached'] is True
    verify_bounds(options,'eth',bounds,source)
    source.watermark=100
    with pytest.raises(ValueError,match='watermark'):verify_bounds(options,'eth',bounds,source)
    source.watermark=200;source.changed=True
    with pytest.raises(ValueError,match='hash changed'):verify_bounds(options,'eth',bounds,source)
    assert db.execute('SELECT count(*) FROM history_coverage').fetchone()[0]==1


def test_initial_scope_requires_explicit_bounds_and_records_finality_lag(db,tmp_path):
    options=RefreshOptions('staging',('eth',),172800,tmp_path)
    with pytest.raises(ValueError,match='initial report scope'):resolve_bounds(db,options,'eth',Source())
    options=replace(options,from_blocks={'eth':0})
    bounds=resolve_bounds(db,options,'eth',Source(head=100))
    assert bounds['cutoffReached'] is False and bounds['toBlock']==100
    alternate=replace(options,confirmations={'eth':10})
    assert resolve_bounds(db,alternate,'eth',Source())['pin']['finality']=='confirmations:10'
    wrong=Source();wrong.w3.eth.chain_id=10
    with pytest.raises(ValueError,match='chain mismatch'):resolve_bounds(db,options,'eth',wrong)


def test_daily_plan_preserves_zero_and_lists_missing_chain_closes_and_price_repairs(db,tmp_path):
    for chain in (1,10):
        db.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES(?,'v3',?,1)",(chain,PARENT))
    db.commit()
    run(db,1,DAY,[(PARENT,'100')])
    run(db,2,DAY+2*86400,[(PARENT,None)])
    zero=dict(chain_id=10,vault=PARENT,timestamp=DAY,block_number=1,asset_units='0',tvl_usd='0',status='ok')
    with db:db.execute('INSERT INTO tvl_snapshots VALUES(1,10,?,?,1,?)',(PARENT,DAY,json.dumps(zero)))
    options=RefreshOptions('staging',('eth','op'),DAY+2*86400+1,tmp_path,first_close=DAY)
    plan=tvl_plan(db,options)
    assert plan['targets']==[(1,DAY+86400),(10,DAY+86400),(10,DAY+2*86400)]
    assert plan['deferredRepairs']==[dict(chainId=1,timestamp=DAY+2*86400,reason='price-or-ownership-unavailable')]
    forced=tvl_plan(db,replace(options,repair_history=True))
    assert len(forced['targets'])==6
    assert forced['unresolvedTargets']==plan['targets']
    assert forced['deferredRepairs']==plan['deferredRepairs']
    with db:
        db.execute("UPDATE tvl_snapshots SET data_json=? WHERE chain_id=1 AND timestamp=?",(
            json.dumps(dict(chain_id=1,vault=PARENT,timestamp=DAY+2*86400,asset_units=None,tvl_usd=None)),DAY+2*86400))
    assert (1,DAY+2*86400) in tvl_plan(db,options)['targets']


def test_legacy_ownership_without_debt_value_remains_a_repair(db,tmp_path):
    db.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES(1,'v3',?,1)",(PARENT,));db.commit()
    run(db,1,DAY,[(PARENT,'100')])
    point=dict(chain_id=1,parent=PARENT,strategy='strategy',child='child',timestamp=DAY,status='ok',owned_usd='10',debt_usd=None)
    with db:db.execute('INSERT INTO tvl_positions VALUES(1,1,?,?,?,?,?)',(PARENT,'strategy','child',DAY,json.dumps(point)))
    options=RefreshOptions('staging',('eth',),DAY+1,tmp_path,first_close=DAY)
    assert tvl_plan(db,options)['deferredRepairs']==[dict(chainId=1,timestamp=DAY,reason='price-or-ownership-unavailable')]


def test_checkpoint_configuration_keeps_secrets_and_paths_out_of_receipts(monkeypatch,tmp_path):
    monkeypatch.setenv('ETH_RPC_URL','https://private.invalid/secret-key')
    options=RefreshOptions('staging',('ethereum',),172800,tmp_path)
    captured=context(options)
    assert captured['chains']==['eth']
    assert 'private.invalid' not in json.dumps(captured) and str(tmp_path) not in json.dumps(captured)
    monkeypatch.setenv('ETH_RPC_URL','https://private.invalid/rotated-key')
    assert context(options)['configurationId']!=captured['configurationId']
    for changes in (dict(chains=()),dict(max_targets=0),dict(confirmations={'eth':0}),dict(first_close=100)):
        with pytest.raises(ValueError):replace(options,**changes)


def test_server_digest_detects_value_corrections_independent_of_row_order(db):
    db.executescript('CREATE TABLE refresh_hash_fixture(id INTEGER PRIMARY KEY,value TEXT);')
    with db:db.executemany('INSERT INTO refresh_hash_fixture VALUES(?,?)',[(1,'one'),(2,'two')])
    ascending=sql_digest(db,'SELECT * FROM refresh_hash_fixture ORDER BY id')
    assert ascending==sql_digest(db,'SELECT * FROM refresh_hash_fixture ORDER BY id DESC')
    with db:db.execute("UPDATE refresh_hash_fixture SET value='corrected' WHERE id=1")
    assert ascending!=sql_digest(db,'SELECT * FROM refresh_hash_fixture ORDER BY id')
