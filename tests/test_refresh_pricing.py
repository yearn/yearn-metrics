"""More than one page, unavailable quotes and accepted caches must coexist."""
from contextlib import closing

import pytest

from yearn_data import pricing,fee_valuation
from yearn_data.storage import connect,init_db
from yearn_data.refresh_state import RefreshState,WorkBudget,WorkDeferred,drain_pages,query_digest


def test_price_pages_resume_after_500_without_repeating_missing_or_overwriting_accepted(postgres_database,monkeypatch):
    with closing(connect('staging')) as conn:
        init_db(conn)
        with conn:
            conn.executemany('''INSERT INTO strategy_reports(chain_id,version,vault_address,strategy_address,
                tx_hash,log_index,block_number,block_timestamp,asset,asset_decimals,gain_raw,loss_raw,net_raw)
                VALUES(1,'v3','vault','strategy',?,0,1,?,?,6,'1','0','1')''',
                [(f'tx-{i}',100 if i<504 else 86401,f'0x{i:040x}') for i in range(1,505)])
            conn.execute("INSERT INTO prices VALUES(1,?,100,1,'yearn-prices',7,'ok','{}')",(f'0x{502:040x}',))
            conn.execute("INSERT INTO prices VALUES(1,?,100,1,'yearn-prices',NULL,'unsupported','{}')",(f'0x{503:040x}',))
        requests=[]
        def fetch(batch):
            requests.extend(batch)
            return {target:(None,'missing',{}) if target[1]==f'0x{1:040x}' else (2,'ok',{}) for target in batch}
        monkeypatch.setattr(pricing,'fetch_yearn_prices_batch',fetch)
        monkeypatch.setattr(pricing,'fetch_yearn_price',lambda *a:pytest.fail('unexpected exact recovery'))
        monkeypatch.setattr(fee_valuation.pricing,'fetch_yearn_prices_batch',fetch)
        monkeypatch.setattr(fee_valuation.pricing,'fetch_yearn_price',lambda *a:pytest.fail('unexpected exact recovery'))
        def select(after,limit):
            return pricing.report_pricing_targets(conn,source='yearn-prices',retry_missing=True,
                retry_statuses=('missing','pending','retryable'),chain_ids={1},before_timestamp=86400,
                after=after,limit=limit)
        def apply(rows):
            pricing.price_unpriced_reports(conn,source='yearn-prices',fallback=None,onchain_fallbacks=False,
                before_timestamp=86400,retry_missing=True,retry_statuses=('missing','pending','retryable'),
                targets=[(r['chain_id'],r['asset'],r['block_timestamp']) for r in rows])
        def output(_result):return query_digest(conn,'SELECT * FROM prices ORDER BY chain_id,token_address,timestamp,source')
        context={'cutoff':86400,'chains':[1]}
        with RefreshState('staging',context,[]) as job:
            identity=job.id
            def first(saved):
                return drain_pages(saved,select=select,apply=apply,key=lambda r:(r['block_timestamp'],r['chain_id'],r['asset']),
                    budget=WorkBudget(500),checkpoint=job.checkpoint)
            with pytest.raises(WorkDeferred):job.stage('report-prices',{},output,first)
        assert len(requests)==500
        with RefreshState('staging',context,resume=identity) as job:
            def remaining(saved):
                return drain_pages(saved,select=select,apply=apply,key=lambda r:(r['block_timestamp'],r['chain_id'],r['asset']),
                    budget=WorkBudget(500),checkpoint=job.checkpoint)
            assert job.stage('report-prices',{},output,remaining)=={'processed':501,'batches':2}
        assert len(requests)==len(set(requests))==501
        assert conn.execute("SELECT price_usd FROM prices WHERE token_address=?",(f'0x{502:040x}',)).fetchone()[0]==7
        assert conn.execute("SELECT price_usd,status FROM prices WHERE token_address=?",(f'0x{1:040x}',)).fetchone()[0] is None
        assert conn.execute('SELECT count(*) FROM prices WHERE timestamp>=86400').fetchone()[0]==0
