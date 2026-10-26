import json
from pathlib import Path

import pytest

from yearn_data.storage import connect, init_db
from yearn_data.tvl import SCHEMA
from yearn_data.tvl_api import TvlStore, tvl_response

DAY = 1704067199  # Sunday UTC close, preceding Monday January 1.
PARENT = '0x'+'11'*20
CHILD = '0x'+'22'*20
BRIDGE = '0x'+'33'*20


def run(conn, number, timestamp, rows, edges=(), status='complete'):
    conn.execute('INSERT INTO tvl_runs VALUES (?,?,?,?,?)', (number,number,number,status,json.dumps({'from':timestamp,'to':timestamp})))
    for address, amount in rows:
        row = {'chain_id':1,'vault':address,'timestamp':timestamp,'block_number':100+timestamp,
               'version':'v3','category':'v3','asset':'0x'+'aa'*20,'asset_decimals':0,
               'total_assets_raw':'10','total_supply_raw':'10','asset_units':'10',
               'price_usd':str(int(amount)//10) if amount is not None else None,
               'tvl_usd':amount,'status':'ok' if amount is not None else 'unavailable','name':'Vault'}
        if address == BRIDGE:
            row['bridge_target_chain'] = 10
        conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)', (number,1,address,timestamp,100+timestamp,json.dumps(row)))
    for child, owned in edges:
        row = {'chain_id':1,'parent':PARENT,'child':child,'strategy':child,'timestamp':timestamp,
               'block_number':100+timestamp,'status':'ok','method':'direct-strategy','debt_usd':owned,
               'owned_usd':owned,'debt_raw':owned,'ownership_ratio':'0.5'}
        conn.execute('INSERT INTO tvl_positions VALUES (?,?,?,?,?,?,?)', (number,1,PARENT,child,child,timestamp,json.dumps(row)))
    conn.commit()


@pytest.fixture
def source(tmp_path):
    path = 'staging'
    conn = connect(path); init_db(conn); conn.executescript(SCHEMA)
    for address, active in ((PARENT,1),(CHILD,1),(BRIDGE,0)):
        conn.execute("INSERT INTO tvl_vaults (chain_id,version,address,active,updated_at) VALUES (1,'v3',?,?,1)", (address,active))
    conn.execute("INSERT INTO tvl_vaults (chain_id,version,address,active,updated_at) VALUES (10,'v3',?,1,1)",(CHILD,))
    conn.commit()
    yield conn, path, tmp_path/'publication'
    conn.close()


