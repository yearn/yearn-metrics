"""Reuse content hashes only while transactional table change markers agree.

The cache lives for one worker invocation. Markers are maintained by Postgres,
including writes outside the coordinator. They are NOT financial dataset IDs.
"""
from psycopg import sql

from .refresh_state import digest, sql_digest
from .storage import table_exists

SCHEMA = '''CREATE TABLE IF NOT EXISTS refresh_table_revisions (
    table_oid OID PRIMARY KEY, revision BIGINT NOT NULL)'''
FUNCTION = '''CREATE OR REPLACE FUNCTION refresh_mark_table_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('UPDATE %I.refresh_table_revisions SET revision=txid_current() '
        'WHERE table_oid=$1 AND revision<>txid_current()', TG_TABLE_SCHEMA) USING TG_RELID;
    RETURN NULL;
END
$$'''
TRIGGER = 'refresh_track_changes'


class Fingerprints:
    def __init__(self):
        self.installed = set()
        self.cache = {}

    def track(self, conn, tables):
        """Install markers atomically with trigger creation, before reading data."""
        if not self.installed:
            with conn:
                conn.raw.execute(SCHEMA)
                conn.raw.execute(FUNCTION)
        for table in tables:
            if table in self.installed or not table_exists(conn, table):
                continue
            with conn:
                # Prevent a writer from committing between the initial marker
                # and installation. Never scan old rows to initialize a marker.
                conn.raw.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(sql.Identifier(table)))
                row = conn.execute('''SELECT t.tgenabled FROM pg_trigger t
                    WHERE t.tgrelid=to_regclass(?) AND t.tgname=?''', (table, TRIGGER)).fetchone()
                if row is None:
                    conn.execute('''INSERT INTO refresh_table_revisions VALUES(to_regclass(?),txid_current())
                        ON CONFLICT(table_oid) DO UPDATE SET revision=excluded.revision''', (table,))
                    conn.raw.execute(sql.SQL('''CREATE TRIGGER {} AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE
                        ON {} FOR EACH STATEMENT EXECUTE FUNCTION refresh_mark_table_changed()''').format(
                            sql.Identifier(TRIGGER), sql.Identifier(table)))
                    conn.raw.execute(sql.SQL('ALTER TABLE {} ENABLE ALWAYS TRIGGER {}').format(
                        sql.Identifier(table), sql.Identifier(TRIGGER)))
            self.installed.add(table)

    def revisions(self, conn, tables):
        result = []
        for table in tables:
            row = conn.execute('''SELECT c.oid,r.revision,t.tgenabled,c.relfilenode,
                    (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum)::text FROM pg_attribute a
                     WHERE a.attrelid=c.oid AND a.attnum>0),c.relkind,
                    EXISTS(SELECT 1 FROM pg_inherits WHERE inhparent=c.oid)
                FROM pg_class c
                LEFT JOIN refresh_table_revisions r ON r.table_oid=c.oid
                LEFT JOIN pg_trigger t ON t.tgrelid=c.oid AND t.tgname=?
                WHERE c.oid=to_regclass(?)''', (TRIGGER, table)).fetchone()
            if row is None:
                result.append((table, 'missing'))
            elif row[1] is None or row[2] != 'A' or row[5] != 'r' or row[6]:
                # ALWAYS also tracks replication-role writes. An altered or
                # disabled trigger cannot silently authorize cache reuse.
                raise ValueError('refresh change tracking is unavailable for '+table)
            else:
                result.append((table, int(row[0]), row[1], int(row[3]), row[4]))
        return result

    def signature(self, conn, queries, *, partial=False):
        """Call inside a repeatable-read transaction after installing markers.

        Each query declares ALL source tables, including joins. Full signatures
        retain the existing content identity; partial checkpoints need only a
        conservative mutation identity to validate their saved acquisition cursor.
        """
        results = []
        for dependencies, statement, params in queries:
            tables = (dependencies,) if isinstance(dependencies, str) else dependencies
            revisions = self.revisions(conn, tables)
            if partial:
                results.append(digest(['refresh-cursor-v1', statement, params, revisions]))
                continue
            key = digest([tables, statement, params])
            cached = self.cache.get(key)
            if cached is not None and cached[0] == revisions:
                results.append(cached[1])
                continue
            value = sql_digest(conn, statement, params) if table_exists(conn, tables[0]) else 'missing:'+tables[0]
            self.cache[key] = (revisions, value)
            results.append(value)
        return digest(results)
