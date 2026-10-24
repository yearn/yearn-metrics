"""Run database contracts on Postgres, isolated from all existing schemas."""
from contextlib import closing
import os
import uuid

import psycopg
from psycopg import sql
import pytest

from yearn_data.postgres import Connection
from yearn_data.storage import connect


@pytest.fixture(autouse=True)
def postgres_database(monkeypatch):
    url = os.environ.get('YEARN_TEST_POSTGRES_URL')
    if not url:
        pytest.fail('Set YEARN_TEST_POSTGRES_URL to a disposable local Postgres database; see docs/postgres.md')
    schemas, connections = [], []
    original = Connection.__init__
    def tracked(connection, *args, **kwargs):
        original(connection, *args, **kwargs)
        connections.append(connection)
    monkeypatch.setattr(Connection, '__init__', tracked)
    with psycopg.connect(url, autocommit=True) as admin:
        def create_schema():
            name = 'yearn_test_' + uuid.uuid4().hex
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
            schemas.append(name)
            return name
        name = create_schema()
        monkeypatch.setenv('NEON_DB_URL', url)
        monkeypatch.setenv('STAGING_DB_URL', url)
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
