"""Durable checkpoints must be proved against live outputs, not saved JSON alone."""
from contextlib import closing
import json

import pytest

from yearn_data.storage import connect
from yearn_data.refresh_state import RefreshState, WorkDeferred, read_receipt, query_digest


@pytest.fixture
def facts(postgres_database):
    with closing(connect('staging')) as conn:
        conn.executescript('CREATE TABLE refresh_facts(id INTEGER PRIMARY KEY,value TEXT NOT NULL);')
        with conn:
            conn.execute("INSERT INTO refresh_facts VALUES(1,'initial')")
        yield conn


def test_resume_reuses_verified_outputs_and_invalidates_changed_inputs_or_outputs(facts):
    context={'scope':[1,10],'cutoff':172800,'code':'v1','config':'cfg'}
    invoked=[]
    def output(_result):return query_digest(facts,'SELECT * FROM refresh_facts ORDER BY id')
    def work(saved):
        invoked.append(True)
        with facts:facts.execute("UPDATE refresh_facts SET value='completed'")
        return {'count':1}
    with RefreshState('staging',context,{'eth':{'last':100}}) as job:
        identity=job.id
        assert job.stage('acquire',{'source':'one'},output,work)=={'count':1}
    with RefreshState('staging',context,resume=identity) as job:
        assert job.bounds=={'eth':{'last':100}}
        assert job.stage('acquire',{'source':'one'},output,work)=={'count':1}
    assert len(invoked)==1
    with facts:facts.execute("UPDATE refresh_facts SET value='external correction'")
    with RefreshState('staging',context,resume=identity) as job:
        job.stage('acquire',{'source':'one'},output,work)
    assert len(invoked)==2
    with RefreshState('staging',context,resume=identity) as job:
        job.stage('acquire',{'source':'new upstream output'},output,work)
    assert len(invoked)==3
    for change in ({'code':'v2'},{'cutoff':259200},{'scope':[1]},{'config':'other'}):
        with pytest.raises(ValueError,match='checkpoint scope'):
            with RefreshState('staging',context|change,resume=identity):pytest.fail('stale checkpoint reused')


def test_partial_progress_survives_interruption_without_persisting_provider_error(facts):
    def output(_result):return query_digest(facts,'SELECT * FROM refresh_facts ORDER BY id')
    with RefreshState('staging',{},[]) as job:
        identity=job.id
        def interrupted(saved):
            with facts:facts.execute("UPDATE refresh_facts SET value='first committed batch'")
            job.checkpoint({'cursor':1,'processed':500})
            raise RuntimeError('private-provider.invalid?key=secret')
        with pytest.raises(RuntimeError):job.stage('prices',{},output,interrupted)
    receipt=read_receipt('staging',identity)
    assert receipt['status']=='failed'
    assert receipt['stages'][0]['progress']=={'cursor':1,'processed':500}
    assert 'private-provider' not in json.dumps(receipt)
    with RefreshState('staging',{},resume=identity) as job:
        def resume(saved):
            assert saved=={'cursor':1,'processed':500}
            with facts:facts.execute("UPDATE refresh_facts SET value='second committed batch'")
            return {'processed':501}
        assert job.stage('prices',{},output,resume)=={'processed':501}
    assert read_receipt('staging',identity)['stages'][0]['status']=='complete'


def test_single_writer_unknown_resume_and_deferred_work(facts):
    with RefreshState('staging',{},[]) as job:
        identity=job.id
        with pytest.raises(ValueError,match='another database refresh'):
            with RefreshState('staging',{},[]):pytest.fail('competing writer acquired lock')
        def defer(saved):
            job.checkpoint({'cursor':500,'processed':500})
            raise WorkDeferred()
        with pytest.raises(WorkDeferred):job.stage('bounded',{},lambda _: 'unchanged',defer)
        assert job.receipt()['status']=='work-deferred'
    receipt=read_receipt('staging',identity)
    assert receipt['stages'][0]['status']=='partial'
    assert receipt['stages'][0]['progress']['cursor']==500
    with pytest.raises(ValueError,match='unknown refresh'):
        with RefreshState('staging',{},[],resume='not-a-real-job'):pytest.fail('unknown resume created a job')


def test_end_of_pass_audit_detects_late_upstream_change_and_rebuilds_dependent_stage(facts):
    desired={'value':'first'};calls=[]
    def output(_):return query_digest(facts,'SELECT * FROM refresh_facts ORDER BY id')
    def producer(_):
        calls.append('producer')
        with facts:facts.execute('UPDATE refresh_facts SET value=?',(desired['value'],))
        return {'updated':True}
    def consumer(_):calls.append('consumer');return {'prepared':True}
    with RefreshState('staging',{},[]) as job:
        job.stage('producer',lambda:desired,output,producer)
        job.stage('consumer',lambda:output({}),lambda _:'consumer-artifact',consumer)
        assert job.changed_stages()==[]
        desired['value']='corrected'
        assert job.changed_stages()==['producer']
        job.begin_pass()
        job.stage('producer',lambda:desired,output,producer)
        job.stage('consumer',lambda:output({}),lambda _:'consumer-artifact',consumer)
        assert job.changed_stages()==[]
    assert calls==['producer','consumer','producer','consumer']
