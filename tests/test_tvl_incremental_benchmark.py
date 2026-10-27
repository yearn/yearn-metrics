"""Reproducible local cost receipt; assertions protect work and known results."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import time

from yearn_data import tvl_history_cache as cache
from yearn_data.storage import connect, init_db
from yearn_data.tvl_api import TvlStore, TvlDataset
from test_tvl_api import run, DAY, PARENT, CHILD


def benchmark_history(root, monkeypatch, full_cache=cache):
    vaults = [PARENT,CHILD]+[f'0x{i+1:040x}' for i in range(78)]
    with closing(connect('staging')) as conn:
        init_db(conn)
        for day in range(32):
            run(conn,day+1,DAY+day*86400,[(v,'100') for v in vaults],[(CHILD,'50')])
    old = TvlStore('staging',root).get()
    cache.prepare(old)
    results=[]
    for phase in ('daily-refresh','targeted-backfill'):
        with closing(connect('staging')) as conn:
            if phase=='daily-refresh':
                run(conn,33,DAY+32*86400,[(v,'100') for v in vaults],[(CHILD,'50')])
            else:
                run(conn,34,DAY+15*86400,[(CHILD,'120')],[(CHILD,'60')])
        candidate = TvlStore('staging',root).get()
        manifest=deepcopy(candidate.manifest)
        manifest['datasetId']=('a' if phase=='daily-refresh' else 'b')*64
        full=TvlDataset(manifest)
        values=[]
        for mode,dataset in [('incremental',candidate),('full',full)]:
            metrics=dict(selectedInputRowsRead=0,totalsRowsRead=0,accountedDates=0)
            original_inputs=TvlDataset.selected_rows
            original_account=TvlDataset.account_frame
            def inputs(self,*args,**kwargs):
                for row in original_inputs(self,*args,**kwargs):
                    metrics['selectedInputRowsRead']+=1
                    yield row
            def account(self,*args,**kwargs):
                metrics['accountedDates']+=1
                return original_account(self,*args,**kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(TvlDataset,'selected_rows',inputs)
                patch.setattr(TvlDataset,'account_frame',account)
                for module in {cache,full_cache}:
                    original_rows=module.rows
                    def counted_rows(*args,_original=original_rows,**kwargs):
                        for timestamp,points in _original(*args,**kwargs):
                            metrics['totalsRowsRead']+=len(points)
                            yield timestamp,points
                    patch.setattr(module,'rows',counted_rows)
                started=time.perf_counter()
                if mode=='incremental':
                    receipt=cache.prepare(dataset,backfill=(DAY+15*86400,DAY+15*86400)
                                          if phase=='targeted-backfill' else None)
                elif full_cache is cache:
                    receipt=cache.prepare(dataset,full_rebuild=True)
                else:
                    receipt=full_cache.prepare(dataset)
                elapsed=time.perf_counter()-started
            with closing(connect('staging',readonly=True)) as conn:
                rows=[tuple(r) for r in conn.execute('''SELECT timestamp,chain_id,vault,name,category,version,
                    asset_units,tvl_usd,external_tvl_usd FROM tvl_chart_rows WHERE dataset_id=?
                    ORDER BY timestamp,chain_id,vault''',(dataset.id,))]
                totals=[tuple(r) for r in conn.execute('''SELECT timestamp,group_by,mode,series,value
                    FROM tvl_chart_totals WHERE dataset_id=? ORDER BY timestamp,group_by,mode,series''',(dataset.id,))]
            values.append(dict(mode=mode,seconds=elapsed,**metrics,chartRowsWritten=len(rows),
                               totalsRowsWritten=len(totals),copiedChartRows=receipt.get('copiedRows',0),
                               copiedTotalsRows=receipt.get('totalsRowsCopied',0),
                               chartCopySeconds=receipt.get('copySeconds',0),
                               totalsCopySeconds=receipt.get('totalsCopySeconds',0),
                               recomputedDates=len(receipt.get('recomputedDates',[])) if mode=='incremental' else 33,
                               reusedDates=len(receipt.get('reusedDates',[])) if mode=='incremental' else 0,
                               digest=hashlib.sha256(json.dumps([rows,totals]).encode()).hexdigest()))
        assert values[0]['digest']==values[1]['digest']
        assert values[0]['recomputedDates']==values[0]['accountedDates']==1
        assert values[0]['reusedDates']==32
        assert values[1]['accountedDates']==33
        assert values[0]['totalsRowsRead']==80 and values[1]['totalsRowsRead']==2640
        assert values[0]['selectedInputRowsRead']==values[1]['selectedInputRowsRead']==2673
        assert values[0]['copiedChartRows']==2560
        assert values[0]['copiedTotalsRows']==128
        chart=candidate.history(interval='daily')['chart']
        assert chart[0]['Ethereum']==7950
        if phase=='targeted-backfill':
            assert chart[15]['Ethereum']==7960
        results.append(dict(phase=phase,runs=values))
    return dict(inventory=80,initialDates=32,finalDates=33,positionEdgesPerDate=1,
                limits='Offline fixed inputs and local Postgres; logical rows returned/read/written, not physical page or query-plan I/O; selected inputs and immutable row copy remain full-history costs.',
                phases=results)


def test_incremental_refresh_and_backfill_skip_accounting_unchanged_history(postgres_database,tmp_path,monkeypatch):
    receipt=benchmark_history(tmp_path/'pub',monkeypatch)
    print('\nHISTORY_BENCHMARK='+json.dumps(receipt,sort_keys=True))
