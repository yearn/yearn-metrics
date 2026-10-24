"""A known zero fee does not make other positive, unpriced amounts complete."""
import json
import pytest
from test_fee_valuation import db as db, seed, point, outputs, ASSET, CUTOFF
from yearn_data import fee_valuation as valuation


@pytest.mark.parametrize('field',[f for f in valuation.FIELDS if f!='total_fees_paid_raw'])
@pytest.mark.parametrize('state',['missing','retryable','metadata','priced','zero'])
def test_zero_fee_completeness_includes_every_positive_component(db, field, state):
    seed(db,1,fee='0',decimals=None if state=='metadata' else 6)
    amounts=json.loads(db.execute('SELECT accounting_json FROM canonical_fee_reports').fetchone()[0])
    amounts[field]='0' if state=='zero' else '1000000'
    db.execute('UPDATE canonical_fee_reports SET accounting_json=?',(json.dumps(amounts),));db.commit()
    target=(1,ASSET,CUTOFF-1)
    if state=='priced':valuation.save_price(db,target,point(target))
    elif state=='retryable':valuation.save_price(db,target,(None,'retryable',{'reason':'test'}))
    run=valuation.run_fee_usd(db,before_timestamp=CUTOFF)
    event=outputs(db,run,'fee_usd_events')[0]
    complete=state in ('priced','zero')
    assert event['total_fees_paid_usd']=='0'
    assert event[field.replace('_raw','_usd')]==('2.5' if state=='priced' else '0' if state=='zero' else None)
    if complete:assert event['valuation_status']=='zero_fee'
    elif state=='metadata':assert event['valuation_status']=='metadata_unavailable'
    else:assert event['valuation_status'] in ('zero_fee_unpriced_component','zero_fee_unpriced_distribution')
    for group in ('family','day','vault','asset','chain'):
        total=outputs(db,run,'fee_usd_by_'+group)[0]
        assert total['pricing_complete_for_accepted_fees'] is complete
        assert total['fee_usd_complete'] is complete
        assert total['zero_fee_events']==1
        assert total['unpriced_component_events']==(0 if complete else 1)
