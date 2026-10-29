"""Run daily TVL in restartable monthly batches, using the existing accounting.

Only transport and checkpoints live here. No balance, valuation, or overlap
formula is duplicated. Run with --help; progress is written beside the exports.
"""
from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import time

import requests
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from yearn_data.config import load_environment, TVL_EXCLUDED_CHAIN_IDS
from yearn_data.multicall import MULTICALL3, MULTICALL3_ABI
from yearn_data.pricing import fetch_yearn_price, fetch_yearn_prices_batch, yearn_prices_token_key
from yearn_data.pricing import YearnPricesAuthenticationError, YearnPricesConfigurationError
from yearn_data.storage import connect, init_db, database_reference
from yearn_data.tvl import export_tvl, BACKFILL_SCHEMA
from yearn_data import tvl_sources as sources

from yearn_data.tvl_batch import BatchedReader, Prices as CachedPrices

UTC = timezone.utc
SCHEMA = BACKFILL_SCHEMA


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def call_data(signature, arg=None):
    data = '0x' + bytes(Web3.keccak(text=signature)[:4]).hex()
    return data if arg is None else data + arg.lower().removeprefix('0x').rjust(64, '0')


class Prices(CachedPrices):
    def __init__(self, conn):
        super().__init__(conn, retry_missing=True, retry_statuses=('retryable','invalid','unavailable'),
                         fetch_batch=fetch_yearn_prices_batch,
                         fetch_exact=fetch_yearn_price)



def deployment(reader, vault):
    address = vault['address'].lower()
    known = vault['deployment_block']
    if known and reader.exists(address,known) and not reader.exists(address,known-1):
        block = known
    else:
        if not reader.exists(address,int(reader.head['number'])):
            raise ValueError('no contract at finalized head')
        lo,hi = 0,int(reader.head['number'])
        while lo<hi:
            mid = (lo+hi)//2
            if reader.exists(address,mid): hi=mid
            else: lo=mid+1
        block = lo
    return block,int(reader.w3.eth.get_block(block)['timestamp'])


def monthly_ranges(start,end):
    cursor = datetime.fromtimestamp(end,UTC).replace(day=1,hour=0,minute=0,second=0)
    while int(cursor.timestamp()) <= end:
        last = cursor.replace(day=calendar.monthrange(cursor.year,cursor.month)[1],hour=23,minute=59,second=59)
        lo,hi = max(start,int(cursor.timestamp())+86399),min(end,int(last.timestamp()))
        if lo<=hi: yield lo,hi
        year,month = (cursor.year-1,12) if cursor.month==1 else (cursor.year,cursor.month-1)
        cursor = cursor.replace(year=year,month=month)
        if int(last.timestamp())<start: break


def batch_export_complete(out,run_id):
    try:
        receipt = json.loads((out/'export-complete.json').read_text())
        return receipt == {'run_id':run_id} and all(
            (out/name).is_file() and (out/name).stat().st_size > 0
            for name in ('vaults.csv','positions.csv','history.csv','tvl.json'))
    except (OSError,ValueError):
        return False