def test_accumulated_history_repair_and_failed_retry_preserve_known_data(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    run(conn,2,DAY+7*86400,[(PARENT,'200'),(CHILD,'200')],[(CHILD,'100')])
    # A high-ID old repair adds to history without erasing the newer date.
    run(conn,3,DAY,[(CHILD,'120')])
    run(conn,4,DAY,[(PARENT,None)],status='incomplete')
    run(conn,5,DAY+14*86400,[(PARENT,'999')],status='running')
    store = TvlStore(path,root)
    summary = store.get().summary()
    assert summary['totalTvl'] == 300
    history = store.get().history()
    assert history['range']['to'] == DAY+7*86400
    assert len(history['chart']) == 2
    # Parent 100 + repaired child 120 - recorded overlap 50.
    assert history['chart'][0]['Ethereum'] == 170
    assert history['chart'][1]['Ethereum'] == 300


def test_auto_refresh_and_pinned_publication(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100')])
    store = TvlStore(path,root,refresh_seconds=0)
    old = store.get().id
    run(conn,2,DAY+86400,[(PARENT,'200')])
    assert store.get().id != old
    assert store.get().summary()['totalTvl'] == 200
    assert store.get(old).summary()['totalTvl'] == 100
    status, result = tvl_response(store,'/api/tvl/history/runs/latest?datasetId='+old)
    assert status == 200 and result['datasetId'] == old
    assert result['chart'][0]['Ethereum'] == 100


def test_incomplete_ok_position_cannot_replace_complete_ownership(source):
    from yearn_data.canonical_history import _stage_observations

    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    run(conn,2,DAY,[(PARENT,'100')],[(CHILD,'60')])
    row = json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=2').fetchone()[0])
    row.update(owned_usd=None, mapping_status='child_outside_selection')
    conn.execute('UPDATE tvl_positions SET data_json=? WHERE run_id=2',(json.dumps(row),))
    conn.commit()

    dataset = TvlStore(path,root).get()
    assert dataset.summary()['totalTvl'] == 150
    assert dataset.frames((DAY,))[0][2][0]['source_run_id'] == 1
    # Retention must keep the same complete evidence as the API selector.
    _stage_observations(conn, 'tvl_positions', dataset.run_ids)
    assert conn.execute('SELECT run_id FROM canonical_keep_tvl_positions').fetchone()[0] == 1

    # A complete later repair still supersedes the old amount.
    run(conn,3,DAY,[],[(CHILD,'75')])
    assert TvlStore(path,root).get().summary()['totalTvl'] == 125

    # Observed zero debt is complete even without an owned-value read.
    run(conn,4,DAY,[],[(CHILD,'0')])
    row.update(debt_usd='0',debt_raw='0',owned_usd=None)
    conn.execute('UPDATE tvl_positions SET data_json=? WHERE run_id=4',(json.dumps(row),))
    run(conn,5,DAY,[],[(CHILD,None)],status='incomplete')
    dataset = TvlStore(path,root).get()
    assert dataset.summary()['totalTvl'] == 200
    assert dataset.frames((DAY,))[0][2][0]['source_run_id'] == 4


def test_registry_correction_applies_to_history_and_is_pinned(source, monkeypatch):
    from yearn_data import tvl_api
    from yearn_data.canonical_history import _stage_observations

    conn,path,root = source
    strategy = '0x'+'44'*20
    old_child = '0x'+'55'*20
    conn.execute("INSERT INTO tvl_vaults (chain_id,version,address,active,updated_at) VALUES (1,'v3',?,1,1)", (old_child,))

    def position(number, timestamp, child, getter=None):
        row = {'chain_id':1,'parent':PARENT,'strategy':strategy,'child':child,
               'timestamp':timestamp,'block_number':100+timestamp,'status':'ok',
               'method':'service-registry','debt_usd':'50','owned_usd':'80'}
        if getter: row['vault_getter'] = getter
        conn.execute('INSERT INTO tvl_positions VALUES (?,?,?,?,?,?,?)',
                     (number,1,PARENT,strategy,child,timestamp,json.dumps(row)))
        conn.commit()

    for number, day in ((1,DAY),(2,DAY+86400)):
        run(conn,number,day,[(PARENT,'100'),(CHILD,'100'),(old_child,'100')])
        position(number,day,old_child)
    monkeypatch.setattr(tvl_api,'TARGET_CORRECTIONS',[])
    old = TvlStore(path,root).get()
    assert old.summary()['totalTvl'] == 250

    monkeypatch.setattr(tvl_api,'TARGET_CORRECTIONS',[
        {'chainId':1,'strategyAddress':strategy,'targetVaultAddress':CHILD,
         'supersededTargetVaultAddresses':[old_child]}])
    corrected = TvlStore(path,root).get()
    assert corrected.id != old.id
    for _, rows, edges, diagnostics in corrected.frames(corrected.dates()):
        assert next(r for r in rows if r['vault']==old_child)['external_tvl_usd'] == '100'
        assert edges[0]['overlap_usd'] == '0'
        assert diagnostics['status'] == 'incomplete'
        assert diagnostics['external_tvl_usd'] is None
    # Excluded stale edges still contribute to unresolved accounting diagnostics.
    for chain,category,expected in ((None,None,1),(1,None,1),(10,None,0),
                                    (None,'v3',1),(None,'v2',0)):
        assert corrected.graph(chain_id=chain,category=category)['diagnostics']['unresolvedPositions'] == expected

    # Recollect one date. The other stays explicitly unresolved; amounts are not
    # transferred from the old child. Failed retries cannot erase the repair.
    run(conn,3,DAY,[])
    position(3,DAY,CHILD,'vault()')
    run(conn,4,DAY,[],status='incomplete')
    position(4,DAY,CHILD,'vault()')
    failed = json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=4').fetchone()[0])
    failed.update(status='unavailable',owned_usd=None)
    conn.execute('UPDATE tvl_positions SET data_json=? WHERE run_id=4',(json.dumps(failed),))
    conn.commit()
    repaired = TvlStore(path,root).get()
    _, rows, edges, diagnostics = repaired.frames((DAY,))[0]
    assert diagnostics['status'] == 'complete'
    assert diagnostics['external_tvl_usd'] == '250'
    assert {r['vault']:r['external_tvl_usd'] for r in rows} == {PARENT:'100',CHILD:'50',old_child:'100'}
    assert next(e for e in edges if e['child']==CHILD)['source_run_id'] == 3
    assert repaired.frames((DAY+86400,))[0][3]['status'] == 'incomplete'

    _stage_observations(conn,'tvl_positions',repaired.run_ids)
    assert {r[0] for r in conn.execute('SELECT DISTINCT run_id FROM canonical_keep_tvl_positions')} == {1,2,3}
    # Raw observations and publications made with the old policy stay auditable.
    assert json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=1').fetchone()[0])['owned_usd'] == '80'
    assert TvlStore(path,root).get(old.id).summary()['totalTvl'] == 250


def test_complete_position_from_different_block_is_not_applied(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    run(conn,2,DAY,[(PARENT,'100')],[(CHILD,None)])
    row = json.loads(conn.execute('SELECT data_json FROM tvl_snapshots WHERE run_id=2').fetchone()[0])
    row['block_number'] += 1
    conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE run_id=2',(json.dumps(row),))
    conn.commit()
    dataset = TvlStore(path,root).get()
    assert dataset.summary()['totalTvl'] == 100
    assert next(r for r in dataset.frames((DAY,))[0][1] if r['vault']==CHILD)['external_tvl_usd'] is None
    assert dataset.frames((DAY,))[0][2][0]['reason'] == 'selected_block_mismatch'


def test_failed_publication_retains_last_selection(source, monkeypatch):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100')])
    store = TvlStore(path,root,refresh_seconds=0)
    previous = (root/'current.json').read_bytes()
    replace = Path.replace
    def fail(source,target):
        if Path(target) == root/'current.json':
            raise OSError('interrupted publication')
        return replace(source,target)
    monkeypatch.setattr(Path,'replace',fail)
    assert store.get().summary()['totalTvl'] == 100
    assert (root/'current.json').read_bytes() == previous
    assert store.last_error == 'interrupted publication'


def test_weekly_stock_values_and_constant_price_timeframe(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    run(conn,2,DAY+86400,[(PARENT,'200'),(CHILD,'200')],[(CHILD,'100')])
    run(conn,3,DAY+2*86400,[(PARENT,'300'),(CHILD,'300')],[(CHILD,'150')])
    dataset = TvlStore(path,root).get()
    # Weekly TVL is the last stock observation, not a sum of daily values.
    assert [r['Ethereum'] for r in dataset.history()['chart']] == [150,450]
    fixed = dataset.history(start=DAY+86400,constant=True)
    assert len(fixed['chart']) == 1
    assert fixed['actualChart'][0]['Ethereum'] == 450
    assert fixed['constantPriceChart'][0]['Ethereum'] == 300
    assert all(r['timestamp'] == DAY+86400 for r in fixed['references'])
    assert dataset.history(start=DAY+3*86400)['chart'] == []


def test_current_bridge_policy_not_retroactive_and_chain_filter_consistent(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(BRIDGE,'50')])
    run(conn,2,DAY+86400,[(PARENT,'100'),(BRIDGE,'50')])
    dataset = TvlStore(path,root,current_bridge_policy='retired-registry').get()
    assert dataset.summary()['totalTvl'] == 100
    assert dataset.summary(1)['totalTvl'] == 100
    assert dataset.summary()['vaultBridgeExcluded'] == 50
    assert dataset.history(interval='daily')['chart'][0]['Ethereum'] == 150
    assert dataset.history(interval='daily')['chart'][1]['Ethereum'] == 100
    assert dataset.audit()['crossChainOverlap'] == 50


def test_unavailable_amounts_and_invalid_filters(source):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,None)])
    store = TvlStore(path,root)
    assert store.get().summary()['totalTvl'] is None
    assert store.get().history()['chart'] == [{'timestamp':DAY}]
    for query in ('chainId=999','from=x','from=10&to=1','interval=monthly','mode=unknown','groupBy=unknown','chainId=1&chainId=1','includeCurrent=maybe'):
        assert tvl_response(store,'/api/tvl/history/runs/latest?'+query)[0] == 400
    assert tvl_response(store,'/api/tvl/no-such-route')[0] == 404


