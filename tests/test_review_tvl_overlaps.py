"""Exercise the documented CLI with a pinned fixture and offline archive reads."""
import json
from pathlib import Path
import runpy
import sys

import pytest

from test_tvl_api import source as source, run, DAY, PARENT
from yearn_data import config, tvl_sources
from yearn_data.tvl_api import TvlStore


@pytest.mark.parametrize('mode', ['observed-parent','unknown-parent','explicit-parent','getter-failed'])
def test_overlap_review_cli_handles_getter_route_without_source(source,tmp_path,monkeypatch,capsys,mode):
    conn,path,root=source
    # Use the actual corrected DAI -> Yearn Term USDS registry entry, which has
    # a getter but intentionally no sourceVaultAddress.
    route=next(dict(r) for r in tvl_sources.REGISTRY['targets']
               if r.get('vaultGetter') and not r.get('sourceVaultAddress'))
    child=route['targetVaultAddress'].lower()
    old=route['supersededTargetVaultAddresses'][0].lower()
    strategy=route['strategyAddress'].lower()
    for address in (child,old):
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,active,updated_at) VALUES (1,'v3',?,1,1)",(address,))
    run(conn,1,DAY,[(PARENT,'100'),(child,'100'),(old,'100')])
    if mode!='unknown-parent':
        position={'chain_id':1,'parent':PARENT,'strategy':strategy,'child':old,
                  'timestamp':DAY,'block_number':100+DAY,'method':'service-registry',
                  'status':'ok','debt_usd':'30','owned_usd':'80'}
        conn.execute('INSERT INTO tvl_positions VALUES (1,1,?,?,?,?,?)',
                     (PARENT,strategy,old,DAY,json.dumps(position)));conn.commit()
    if mode=='explicit-parent':route['sourceVaultAddress']=PARENT
    monkeypatch.setattr(tvl_sources,'REGISTRY',{'targets':[route],'crossChainStrategies':[]})
    calls=[]
    class Archive:
        def __init__(self,chain):assert chain==1
        def debt(self,parent,holder,version,block):
            assert (parent,holder,block)==(PARENT,strategy,100+DAY)
            return 3
        def uint(self,address,signature,block,*args):
            calls.append((address,signature,block,args))
            assert block==100+DAY
            if signature==route['vaultGetter']:
                assert address==strategy
                return int(old if mode=='getter-failed' else child,16)
            assert (address,signature,args)==(child,'balanceOf(address)',(strategy,))
            return 5
    monkeypatch.setattr(tvl_sources,'ArchiveReader',Archive)
    monkeypatch.setattr(config,'load_environment',lambda paths:None)
    dataset=TvlStore(path,root).get()
    original_positions=[tuple(r) for r in conn.execute('SELECT * FROM tvl_positions')]
    current=(root/'current.json').read_bytes()
    out=tmp_path/'review'
    monkeypatch.setattr(sys,'argv',['review_tvl_overlaps.py','--publication',str(root),
                                  '--out',str(out),'--env',str(tmp_path/'unused.env')])
    runpy.run_path(str(Path(__file__).parents[1]/'scripts/review_tvl_overlaps.py'),run_name='__main__')
    summary=json.loads(capsys.readouterr().out)
    validation=json.loads((out/'validation.json').read_text())
    graph=json.loads((out/'graph.json').read_text())
    assert summary['sourceDatasetId']==validation['sourceDatasetId']==dataset.id
    assert graph['datasetId']==validation['reviewDatasetId']
    from yearn_data.tvl_review import ReviewDataset
    saved=ReviewDataset.load(out/'frame.json')
    assert saved.id==graph['datasetId'] and saved.graph()==graph
    assert not list(out.glob('*.sqlite'))
    if mode=='unknown-parent':
        assert not validation['verifiedPositions'] and not calls
        assert validation['unresolvedCandidates']==[{
            'chainId':1,'strategyAddress':strategy,'targetVaultAddress':child,
            'reason':'source_parent_not_observed'}]
    else:
        assert not validation['unresolvedCandidates']
        assert len(validation['verifiedPositions'])==1
        edge=validation['verifiedPositions'][0]['edge']
        assert (edge['parent'],edge['strategy'],edge['child'])==(PARENT,strategy,child)
        assert edge['vault_getter']==route['vaultGetter']
        if mode=='getter-failed':
            assert edge['status']=='unavailable' and edge['owned_usd'] is None
            assert validation['after']['status']=='incomplete'
        else:
            assert edge['status']=='ok'
            assert validation['verifiedPositions'][0]['revisedOverlapUsd']=='30'
            assert validation['after']['status']=='complete'
            assert graph['diagnostics']['unresolvedPositions']==0
    assert [tuple(r) for r in conn.execute('SELECT * FROM tvl_positions')]==original_positions
    assert (root/'current.json').read_bytes()==current


