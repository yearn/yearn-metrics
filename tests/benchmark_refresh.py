"""Explicit offline measurement, not a CI timing threshold.

Run the documented command with both exact catch-up module paths. The ordinary
workflow regressions own correctness; this harness records the full TVL barrier.
"""
from contextlib import closing
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest

from test_refresh_workflow import ASSET,HASH,Source,workflow as workflow
from yearn_data import refresh
from yearn_data.coverage import commit_range
from yearn_data.history_sources import report_scope,write_rows
from yearn_data.storage import connect


class MeasuredConnection:
    def __init__(self,conn,reads,delay):self.conn=conn;self.reads=reads;self.delay=delay
    def __getattr__(self,name):return getattr(self.conn,name)
    def __enter__(self):self.conn.__enter__();return self
    def __exit__(self,*args):return self.conn.__exit__(*args)
    def execute(self,sql,params=()):
        if sql.lstrip().upper().startswith('SELECT') and 'FROM history_coverage' in sql:
            self.reads.append(1)
            if self.delay:time.sleep(self.delay)
        return self.conn.execute(sql,params)


@pytest.mark.parametrize('trial',range(3))
@pytest.mark.parametrize('implementation',('before','after'))
def test_full_refresh_tvl_dispatch_measurement(workflow,monkeypatch,implementation,trial):
    options,_=workflow
    path=Path(os.environ['YEARN_BENCHMARK_'+implementation.upper()+'_CATCHUP'])
    spec=importlib.util.spec_from_file_location('yearn_data._benchmark_'+implementation,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(refresh.catchup,'catch_up',module.catch_up)
    delay=float(os.environ.get('YEARN_BENCHMARK_ROUNDTRIP_MS','0'))/1000
    source=Source('eth')
    calls=[]
    native_reports=source.reports
    def reports(*args):calls.append((args[0]['address'],args[1],args[2]));return native_reports(*args)
    source.reports=reports
    monkeypatch.setattr(refresh,'chain_source',lambda _:source)
    with closing(connect('staging')) as conn:
        with conn:
            conn.execute('DELETE FROM vaults');conn.execute('DELETE FROM tvl_vaults')
            for i in range(120):
                address=f'0x{i+10000:040x}'
                for table in ('vaults','tvl_vaults'):
                    conn.execute(f'''INSERT INTO {table}(chain_id,version,address,asset,asset_decimals,
                        api_version,active,updated_at) VALUES(1,'v3',?,?,6,'3.0.2',?,0)''',(address,ASSET,i%2))
        vaults=[dict(r) for r in conn.execute('SELECT * FROM vaults ORDER BY address')]
        for i,vault in enumerate(vaults[:80]):
            commit_range(conn,report_scope(vault),0,172 if i<40 else 0,source='rpc',end_hash=HASH,run_id=None,
                write=lambda db,v=vault,n=i:write_rows(db,'strategy_reports',native_reports(v,0,172,'rpc') if n<40 else [],report_metadata=v))
    reads=[]
    monkeypatch.setattr(refresh,'connect',lambda *a,**k:MeasuredConnection(connect(*a,**k),reads,delay))
    phases=[]
    for phase in ('extend','covered-repeat'):
        events=[];barrier=[]
        calls.clear();reads.clear()
        started=time.perf_counter()
        def progress(event):
            events.append(event)
            if event['event']=='tvl-collection-started' and not barrier:barrier.append(time.perf_counter()-started)
        # A repeat already has TVL closes: explicit repair makes both trials
        # dispatch the same native collection rather than timing a skipped stage.
        from dataclasses import replace
        measured_options=replace(options,repair_history=True)
        result=refresh.run_refresh(measured_options,progress=progress)
        elapsed=time.perf_counter()-started
        assert result['status']=='prepared' and result['result']['publishReady'] is True,result
        assert len(barrier)==1
        assert len(calls)==(80 if phase=='extend' else 0)
        phases.append(dict(phase=phase,secondsToFirstTvlDispatch=barrier[0],secondsToPrepared=elapsed,
                           coverageSelects=len(reads),reportFetches=len(calls),
                           stages=[dict(stage=e['stage'],seconds=e['seconds']) for e in events if e['event']=='stage-completed']))
    with closing(connect('staging',readonly=True)) as conn:
        rows=[dict(r) for r in conn.execute('SELECT * FROM strategy_reports ORDER BY chain_id,tx_hash,log_index')]
        coverage=[tuple(r) for r in conn.execute("SELECT target,from_block,to_block,row_count FROM history_coverage WHERE family='reports' ORDER BY target,from_block,to_block")]
        assert len(rows)==120 and {r['gain_raw'] for r in rows}=={'1000000'}
        earned=json.loads(conn.execute("SELECT row_json FROM analysis_outputs WHERE run_id=? AND name='total_yield_summary'",(result['result']['earningsRunId'],)).fetchone()[0])
        assert Decimal(earned['net_yield_usd'])==Decimal('240')
        fees=str(conn.execute("SELECT sum(CAST(json_extract(row_json,'$.total_fees_paid_usd') AS NUMERIC)) FROM analysis_outputs WHERE run_id=? AND name='fee_usd_events'",(result['result']['feesRunId'],)).fetchone()[0])
        assert Decimal(fees)==Decimal('24')
    receipt=dict(implementation=implementation,trial=trial,moduleSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                 inventory=120,active=60,inactive=60,initialFullyCovered=40,initialPartiallyCovered=40,
                 initialUncovered=40,cutoff=options.cutoff,simulatedCoverageReadLatencyMs=delay*1000,
                 reportRows=len(rows),reportsDigest=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest(),
                 coverageRows=len(coverage),coverageDigest=hashlib.sha256(json.dumps(coverage).encode()).hexdigest(),
                 knownNetYieldUsd=earned['net_yield_usd'],feeSummary=fees,phases=phases)
    print('\nREFRESH_BENCHMARK='+json.dumps(receipt,sort_keys=True))
    if output:=os.environ.get('YEARN_BENCHMARK_RECEIPTS'):
        with Path(output).open('a') as handle:handle.write(json.dumps(receipt,sort_keys=True)+'\n')