def test_rpc_failure_holds_common_close_until_chain_repair(source):
    conn, path, root = source
    def add_other_chain(number, timestamp, amount, observed=True):
        run(conn, number, timestamp, [])
        row = {'chain_id':10,'vault':CHILD,'timestamp':timestamp,'block_number':timestamp,
               'version':'v3','category':'v3','asset_units':'10' if observed else None,
               'tvl_usd':amount,'total_supply_raw':'10','total_assets_raw':'10'}
        conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)',
                     (number,10,CHILD,timestamp,timestamp,json.dumps(row)))
        conn.commit()
    run(conn, 1, DAY, [(PARENT,'100')])
    add_other_chain(2, DAY, '100')
    run(conn, 3, DAY+86400, [(PARENT,'200')])
    add_other_chain(4, DAY+86400, None, observed=False)
    store = TvlStore(path, root, refresh_seconds=0)
    assert store.get().summary()['asOfTimestamp'] == DAY
    assert store.get().summary()['totalTvl'] == 200
    # A healthy finalized RPC repairs only the failed chain at the same close.
    add_other_chain(5, DAY+86400, '150')
    assert store.get().summary()['asOfTimestamp'] == DAY+86400
    assert store.get().summary()['totalTvl'] == 350


def test_curation_and_audit_require_positive_allocator_evidence(source):
    conn, path, root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    conn.execute("INSERT INTO strategy_reports (chain_id,vault_address,strategy_address,tx_hash,log_index,block_number,block_timestamp,version,gain_raw,loss_raw,net_raw,current_debt_raw) VALUES (1,?,?,?,0,1,?,'v3','0','0','0','0')",(PARENT,CHILD,'0x01',DAY))
    conn.commit()
    dataset = TvlStore(path,root).get()
    products = dataset.curation()
    assert products['vaultCount'] == 1
    assert products['byFamily']['v3_allocator']['grossTvlUsd'] == 100
    assert products['byFamily']['morpho_curated']['vaultCount'] == 0
    audit = {v['address']:v for v in dataset.audit()['vaults']}
    assert audit[PARENT]['vaultType'] == 1
    assert audit[CHILD]['vaultType'] is None
    assert audit[PARENT]['strategies'][0]['debtUsd'] == 50