def export_batch(conn,out,run_id):
    # Only publish completion after every output is written. A failed export
    # keeps its saved collection and will be retried on the next invocation.
    receipt = out/'export-complete.json'
    receipt.unlink(missing_ok=True)
    export_tvl(conn,out,run_id)
    atomic_json(receipt,{'run_id':run_id})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',required=True,help='Postgres logical database: neon or staging')
    p.add_argument('--heads',type=Path,required=True,help='Verified finalized-head preflight JSON')
    p.add_argument('--env',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--from-date',default='2020-01-01')
    p.add_argument('--to-date',required=True)
    p.add_argument('--limit-batches',type=int)
    args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    lock_path = args.out/'.backfill.lock'
    lock=lock_path.open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    load_environment([args.env])
    conn=connect(args.db,direct=True)
    # A direct connection retains the lock for this backfill job.
    if not conn.execute('SELECT pg_try_advisory_lock(743219887)').fetchone()[0]:
        conn.close()
        raise ValueError('a TVL backfill already holds the database lock')
    init_db(conn)
    conn.executescript(SCHEMA)
    heads={r['chain_id']:r for r in json.loads(args.heads.read_text())}
    start=int(datetime.strptime(args.from_date,'%Y-%m-%d').replace(tzinfo=UTC).timestamp())+86399
    end=int(datetime.strptime(args.to_date,'%Y-%m-%d').replace(tzinfo=UTC).timestamp())+86399
    state={'phase':'preparing','pid':os.getpid(),'from':args.from_date,'through':args.to_date,
           'interval':'daily UTC close','price_source':'yearn-prices','db':database_reference(args.db),
           'scope':'known catalog; known candidate overlap mappings; curation reported separately',
           'excluded_chain_ids':sorted(TVL_EXCLUDED_CHAIN_IDS),
           'pending_chains':[], 'price_limits':[], 'completed_batches':0}
    def publish(**values):
        state.update(values,updated_at=int(time.time()))
        state['completed_batches']=conn.execute("SELECT count(*) FROM tvl_backfill_batches WHERE status IN ('complete','incomplete')").fetchone()[0]
        state['snapshots']=conn.execute('SELECT count(*) FROM tvl_snapshots s JOIN tvl_backfill_batches b ON b.run_id=s.run_id').fetchone()[0]
        atomic_json(args.out/'status.json',state)
    discovery = conn.execute('SELECT summary_json FROM tvl_discovery_runs WHERE summary_json IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    if discovery:
        summary = json.loads(discovery['summary_json'])
        state['catalog_status'] = summary['status']
        state['catalog_failures'] = [f for source in summary['sources'] for f in source.get('failures',[])]
    publish()
    try:
        all_vaults=[dict(r) for r in conn.execute('SELECT * FROM tvl_vaults')
                    if r['chain_id'] not in TVL_EXCLUDED_CHAIN_IDS]
        chain_vaults={}
        for v in all_vaults:chain_vaults.setdefault(v['chain_id'],[]).append(v)
        readers={};births={};starts={}
        for chain,vaults in chain_vaults.items():
            h=heads.get(chain)
            if not h or h['status']!='ready':
                state['pending_chains'].append({'chain_id':chain,'vaults':len(vaults),'reason':h.get('reason','missing_archive_rpc') if h else 'missing_archive_rpc'})
                continue
            try:
                reader=sources.ArchiveReader(chain)
                if int(reader.w3.eth.chain_id)!=chain:raise ValueError('RPC chain mismatch')
                pinned=reader.w3.eth.get_block(h['head_block'])
                if bytes(pinned['hash']).hex()!=h['head_hash']:raise ValueError('pinned head changed')
                reader.head=pinned
                readers[chain]=reader;births[chain]={}
                try:
                    yearn_prices_token_key(chain,'0x'+'00'*20)
                except ValueError:
                    state['price_limits'].append({'chain_id':chain,'reason':'unsupported_yearn_prices_namespace','balances':'archive-rpc'})
            except Exception as exc:
                state['pending_chains'].append({'chain_id':chain,'vaults':len(vaults),'reason':type(exc).__name__});continue
            publish(active_chain=chain,phase='resolving deployment dates')
            cached={r['address']:r for r in conn.execute("SELECT * FROM tvl_backfill_births WHERE chain_id=? AND status='ok'",(chain,))}
            pending=[v for v in vaults if v['address'].lower() not in cached]
            def resolve(v):
                try:
                    block,ts=deployment(reader,v)
                    return (chain,v['address'].lower(),block,ts,'ok',None)
                except Exception as exc:
                    return (chain,v['address'].lower(),None,None,'unavailable',type(exc).__name__)
            with ThreadPoolExecutor(max_workers=6) as pool:
                for index,result in enumerate(pool.map(resolve,pending),1):
                    if index%20==0: publish(deployment_progress={"chain_id":chain,"checked":index,"remaining":len(pending)-index})
                    with conn:conn.execute('INSERT INTO tvl_backfill_births VALUES (?,?,?,?,?,?) ON CONFLICT(chain_id,address) DO UPDATE SET block_number=excluded.block_number, timestamp=excluded.timestamp, status=excluded.status, reason=excluded.reason',result)
            rows=conn.execute('SELECT * FROM tvl_backfill_births WHERE chain_id=?',(chain,)).fetchall()
            for r in rows:
                if r['status']=='ok':births[chain][r['address']]=r['block_number']
            known_times=[r['timestamp'] for r in rows if r['status']=='ok']
            earliest=min(known_times) if len(known_times)==len(vaults) else max(start-86399,h['genesis_timestamp'])
            starts[chain]=max(start,(earliest//86400)*86400+86399)
            publish(deployment_dates={'chain_id':chain,'verified':len(known_times),'unavailable':len(vaults)-len(known_times)})
        prices=Prices(conn)
        sources.sync_strategies(conn)
        tasks=[]
        for chain in readers:
            for lo,hi in monthly_ranges(starts[chain],end):tasks.append((lo,hi,chain))
        tasks.sort(reverse=True)
        state['scheduled_batches']=len(tasks)
        performed=0
        for lo,hi,chain in tasks:
            month=datetime.fromtimestamp(lo,UTC).strftime('%Y-%m')
            batch_out=args.out/'batches'/str(chain)/month
            existing=conn.execute('SELECT * FROM tvl_backfill_batches WHERE chain_id=? AND from_timestamp=? AND to_timestamp=?',(chain,lo,hi)).fetchone()
            if existing and existing['status'] in ('complete','incomplete'):
                if not batch_export_complete(batch_out,existing['run_id']):
                    publish(phase='restoring exports',active_chain=chain,active_month=month,active_run=existing['run_id'])
                    export_batch(conn,batch_out,existing['run_id'])
                continue
            selected=[v for v in chain_vaults[chain] if v['address'].lower() not in births[chain] or births[chain][v['address'].lower()]<=readers[chain].block_at(hi)]
            if not selected:continue
            edges=sources.candidate_edges(conn,selected)
            reader=BatchedReader(chain,conn,heads[chain],selected,edges,births[chain],prices)
            publish(phase='running',active_chain=chain,active_month=month,selected_vaults=len(selected))
            with conn:conn.execute("INSERT INTO tvl_backfill_batches VALUES (?,?,?,NULL,'running') ON CONFLICT(chain_id,from_timestamp,to_timestamp) DO UPDATE SET run_id=excluded.run_id, status=excluded.status",(chain,lo,hi))
            def progress(message):
                print(message,flush=True)
                run=conn.execute('SELECT max(id) FROM tvl_runs').fetchone()[0]
                with conn:conn.execute('UPDATE tvl_backfill_batches SET run_id=? WHERE chain_id=? AND from_timestamp=? AND to_timestamp=?',(run,chain,lo,hi))
                publish(active_run=run,last_message=message)
                prices.cache.clear()
            run=sources.collect_tvl(conn,lo,hi,chain_ids=[chain],addresses=[v['address'] for v in selected],reader_factory=lambda _:reader,progress=progress)
            status=conn.execute('SELECT status FROM tvl_runs WHERE id=?',(run,)).fetchone()[0]
            with conn:conn.execute('UPDATE tvl_backfill_batches SET run_id=?,status=? WHERE chain_id=? AND from_timestamp=? AND to_timestamp=?',(run,status,chain,lo,hi))
            export_batch(conn,batch_out,run)
            performed+=1
            publish(last_batch_status=status)
            if args.limit_batches and performed>=args.limit_batches:
                publish(phase='paused after requested batch limit');return
        publish(phase='finished; inspect incomplete batches and pending chains')
    except BaseException as exc:
        publish(phase='stopped',failure_class=type(exc).__name__)
        raise
    finally:
        conn.close()
        lock.close()


if __name__=='__main__':main()
