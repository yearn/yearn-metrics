"""Unmarked tests must fail before a database connection reaches the driver."""
import psycopg
import pytest

from yearn_data.storage import connect
from yearn_data.postgres import Connection


def test_database_access_without_fixture_is_blocked(monkeypatch):
    def unexpected_transport(*args, **kwargs):
        pytest.fail('database guard allowed a transport attempt')

    monkeypatch.setattr(psycopg.Connection, '_connect_gen', unexpected_transport)
    url = 'postgresql://127.0.0.1:1/test'
    # Even credentials loaded after the guard must not permit real access.
    monkeypatch.setenv('NEON_DB_URL', url)
    with pytest.raises(pytest.fail.Exception, match='requires the postgres_database fixture'):
        connect('neon')
    with pytest.raises(pytest.fail.Exception, match='requires the postgres_database fixture'):
        psycopg.connect(url)


def test_database_fixture_rejects_other_database_or_schema(postgres_database, monkeypatch):
    with pytest.raises(pytest.fail.Exception, match='schema created by postgres_database'):
        connect('staging', schema='public')
    monkeypatch.setenv('NEON_DB_URL', 'postgresql://127.0.0.1:1/other')
    with pytest.raises(pytest.fail.Exception, match='must use YEARN_TEST_POSTGRES_URL'):
        connect('neon')
    with pytest.raises(pytest.fail.Exception, match='must use YEARN_TEST_POSTGRES_URL'):
        Connection(url='postgresql://127.0.0.1:1/other')