def test_rejected_provider_quote_is_not_served_or_used_as_reference(source, monkeypatch):
    import yearn_data.tvl_api as api
    conn, path, root = source
    asset = '0x'+'aa'*20
    monkeypatch.setattr(api, 'PRICE_REJECTIONS', [{'chainId':1,'asset':asset,'timestamp':DAY,
                                               'priceUsd':'10','reason':'verified_bad_quote'}])
    run(conn,1,DAY,[(PARENT,'100')])
    run(conn,2,DAY+86400,[(PARENT,'120')])
    store = TvlStore(path,root,refresh_seconds=0)
    result = store.get().history(interval='daily',constant=True)
    assert result['actualChart'][0] == {'timestamp':DAY}
    assert result['references'][0]['timestamp'] == DAY+86400
    # A corrected canonical quote at the same identity is accepted automatically.
    run(conn,3,DAY,[(PARENT,'130')])
    repaired = store.get().history(interval='daily',constant=True)
    assert repaired['actualChart'][0]['Ethereum'] == 130
    assert repaired['references'][0]['timestamp'] == DAY


def test_version_and_constant_price_share_accounting_and_all_time_bounds(source, monkeypatch):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    run(conn,2,DAY+86400,[(PARENT,'200'),(CHILD,'200')],[(CHILD,'100')])
    dataset = TvlStore(path,root).get()
    chain = dataset.history(constant=True)
    def unexpected_scan(*args, **kwargs):
        pytest.fail('changing chart grouping rescanned stored source data')
    monkeypatch.setattr(dataset,'iter_frames',unexpected_scan)
    monkeypatch.setattr(dataset,'reference_candidates',unexpected_scan)
    # Explicit full bounds are the same cache identity as omitted bounds.
    assert dataset.history(start=DAY,end=DAY+86400,constant=True) is chain
    versions = dataset.history(group='category',constant=True)
    assert versions['actualChart'][0]['v3'] == chain['actualChart'][0]['Ethereum']
    assert versions['constantPriceChart'][1]['v3'] == chain['constantPriceChart'][1]['Ethereum']


