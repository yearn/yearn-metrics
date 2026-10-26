"""Zero fees preserve component values; persisted rollups preserve completeness."""
import json

import pytest

from test_fee_valuation import db as db, seed, point, outputs, ASSET, CUTOFF, DAY
from yearn_data import fee_valuation as valuation

COMPONENTS = [field for field in valuation.FIELDS if field != 'total_fees_paid_raw']
STATES = ['missing', 'retryable', 'metadata', 'priced', 'zero']
DISTRIBUTIONS = {'locker_bonus_raw', 'staker_distribution_raw', 'recovery_distribution_raw'}


def assert_component(event, field, state):
    assert event['total_fees_paid_usd'] == '0'
    expected = '2.5' if state == 'priced' else '0' if state == 'zero' else None
    assert event[field.replace('_raw', '_usd')] == expected
    if state in ('priced', 'zero'):
        status = 'zero_fee'
    elif state == 'metadata':
        status = 'metadata_unavailable'
    else:
        status = 'zero_fee_unpriced_distribution' if field in DISTRIBUTIONS else 'zero_fee_unpriced_component'
    assert event['valuation_status'] == status


@pytest.mark.parametrize('field', COMPONENTS)
@pytest.mark.parametrize('state', STATES)
def test_zero_fee_component_value(field, state):
    event = {'chain_id': 1, 'asset': ASSET, 'asset_decimals': None if state == 'metadata' else 6,
             'block_timestamp': DAY + 100,
             'accounting': {'total_fees_paid_raw': '0', field: '0' if state == 'zero' else '1000000'}}
    cache = {}
    if state in ('priced', 'retryable'):
        cache[(1, ASSET, CUTOFF-1)] = {
            'price_usd': '2.5' if state == 'priced' else None,
            'status': 'ok' if state == 'priced' else 'retryable',
            'evidence_json': '{}', 'fetched_at': 1}
    result = valuation.value_event(event, cache)
    assert_component(result, field, state)
    assert result['price_status'] == ('ok' if state == 'priced' else 'retryable' if state == 'retryable' else 'not_requested')


@pytest.mark.parametrize('state', STATES)
def test_persisted_component_completeness_in_every_rollup(db, state):
    # Distinct vaults/assets isolate every component in their own buckets;
    # family/day/chain buckets also exercise aggregation across multiple events.
    for number, field in enumerate(COMPONENTS, 1):
        asset = f'0x{number:040x}'
        tx = f'0x{number:064x}'
        seed(db, number, fee='0', asset=asset, decimals=None if state == 'metadata' else 6)
        db.execute('UPDATE strategy_reports SET vault_address=? WHERE tx_hash=?', (asset, tx))
        row = db.execute('SELECT accounting_json FROM canonical_fee_reports WHERE tx_hash=?', (tx,)).fetchone()
        amounts = json.loads(row[0])
        amounts[field] = '0' if state == 'zero' else '1000000'
        db.execute('UPDATE canonical_fee_reports SET accounting_json=? WHERE tx_hash=?', (json.dumps(amounts), tx))
        target = (1, asset, CUTOFF-1)
        if state == 'priced':
            valuation.save_price(db, target, point(target))
        elif state == 'retryable':
            valuation.save_price(db, target, (None, 'retryable', {'reason': 'test'}))
    db.commit()
    run = valuation.run_fee_usd(db, before_timestamp=CUTOFF)
    events = {int(event['tx_hash'], 16): event for event in outputs(db, run, 'fee_usd_events')}
    assert set(events) == set(range(1, len(COMPONENTS)+1))
    for number, field in enumerate(COMPONENTS, 1):
        assert_component(events[number], field, state)
    complete = state in ('priced', 'zero')
    for group in ('family', 'day', 'vault', 'asset', 'chain'):
        totals = outputs(db, run, 'fee_usd_by_'+group)
        assert len(totals) == (1 if group in ('family', 'day', 'chain') else len(COMPONENTS))
        assert sum(total['events'] for total in totals) == len(COMPONENTS)
        for total in totals:
            assert total['pricing_complete_for_accepted_fees'] is complete
            assert total['fee_usd_complete'] is complete
            assert total['zero_fee_events'] == total['events']
            assert total['unpriced_component_events'] == (0 if complete else total['events'])
