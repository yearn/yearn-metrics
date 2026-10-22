from yearn_data.config import CHAINS
from yearn_data.storage import connect, init_db, seed_chains


def test_init_db_and_seed_chains(tmp_path):
    conn = connect('staging')
    init_db(conn)
    seed_chains(conn, CHAINS)
    count = conn.execute("SELECT count(*) AS c FROM chains").fetchone()["c"]
    assert count == len(CHAINS)