def test_concurrent_group_requests_share_work_without_blocking_summary(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100')])
    dataset = TvlStore(path,root).get()
    entered, release, joined = Event(), Event(), Event()
    original = dataset._history_rows
    count = []
    def slow_rows(*args):
        count.append(1)
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(dataset,'_history_rows',slow_rows)
    original_once = dataset.compute_once
    def notice_join(identity,compute):
        if identity in dataset._pending:
            joined.set()
        return original_once(identity,compute)
    monkeypatch.setattr(dataset,'compute_once',notice_join)
    with ThreadPoolExecutor(max_workers=3) as pool:
        chain = pool.submit(dataset.history)
        assert entered.wait(5)
        versions = pool.submit(dataset.history,group='category')
        assert joined.wait(5)
        assert pool.submit(dataset.summary).result(timeout=2)['totalTvl'] == 100
        release.set()
        assert chain.result(timeout=5)['chart'][0]['Ethereum'] == 100
        assert versions.result(timeout=5)['chart'][0]['v3'] == 100
    assert len(count) == 1


def test_long_histories_keep_streaming_instead_of_filling_shared_cache(source, monkeypatch):
    import yearn_data.tvl_api as api
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100')])
    run(conn,2,DAY+86400,[(PARENT,'200')])
    dataset = TvlStore(path,root).get()
    monkeypatch.setattr(api,'HISTORY_CACHE_MAX_DATES',1)
    def oversized_cache(*args, **kwargs):
        pytest.fail('long history was retained in the shared row cache')
    monkeypatch.setattr(dataset,'history_rows',oversized_cache)
    assert [r['Ethereum'] for r in dataset.history(interval='daily')['chart']] == [100,200]


def test_reference_skips_a_full_window_of_rejected_prices(source, monkeypatch):
    import yearn_data.tvl_api as api
    conn,path,root = source
    asset='0x'+'aa'*20
    rejected=[]
    for index in range(8):
        timestamp=DAY+index*86400
        run(conn,index+1,timestamp,[(PARENT,'720000000')])
        rejected.append({'chainId':1,'asset':asset,'timestamp':timestamp,
                         'priceUsd':'72000000','reason':'verified_bad_quote'})
    run(conn,9,DAY+8*86400,[(PARENT,'10')])
    monkeypatch.setattr(api,'PRICE_REJECTIONS',rejected)
    store=TvlStore(path,root)
    result=store.get().history(interval='daily',constant=True)
    assert result['references'] == [{'vault':'1:'+PARENT,'timestamp':DAY+8*86400,
                                     'priceUsd':1,'source':'stored-tvl','depegCandidateSkipped':False}]
    assert result['constantPriceChart'][-1]['Ethereum'] == 10
    assert all(row == {'timestamp':DAY+index*86400} for index,row in enumerate(result['actualChart'][:-1]))


def test_dated_bridge_cutoff_preserves_pre_migration_and_pinned_selection(source, monkeypatch):
    import yearn_data.tvl_api as api
    conn,path,root = source
    cutoff=DAY+86400
    monkeypatch.setattr(api,'BRIDGE_MIGRATIONS',[{'sourceChainId':1,'sourceVaultAddress':BRIDGE,
                                               'targetChainId':10,'migrationTimestamp':cutoff}])
    run(conn,1,DAY,[(PARENT,'100'),(BRIDGE,'50')])
    store=TvlStore(path,root,current_bridge_policy='retired-registry',refresh_seconds=0)
    old=store.get().id
    # Today's inactive flag must not exclude a dated vault before its migration.
    assert store.get().summary()['totalTvl'] == 150
    run(conn,2,cutoff,[(PARENT,'100'),(BRIDGE,'50')])
    run(conn,3,cutoff+86400,[(PARENT,'100'),(BRIDGE,'50')])
    dataset=store.get()
    assert dataset.id != old
    assert store.get(old).summary()['totalTvl'] == 150
    history=dataset.history(interval='daily',constant=True)
    assert [r['Ethereum'] for r in history['actualChart']] == [150,100,100]
    assert [r['Ethereum'] for r in history['constantPriceChart']] == [150,100,100]
    assert [r['Ethereum'] for r in dataset.history(mode='raw',interval='daily')['chart']] == [150,150,150]
    assert dataset.summary()['vaultBridgeExcluded'] == 50
    assert dataset.summary(1)['totalTvl'] == 100
    assert [r['Ethereum'] for r in dataset.history(chain_id=1,interval='daily')['chart']] == [150,100,100]


def test_excluded_bridge_parent_does_not_also_deduct_child_holdings(source, monkeypatch):
    import yearn_data.tvl_api as api
    conn,path,root = source
    monkeypatch.setattr(api,'BRIDGE_MIGRATIONS',[{'sourceChainId':1,'sourceVaultAddress':BRIDGE,
                                               'targetChainId':10,'migrationTimestamp':DAY+86400}])
    # Dated evidence remains authoritative if the live catalogue says active.
    conn.execute('UPDATE tvl_vaults SET active=1 WHERE address=?',(BRIDGE,));conn.commit()
    for number,timestamp in ((1,DAY),(2,DAY+86400)):
        run(conn,number,timestamp,[(BRIDGE,'100'),(CHILD,'100')],[(CHILD,'50')])
        row=conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=?',(number,)).fetchone()
        edge=json.loads(row[0]);edge['parent']=BRIDGE
        conn.execute('UPDATE tvl_positions SET parent=?,data_json=? WHERE run_id=?',(BRIDGE,json.dumps(edge),number));conn.commit()
    dataset=TvlStore(path,root).get()
    assert [r['Ethereum'] for r in dataset.history(interval='daily')['chart']] == [150,100]
    assert dataset.summary()['overlapExcluded'] == 0
    assert dataset.summary()['vaultBridgeExcluded'] == 100
    _,_,positions,diagnostics=dataset.frames((DAY+86400,))[0]
    assert diagnostics['known_external_tvl_usd'] == '100'
    assert diagnostics['known_bridge_excluded_usd'] == '100'
    assert diagnostics['external_tvl_usd'] == '100'
    assert positions[0]['accounting_excluded'] is True
    assert positions[0]['overlap_usd'] == '0'


def test_turtle_policy_excludes_all_history_and_preserves_raw_evidence(source):
    from yearn_data.tvl_policy import VAULT_POLICIES
    from yearn_data.tvl_api import TvlDataset
    conn,path,root = source
    turtle=VAULT_POLICIES[0]['address'].lower()
    conn.execute("INSERT INTO tvl_vaults(chain_id,address,version,tvl_category,updated_at) VALUES (1,?,'erc4626','curation',1)",(turtle,))
    for number,timestamp in ((1,DAY),(2,DAY+86400)):
        run(conn,number,timestamp,[(turtle,'100'),(CHILD,'100')],[(CHILD,'50')])
        raw=json.loads(conn.execute('SELECT data_json FROM tvl_positions WHERE run_id=?',(number,)).fetchone()[0])
        raw['parent']=turtle
        conn.execute('UPDATE tvl_positions SET parent=?,data_json=? WHERE run_id=?',(turtle,json.dumps(raw),number))
    conn.commit()
    dataset=TvlStore(path,root).get()
    for mode in ('raw','external'):
        assert [r['Ethereum'] for r in dataset.history(mode=mode,interval='daily')['chart']]==[100,100]
    assert dataset.summary()['overlapExcluded']==0
    assert dataset.catalog[(1,turtle)]['tvl_category']=='v3'
    assert dataset.catalog[(1,turtle)]['version']=='v3'
    assert conn.execute('SELECT COUNT(*) FROM tvl_snapshots WHERE vault=?',(turtle,)).fetchone()[0]==2
    assert all(n.get('address','').lower()!=turtle for n in dataset.graph()['nodes'])
    # Old manifests have no exclusion policy, even after the code is deployed.
    legacy=dict(dataset.manifest);legacy.pop('vaultPolicies');legacy['datasetId']='legacy'
    assert TvlDataset(legacy).summary()['totalTvl']==150


def test_internal_curation_policy_is_pinned_and_prepared_history_matches(source):
    from yearn_data.tvl_api import TvlDataset
    from yearn_data.tvl_history_cache import prepare
    conn,path,root=source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'110')],[(CHILD,'100')])
    for stored in conn.execute('SELECT vault,data_json FROM tvl_snapshots').fetchall():
        row=json.loads(stored['data_json']);row['category']='curation'
        conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE vault=?',(json.dumps(row),stored['vault']))
    conn.commit()
    dataset=TvlStore(path,root).get()
    assert dataset.summary()['totalTvl']==110
    assert dataset.summary()['curationTvl']==210  # Existing summary field is gross.
    history=dataset.history(interval='daily',group='category')
    prepare(dataset)
    assert dataset.history(interval='daily',group='category')['chart']==history['chart']
    legacy=dict(dataset.manifest);legacy.pop('deductInternalCuration');legacy.pop('ownershipAccounting');legacy['datasetId']='legacy'
    assert TvlDataset(legacy).summary()['totalTvl']==210


