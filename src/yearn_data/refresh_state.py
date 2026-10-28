"""Durable refresh checkpoints; no scheduler, source acquisition or accounting."""
from contextlib import closing
import hashlib
import json
import time
import uuid

from .storage import connect

LOCK = 743219891
SCHEMA = '''
CREATE TABLE IF NOT EXISTS refresh_jobs (
 id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 status TEXT NOT NULL, context_json TEXT NOT NULL, bounds_json TEXT NOT NULL,
 result_json TEXT NOT NULL, initial_release_id TEXT);
CREATE TABLE IF NOT EXISTS refresh_stages (
 job_id TEXT NOT NULL REFERENCES refresh_jobs(id), name TEXT NOT NULL,
 ordinal INTEGER NOT NULL, status TEXT NOT NULL, input_digest TEXT NOT NULL,
 output_digest TEXT, state_json TEXT NOT NULL, result_json TEXT NOT NULL,
 updated_at INTEGER NOT NULL, PRIMARY KEY(job_id,name));
'''


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def query_digest(conn,statement,params=()):
    return sql_digest(conn,statement,params)


class CheckpointRejected(ValueError):
    """An owned, non-sensitive checkpoint validation diagnostic."""


class RefreshBusy(ValueError):
    """Another refresh owns the database writer lock."""


class WorkDeferred(Exception):
    """The invocation's explicit work budget is exhausted; resume the same job."""


class RefreshState:
    def __init__(self,database,context,bounds=None,*,resume=None,progress=None,initial_release=None):
        self.database=database
        self.context=context
        self.bounds=bounds
        self.resume=resume
        self.id=resume or uuid.uuid4().hex
        self.progress=progress or (lambda event:None)
        self.conn=None
        self.ordinal=0
        self.current=None
        self.checks=[]
        self.initial_release=initial_release

    def __enter__(self):
        self.conn=connect(self.database,direct=True)
        try:
            if not self.conn.execute('SELECT pg_try_advisory_lock(?)',(LOCK,)).fetchone()[0]:
                raise RefreshBusy('another database refresh is running')
            self.conn.executescript(SCHEMA)
            row=self.conn.execute('SELECT context_json,bounds_json FROM refresh_jobs WHERE id=?',(self.id,)).fetchone()
            if row:
                if json.loads(row[0])!=self.context:
                    raise CheckpointRejected('refresh checkpoint scope, cutoff, code or configuration changed; start a new job')
                retained=json.loads(row[1])
                if self.bounds is not None and retained!=self.bounds:
                    raise CheckpointRejected('refresh checkpoint bounds changed; start a new job')
                self.bounds=retained
            else:
                if self.resume is not None or self.bounds is None:
                    raise CheckpointRejected('unknown refresh resume ID')
                now=int(time.time())
                with self.conn:
                    initial=self.initial_release() if callable(self.initial_release) else self.initial_release
                    self.conn.execute('INSERT INTO refresh_jobs VALUES (?,?,?,?,?,?,?,?)',
                        (self.id,now,now,'running',encoded(self.context),encoded(self.bounds),'{}',initial))
            return self
        except BaseException:
            self.conn.close()
            self.conn=None
            raise

    def __exit__(self,kind,value,traceback):
        if self.conn is not None:
            try:
                self.conn.execute('SELECT pg_advisory_unlock(?)',(LOCK,))
            finally:
                self.conn.close()

    def emit(self,event,**details):
        self.progress({'jobId':self.id,'event':event,**details})

    def set_bounds(self,bounds):
        if self.bounds and self.bounds!=bounds:
            raise CheckpointRejected('saved refresh bounds cannot be replaced')
        with self.conn:
            self.conn.execute('UPDATE refresh_jobs SET bounds_json=?,updated_at=? WHERE id=?',
                              (encoded(bounds),int(time.time()),self.id))
        self.bounds=bounds

    def _save(self,status,state,result,output_digest=None):
        stage=self.current
        with self.conn:
            if status=='running':
                self.conn.execute("UPDATE refresh_jobs SET status='running',updated_at=? WHERE id=?",
                                  (int(time.time()),self.id))
            self.conn.execute('''INSERT INTO refresh_stages VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id,name) DO UPDATE SET ordinal=excluded.ordinal,status=excluded.status,
                input_digest=excluded.input_digest,output_digest=excluded.output_digest,
                state_json=excluded.state_json,result_json=excluded.result_json,updated_at=excluded.updated_at''',
                (self.id,stage['name'],stage['ordinal'],status,stage['input'],output_digest,
                 encoded(state),encoded(result),int(time.time())))

    def checkpoint(self,state,result=None):
        """Call after a committed domain batch, before attempting the next one."""
        result={} if result is None else result
        self._save('running',state,result,self.current['output'](result))
        self.emit('checkpoint',stage=self.current['name'],processed=state.get('processed',0))

    def stage(self,name,inputs,output,action):
        self.ordinal+=1
        read_inputs=inputs if callable(inputs) else lambda:inputs
        incoming=digest(read_inputs())
        row=self.conn.execute('SELECT * FROM refresh_stages WHERE job_id=? AND name=?',(self.id,name)).fetchone()
        state={};result={}
        self.current={'name':name,'ordinal':self.ordinal,'input':incoming,'output':output}
        if row:
            state=json.loads(row['state_json']);result=json.loads(row['result_json'])
            valid=row['input_digest']==incoming and row['output_digest']==output(result)
            if valid and row['status']=='complete':
                self.checks.append((name,read_inputs,incoming,output,result,row['output_digest']))
                self.emit('stage-reused',stage=name)
                return result
            if not valid:
                self.emit('stage-invalidated',stage=name)
                state={};result={}
        self._save('running',state,result,output(result))
        self.emit('stage-started',stage=name)
        started=time.monotonic()
        try:
            result=action(state)
            outgoing=output(result)
            status=result.get('checkpointStatus','complete')
            if status not in ('complete','incomplete'):
                raise ValueError('invalid refresh checkpoint status')
            self._save(status,{},result,outgoing)
            self.checks.append((name,read_inputs,incoming,output,result,outgoing))
            self.emit('stage-completed' if status=='complete' else 'stage-incomplete',stage=name,seconds=time.monotonic()-started)
            return result
        except WorkDeferred:
            # The last durable checkpoint owns partial progress. Do not replace
            # it with the stale state passed to this invocation's action.
            with self.conn:
                self.conn.execute("UPDATE refresh_stages SET status='partial' WHERE job_id=? AND name=?",(self.id,name))
            self.finish('work-deferred',{})
            self.emit('stage-deferred',stage=name)
            raise
        except BaseException:
            # Never persist provider exception text, credentials or endpoints.
            with self.conn:
                self.conn.execute("UPDATE refresh_stages SET status='failed' WHERE job_id=? AND name=?",(self.id,name))
            self.finish('failed',{'failedStage':name})
            self.emit('stage-failed',stage=name)
            raise

    def begin_pass(self):
        self.ordinal=0
        self.checks=[]

    def changed_stages(self):
        return [name for name,read_inputs,incoming,output,result,outgoing in self.checks
                if digest(read_inputs())!=incoming or output(result)!=outgoing]

    def finish(self,status,result):
        with self.conn:
            self.conn.execute('UPDATE refresh_jobs SET status=?,updated_at=?,result_json=? WHERE id=?',
                              (status,int(time.time()),encoded(result),self.id))

    def receipt(self):
        row=self.conn.execute('SELECT * FROM refresh_jobs WHERE id=?',(self.id,)).fetchone()
        stages=[dict(name=r['name'],status=r['status'],result=json.loads(r['result_json']),
                     progress=json.loads(r['state_json'])) for r in self.conn.execute(
                     'SELECT * FROM refresh_stages WHERE job_id=? ORDER BY ordinal',(self.id,))]
        return dict(jobId=self.id,status=row['status'],context=self.context,bounds=self.bounds,
                    servingReleaseIdBefore=row['initial_release_id'],result=json.loads(row['result_json']),stages=stages)


