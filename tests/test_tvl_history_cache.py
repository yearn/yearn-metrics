import json
from contextlib import closing

import pytest

from yearn_data.storage import connect,init_db
from yearn_data.tvl_api import TvlStore,TvlDataset,chart_response
from yearn_data.tvl_history_cache import prepare,ready
from test_tvl_api import run,DAY,PARENT,CHILD,BRIDGE


def test_prepared_history_preserves_repairs_unknowns_nested_caps_and_window_prices(postgres_database, tmp_path,monkeypatch):
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        for address in (PARENT,CHILD,BRIDGE):
            conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,updated_at) VALUES (1,'v3',?,1)",(address,))
        for day in range(9):
            run(conn,day+1,DAY+day*86400,[(PARENT,'100'),(CHILD,str(100+10*day)),(BRIDGE,None)],[(CHILD,'50')])
        run(conn,10,DAY,[(CHILD,'150')])
        run(conn,11,DAY,[(PARENT,None)],status='incomplete')
    dataset=TvlStore(path,tmp_path/'publication').get()
    expected=[dataset.history(group='vault',interval='daily',constant=True),
              dataset.history(group='chain',interval='daily'),
              dataset.history(group='category',start=DAY+5*86400,constant=True)]
    cached=TvlDataset(dataset.manifest)
    assert not cached.history_prepared()
    assert prepare(dataset)['status']=='prepared'
    assert ready(dataset)
    monkeypatch.setattr(cached,'iter_frames',lambda *a,**k:pytest.fail('prepared read recalculated nested positions'))
    monkeypatch.setattr(cached,'reference_candidates',lambda *a,**k:pytest.fail('prepared reference read rescanned raw JSON'))
    actual=[cached.history(group='vault',interval='daily',constant=True),
            cached.history(group='chain',interval='daily'),
            cached.history(group='category',start=DAY+5*86400,constant=True)]
    assert actual==expected
    # Exercise the compact public route against prepared rows and its response cache.
    from types import SimpleNamespace
    from yearn_data.tvl_api import tvl_response
    store = SimpleNamespace(get=lambda _: cached)
    url = '/api/tvl/history/runs/latest/constant-price?groupBy=vault&interval=daily&format=chart&top=1&topMetric=constant-price'
    status, compact = tvl_response(store, url)
    assert status == 200
    assert compact['series'] == [f'Vault (1:{CHILD[:8]})', 'All other vaults']
    # The repaired child has 150 less 50 owned by its parent; the incomplete
    # parent repair retains its earlier 100. Both first quotes are references.
    assert compact['constantPriceChart'][0] == {
        'timestamp': DAY, f'Vault (1:{CHILD[:8]})': 100.0, 'All other vaults': 100.0}
    assert compact['points'] == compact['references'] == []
    with monkeypatch.context() as context:
        context.setattr(cached, '_history', lambda *a, **k: pytest.fail('compact response missed cache'))
        assert tvl_response(store, url) == (200, compact)
    assert cached.history(interval='daily',start=DAY+100*86400)['chart']==[]
    assert prepare(dataset)['status']=='already-prepared'
    # A new immutable publication cannot consume the previous dataset's rows.
    with closing(connect(path)) as conn:run(conn,12,DAY+9*86400,[(PARENT,'200')])
    newer=TvlStore(path,tmp_path/'publication').get()
    assert newer.id!=dataset.id and not ready(newer)


def test_interrupted_preparation_never_exposes_partial_rows(postgres_database, tmp_path):
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')])
    dataset=TvlStore(path,tmp_path/'publication').get()
    def interrupted(message):raise RuntimeError('injected interruption')
    with pytest.raises(RuntimeError,match='interruption'):prepare(dataset,interrupted)
    assert not ready(dataset)
    with closing(connect(path,readonly=True)) as conn:
        assert conn.execute('SELECT count(*) FROM tvl_chart_rows').fetchone()[0]==0
    assert prepare(dataset)['status']=='prepared'