def test_chain_drilldown_reuses_all_chain_valuations_and_references(source, monkeypatch):
    conn,path,root = source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
    # Include a second chain with the same vault address to exercise chain identity.
    other={'chain_id':10,'vault':CHILD,'timestamp':DAY,'block_number':100+DAY,
           'version':'v3','category':'v3','asset_units':'10','tvl_usd':'300'}
    conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)',(1,10,CHILD,DAY,100+DAY,json.dumps(other)));conn.commit()
    dataset=TvlStore(path,root).get()
    main=dataset.history(constant=True)
    def unexpected_scan(*args,**kwargs):
        pytest.fail('chain drilldown rescanned stored source observations')
    monkeypatch.setattr(dataset,'iter_frames',unexpected_scan)
    monkeypatch.setattr(dataset,'reference_candidates',unexpected_scan)
    drilldown=dataset.history(group='vault',chain_id=1,constant=True)
    assert sum(v for k,v in drilldown['actualChart'][0].items() if k!='timestamp') == main['actualChart'][0]['Ethereum']
    assert sum(v for k,v in drilldown['constantPriceChart'][0].items() if k!='timestamp') == main['constantPriceChart'][0]['Ethereum']
    assert {r['vault'] for r in drilldown['references']} == {'1:'+PARENT,'1:'+CHILD}
    optimism=dataset.history(group='vault',chain_id=10,constant=True)
    assert sum(v for k,v in optimism['actualChart'][0].items() if k!='timestamp') == main['actualChart'][0]['Optimism']
    assert len(optimism['references']) == 1
    assert optimism['references'][0]['vault'] == '10:'+CHILD


@pytest.mark.parametrize('policy_field', [True, False])
def test_curation_preserves_manifest_policy_over_packaged_inventory(source, policy_field):
    from yearn_data import tvl_policy
    from yearn_data.tvl_api import TvlDataset
    conn, path, root = source
    parent = tvl_policy.VAULT_POLICIES[0]['address'].lower()
    conn.execute("INSERT INTO tvl_vaults(chain_id,address,version,active,updated_at) VALUES (1,?,'v3',1,1)", (parent,))
    run(conn, 1, DAY, [(parent, '100'), (CHILD, '110')], [(CHILD, '50')])
    edge = json.loads(conn.execute('SELECT data_json FROM tvl_positions').fetchone()[0])
    edge['parent'] = parent
    conn.execute('UPDATE tvl_positions SET parent=?,data_json=?', (parent, json.dumps(edge)))
    for stored in conn.execute('SELECT vault,data_json FROM tvl_snapshots').fetchall():
        row = json.loads(stored['data_json']); row['category'] = 'curation'
        conn.execute('UPDATE tvl_snapshots SET data_json=? WHERE vault=?', (json.dumps(row), stored['vault']))
    conn.commit()
    manifest = dict(TvlStore(path, root).get().manifest)
    if policy_field:
        manifest['vaultPolicies'] = []
    else:
        manifest.pop('vaultPolicies')
    dataset = TvlDataset(manifest)
    assert dataset.summary()['totalTvl'] == 160
    assert dataset.graph()['totalTvlUsd'] == 160
    assert dataset.curation()['totalTvlUsd'] == 160
    assert {v['address'] for v in dataset.curation()['vaults']} == {parent, CHILD}