@pytest.mark.parametrize('constant,group', [(False,'chain'),(False,'vault'),(True,'chain'),(True,'vault')])
def test_saved_review_refuses_history_without_database_access(source,tmp_path,monkeypatch,constant,group):
    from yearn_data import tvl_api, tvl_history_cache
    from yearn_data.tvl_review import ReviewDataset
    conn,database,publication=source
    run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore(database,publication).get()
    _,rows,positions,_=dataset.frames((DAY,))[0]
    review=ReviewDataset(dataset.manifest,DAY,rows,positions)
    review.save(tmp_path/'saved')
    monkeypatch.delenv('NEON_DB_URL')
    monkeypatch.delenv('STAGING_DB_URL')
    def unexpected(*args,**kwargs):
        pytest.fail('saved frame accessed a live database')
    monkeypatch.setattr(tvl_api,'read_db',unexpected)
    monkeypatch.setattr(tvl_history_cache,'connect',unexpected)
    saved=ReviewDataset.load(tmp_path/'saved'/'frame.json')
    assert saved.graph()['totalTvlUsd']==100
    with pytest.raises(ValueError,match='single captured close'):
        saved.history(group=group,constant=constant)


def test_review_reaccounts_stale_bridge_fields(source):
    from yearn_data.tvl_review import ReviewDataset
    conn,database,publication=source
    run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore(database,publication).get()
    _,rows,positions,_=dataset.frames((DAY,))[0]
    rows[0].update(bridge_excluded_usd='100',bridge_target_chain=10,
                   bridge_migration_timestamp=DAY-1,external_tvl_usd='0')
    manifest=dataset.manifest|{'currentBridgePolicy':'none','bridgeMigrations':[]}
    # A real target-chain observation makes stale routing affect completeness.
    rows.append(dict(rows[0],chain_id=10,vault='0x'+'88'*20,tvl_usd='50',
                     bridge_target_chain=None,bridge_excluded_usd=None,bridge_migration_timestamp=None))
    saved=ReviewDataset(manifest,DAY,rows,positions)
    _,revised,_,diagnostics=saved.frames((DAY,))[0]
    assert revised[0]['external_tvl_usd']=='100'
    assert diagnostics['external_tvl_usd']=='150'
    assert diagnostics['status']=='complete'
    assert 'bridge_migration_timing_unverified' not in diagnostics['issues']
    assert 'bridge_target_chain' not in revised[0]
    graph=saved.graph()
    assert not graph['bridgeExclusions']
    assert graph['totalTvlUsd']==150


def test_review_preserves_an_observed_bridge_target(source):
    from yearn_data.tvl_review import ReviewDataset
    conn,database,publication=source
    run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore(database,publication).get()
    _,rows,positions,_=dataset.frames((DAY,))[0]
    # No dated migration marker: this target is raw evidence, not an output field.
    rows[0]['bridge_target_chain']=10
    rows.append(dict(rows[0],chain_id=10,vault='0x'+'88'*20,tvl_usd='50',bridge_target_chain=None))
    review=ReviewDataset(dataset.manifest|{'bridgeMigrations':[]},DAY,rows,positions)
    _,revised,_,diagnostics=review.frames((DAY,))[0]
    assert revised[0]['bridge_target_chain']==10
    assert diagnostics['status']=='incomplete'
    assert 'bridge_migration_timing_unverified' in diagnostics['issues']
