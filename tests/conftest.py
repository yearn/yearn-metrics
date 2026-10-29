"""Explicit Postgres contracts; other tests cannot open database connections."""
from contextlib import closing
import os
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from yearn_data.postgres import Connection
from yearn_data.storage import connect

DRIVER_CONNECT = psycopg.connect


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        if 'postgres_database' in item.fixturenames:
            item.add_marker(pytest.mark.postgres)


@pytest.fixture(autouse=True)
def database_guard(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('Database access requires the postgres_database fixture')
    monkeypatch.setattr(psycopg, 'connect', blocked)
    for name in ('NEON_DB_URL', 'STAGING_DB_URL'):
        monkeypatch.setenv(name, 'postgresql://unconfigured-test.invalid/test')
    monkeypatch.setenv('YEARN_DATA_DB', 'staging')
    # Real worker configuration may enable reporting; tests never post to GitHub.
    monkeypatch.setenv('YEARN_REFRESH_GITHUB_REPORTING', 'off')
    monkeypatch.delenv('YEARN_DATA_DB_SCHEMA', raising=False)


@pytest.fixture
def postgres_database(database_guard, monkeypatch):
    test_url = os.environ.get('YEARN_TEST_POSTGRES_URL')
    if not test_url:
        pytest.fail('Set YEARN_TEST_POSTGRES_URL to a disposable local Postgres database; see docs/postgres.md')
    schemas, connections = [], []
    original = Connection.__init__
    def tracked(connection, url, *args, **kwargs):
        if conninfo_to_dict(url) != conninfo_to_dict(test_url):
            pytest.fail('Database tests must use YEARN_TEST_POSTGRES_URL')
        if kwargs.get('schema') not in schemas:
            pytest.fail('Database tests must use a schema created by postgres_database')
        original(connection, url, *args, **kwargs)
        connections.append(connection)
    monkeypatch.setattr(Connection, '__init__', tracked)
    monkeypatch.setattr(psycopg, 'connect', DRIVER_CONNECT)
    with psycopg.connect(test_url, autocommit=True) as admin:
        def create_schema():
            name = 'yearn_test_' + uuid.uuid4().hex
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
            schemas.append(name)
            return name
        name = create_schema()
        monkeypatch.setenv('NEON_DB_URL', test_url)
        monkeypatch.setenv('STAGING_DB_URL', test_url)
        monkeypatch.setenv('YEARN_DATA_DB_SCHEMA', name)
        monkeypatch.setenv('YEARN_DATA_DB', 'staging')
        try:
            yield create_schema
        finally:
            for connection in connections:
                connection.close()
            for name in reversed(schemas):
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


@pytest.fixture
def pg(postgres_database):
    with closing(connect('staging')) as conn:
        yield conn, os.environ['YEARN_DATA_DB_SCHEMA']


@pytest.fixture
def new_pg(postgres_database):
    return lambda: connect('staging', schema=postgres_database())