@pytest.mark.parametrize('analytics_enabled', [False,True])
def test_pairing_comparison_is_served_only_by_prepared_analytics(source,monkeypatch,analytics_enabled):
    import inspect
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from yearn_data import pairing, analytics
    conn,database,publication=source
    run(conn,1,DAY,[(PARENT,'100')])
    store=TvlStore(database,publication)
    assert tvl_response(store,'/api/comparison')[0]==404
    assert 'comparison_database' not in inspect.signature(TvlStore).parameters
    assert 'comparison_database' not in inspect.signature(pairing.serve_pairing).parameters
    monkeypatch.setattr(pairing,'PairingStore',lambda _:SimpleNamespace(get=lambda:SimpleNamespace(database=database)))
    monkeypatch.setattr(analytics,'AnalyticsStore',lambda _:SimpleNamespace(get=lambda:None))
    monkeypatch.setattr(analytics,'analytics_response',lambda *_:(200,{'source':'prepared'}))
    def create_server(address,handler):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        serve=server.serve_forever
        def exercise():
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            try:
                url=f'http://127.0.0.1:{server.server_port}/api/comparison'
                if analytics_enabled:
                    with urlopen(url) as response:
                        assert response.status==200 and json.load(response)=={'source':'prepared'}
                else:
                    with pytest.raises(HTTPError) as error:urlopen(url)
                    assert error.value.code==404
            finally:
                server.shutdown();thread.join()
        server.serve_forever=exercise
        return server
    monkeypatch.setattr(pairing,'ThreadingHTTPServer',create_server)
    pairing.serve_pairing('unused',tvl_publication=publication,analytics=analytics_enabled)


@pytest.mark.parametrize('bridge_value', [None,'0','50'])
def test_bridge_membership_survives_unavailable_valuation(source,bridge_value):
    conn,path,root=source
    run(conn,1,DAY,[(PARENT,'100'),(BRIDGE,bridge_value)])
    store=TvlStore(path,root,current_bridge_policy='retired-registry')
    expected=None if bridge_value is None else float(bridge_value)
    for query in ('','?chainId=1'):
        status,summary=tvl_response(store,'/api/tvl'+query)
        assert status==200
        assert summary['vaultBridgeExcluded']==expected
        assert summary['crossChainOverlapByChain']['Ethereum']==expected
        assert summary['crossChainOverlapByCategory']['v3']==expected
        assert summary['crossChainOverlapByCategory']['v2']==0
        _,audit=tvl_response(store,'/api/audit/tree'+query)
        assert audit['crossChainOverlap']==expected
        assert [(r['address'],r['tvlUsd']) for r in audit['crossChainVaults']]==[(BRIDGE,expected)]
        _,graph=tvl_response(store,'/api/tvl/graph'+query)
        assert graph['totalBridgeExcludedTvlUsd']==expected
        assert [(r['address'],r['tvlUsd']) for r in graph['bridgeExclusions']]==[(BRIDGE,expected)]
    assert store.get().graph(chain_id=10)['totalBridgeExcludedTvlUsd']==0


@pytest.mark.parametrize('suffix', ['', '/constant-price'])
@pytest.mark.parametrize('format', ['full','chart'])
def test_public_vault_history_rejects_oversized_window_before_work(source,monkeypatch,suffix,format):
    conn,path,root=source;run(conn,1,DAY,[(PARENT,'100')])
    store=TvlStore(path,root);dataset=store.get()
    monkeypatch.setattr(dataset,'sampled_dates',lambda *args:tuple(range(367)))
    monkeypatch.setattr(dataset,'history',lambda *args,**kwargs:pytest.fail('oversized history was computed'))
    status,result=tvl_response(store,f'/api/tvl/history/runs/latest{suffix}?groupBy=vault&interval=daily&format={format}')
    assert status==400 and 'limit' in result['error']


@pytest.mark.parametrize('prepared', [False,True])
@pytest.mark.parametrize('chain', [None,1])
def test_public_history_point_limit_and_cache_budget(source,monkeypatch,prepared,chain):
    from yearn_data import tvl_api as api
    conn,path,root=source
    for i in range(3):run(conn,i+1,DAY+i*86400,[(PARENT,'100'),(CHILD,'200')])
    store=TvlStore(path,root);dataset=store.get()
    if prepared:
        from yearn_data.tvl_history_cache import prepare
        prepare(dataset)
    opened=[]
    original=api.read_db
    def track(database):
        connection=original(database);opened.append(connection);return connection
    monkeypatch.setattr(api,'read_db',track)
    monkeypatch.setattr(api,'PUBLIC_HISTORY_MAX_POINTS',2)
    url='/api/tvl/history/runs/latest?groupBy=vault&interval=daily'+('&chainId=1' if chain else '')
    status,result=tvl_response(store,url)
    assert status==400 and 'limit' in result['error']
    assert all(connection.raw.closed for connection in opened)
    assert tvl_response(store,url+f'&from={DAY+2*86400}')[0]==200
    # Ordinary offline history is not constrained by the public response budget.
    assert len(dataset.history(group='vault',interval='daily')['points'])==6
    monkeypatch.setattr(api,'HISTORY_RESPONSE_CACHE_BYTES',1000)
    for start in (DAY,DAY+1,DAY+86401):dataset.history(start=start)
    assert dataset._history_cache_bytes<=1000