def read_receipt(database,identity):
    with closing(connect(database,readonly=True)) as conn:
        from .storage import table_exists
        if not table_exists(conn,'refresh_jobs'):raise CheckpointRejected('unknown refresh job')
        row=conn.execute('SELECT context_json,bounds_json FROM refresh_jobs WHERE id=?',(identity,)).fetchone()
    if row is None:
        raise CheckpointRejected('unknown refresh job')
    # Normal receipt inspection must not acquire the writer lock or mutate state.
    instance=RefreshState(database,json.loads(row[0]),json.loads(row[1]),resume=identity)
    with closing(connect(database,readonly=True)) as conn:
        instance.conn=conn
        return instance.receipt()


class WorkBudget:
    def __init__(self,limit):
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1:
            raise ValueError('work limit must be a positive integer')
        self.remaining=limit


def drain_pages(saved,*,select,apply,key,budget,checkpoint,batch_size=500):
    """Commit bounded chronological pages once, retaining the attempted cursor.

    A missing/permanently unavailable result cannot starve later pages. The
    selector owns domain eligibility; `apply` must commit before returning.
    """
    state=dict(saved)
    state.setdefault('processed',0);state.setdefault('batches',0)
    while True:
        rows=select(state.get('cursor'),min(batch_size,budget.remaining) if budget.remaining else 1)
        if not rows:
            return {'processed':state['processed'],'batches':state['batches']}
        if not budget.remaining:
            checkpoint(state,{'processed':state['processed'],'batches':state['batches'],'pendingAtLeast':1})
            raise WorkDeferred()
        apply(rows)
        budget.remaining-=len(rows)
        state.update(cursor=key(rows[-1]),processed=state['processed']+len(rows),batches=state['batches']+1)
        checkpoint(state,{'processed':state['processed'],'batches':state['batches']})


def sql_digest(conn,statement,params=()):
    """Hash an owned SELECT on Postgres; return only a count and SHA256 digest.

    Sorting row hashes makes this independent of physical row order. Hashing
    on the server avoids transferring full historical JSON for each checkpoint.
    `statement` is application-owned SQL, never operator-supplied text.
    """
    row=conn.execute(f'''SELECT count(*),encode(sha256(convert_to(
        COALESCE(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') FROM (
            SELECT encode(sha256(convert_to(row_to_json(d)::text,'UTF8')),'hex') h
            FROM ({statement}) d) hashes''',params).fetchone()
    return digest(tuple(row))
