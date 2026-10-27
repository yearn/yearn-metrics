import pytest
from yearn_data import cli, storage


def test_loaded_default_can_be_overridden_without_switching_other_database(monkeypatch,tmp_path):
    env=tmp_path/'local.env'
    env.write_text('YEARN_DATA_DB=neon\n')
    monkeypatch.delenv('YEARN_DATA_DB',raising=False)
    selected=[]
    monkeypatch.setattr(cli,'open_db',lambda database:selected.append(database))
    assert cli.main(['--env',str(env),'init-db'])==0
    assert cli.main(['--env',str(env),'--db','staging','init-db'])==0
    assert selected==['neon','staging']


def test_staging_connection_uses_separate_credentials(monkeypatch):
    from yearn_data import postgres
    monkeypatch.setenv('NEON_DB_URL','postgresql://primary.invalid/primary')
    monkeypatch.setenv('STAGING_DB_URL','postgresql://127.0.0.1/staging?sslmode=disable')
    urls=[]
    monkeypatch.setattr(postgres,'Connection',lambda url,**kwargs:urls.append(url))
    storage.connect('staging')
    assert urls==['postgresql://127.0.0.1/staging?sslmode=disable']
    assert storage.database_reference('staging')=='staging'


def test_cli_defaults_to_neon_without_environment_override(monkeypatch):
    monkeypatch.delenv('YEARN_DATA_DB', raising=False)
    monkeypatch.setattr(cli, 'load_environment', lambda paths: None)
    selected=[]
    monkeypatch.setattr(cli, 'open_db', lambda database: selected.append(database))
    assert cli.main(['init-db'])==0
    assert selected==['neon']


def test_missing_neon_credentials_fail_without_creating_a_local_database(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('NEON_DB_URL', raising=False)
    with pytest.raises(ValueError,match='NEON_DB_URL is required'):
        storage.connect()
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('selector',['history.sqlite',':memory:','data/yearn.db',''])
def test_retired_file_selectors_fail_without_creating_files(monkeypatch,tmp_path,selector):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='SQLite storage is retired'):
        storage.connect(selector)
    with pytest.raises(ValueError,match='SQLite storage is retired'):
        storage.database_reference(selector)
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('arguments',[
    ['migrate-postgres','--source','history.sqlite'],
    ['merge-databases','--fees-db','fees.sqlite','--tvl-db','tvl.sqlite','--out','shared.sqlite'],
    ['tvl','import-catalog','--service-db','history.sqlite'],
])
def test_retired_migration_commands_are_unavailable(arguments):
    with pytest.raises(SystemExit) as error:
        cli.build_parser().parse_args(arguments)
    assert error.value.code==2
