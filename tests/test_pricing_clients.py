"""Provider evidence contracts stay distinct from valuation and batch outages."""
import pytest
import requests

from yearn_data import pricing_clients as clients, pricing_adapters as adapters

ASSET = '0x'+'11'*20
OTHER = '0x'+'22'*20
USDC = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'


class Response:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_yearn_batch_exposes_accepted_evidence_and_unresolved_targets(monkeypatch):
    monkeypatch.setenv('YEARN_PRICE_PROD_KEY', 'test-only')
    monkeypatch.setattr(clients.requests, 'get', lambda *a, **kw: Response({'coins': {
        'ethereum:'+ASSET: {'prices': [{'timestamp': 86399, 'price': 2.5, 'source': 'oracle'}]},
        'ethereum:'+OTHER: {'prices': [{'timestamp': 86399, 'price': False, 'source': 'oracle'}]},
    }}))
    result = clients.fetch_yearn_prices_batch([(1, ASSET, 100), (1, OTHER, 100)])
    accepted = result.target_results[(1, ASSET, 86399)]
    assert accepted.value == 2.5 and accepted.status == 'ok'
    assert accepted.provider == 'yearn-prices' and accepted.reason == 'ok'
    assert accepted.requested_timestamp == accepted.evidence_timestamp == 86399
    assert accepted.provenance['upstream_source'] == 'oracle'
    unresolved = result.target_results[(1, OTHER, 86399)]
    assert unresolved.value is None and unresolved.status == 'unresolved'
    assert unresolved.reason == 'unaccepted-batch-target' and unresolved.evidence_timestamp is None
    assert (1, OTHER, 86399) not in result  # Sparse compatibility still requests exact verification.


def test_defillama_provider_and_adapter_paths_require_observations(monkeypatch):
    monkeypatch.setattr(clients.requests, 'get', lambda *a, **kw: Response({'coins': {
        'ethereum:'+ASSET: {'prices': [{'timestamp': 101, 'price': 3}]},
    }}))
    targets = [(1, ASSET, 100), (1, USDC, 100)]
    batch = clients.fetch_defillama_prices_batch(targets)
    quote = batch[(1, ASSET, 100)]
    assert quote.value == 3 and quote.provider == 'defillama'
    assert quote.requested_timestamp == 100 and quote.evidence_timestamp == 101
    missing = clients.fetch_defillama_price(1, USDC, 100)
    assert missing.value is None and missing.status == 'missing' and missing.evidence_timestamp is None
    # Neither layer can synthesize a parity quote for an unresolved token.
    legacy = adapters.fetch_defillama_price(1, USDC, 100)
    assert legacy.value is None and legacy.provider == 'defillama'
    assert legacy.status == 'missing'
    assert legacy.evidence_timestamp is None


@pytest.mark.parametrize('provider', ['yearn-prices', 'defillama'])
def test_batch_transport_failure_is_not_an_individual_unresolved_target(monkeypatch, provider):
    monkeypatch.setenv('YEARN_PRICE_PROD_KEY', 'test-only')
    monkeypatch.setenv('YEARN_PRICE_HTTP_ATTEMPTS', '1')
    def fail(*args, **kwargs):
        raise requests.ConnectionError('offline transport failure')
    monkeypatch.setattr(clients.requests, 'get', fail)
    fetch = clients.fetch_yearn_prices_batch if provider == 'yearn-prices' else clients.fetch_defillama_prices_batch
    error = clients.YearnPricesRequestError if provider == 'yearn-prices' else requests.ConnectionError
    with pytest.raises(error):
        fetch([(1, ASSET, 100)])
    monkeypatch.setattr(clients.requests, 'get', lambda *a, **kw: Response({'coins': {}}))
    result = fetch([(1, ASSET, 100)])
    unresolved = next(iter(result.target_results.values()))
    assert unresolved.value is None and unresolved.status in {'unresolved', 'missing'}



def test_scalar_defillama_sparse_result_keeps_named_evidence_contract(monkeypatch):
    assert adapters.fetch_defillama_price is clients.fetch_defillama_price
    monkeypatch.setattr(clients,'fetch_defillama_prices_batch',lambda *a,**k:{})
    result=clients.fetch_defillama_price(1,ASSET,100)
    assert isinstance(result,clients.PriceResult)
    assert result.value is None and result.status=='missing'
    assert result.provider=='defillama' and result.requested_timestamp==100
    assert result.evidence_timestamp is None