def test_compact_top_series_preserves_all_capital_and_neutral_total():
    original={'meta':{'referenceWindowPoints':7},'points':[{'large':'duplicate'}],
              'references':[{'vault':'debug-only'}],
              'actualChart':[{'timestamp':1,'old':500,'a':1,'b':2},{'timestamp':2,'old':0,'a':10,'b':20}],
              'constantPriceChart':[{'timestamp':1,'old':450,'a':2,'b':4},{'timestamp':2,'old':0,'a':20,'b':40}]}
    compact=chart_response(original,top=1)
    # Rank by latest value, not peak. Keep every omitted vault in the remainder.
    assert compact['meta']['topSeries']==['b','All other vaults']
    assert compact['actualChart']==[{'timestamp':1,'b':2,'All other vaults':501},
                                   {'timestamp':2,'b':20,'All other vaults':10}]
    assert compact['constantPriceChart']==[{'timestamp':1,'Price-neutral TVL':456},
                                          {'timestamp':2,'Price-neutral TVL':60}]
    assert compact['points']==compact['references']==[]
    assert original['points'] and original['references']


def test_prepared_histories_keep_chain_identity_and_window_reference_changes(postgres_database, tmp_path):
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        for day in range(8):
            run(conn,day+1,DAY+day*86400,[(PARENT,str(100+day*10))])
            point={'chain_id':10,'vault':PARENT,'timestamp':DAY+day*86400,'block_number':100,
                   'version':'v3','asset_units':'10','tvl_usd':str(300+day*10)}
            conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)',(day+1,10,PARENT,point['timestamp'],100,json.dumps(point)))
            conn.commit()
    source=TvlStore(path,tmp_path/'publication').get()
    old=source.history(chain_id=10,interval='daily',constant=True,start=DAY+5*86400)
    prepare(source)
    cached=TvlDataset(source.manifest)
    assert cached.history(chain_id=10,interval='daily',constant=True,start=DAY+5*86400)==old
    assert all(r['vault']=='10:'+PARENT for r in old['references'])


def test_published_only_reader_keeps_selection_until_updater_publishes(postgres_database, tmp_path):
    path='staging';publication=tmp_path/'publication'
    with closing(connect(path)) as conn:
        init_db(conn);run(conn,1,DAY,[(PARENT,'100')])
    previous=TvlStore(path,publication).get().id
    with closing(connect(path)) as conn:run(conn,2,DAY+86400,[(PARENT,'200')])
    reader=TvlStore(path,publication,auto_publish=False)
    assert reader.get().id==previous
    assert reader.get().summary()['totalTvl']==100
    new=TvlStore(path,publication).get()
    assert new.id!=previous and reader.get().id==new.id


