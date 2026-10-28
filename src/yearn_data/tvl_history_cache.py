"""Prepared chart rows for an immutable TVL publication.

Run the existing accounting once, then serve slim dated rows instead of repeatedly
loading raw snapshots and positions. The completion marker and rows commit together.
"""
from contextlib import closing
import time
from collections import defaultdict
from decimal import Decimal,localcontext

from .storage import connect, table_exists
from .tvl import decimal

VERSION = 'dated-accounting-2'
FIELDS = ('chain_id','vault','name','category','version','asset_units','tvl_usd','external_tvl_usd')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS tvl_chart_publications (
    dataset_id TEXT PRIMARY KEY, version TEXT NOT NULL,
    row_count INTEGER NOT NULL, prepared_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tvl_chart_rows (
    dataset_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
    chain_id INTEGER NOT NULL, vault TEXT NOT NULL, name TEXT,
    category TEXT, version TEXT NOT NULL, asset_units TEXT, tvl_usd TEXT,
    external_tvl_usd TEXT,
    PRIMARY KEY(dataset_id,timestamp,chain_id,vault));
CREATE INDEX IF NOT EXISTS tvl_chart_rows_vault_idx
ON tvl_chart_rows(dataset_id,chain_id,vault,timestamp);
CREATE TABLE IF NOT EXISTS tvl_chart_totals (
    dataset_id TEXT NOT NULL, timestamp INTEGER NOT NULL,
    group_by TEXT NOT NULL, mode TEXT NOT NULL, series TEXT NOT NULL,
    value TEXT NOT NULL, PRIMARY KEY(dataset_id,timestamp,group_by,mode,series));
'''


def ready(dataset):
    with closing(connect(dataset.database,readonly=True)) as conn:
        return table_exists(conn,'tvl_chart_publications') and conn.execute(
            'SELECT 1 FROM tvl_chart_publications WHERE dataset_id=? AND version IN (?,?)',
            (dataset.id,VERSION,VERSION+'+totals')).fetchone() is not None


def totals_ready(dataset):
    with closing(connect(dataset.database,readonly=True)) as conn:
        return table_exists(conn,'tvl_chart_publications') and conn.execute(
            'SELECT 1 FROM tvl_chart_publications WHERE dataset_id=? AND version=?',
            (dataset.id,VERSION+'+totals')).fetchone() is not None


def populated_dates(dataset, timestamps):
    """Keep dates with accounted rows, including rows with unavailable values."""
    if not timestamps:
        return ()
    values = ','.join('(?)' for _ in timestamps)
    statement = f"""SELECT requested.timestamp FROM (VALUES {values}) AS requested(timestamp)
        WHERE EXISTS (SELECT 1 FROM tvl_chart_rows r
            WHERE r.dataset_id=? AND r.timestamp=requested.timestamp)
        ORDER BY requested.timestamp"""
    with closing(connect(dataset.database, readonly=True)) as conn:
        return tuple(row['timestamp'] for row in conn.execute(statement, (*timestamps, dataset.id)))


def totals(dataset,timestamps,group,mode,chain_id=None):
    if not timestamps:return []
    marks=','.join('?' for _ in timestamps)
    statement=f'SELECT timestamp,series,value FROM tvl_chart_totals WHERE dataset_id=? AND timestamp IN ({marks}) AND group_by=? AND mode=?'
    args=(dataset.id,*timestamps,group,mode)
    if chain_id is not None:
        from .tvl_api import CHAINS
        statement+=' AND series=?';args+=(CHAINS.get(chain_id,f'Chain {chain_id}'),)
    statement+=' ORDER BY timestamp,series'
    with closing(connect(dataset.database,readonly=True)) as conn:
        return list(conn.execute(statement,args))


def prepare_totals(dataset):
    if totals_ready(dataset):return
    from .tvl_api import CHAINS
    dates=tuple(date for date in dataset.dates() if date<=dataset.as_of())
    with closing(connect(dataset.database,direct=True)) as conn:
        conn.executescript(SCHEMA)
        with conn:
            # Keep the copy-source marker and its totals in one snapshot even
            # if consolidation retires that publication between statements.
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            if not conn.execute('SELECT pg_try_advisory_xact_lock(743219889)').fetchone()[0]:
                raise ValueError('TVL chart preparation is already running')
            if totals_ready(dataset):return
            from .tvl_incremental import preparation
            plan = preparation(conn,dataset.id) if table_exists(conn,'tvl_chart_preparations') else None
            previous = plan.get('previousDatasetId') if plan else None
            reusable = bool(previous and conn.execute('SELECT 1 FROM tvl_chart_publications WHERE dataset_id=? AND version=?',
                (previous,VERSION+'+totals')).fetchone())
            reused = plan['reusedDates'] if reusable else []
            reused_set = set(reused)
            pending = [day for day in dates if day not in reused_set]
            started = time.monotonic()
            copied = read = written = 0
            conn.execute('DELETE FROM tvl_chart_totals WHERE dataset_id=?',(dataset.id,))
            for offset in range(0,len(reused),400):
                days=reused[offset:offset+400]
                marks=','.join('?' for _ in days)
                copied+=conn.execute(f'''INSERT INTO tvl_chart_totals SELECT ?,timestamp,group_by,mode,series,value
                    FROM tvl_chart_totals WHERE dataset_id=? AND timestamp IN ({marks})''',(dataset.id,previous,*days)).rowcount
            copy_seconds=time.monotonic()-started
            for offset in range(0,len(pending),31):
                batch=[]
                for timestamp,points in rows(dataset,pending[offset:offset+31]):
                    read+=len(points)
                    groups=defaultdict(list)
                    for row in points:
                        for mode,field in (('raw','tvl_usd'),('external','external_tvl_usd')):
                            if row[field] is None:continue
                            value=Decimal(row[field])
                            groups[('chain',mode,CHAINS.get(row['chain_id'],f"Chain {row['chain_id']}"))].append(value)
                            groups[('category',mode,row['category'])].append(value)
                    with localcontext() as ctx:
                        ctx.prec=78
                        batch.extend((dataset.id,timestamp,*identity,str(sum(values,Decimal(0))))
                                     for identity,values in groups.items())
                conn.executemany('INSERT INTO tvl_chart_totals VALUES (?,?,?,?,?,?)',batch)
                written+=len(batch)
            conn.execute('UPDATE tvl_chart_publications SET version=? WHERE dataset_id=?',(VERSION+'+totals',dataset.id))
            if plan:
                import json
                plan.update(status='prepared',totalsRowsRead=read,totalsRowsCopied=copied,totalsRowsWritten=written,
                            totalsCopySeconds=copy_seconds,totalsSeconds=time.monotonic()-started)
                conn.execute('UPDATE tvl_chart_preparations SET receipt_json=? WHERE dataset_id=?',
                             (json.dumps(plan,sort_keys=True),dataset.id))


def rows(dataset,timestamps,chain_id=None):
    if not timestamps:return
    marks = ','.join('?' for _ in timestamps)
    statement = f'SELECT timestamp,{",".join(FIELDS)} FROM tvl_chart_rows WHERE dataset_id=? AND timestamp IN ({marks})'
    args = (dataset.id,*timestamps)
    if chain_id is not None:
        statement += ' AND chain_id=?';args += (chain_id,)
    statement += ' ORDER BY timestamp,chain_id,vault'
    with closing(connect(dataset.database,readonly=True)) as conn, conn:
        records = conn.stream(statement,args)
        from itertools import groupby
        for timestamp,group in groupby(records,lambda row:row['timestamp']):
            yield timestamp,tuple({field:row[field] for field in FIELDS} for row in group)


def references(dataset,window):
    if not window:return
    marks=','.join('?' for _ in window)
    statement=f'''WITH candidates AS (
        SELECT timestamp,chain_id,vault,asset_units,tvl_usd,
            ROW_NUMBER() OVER (PARTITION BY chain_id,vault ORDER BY timestamp) n
        FROM tvl_chart_rows WHERE dataset_id=? AND timestamp IN ({marks})
        AND CAST(asset_units AS REAL)>0 AND CAST(tvl_usd AS REAL)>0)
        SELECT timestamp,chain_id,vault,asset_units,tvl_usd FROM candidates
        WHERE n<=7 ORDER BY timestamp,chain_id,vault'''
    with closing(connect(dataset.database,readonly=True)) as conn:
        yield from (dict(row) for row in conn.execute(statement,(dataset.id,*window)))


def prepare(dataset,progress=None,*,full_rebuild=False,backfill=None):
    if ready(dataset):
        if full_rebuild or backfill is not None:
            raise ValueError('already prepared immutable dataset; collect/publish a new revision for a rebuild')
        had_totals = totals_ready(dataset)
        prepare_totals(dataset)
        with closing(connect(dataset.database,readonly=True)) as conn:
            if table_exists(conn,'tvl_chart_preparations'):
                from .tvl_incremental import preparation
                receipt = preparation(conn,dataset.id)
                if receipt:
                    return receipt|{'status':'already-prepared' if had_totals else 'prepared'}
        return {'datasetId':dataset.id,'status':'already-prepared' if had_totals else 'prepared'}
    from .tvl_incremental import build, preparation
    build(dataset, progress, full_rebuild=full_rebuild, backfill=backfill)
    prepare_totals(dataset)
    with closing(connect(dataset.database,readonly=True)) as conn:
        receipt = preparation(conn, dataset.id)
        if receipt is None:
            raise ValueError('prepared TVL dataset was retired before its receipt could be read; retry preparation')
        return receipt


def prepare_price_repair(dataset, previous):
    """Copy an immutable prepared publication, recalculating only newly rejected days.

    All accounting inputs and policies other than quote rejection must be identical.
    A marker commits only with the copied rows and recomputed daily totals.
    """
    from .tvl_api import CHAINS, encoded
    if not ready(previous) or not totals_ready(previous):
        raise ValueError('previous TVL history and totals must be prepared')
    if dataset.as_of()!=previous.as_of():
        raise ValueError('price repair must preserve the accounting close')
    excluded={'datasetId','publishedAt','asOfTimestamp','diagnostics','priceRejections'}
    before={k:v for k,v in previous.manifest.items() if k not in excluded}
    after={k:v for k,v in dataset.manifest.items() if k not in excluded}
    if encoded(before)!=encoded(after):
        raise ValueError('price repair must preserve all other publication inputs')
    def rejection_set(manifest):
        quotes={}
        for row in manifest.get('priceRejections',[]):
            coordinate=(row['chainId'],row['asset'].lower(),row['timestamp'])
            price = decimal(row['priceUsd'])
            if coordinate in quotes and quotes[coordinate]!=price:
                raise ValueError('price repair cannot use conflicting quotes at one rejection coordinate')
            quotes[coordinate]=price
        return {(*coordinate,price) for coordinate,price in quotes.items()}
    old=rejection_set(previous.manifest)
    new=rejection_set(dataset.manifest)
    if not old<=new:raise ValueError('price repair cannot remove previous rejections')
    close=dataset.as_of()
    published_dates={date for date in dataset.dates() if date<=close}
    changed=tuple(sorted({r[2] for r in new-old}&published_dates))
    if ready(dataset):
        prepare_totals(dataset)
        return {'status':'already-prepared','datasetId':dataset.id}
    # Native daily accounting reapplies child/parent caps after valuation rejection.
    frames=list(dataset.iter_frames(changed))
    repaired=[];sums=[]
    for timestamp,points,_,_ in frames:
        grouped=defaultdict(list)
        for row in points:
            values=[row.get(field) for field in FIELDS];values[3]=row.get('category',row['version'])
            values[5:]=[None if v is None else str(v) for v in values[5:]]
            repaired.append((dataset.id,timestamp,*values))
            for mode,field in [('raw','tvl_usd'),('external','external_tvl_usd')]:
                if row.get(field) is None:continue
                for group,series in [('chain',CHAINS.get(row['chain_id'],f"Chain {row['chain_id']}")),('category',row.get('category',row['version']))]:
                    grouped[(group,mode,series)].append(Decimal(str(row[field])))
        with localcontext() as ctx:
            ctx.prec=78
            sums.extend((dataset.id,timestamp,*group,str(sum(values,Decimal(0)))) for group,values in grouped.items())
    with closing(connect(dataset.database,direct=True)) as conn,conn:
        if not conn.execute('SELECT pg_try_advisory_xact_lock(743219889)').fetchone()[0]:
            raise ValueError('TVL chart preparation is already running')
        existing=conn.execute('SELECT version FROM tvl_chart_publications WHERE dataset_id=?',(dataset.id,)).fetchone()
        if existing:
            if existing['version']==VERSION+'+totals':
                dataset._prepared=True
                return {'status':'already-prepared','datasetId':dataset.id}
            if existing['version']==VERSION:
                raise ValueError('TVL chart preparation is already running; retry to complete totals')
            raise ValueError('existing chart rows require full history preparation')
        marks=','.join('?' for _ in changed)
        unchanged=f' AND timestamp NOT IN ({marks})' if changed else ''
        conn.execute(f'INSERT INTO tvl_chart_rows SELECT ?,timestamp,{",".join(FIELDS)} FROM tvl_chart_rows WHERE dataset_id=? AND timestamp<=?{unchanged}',(dataset.id,previous.id,close,*changed))
        conn.executemany('INSERT INTO tvl_chart_rows VALUES (?,?,?,?,?,?,?,?,?,?)',repaired)
        conn.execute(f'INSERT INTO tvl_chart_totals SELECT ?,timestamp,group_by,mode,series,value FROM tvl_chart_totals WHERE dataset_id=? AND timestamp<=?{unchanged}',(dataset.id,previous.id,close,*changed))
        conn.executemany('INSERT INTO tvl_chart_totals VALUES (?,?,?,?,?,?)',sums)
        count=conn.execute('SELECT COUNT(*) FROM tvl_chart_rows WHERE dataset_id=?',(dataset.id,)).fetchone()[0]
        conn.execute('INSERT INTO tvl_chart_publications VALUES (?,?,?,?)',(dataset.id,VERSION+'+totals',count,int(time.time())))
        conn.raw.execute('ANALYZE tvl_chart_rows')
    dataset._prepared=True
    return {'status':'prepared','datasetId':dataset.id,'recomputedDates':list(changed),'rows':count}
