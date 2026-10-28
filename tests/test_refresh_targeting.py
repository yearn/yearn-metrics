"""Forward cutoff coverage reuse and explicitly scoped fee target acquisition."""
from yearn_data import fee_valuation,tokenized_fees
from test_fee_valuation import db as db,seed,point,ASSET,OTHER,DAY


def test_tokenized_ranges_reuse_earlier_cutoffs_and_invalidate_inventory_or_finality(db):
    vault=dict(chain_id=1,address=OTHER,asset_address=ASSET,asset_decimals=6,api_version='3.0.2',
        contract_family=tokenized_fees.FAMILY,classification_status='complete',classification_source='offline-fixture',deployment_block=10)
    fingerprint=tokenized_fees.inventory_fingerprint(vault)
    with db:
        db.execute('INSERT INTO tokenized_fee_ranges VALUES(1,?,10,19,172800,?,?)',(OTHER,fingerprint,'finalized'))
        db.execute('INSERT INTO tokenized_fee_ranges VALUES(1,?,20,24,172800,?,?)',(OTHER,fingerprint,'finalized'))
    def ranges(inventory=vault,confirmations=None):
        return [(r['fromBlock'],r['toBlock']) for r in tokenized_fees.pending_tokenized_ranges(
            db,[inventory],'eth',0,34,259200,chunk_size=10,confirmations=confirmations)]
    assert ranges()==[(25,34)]
    assert ranges(vault|{'api_version':'3.0.3'})==[(10,19),(20,29),(30,34)]
    assert ranges(confirmations=10)==[(10,19),(20,29),(30,34)]
    assert tokenized_fees.pending_tokenized_ranges(db,[vault],'eth',0,9,259200)==[]


def test_explicit_fee_targets_filter_family_chain_and_day_without_losing_global_metadata_gaps(db,monkeypatch):
    seed(db,1,asset=ASSET,timestamp=DAY+10)
    seed(db,2,asset=OTHER,timestamp=DAY+20)
    seed(db,3,asset=ASSET,timestamp=DAY+86400+10)
    seed(db,4,asset=ASSET,timestamp=DAY+30,tokenized=True)
    seed(db,5,asset=ASSET,timestamp=DAY+10,chain=10)
    seed(db,6,asset=None,decimals=None,timestamp=DAY+10)
    cutoff=DAY+2*86400
    target=(1,ASSET,DAY+86399)
    selected=list(fee_valuation.fee_events(db,cutoff,targets={target},chain_ids={1}))
    assert len(selected)==2 and {e['contract_family'] for e in selected}=={'yearn-v3-allocator','yearn-v3-tokenized-strategy'}
    work=fee_valuation.fee_pricing_work(db,before_timestamp=cutoff,refresh=True,chain_ids={1})
    assert len(work.targets)==3 and work.metadata_gaps==1
    calls=[]
    monkeypatch.setattr(fee_valuation.pricing,'fetch_yearn_prices_batch',lambda targets:calls.extend(targets) or {t:point(t) for t in targets})
    result=fee_valuation.price_fees(db,before_timestamp=cutoff,targets={target},chain_ids={1})
    assert result['selected_asset_days']==1 and calls==[target]
    assert fee_valuation.price_fees(db,before_timestamp=cutoff,targets={target},chain_ids={1})['selected_asset_days']==0
    assert calls==[target]


def test_tokenized_worker_shares_headers_across_configured_chunks(db,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from yearn_data import refresh
    from yearn_data.refresh_scope import RefreshOptions
    from yearn_data.storage import connect
    from contextlib import closing
    import pytest
    vault=dict(chain_id=1,address=OTHER,asset_address=ASSET,asset_decimals=6,api_version='3.0.2',
        contract_family=tokenized_fees.FAMILY,classification_status='complete',
        classification_source='offline-fixture',deployment_block=0)
    headers=[];scans=[]
    def block(number):
        headers.append(number)
        return dict(number=100 if number=='finalized' else number,timestamp=1000)
    def logs(query):
        scans.append((query['fromBlock'],query['toBlock']))
        return []
    client=SimpleNamespace(eth=SimpleNamespace(chain_id=1,get_block=block,get_logs=logs))
    monkeypatch.setattr(refresh,'chain_source',lambda _:SimpleNamespace(w3=client))
    monkeypatch.setattr(refresh,'PRODUCTS',[dict(chainId=1,memberAddresses=[OTHER],earningsAddress=ASSET)])
    monkeypatch.setattr(tokenized_fees,'load_inventory',lambda:[vault])
    options=RefreshOptions('staging',('eth',),172800,tmp_path/'pub',chunk_size=10)
    job=SimpleNamespace(id='batch',bounds={'eth':{'toBlock':34}},checkpoint=lambda *a:None)
    worker=refresh.RefreshWorker(options,job)
    result=worker.acquire_tokenized('eth',{})
    assert result['processed']==4 and result['batches']==1
    assert headers==['finalized',34]
    assert scans==[(0,9),(10,19),(20,29),(30,34)]
    with closing(connect('staging')) as conn:
        assert conn.execute('SELECT count(*) FROM tokenized_fee_ranges').fetchone()[0]==4
        window=tokenized_fees.TokenizedWindow(client,'eth',34,172800)
        with pytest.raises(ValueError,match='window does not match'):
            tokenized_fees.index_tokenized_fees(conn,[vault],'eth',0,9,259200,client=client,window=window)
