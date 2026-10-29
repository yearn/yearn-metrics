"""One latest-run issue, idempotent updates, and isolated notification failures."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from yearn_data import github_warning_report as report,refresh
from yearn_data.refresh_state import read_receipt
from test_refresh_workflow import workflow as workflow


@pytest.fixture
def receipt(monkeypatch):
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_REPORTING','on')
    return dict(jobId='abc123',createdAt=1793270000,status='prepared',
        context=dict(database='staging',cutoff=1793232000,chains=['eth','ftm'],windowDays=7),
        result=dict(publishReady=True,publicationBlockers=[],warnings=[
            dict(code='processing-gaps',stage='reportPrices',message='Missing',details={'missing':7240}),
            dict(code='incomplete-source',stage='catalog:eth',message='Incomplete',details=dict(sources=[
                dict(source='kong',status='incomplete',failures=1,findings=[dict(address='0x123',reason='missing_asset_metadata')])]))]))


def test_report_is_latest_run_findings_without_internal_work_or_conversation(receipt):
    receipt['context'].update(branch='internal-branch',configuration='private-key')
    receipt['result']['conversation']='The maintainer told us an unreleased product exists'
    receipt['result']['work']='Local work has not been pushed'
    warning=receipt['result']['warnings'][1]
    warning['details']['sources'][0]['findings'][0]['reason']='ValueError https://private.tail123.ts.net/token?secret @someone <script>'
    body=report.render(receipt)
    assert '2026-10-22–2026-10-28 UTC' in body
    assert '7,240 missing' in body and '0x123' in body
    assert 'Next check' not in body and 'Context / significance' in body
    assert all(s not in body for s in ('internal-branch','private-key','maintainer','not been pushed','tail123','?secret','@someone','<script>'))
    assert receipt['jobId'] in body
    receipt['result']['warnings']=[]
    assert 'No warnings were reported' in report.render(receipt)
    del receipt['result']['warnings']
    receipt['status']='failed';receipt['result']['failedStage']='reports:eth';receipt['result']['publishReady']=False
    body=report.render(receipt)
    assert 'Warning counts are unavailable' in body and 'Stopped at: Ethereum reports' in body
    assert 'not cleared for publication.' in body


def test_no_post_until_exact_template_approved(receipt,monkeypatch):
    monkeypatch.delenv('YEARN_REFRESH_GITHUB_TEMPLATE_SHA256',raising=False)
    monkeypatch.setattr(report,'gh',lambda *a:pytest.fail('unapproved template posted'))
    assert report.update(receipt)['status']=='awaiting-template-approval'
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_TEMPLATE_SHA256','old-template')
    assert report.update(receipt)['status']=='awaiting-template-approval'


def test_updates_same_issue_without_creation_repeat_is_noop_and_older_run_is_skipped(receipt,monkeypatch):
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_TEMPLATE_SHA256',report.template_digest())
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_REPOSITORY','yearn/yearn-metrics')
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_ISSUE','92')
    current={'title':'Existing warning report','body':'Old findings','state':'open','html_url':'https://github.com/yearn/yearn-metrics/issues/92'}
    calls=[]
    def gh(method,endpoint,payload=None):
        assert endpoint=='repos/yearn/yearn-metrics/issues/92'
        assert method in ('GET','PATCH')
        calls.append(method)
        if method=='PATCH':current.update(payload)
        return deepcopy(current)
    monkeypatch.setattr(report,'gh',gh)
    assert report.update(receipt)['status']=='updated'
    assert report.update(receipt)['status']=='unchanged'
    assert calls==['GET','PATCH','GET']
    older=deepcopy(receipt);older['context']['cutoff']-=86400
    assert report.update(older)['status']=='skipped-older-run'
    older=deepcopy(receipt);older['createdAt']-=1
    assert report.update(older)['status']=='skipped-older-run'
    # A closed target is reused rather than replaced with a new issue.
    current['state']='closed'
    assert report.update(receipt)['status']=='updated' and current['state']=='open'
    cleared=deepcopy(receipt);cleared['createdAt']+=1;cleared['result']['warnings']=[]
    assert report.update(cleared)['status']=='updated'
    assert 'No warnings were reported' in current['body'] and '7,240 missing' not in current['body']


def test_gh_transport_uses_json_stdin_bounded_timeout_and_never_exposes_errors(monkeypatch):
    def run(args,**kwargs):
        assert args==['gh','api','--hostname','github.com','--method','PATCH','repos/yearn/yearn-metrics/issues/92','--input','-']
        assert json.loads(kwargs['input'])=={'body':'literal `$(secret)`\nnext line'}
        assert kwargs['timeout']==15
        return SimpleNamespace(returncode=1,stdout='',stderr='private-token secret')
    monkeypatch.setattr(report.subprocess,'run',run)
    with pytest.raises(RuntimeError) as error:
        report.gh('PATCH','repos/yearn/yearn-metrics/issues/92',{'body':'literal `$(secret)`\nnext line'})
    assert 'private-token' not in str(error.value)


def test_reporting_timeout_and_receipt_write_failure_do_not_change_selected_release(workflow,monkeypatch):
    options,_=workflow
    attempts=[]
    def failing(receipt):
        attempts.append(receipt['status'])
        raise TimeoutError('https://provider.invalid/private-token')
    monkeypatch.setattr(report,'update',failing)
    native_connect=report.connect
    def broken_persistence(*args,**kwargs):raise RuntimeError('report DB unavailable')
    monkeypatch.setattr(report,'connect',broken_persistence)
    selected=refresh.run_refresh(options,mode='publish')
    assert selected['status']=='selected',selected
    assert refresh.selection('staging')==selected['result']['releaseId']
    outcome=selected['result']['githubWarningReport']
    assert outcome==dict(status='failed',failureClass='TimeoutError',receiptPersistenceFailureClass='RuntimeError')
    assert 'private-token' not in json.dumps(selected)
    # Recovery retries the notification, and cannot repeat financial publication.
    monkeypatch.setattr(report,'connect',native_connect)
    monkeypatch.setattr(refresh,'publish',lambda *a,**k:pytest.fail('notification retry republished finances'))
    repeated=refresh.run_refresh(options,mode='publish',resume=selected['jobId'])
    assert repeated['status']=='selected' and repeated['result']['releaseId']==selected['result']['releaseId']
    saved=read_receipt('staging',selected['jobId'])
    assert saved['status']=='selected' and saved['result']['githubWarningReport']['status']=='failed'
    assert attempts==['selected','selected']


def test_concurrent_issue_writer_defers_notification_without_creating_duplicate(receipt,monkeypatch):
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_TEMPLATE_SHA256',report.template_digest())
    def busy(*args):raise BlockingIOError()
    monkeypatch.setattr(report.fcntl,'flock',busy)
    monkeypatch.setattr(report,'gh',lambda *a:pytest.fail('concurrent notification wrote to issue'))
    assert report.update(receipt)==dict(status='deferred',reason='issue-update-in-progress')


def test_catalog_report_explains_retained_metadata_and_uncertain_discovery_cause(receipt):
    warning=receipt['result']['warnings'][1]
    warning['details']['sources'][0]['findings'][0].update(vault_name='Example vault',
        retained_asset='0x456',retained_asset_symbol='USDC',retained_asset_decimals=6)
    receipt['result']['warnings'].append(dict(code='incomplete-source',stage='catalog:ftm',message='Incomplete',
        details=dict(sources=[dict(source='kong',status='complete',discovered=30,failures=0),
            dict(source='curation-discovery',status='incomplete',failures=1,findings=[
                dict(chain_id=250,stage='morpho-api',family='morpho-v1',role='owner',reason='ValueError')])])))
    body=report.render(receipt)
    assert '**Example vault**' in body and 'USDC, 6 decimals' in body
    assert 'Retained token metadata is available for 1 of these 1 vaults' in body
    assert 'Kong refreshed 30 tracked vaults successfully' in body
    assert 'Only ValueError was recorded' in body
    assert 'has not been confirmed as the cause' in body
    assert '{"address"' not in body


def test_github_template_is_canonical_and_changes_refresh_identity(tmp_path,monkeypatch):
    from yearn_data import refresh_scope
    assert report.TEMPLATE_PATH.parts[-2:]==('.github','weekly-sync-warning-report.md')
    template=tmp_path/'weekly-sync-warning-report.md'
    template.write_bytes(report.TEMPLATE_PATH.read_bytes())
    monkeypatch.setattr(refresh_scope,'TEMPLATE_PATH',template)
    before=refresh_scope.code_identity()
    template.write_text(template.read_text()+'\nChanged layout\n')
    assert refresh_scope.code_identity()!=before
    # A missing optional notification asset must not prevent financial work.
    template.unlink()
    assert refresh_scope.code_identity()!=before
