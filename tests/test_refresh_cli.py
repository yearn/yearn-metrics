import json
import pytest

from yearn_data import cli
from test_refresh_workflow import workflow as workflow,CUTOFF


def test_explicit_database_and_scope_are_required_before_connections(monkeypatch,tmp_path):
    monkeypatch.setattr(cli,'load_environment',lambda _:None)
    with pytest.raises(ValueError,match='explicit --db'):
        cli.main(['refresh'])
    with pytest.raises(ValueError,match='--chains'):
        cli.main(['--db','staging','refresh'])
    with pytest.raises(ValueError,match='named Postgres'):
        cli.main(['--db','data.sqlite','refresh','--inspect','unavailable'])


def test_cli_acquire_inspect_and_prepare_use_machine_receipts_and_stderr_progress(workflow,monkeypatch,tmp_path,capsys):
    options,_=workflow
    monkeypatch.setattr(cli,'load_environment',lambda _:None)
    receipt=tmp_path/'receipt.json'
    args=['--db','staging','refresh','--chains','eth','--cutoff',str(CUTOFF),'--publication-root',str(options.publication_root),
          '--from-block','eth:0','--first-close','86399','--source','rpc']
    assert cli.main([*args,'--mode','acquire','--receipt',str(receipt)])==0
    output=capsys.readouterr()
    acquired=json.loads(output.out)
    assert acquired==json.loads(receipt.read_text())
    assert any(json.loads(line)['event']=='stage-started' for line in output.err.splitlines())
    identity=acquired['jobId']
    assert cli.main(['--db','staging','refresh','--inspect',identity])==0
    assert json.loads(capsys.readouterr().out)['status']=='acquired'
    assert cli.main([*args,'--resume',identity])==0
    assert json.loads(capsys.readouterr().out)['result']['publishReady'] is True
