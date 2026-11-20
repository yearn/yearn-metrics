"""A release points directly to the immutable stored rows for each chart day."""
from uuid import uuid4

from .storage import table_exists


def enabled(conn):
    return table_exists(conn, 'tvl_chart_days')


def totals_enabled(conn):
    return table_exists(conn, 'tvl_chart_total_values')


def lock_memberships(conn):
    # Take this lock before the repeatable-read snapshot. Retirement must see
    # every committed reference before deciding a physical day is unreferenced.
    conn.raw.execute('LOCK TABLE tvl_chart_days IN SHARE ROW EXCLUSIVE MODE NOWAIT')


def new_owner():
    return uuid4().hex


def link(conn, target, previous, reused, changed, owner):
    """Pin the source's physical day, even when the source itself shares it."""
    if reused:
        inserted = conn.execute('''INSERT INTO tvl_chart_days
            SELECT ?,timestamp,owner_id,row_count FROM tvl_chart_days
            WHERE dataset_id=? AND timestamp=ANY(?::bigint[])''', (target, previous, reused))
        if inserted.rowcount != len(reused):
            raise ValueError('missing prepared chart day for reuse')
    if changed:
        conn.execute('''INSERT INTO tvl_chart_days
            SELECT ?,d.timestamp,?,count(v.timestamp) FROM unnest(?::bigint[]) d(timestamp)
            LEFT JOIN tvl_chart_row_values v ON v.owner_id=? AND v.timestamp=d.timestamp
            GROUP BY d.timestamp''', (target, owner, changed, owner))
    return conn.execute('SELECT coalesce(sum(row_count),0)::bigint FROM tvl_chart_days WHERE dataset_id=?',
                        (target,)).fetchone()[0]


def retire(conn, dataset_id, *, keep_selected):
    """Remove memberships, then delete only day versions with no surviving owner reference."""
    operator = '<>' if keep_selected else '='
    # The main query sees the pre-delete snapshot of a data-modifying CTE.
    # Explicitly restrict the surviving references to the complementary set.
    keep = '=' if keep_selected else '<>'
    retire_totals = '''
        , removed_totals AS (
            DELETE FROM tvl_chart_total_values t USING obsolete o
            WHERE (t.owner_id,t.timestamp)=(o.owner_id,o.timestamp)
        )''' if totals_enabled(conn) else ''
    conn.execute(f'''WITH removed AS (
            DELETE FROM tvl_chart_days WHERE dataset_id{operator}? RETURNING owner_id,timestamp
        ), obsolete AS (
            SELECT owner_id,timestamp FROM removed
            EXCEPT SELECT owner_id,timestamp FROM tvl_chart_days WHERE dataset_id{keep}?
        )
        {retire_totals}
        DELETE FROM tvl_chart_row_values v USING obsolete o
        WHERE (v.owner_id,v.timestamp)=(o.owner_id,o.timestamp)''', (dataset_id,dataset_id))