def test_price_only_repair_preserves_old_publication_and_recalculates_deductions(postgres_database, tmp_path):
    from yearn_data.tvl_history_cache import prepare_price_repair
    from copy import deepcopy
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')],[(CHILD,'50')])
        run(conn,2,DAY+86400,[(PARENT,'200'),(CHILD,'200')],[(CHILD,'100')])
    old=TvlStore(path,tmp_path/'publication').get();prepare(old)
    manifest=deepcopy(old.manifest);manifest['datasetId']='f'*64
    manifest['publishedAt']+=1;manifest['diagnostics']={'note':'updated rejection diagnostics'}
    manifest['priceRejections'].append({'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY,'priceUsd':'10','reason':'verified quote'})
    repaired=TvlDataset(manifest)
    result=prepare_price_repair(repaired,old)
    assert result['recomputedDates']==[DAY]
    assert old.history(interval='daily')['chart'][0]['Ethereum']==150
    chart=repaired.history(interval='daily')['chart']
    assert chart[0].get('Ethereum') is None
    assert chart[1]['Ethereum']==300
    assert prepare_price_repair(repaired,old)['status']=='already-prepared'
    incompatible=deepcopy(manifest);incompatible['datasetId']='e'*64;incompatible['currentBridgePolicy']='retired-registry'
    with pytest.raises(ValueError,match='preserve all other'):prepare_price_repair(TvlDataset(incompatible),old)


def test_price_repair_rejects_source_with_interrupted_total_preparation(postgres_database, tmp_path, monkeypatch):
    from copy import deepcopy
    from yearn_data import tvl_history_cache as cache
    path = 'staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn, 1, DAY, [(PARENT, '100')])
        run(conn, 2, DAY + 86400, [(PARENT, '200')])
    old = TvlStore(path, tmp_path / 'publication').get()
    def interrupted(_dataset):
        raise RuntimeError('total preparation interrupted')
    monkeypatch.setattr(cache, 'prepare_totals', interrupted)
    with pytest.raises(RuntimeError, match='interrupted'):
        prepare(old)
    assert ready(old) and not cache.totals_ready(old)
    manifest = deepcopy(old.manifest)
    manifest['datasetId'] = 'f' * 64
    manifest['priceRejections'].append({'chainId': 1, 'asset': '0x' + 'aa' * 20,
        'timestamp': DAY, 'priceUsd': '10', 'reason': 'verified quote'})
    repaired = TvlDataset(manifest)
    with pytest.raises(ValueError, match='totals'):
        cache.prepare_price_repair(repaired, old)
    assert not ready(repaired)


def test_price_repair_resumes_interrupted_destination_totals(postgres_database, tmp_path, monkeypatch):
    from copy import deepcopy
    from yearn_data import tvl_history_cache as cache
    path = 'staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn, 1, DAY, [(PARENT, '100')])
        run(conn, 2, DAY + 86400, [(PARENT, '200')])
    old = TvlStore(path, tmp_path / 'publication').get()
    prepare(old)
    manifest = deepcopy(old.manifest)
    manifest['datasetId'] = 'f' * 64
    manifest['priceRejections'].append({'chainId': 1, 'asset': '0x' + 'aa' * 20,
        'timestamp': DAY, 'priceUsd': '10', 'reason': 'verified quote'})
    repaired = TvlDataset(manifest)
    expected = repaired.history(interval='daily')['chart']
    def interrupted(_dataset):
        raise RuntimeError('total preparation interrupted')
    with monkeypatch.context() as patch:
        patch.setattr(cache, 'prepare_totals', interrupted)
        with pytest.raises(RuntimeError, match='interrupted'):
            prepare(repaired)
    assert ready(repaired) and not cache.totals_ready(repaired)
    assert cache.prepare_price_repair(repaired, old)['status'] == 'already-prepared'
    assert cache.totals_ready(repaired)
    fresh=TvlDataset(manifest)
    monkeypatch.setattr(fresh,'iter_frames',lambda *a,**k:pytest.fail('resumed chart bypassed prepared totals'))
    assert fresh.history(interval='daily')['chart'] == expected
    with closing(connect(path,readonly=True)) as conn:
        assert [row[0] for row in conn.execute('SELECT value FROM tvl_chart_totals WHERE dataset_id=? ORDER BY timestamp,group_by,mode,series',(fresh.id,))]==['200']*4


def test_price_repair_keeps_unknown_chain_series_across_multiple_dates(postgres_database, tmp_path):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        for offset in range(3):
            timestamp=DAY+offset*86400
            run(conn,offset+1,timestamp,[(PARENT,'100')])
            row={'chain_id':146,'vault':CHILD,'timestamp':timestamp,'block_number':100+offset,
                 'version':'v2','category':'v2','asset':'0x'+'bb'*20,'asset_units':'10',
                 'tvl_usd':str(100*(offset+1)),'price_usd':str(10*(offset+1))}
            conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)',
                         (offset+1,146,CHILD,timestamp,100+offset,json.dumps(row)))
            conn.commit()
    old=TvlStore(path,tmp_path/'publication').get();prepare(old)
    manifest=deepcopy(old.manifest);manifest['datasetId']='f'*64
    manifest['priceRejections'] += [{'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY+offset*86400,
                                    'priceUsd':'10','reason':'verified fixture'} for offset in (0,1)]
    fresh=TvlDataset(manifest)
    result=prepare_price_repair(fresh,old)
    assert result['recomputedDates']==[DAY,DAY+86400]
    chart=TvlDataset(manifest).history(interval='daily')['chart']
    assert [r['Chain 146'] for r in chart]==[100,200,300]
    assert not any('146' in r for r in chart)
    assert old.history(interval='daily')['chart'][0]['Ethereum']==100
    assert chart[0].get('Ethereum') is None and chart[2]['Ethereum']==100
    categories=TvlDataset(manifest).history(interval='daily',group='category')['chart']
    assert [r['v2'] for r in categories]==[100,200,300]