def test_public_history_bounds_reference_metadata(source,monkeypatch):
    from yearn_data import tvl_api as api
    conn,path,root=source;run(conn,1,DAY,[(PARENT,'100')])
    store=TvlStore(path,root)
    monkeypatch.setattr(api,'PUBLIC_HISTORY_MAX_BYTES',100)
    status,result=tvl_response(store,'/api/tvl/history/runs/latest/constant-price?groupBy=vault')
    assert status==400 and 'size limit' in result['error']
    assert not store.get()._history_cache


@pytest.mark.parametrize('suffix', ['', '/constant-price'])
@pytest.mark.parametrize('format', ['full','chart'])
def test_public_chain_history_matches_prepared_dates(source,monkeypatch,suffix,format):
    from types import SimpleNamespace
    from yearn_data.tvl_api import TvlDataset
    from yearn_data.tvl_history_cache import prepare
    conn,path,root=source
    run(conn,1,DAY,[(PARENT,'100')])
    run(conn,2,DAY+86400,[(PARENT,'100'),(CHILD,'50')])
    row=json.loads(conn.execute('SELECT data_json FROM tvl_snapshots WHERE run_id=2 AND vault=?',(CHILD,)).fetchone()[0])
    row['chain_id']=10
    conn.execute('UPDATE tvl_snapshots SET chain_id=10,data_json=? WHERE run_id=2 AND vault=?',(json.dumps(row),CHILD));conn.commit()
    store=TvlStore(path,root);dataset=store.get()
    monkeypatch.setattr(dataset,'history_rows',lambda *args:pytest.fail('public history filled the shared row cache'))
    url=f'/api/tvl/history/runs/latest{suffix}?chainId=10&interval=daily&format={format}'
    status,streamed=tvl_response(store,url)
    assert status==200
    prepare(dataset)
    prepared_store=SimpleNamespace(get=lambda _:TvlDataset(dataset.manifest))
    status,prepared=tvl_response(prepared_store,url)
    assert status==200
    assert streamed==prepared
    for field in ('chart','actualChart','constantPriceChart'):
        if field in streamed:assert [r['timestamp'] for r in streamed[field]]==[DAY+86400]


@pytest.mark.parametrize('address', [
    '0x7b5a0182e400b241b317e781a4e9dedfc1429822',
    '0xcc6a16be713f6a714f68b0e1f4914fd3db15fbef',
    '0x48c03b6ffd0008460f8657db1037c7e09deedfcb',
    '0x92c82f5f771f6a44cfa09357dd0575b81bf5f728',
])
def test_katana_predeposits_excluded_before_and_after_migration(source, address):
    from yearn_data.tvl_api import TvlDataset
    conn, path, root = source
    conn.execute("INSERT INTO tvl_vaults(chain_id,address,version,tvl_category,updated_at) VALUES (1,?,'v3','v3',1)", (address,))
    conn.execute("INSERT INTO tvl_vaults(chain_id,address,version,tvl_category,updated_at) VALUES (747474,?,'v3','v3',1)", (CHILD,))
    dates = (1751241599, 1751327999)  # June 29 and 30 closes, across the migration.
    for number, timestamp in enumerate(dates, 1):
        run(conn, number, timestamp, [(address, '100'), (CHILD, '200')])
        row = json.loads(conn.execute('SELECT data_json FROM tvl_snapshots WHERE run_id=? AND vault=?', (number, CHILD)).fetchone()[0])
        row['chain_id'] = 747474
        conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)', (number, 747474, CHILD, timestamp, 100+timestamp, json.dumps(row)))
    conn.commit()
    dataset = TvlStore(path, root).get()
    for mode in ('raw', 'external'):
        history = dataset.history(mode=mode, interval='daily', constant=True)
        for chart in ('actualChart', 'constantPriceChart'):
            assert [r['Ethereum'] for r in history[chart]] == [200, 200]
            assert [r['Katana'] for r in history[chart]] == [200, 200]
    for grouping in ('vault', 'category'):
        history = dataset.history(group=grouping, chain_id=1, interval='daily', constant=True)
        for chart in ('actualChart', 'constantPriceChart'):
            assert [sum(v for k,v in r.items() if k != 'timestamp') for r in history[chart]] == [200, 200]
    assert dataset.summary()['totalTvl'] == 400
    assert conn.execute('SELECT COUNT(*) FROM tvl_snapshots WHERE vault=?', (address,)).fetchone()[0] == 2
    legacy = dict(dataset.manifest)
    legacy['vaultPolicies'] = [p for p in legacy['vaultPolicies'] if p['address'].lower() != address]
    legacy['datasetId'] = 'legacy-predeposit-' + address
    assert [r['Ethereum'] for r in TvlDataset(legacy).history(interval='daily')['chart']] == [300, 200]
