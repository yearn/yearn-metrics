"""Ownership and materiality boundaries; no RPC or database required."""
from copy import deepcopy

import pytest

from yearn_data.strategy_earnings import POLICY, apply_ownership, digest
from yearn_data.fee_products import PRODUCTS
from yearn_data.pairing import PairingDataset

START, END = 1735689600, 1767225600  # Full UTC year 2025.
VAULT, OWNER = '0x' + '11' * 20, '0x' + '22' * 20


def fee(n=1, gain='100', loss='20', **overrides):
    return dict(chain_id=1, tx_hash=f'0x{n:064x}', log_index=0, block_timestamp=START,
                vault_address=VAULT, asset='0x' + '33' * 20,
                contract_family='yearn-v3-tokenized-strategy', gross_gain_usd=gain,
                loss_usd=loss, total_fees_paid_usd='7') | overrides


def ledger(rows, weights=(('included', '1', '2'), ('parent-overlap', '1', '2'))):
    return {'policy': POLICY, 'sourceFeeRowsSha256': digest(rows),
            'periods': [{'since': START, 'until': END}], 'reports': [
                {'chainId': r['chain_id'], 'txHash': r['tx_hash'], 'logIndex': r['log_index'],
                 'vaultAddress': r['vault_address'], 'blockTimestamp': r['block_timestamp'],
                 'fractions': [{'classification': s, 'pathId': f'1:{OWNER}',
                                'numerator': n, 'denominator': d} for s, n, d in weights]}
                for r in rows]}


def test_counts_only_external_fraction_and_preserves_raw_earnings_and_fees():
    fees = [fee()]
    original = deepcopy(fees)
    rows, meta = apply_ownership(fees, ledger(fees))
    assert (rows[0]['gross_gain_usd'], rows[0]['loss_usd'], rows[0]['net_yield_usd']) == ('50', '10', '40')
    assert rows[0]['raw_net_yield_usd'] == '80'
    assert fees == original
    # The API's summary, catalog and time series consume the same counted rows.
    dataset = PairingDataset.__new__(PairingDataset)
    dataset.fees, dataset.earnings = fees, rows
    dataset.id, dataset.cutoff, dataset.chains = 'candidate', END, (1,)
    dataset.vaults, dataset.vault_names = {(1, VAULT)}, {}
    dataset.strategy_earnings = meta
    assert dataset.view('summary')['netLifetimeEarningsUsd'] == '40'
    assert dataset.view('history')['buckets'][0]['netLifetimeEarningsUsd'] == '40'
    assert dataset.view('vaults')['vaults'][0]['netLifetimeEarningsUsd'] == '40'
    assert dataset.view('vault-history')['buckets'][0]['vaults'][0]['lifetimeEarnings']['netYieldUsd'] == '40'
    assert dataset.view('summary')['totalFeesPaidUsd'] == '7'


def test_annual_threshold_combines_reports_and_gains_with_losses_not_net():
    fees = [fee(1, '600', '0'), fee(2, '0', '400')]
    with pytest.raises(ValueError, match='reaches annual'):
        apply_ownership(fees, ledger(fees, (('unresolved', '1', '1'),)))
    fees[1]['loss_usd'] = '399.99'
    rows, meta = apply_ownership(fees, ledger(fees, (('unresolved', '1', '1'),)))
    assert all(r['net_yield_usd'] == '0' for r in rows)
    assert meta['coveragePeriods'][0]['omittedGrossActivityUsd'] == '999.99'
    assert rows[0]['raw_net_yield_usd'] == '600'


def test_threshold_combines_same_beneficiary_across_different_strategies():
    fees = [fee(1, '600', '0'), fee(2, '500', '0', vault_address='0x' + '44' * 20)]
    with pytest.raises(ValueError, match='reaches annual'):
        apply_ownership(fees, ledger(fees, (('unresolved', '1', '1'),)))


def test_combined_omission_is_visible_even_when_each_path_is_small():
    fees = [fee(1, '600', '0'), fee(2, '700', '0', vault_address='0x' + '44' * 20)]
    evidence = ledger(fees)
    evidence['reports'] = []
    rows, meta = apply_ownership(fees, evidence)
    assert meta['coveragePeriods'][0]['omittedGrossActivityUsd'] == '1300'
    assert len(meta['coveragePeriods'][0]['omittedPaths']) == 2
    assert all(r['net_yield_usd'] == '0' for r in rows)


def test_missing_evidence_is_never_assumed_external_or_zero():
    fees = [fee(gain='1001', loss='0')]
    evidence = ledger(fees)
    evidence['reports'] = []
    with pytest.raises(ValueError, match='reaches annual'):
        apply_ownership(fees, evidence)
    fees[0]['gross_gain_usd'] = None
    evidence['sourceFeeRowsSha256'] = digest(fees)
    with pytest.raises(ValueError, match='unpriced unresolved'):
        apply_ownership(fees, evidence)


def test_known_direct_ownership_preserves_missing_prices():
    fees = [fee(gain=None, loss=None)]
    rows, _ = apply_ownership(fees, ledger(fees, (('included', '1', '1'),)))
    assert rows[0]['net_yield_usd'] is None
    assert rows[0]['gross_gain_usd'] is None


@pytest.mark.parametrize('mutation', ['source', 'event', 'weights', 'duplicate', 'period'])
def test_rejects_stale_or_ambiguous_evidence(mutation):
    fees = [fee()]
    evidence = ledger(fees)
    if mutation == 'source':
        fees[0]['gross_gain_usd'] = '101'
    elif mutation == 'event':
        evidence['reports'][0]['blockTimestamp'] += 1
    elif mutation == 'weights':
        evidence['reports'][0]['fractions'][0]['numerator'] = '2'
    elif mutation == 'duplicate':
        evidence['reports'].append(deepcopy(evidence['reports'][0]))
    else:
        evidence['periods'][0]['until'] -= 86400
    with pytest.raises(ValueError):
        apply_ownership(fees, evidence)


def test_product_distribution_legs_remain_excluded_and_fees_untouched():
    fees = [fee(i + 1, gain='10000', loss='0', vault_address=member)
            for i, member in enumerate(m for p in PRODUCTS for m in p['memberAddresses']
                                       if m != p['earningsAddress'])]
    evidence = ledger(fees)
    evidence['reports'] = []
    original = deepcopy(fees)
    rows, meta = apply_ownership(fees, evidence)
    assert rows == []
    assert meta['redistributedReportCount'] == len(fees)
    assert fees == original


def test_coverage_is_explicit_and_does_not_relabel_unaudited_history_as_zero():
    fees = [fee(), fee(2, block_timestamp=START - 1)]
    evidence = ledger(fees)
    evidence['reports'] = evidence['reports'][:1]
    rows, meta = apply_ownership(fees, evidence)
    assert len(rows) == 1
    assert meta['outsideCoverageReportCount'] == 1
    assert meta['coveragePeriods'][0]['since'] == START