def test_prepare_history_cli_uses_incremental_repair(postgres_database, tmp_path,monkeypatch,capsys):
    from yearn_data import cli,tvl_api
    path='staging';publication=tmp_path/'publication'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')])
        run(conn,2,DAY+86400,[(PARENT,'200')])
    old=TvlStore(path,publication).get();prepare(old)
    monkeypatch.setattr(tvl_api,'PRICE_REJECTIONS',tvl_api.PRICE_REJECTIONS+[
        {'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY,'priceUsd':'10','reason':'fixture'}])
    new=TvlStore(path,publication).get()
    assert new.id!=old.id
    calls=[];original=TvlDataset.iter_frames
    def frames(dataset,dates,*args,**kwargs):
        calls.append(tuple(dates))
        yield from original(dataset,dates,*args,**kwargs)
    monkeypatch.setattr(TvlDataset,'iter_frames',frames)
    monkeypatch.setattr(cli,'load_environment',lambda path:None)
    assert cli.main(['--db',str(path),'tvl','prepare-history','--publication',str(publication),
                     '--current-bridge-policy','none','--previous-dataset-id',old.id])==0
    receipt=json.loads(capsys.readouterr().out)
    assert receipt['datasetId']==new.id and receipt['recomputedDates']==[DAY]
    assert calls==[(DAY,)]
    stored=TvlDataset(new.manifest)
    assert stored.history(interval='daily')['chart'][0].get('Ethereum') is None
    assert stored.history(interval='daily')['chart'][1]['Ethereum']==200
    assert old.history(interval='daily')['chart'][0]['Ethereum']==100


def test_repair_rejects_conflicting_quote_that_would_undo_prior_rejection(postgres_database, tmp_path):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn);run(conn,1,DAY,[(PARENT,'100')])
    original=TvlStore(path,tmp_path/'publication').get()
    manifest=deepcopy(original.manifest);manifest['datasetId']='d'*64
    rejected={'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY,'priceUsd':'10','reason':'fixture'}
    manifest['priceRejections'].append(rejected)
    previous=TvlDataset(manifest);prepare(previous)
    updated=deepcopy(manifest);updated['datasetId']='e'*64
    updated['priceRejections'].append(rejected|{'priceUsd':'12'})
    with pytest.raises(ValueError,match='conflicting quotes'):
        prepare_price_repair(TvlDataset(updated),previous)
    with closing(connect(path,readonly=True)) as conn:
        assert conn.execute('SELECT 1 FROM tvl_chart_publications WHERE dataset_id=?', (updated['datasetId'],)).fetchone() is None
    assert previous.history(interval='daily')['chart'][0].get('Ethereum') is None


def test_quote_rejection_inventory_has_unique_coordinates():
    from yearn_data.tvl_api import PRICE_REJECTIONS
    coordinates=[(r['chainId'],r['asset'].lower(),r['timestamp']) for r in PRICE_REJECTIONS]
    assert len(coordinates)==len(set(coordinates))


def test_price_repair_refuses_withdrawn_rejection_even_when_another_is_added(postgres_database, tmp_path):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')]);run(conn,2,DAY+86400,[(PARENT,'200')])
    original=TvlStore(path,tmp_path/'publication').get()
    rejected={'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY,'priceUsd':'10','reason':'fixture'}
    manifest=deepcopy(original.manifest);manifest['datasetId']='d'*64
    manifest['priceRejections'].append(rejected)
    previous=TvlDataset(manifest);prepare(previous)
    updated=deepcopy(original.manifest);updated['datasetId']='e'*64
    updated['priceRejections'].append(rejected|{'timestamp':DAY+86400,'priceUsd':'20'})
    target=TvlDataset(updated)
    with pytest.raises(ValueError,match='cannot remove previous rejections'):
        prepare_price_repair(target,previous)
    assert not ready(target)
    assert previous.history(interval='daily')['chart'][0].get('Ethereum') is None


def test_price_repair_rejects_changed_close_that_moves_bridge_deduction(postgres_database, tmp_path):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,active,updated_at) VALUES (1,'v3',?,0,1)",(BRIDGE,))
        conn.execute("INSERT INTO tvl_vaults(chain_id,version,address,active,updated_at) VALUES (10,'v3',?,1,1)",(CHILD,))
        run(conn,1,DAY,[(PARENT,'100'),(BRIDGE,'100')])
        run(conn,2,DAY+86400,[(PARENT,'100'),(BRIDGE,'100')])
    previous=TvlStore(path,tmp_path/'publication',current_bridge_policy='retired-registry').get();prepare(previous)
    manifest=deepcopy(previous.manifest);manifest.update(datasetId='f'*64,asOfTimestamp=DAY)
    manifest['priceRejections'].append({'chainId':1,'asset':'0x'+'aa'*20,
        'timestamp':DAY+86400,'priceUsd':'10','reason':'fixture'})
    native=TvlDataset(manifest).history(interval='daily')['chart']
    assert native==[{'timestamp':DAY,'Ethereum':100}]
    assert previous.history(interval='daily')['chart'][0]['Ethereum']==200
    repaired=TvlDataset(manifest)
    with pytest.raises(ValueError,match='accounting close'):
        prepare_price_repair(repaired,previous)
    assert not ready(repaired)


def test_price_repair_can_copy_metadata_only_inventory_update(postgres_database, tmp_path,monkeypatch):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging'
    with closing(connect(path)) as conn:
        init_db(conn)
        run(conn,1,DAY,[(PARENT,'100')]);run(conn,2,DAY+86400,[(PARENT,'200')])
    original=TvlStore(path,tmp_path/'publication').get()
    rejected={'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY,'priceUsd':'10',
              'reason':'fixture','evidence':{'canonicalCurveQuote':{}}}
    manifest=deepcopy(original.manifest);manifest['datasetId']='d'*64
    manifest['priceRejections'].append(rejected)
    previous=TvlDataset(manifest);prepare(previous)
    updated=deepcopy(manifest);updated['datasetId']='e'*64
    updated['priceRejections'][-1]['evidence'].pop('canonicalCurveQuote')
    target=TvlDataset(updated)
    def no_recompute(dates,*args,**kwargs):
        assert tuple(dates)==()
        return iter(())
    monkeypatch.setattr(target,'iter_frames',no_recompute)
    result=prepare_price_repair(target,previous)
    assert result['recomputedDates']==[] and result['rows']==2
    assert TvlDataset(updated).history(interval='daily')==TvlDataset(manifest).history(interval='daily')|{'datasetId':target.id}


def test_rejection_evidence_does_not_publish_empty_or_repr_values():
    from yearn_data.tvl_api import PRICE_REJECTIONS
    for row in PRICE_REJECTIONS:
        evidence=row.get('evidence',{})
        assert not any(value=='None' for value in evidence.values())
        assert 'canonicalCurveQuote' not in evidence or evidence['canonicalCurveQuote']


@pytest.mark.parametrize('include_current', [False,True])
def test_price_repair_bounds_recalculation_and_copied_rows_to_publication(postgres_database, tmp_path,include_current):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    path='staging';future=DAY+2*86400
    with closing(connect(path)) as conn:
        init_db(conn)
        for offset in range(3):
            timestamp=DAY+offset*86400
            run(conn,offset+1,timestamp,[(PARENT,str(100*(offset+1)))])
            if offset<2:
                row={'chain_id':10,'vault':CHILD,'timestamp':timestamp,'block_number':100+offset,
                     'version':'v2','category':'v2','asset':'0x'+'bb'*20,'asset_units':'10',
                     'tvl_usd':'100','price_usd':'10'}
                conn.execute('INSERT INTO tvl_snapshots VALUES (?,?,?,?,?,?)',
                    (offset+1,10,CHILD,timestamp,100+offset,json.dumps(row)))
                conn.commit()
    previous=TvlStore(path,tmp_path/'publication').get();prepare(previous)
    assert previous.as_of()==DAY+86400 and future in previous.dates()
    # A legacy repair could already have persisted the partial close. It must
    # remain intact in the source while the new publication excludes it.
    with closing(connect(path)) as conn,conn:
        conn.execute('INSERT INTO tvl_chart_rows VALUES (?,?,?,?,?,?,?,?,?,?)',
            (previous.id,future,1,PARENT,'Vault','v3','v3','10','300','300'))
        for mode in ('raw','external'):
            for group,series in (('chain','Ethereum'),('category','v3')):
                conn.execute('INSERT INTO tvl_chart_totals VALUES (?,?,?,?,?,?)',
                    (previous.id,future,group,mode,series,'300'))
        conn.execute('UPDATE tvl_chart_publications SET row_count=row_count+1 WHERE dataset_id=?',(previous.id,))
    manifest=deepcopy(previous.manifest);manifest['datasetId']='f'*64
    new=[{'chainId':1,'asset':'0x'+'aa'*20,'timestamp':future,'priceUsd':'30','reason':'fixture'},
         {'chainId':1,'asset':'0x'+'aa'*20,'timestamp':DAY+3600,'priceUsd':'10','reason':'no snapshot at this timestamp'}]
    if include_current:new.append(new[0]|{'timestamp':DAY,'priceUsd':'10'})
    manifest['priceRejections']+=new
    result=prepare_price_repair(TvlDataset(manifest),previous)
    assert result['recomputedDates']==([DAY] if include_current else [])
    with closing(connect(path,readonly=True)) as conn:
        for table in ('tvl_chart_rows','tvl_chart_totals'):
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_id=? AND timestamp>?',
                                (manifest['datasetId'],previous.as_of())).fetchone()[0]==0
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_id=? AND timestamp=?',
                                (previous.id,future)).fetchone()[0]>0
    chart=TvlDataset(manifest).history(interval='daily')['chart']
    assert [point['Optimism'] for point in chart]==[100,100]
    assert chart[0].get('Ethereum')==(None if include_current else 100)
    assert chart[1]['Ethereum']==200


@pytest.mark.parametrize('price', ['72000000.00', '7.2e7'])
@pytest.mark.parametrize('add_rejection', [False, True])
def test_price_repair_ignores_equivalent_quote_reformatting(postgres_database, tmp_path, monkeypatch, price, add_rejection):
    from copy import deepcopy
    from yearn_data.tvl_history_cache import prepare_price_repair
    with closing(connect('staging')) as conn:
        init_db(conn)
        run(conn, 1, DAY, [(PARENT, '720000000')])
        run(conn, 2, DAY+86400, [(PARENT, '720000000')])
    manifest = deepcopy(TvlStore('staging', tmp_path/'tvl').get().manifest)
    quote = {'chainId':1, 'asset':'0x'+'aa'*20, 'timestamp':DAY, 'priceUsd':'72000000', 'reason':'fixture'}
    manifest.update(datasetId='d'*64, priceRejections=[quote])
    previous = TvlDataset(manifest); prepare(previous)
    updated = deepcopy(manifest); updated['datasetId']='e'*64
    updated['priceRejections'][0]['priceUsd'] = price
    if add_rejection:
        updated['priceRejections'].append(quote | {'timestamp':DAY+86400})
    target = TvlDataset(updated)
    calls=[]; original=target.iter_frames
    def frames(dates, *args, **kwargs):
        calls.append(tuple(dates))
        yield from original(dates, *args, **kwargs)
    monkeypatch.setattr(target, 'iter_frames', frames)
    expected = [DAY+86400] if add_rejection else []
    assert prepare_price_repair(target, previous)['recomputedDates'] == expected
    assert calls == [tuple(expected)]
    chart = target.history(interval='daily')['chart']
    assert chart[0].get('Ethereum') is None
    assert chart[1].get('Ethereum') == (None if add_rejection else 720000000)


@pytest.mark.parametrize('metric', [None, 'constant-price'])
def test_compact_history_limits_emitted_series_preserving_rank_and_missing_prices(monkeypatch, metric):
    """A small chart must not fail the full export's source-point limit."""
    from decimal import Decimal
    dataset = object.__new__(TvlDataset)
    dataset.run_ids = [1]
    dataset.manifest = {'publishedAt': 1}
    dataset.id = 'fixture'
    dates = (DAY, DAY+86400)
    def row(vault, value, units):
        return {'chain_id': 1, 'vault': vault, 'name': vault, 'version': 'v3',
                'tvl_usd': value, 'external_tvl_usd': value, 'asset_units': units}
    frames = [(dates[0], (row('a', '100', '100'), row('b', '200', '2'), row('c', '30', None), row('d', None, None))),
              (dates[1], (row('a', '10', '100'), row('b', '300', '2'), row('d', None, None)))]
    monkeypatch.setattr(dataset, 'dates', lambda: dates)
    monkeypatch.setattr(dataset, 'as_of', lambda: dates[-1])
    monkeypatch.setattr(dataset, 'history_prepared', lambda: False)
    monkeypatch.setattr(dataset, 'history_rows', lambda *args: iter(frames))
    monkeypatch.setattr(dataset, 'streamed_history_rows', lambda *args: iter(frames))
    prices = {(1, 'a'): Decimal(10), (1, 'b'): Decimal(1)}
    monkeypatch.setattr(dataset, 'price_references', lambda *args: ({k: {'vault': k[1]} for k in prices}, prices))
    full = dataset._history('vault', 'external', 'daily', None, None, 1, True)
    with pytest.raises(ValueError, match='point limit'):
        dataset._history('vault', 'external', 'daily', None, None, 1, True, 4)
    compact = dataset._history('vault', 'external', 'daily', None, None, 1, True, 4, 1, metric)
    assert compact['pointCount'] == 4
    # Bounded uncapped internal charts reuse the materialized source in both
    # passes, avoiding a second nested-position calculation during emission.
    with monkeypatch.context() as cached:
        calls = []
        def cached_rows(timestamps, chain_id):
            calls.append((timestamps, chain_id))
            return tuple(frames)
        cached.setattr(dataset, 'history_rows', cached_rows)
        cached.setattr(dataset, 'streamed_history_rows', lambda *args: pytest.fail('cached chart streamed again'))
        uncapped = dataset._history('vault', 'external', 'daily', None, None, 1, True, top=1, top_metric=metric)
        assert uncapped == compact
        assert calls == [(dates, 1), (dates, 1)]
    a, b = ('a (1:a)', 'b (1:b)')
    if metric is None:
        expected = chart_response(full, top=1)
        assert compact['actualChart'] == expected['actualChart']
        assert compact['constantPriceChart'] == expected['constantPriceChart']
        assert compact['meta']['topSeries'] == [b, 'All other vaults']
        assert compact['meta']['sourceSeriesCount'] == 3
        assert compact['constantPriceChart'] == [{'timestamp': date, 'Price-neutral TVL': 1002} for date in dates]
    else:
        assert compact['meta']['topSeries'] == [a, 'All other vaults']
        assert compact['meta']['sourceSeriesCount'] == 2  # c has no reference, d has no value.
        assert compact['constantPriceChart'] == [{'timestamp': date, a: 1000, 'All other vaults': 2} for date in dates]
        assert compact['actualChart'] == [{'timestamp': dates[0], a: 100, 'All other vaults': 230},
                                          {'timestamp': dates[1], a: 10, 'All other vaults': 300}]
    if metric == 'constant-price':
        # Declare unpriced actual capital even when all priced vaults fit in top.
        all_priced = dataset._history('vault', 'external', 'daily', None, None, 1, True, 10, 2, metric)
        assert all_priced['series'] == [a, b, 'All other vaults']
        assert all_priced['meta']['topSeries'] == all_priced['series']
        assert all_priced['actualChart'][0]['All other vaults'] == 30
        assert 'All other vaults' not in all_priced['constantPriceChart'][0]
    # A later alphabetically earlier vault must not win a tie. Ranking keeps
    # each series' last observed value even when absent from the latest close.
    frames[:] = [(dates[0], (row('b', '200', '1000'),)),
                 (dates[1], (row('a', '200', '100'), row('c', '0', None)))]
    sparse = dataset._history('vault', 'external', 'daily', None, None, 1, True, 4, 1, metric)
    assert sparse['meta']['topSeries'] == [b, 'All other vaults']
    assert sparse['actualChart'] == [{'timestamp': dates[0], b: 200},
                                      {'timestamp': dates[1], 'All other vaults': 200}]
    assert sparse['constantPriceChart'] == ([{'timestamp': date, 'Price-neutral TVL': 1000} for date in dates]
        if metric is None else [{'timestamp': dates[0], b: 1000}, {'timestamp': dates[1], 'All other vaults': 1000}])


@pytest.mark.parametrize(('group', 'constant'), [('chain', True), ('vault', False)])
def test_internal_compact_history_rejects_unsupported_charts_before_reading(monkeypatch, group, constant):
    dataset = object.__new__(TvlDataset)
    def unexpected_read(*args):
        pytest.fail('invalid compact chart reached history reads')
    monkeypatch.setattr(dataset, 'dates', unexpected_read)
    monkeypatch.setattr(dataset, 'history_rows', unexpected_read)
    monkeypatch.setattr(dataset, 'streamed_history_rows', unexpected_read)
    with pytest.raises(ValueError, match='top requires constant-price vault history'):
        dataset.history(group=group, constant=constant, top=1)


def test_compact_metric_requires_supported_top_history_contract():
    from yearn_data.tvl_api import tvl_response
    class UnusedStore:
        def get(self, *args):
            pytest.fail('invalid metric reached publication lookup')
    for query in ('topMetric=constant-price',
                  'groupBy=vault&format=chart&top=1&topMetric=unsupported'):
        status, result = tvl_response(UnusedStore(), '/api/tvl/history/runs/latest/constant-price?'+query)
        assert status == 400
        assert 'topMetric' in result['error']
