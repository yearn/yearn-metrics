"""Transactional date markers for skipping unchanged historical input reads.

Markers are invalidation hints, never financial identities. A changed marker
still leads to selection and content hashing. Installation invalidates all old
hints, and markers and observations are read in the same database snapshot.
"""
import json

from psycopg import sql

TABLES = ('tvl_snapshots', 'tvl_positions')
EVENTS = ('insert', 'update', 'delete', 'truncate')
SCHEMA = '''CREATE TABLE IF NOT EXISTS tvl_day_revisions (
    timestamp BIGINT PRIMARY KEY, revision BIGINT NOT NULL)'''
FUNCTION = '''CREATE OR REPLACE FUNCTION tvl_mark_days_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE days_sql TEXT;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        days_sql := 'SELECT -1::bigint AS timestamp';
    ELSIF TG_OP = 'INSERT' THEN
        days_sql := 'SELECT DISTINCT timestamp FROM new_rows';
    ELSIF TG_OP = 'DELETE' THEN
        days_sql := 'SELECT DISTINCT timestamp FROM old_rows';
    ELSE
        days_sql := 'SELECT timestamp FROM old_rows UNION SELECT timestamp FROM new_rows';
    END IF;
    EXECUTE format('INSERT INTO %I.tvl_day_revisions SELECT timestamp,txid_current() FROM (%s) days ORDER BY timestamp '
        'ON CONFLICT(timestamp) DO UPDATE SET revision=excluded.revision', TG_TABLE_SCHEMA, days_sql);
    RETURN NULL;
END
$$'''


def install(conn):
    """Cover existing and external writers, including COPY and replication role."""
    with conn:
        conn.raw.execute(SCHEMA)
        conn.raw.execute(FUNCTION)
        # No writer may slip between discovering a missing trigger and resetting
        # the epoch. Transition tables aggregate bulk writes once per statement.
        conn.raw.execute('LOCK TABLE tvl_snapshots,tvl_positions IN SHARE ROW EXCLUSIVE MODE')
        for table in TABLES:
            for event in EVENTS:
                name = 'tvl_track_days_' + event
                row = conn.execute('''SELECT tgenabled FROM pg_trigger
                    WHERE tgrelid=to_regclass(?) AND tgname=?''', (table, name)).fetchone()
                if row and row[0] == 'A':
                    continue
                conn.raw.execute(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}').format(
                    sql.Identifier(name), sql.Identifier(table)))
                transition = {'insert': 'REFERENCING NEW TABLE AS new_rows',
                              'update': 'REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows',
                              'delete': 'REFERENCING OLD TABLE AS old_rows', 'truncate': ''}[event]
                conn.raw.execute(sql.SQL('CREATE TRIGGER {} AFTER {} ON {} {} '
                                         'FOR EACH STATEMENT EXECUTE FUNCTION tvl_mark_days_changed()').format(
                    sql.Identifier(name), sql.SQL(event.upper()), sql.Identifier(table), sql.SQL(transition)))
                conn.raw.execute(sql.SQL('ALTER TABLE {} ENABLE ALWAYS TRIGGER {}').format(
                    sql.Identifier(table), sql.Identifier(name)))
                conn.execute('''INSERT INTO tvl_day_revisions VALUES(-1,txid_current())
                    ON CONFLICT(timestamp) DO UPDATE SET revision=excluded.revision''')


def state(conn):
    """Include storage/schema/trigger identity; rewrites force a conservative scan."""
    from .tvl_incremental import digest
    signature = []
    for table in TABLES:
        row = conn.execute('''SELECT c.oid,c.relfilenode,c.relkind,
            EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=c.oid),
            (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum)::text FROM pg_attribute a
             WHERE a.attrelid=c.oid AND a.attnum>0),
            (SELECT jsonb_agg(jsonb_build_array(t.oid,t.tgenabled,pg_get_triggerdef(t.oid),
                pg_get_functiondef(t.tgfoid)) ORDER BY t.tgname)::text
             FROM pg_trigger t WHERE t.tgrelid=c.oid AND t.tgname LIKE 'tvl_track_days_%')
            FROM pg_class c WHERE c.oid=to_regclass(?)''', (table,)).fetchone()
        if not row or row[2] != 'r' or row[3]:
            return None, {}
        triggers = json.loads(row[5]) if row[5] else []
        if len(triggers) != 4 or any(trigger[1] != 'A' for trigger in triggers):
            return None, {}
        signature.append(tuple(row))
    revisions = dict(conn.execute('SELECT timestamp,revision FROM tvl_day_revisions'))
    signature.append(revisions.pop(-1, None))
    return digest(signature), revisions

