"""Report counts must not depend on financial valuation or duplicate cohorts."""
from types import SimpleNamespace

import pytest

from yearn_data.harvests import harvest_response, WEEK_SECONDS
from yearn_data.pairing import PairingDataset

END = 1793836800


@pytest.fixture
def store():
    dataset = PairingDataset.__new__(PairingDataset)
    dataset.id = 'a' * 64
    dataset.cutoff = END
    dataset.chains = (1, 10)
    def report(timestamp, family, chain=1, amount=None):
        return {'block_timestamp': timestamp, 'contract_family': family,
                'chain_id': chain, 'vault_address': '0x' + '11' * 20,
                'total_fees_paid_usd': amount}
    dataset.fees = [
        report(END - WEEK_SECONDS - 1, 'yearn-v2-vault'),
        report(END - WEEK_SECONDS, 'yearn-v2-vault', amount='0'),
        report(END - 1, 'yearn-v3-allocator'),
        report(END - 1, 'yearn-v3-tokenized-strategy', chain=10, amount='1'),
        report(END, 'yearn-v3-allocator'),
    ]
    dataset.earnings = dataset.fees[1:3]  # Same reports in the P&L cohort.
    return SimpleNamespace(get=lambda identity=None: dataset)


def test_half_open_week_counts_unpriced_and_zero_reports_without_adding_earnings(store):
    status, data = harvest_response(store, '/api/harvests')
    assert status == 200
    assert data == {
        'datasetId': 'a' * 64, 'reportCount': 3,
        'startTimestamp': END - WEEK_SECONDS, 'endTimestamp': END,
        'chainIds': [1, 10],
        'byContractFamily': {'yearn-v2-vault': 1, 'yearn-v3-allocator': 1,
                             'yearn-v3-tokenized-strategy': 1},
        'methodology': 'published-contract-reports-v1',
    }
    assert harvest_response(store, '/api/harvests?chainId=1')[1]['reportCount'] == 2
    dataset = store.get()
    dataset.fees = []
    assert harvest_response(store, '/api/harvests')[1]['reportCount'] == 0


@pytest.mark.parametrize('query', ['since=1', 'chainId=0', 'chainId=01', 'chainId=999',
    'chainId=1&chainId=10', 'datasetId=bad', 'datasetId='])
def test_rejects_unsupported_or_malformed_filters(store, query):
    assert harvest_response(store, '/api/harvests?' + query)[0] == 400
