from yearn_data.storage import connect
from types import SimpleNamespace

import pytest
from yearn_data.analytics import fee_stack
from yearn_data.analytics_collect import custom_accountant_config
from yearn_data.ybold_fees import VAULT as YBOLD, STAKER, ACCOUNTANT, CODE_HASH
from yearn_data.yvusd_fees import VAULT as YVUSD

LOCKED = '0xaaafea48472f77563961cdb53291dedfb46f9040'
LOAN = '0x' + '1' * 40


def test_own_rates_survive_incomplete_underlying_allocations():
    rows = [{'chain_id': 1, 'vault': YBOLD, 'name': 'Yearn BOLD', 'tvl_usd': '100', 'external_tvl_usd': '10'},
            {'chain_id': 1, 'vault': STAKER, 'name': 'Staked yBOLD', 'tvl_usd': '90', 'external_tvl_usd': '90'}]
    positions = [{'chain_id': 1, 'parent': STAKER, 'strategy': STAKER, 'child': YBOLD, 'debt_usd': '90', 'status': 'ok'},
                 {'chain_id': 1, 'parent': YBOLD, 'strategy': LOAN, 'debt_usd': None, 'status': 'unavailable'}]
    configs = {'vaults': [{'chainId': 1, 'address': YBOLD, 'performanceFee': 0, 'managementFee': 0, 'fixedRateSupported': True},
                         {'chainId': 1, 'address': STAKER, 'performanceFee': 1000, 'managementFee': 0, 'fixedRateSupported': True}]}
    result = fee_stack(rows, positions, configs)
    assert result['chains'][0]['effectivePerfFee'] is None
    entries = {r['vault']['address']: r for r in result['entries']}
    assert entries[STAKER]['performanceFee'] == 1000
    assert entries[STAKER]['vault']['name'] == 'yBOLD'
    assert entries[YBOLD]['performanceFee'] == 0
    assert entries[YBOLD]['vault']['name'] == 'Unstaked yBOLD'
    configs['vaults'][0]['fixedRateSupported'] = False
    assert next(r for r in fee_stack(rows, positions, configs)['entries'] if r['vault']['address'] == YBOLD)['performanceFee'] is None


def test_yvusd_groups_external_capital_and_keeps_underlying_rates():
    rows = [{'chain_id': 1, 'vault': a, 'name': a, 'tvl_usd': str(gross), 'external_tvl_usd': str(external)}
            for a, gross, external in [(YVUSD, 100, 60), (LOCKED, 40, 35), (LOAN, 80, 0)]]
    positions = [{'chain_id': 1, 'parent': parent, 'strategy': child, 'child': child, 'debt_usd': str(debt), 'status': 'ok'}
                 for parent, child, debt in [(LOCKED, YVUSD, 40), (YVUSD, LOAN, 80)]]
    configs = {'vaults': [{'chainId': 1, 'address': a, 'performanceFee': fee, 'managementFee': 0, 'fixedRateSupported': True}
                         for a, fee in [(YVUSD, 0), (LOCKED, 0), (LOAN, 500)]]}
    result = fee_stack(rows, positions, configs)
    entries = {r['vault']['address']: r for r in result['entries']}
    assert LOCKED not in entries
    product = entries[YVUSD]
    assert product['vault']['name'] == 'yvUSD'
    assert product['adjustedTvlUsd'] == 95  # Locked capital owned upstream stays excluded.
    assert product['performanceFee'] == pytest.approx(400)
    assert product['managementFee'] == 0
    assert product['feeRateBasis'] == 'product'
    assert len(product['roots']) == 2
    assert product['roots'][0]['children'][0]['perfFee'] == 500
    assert any(c['effectivePerfFee'] > 0 for c in result['chains'])
    rows[1]['external_tvl_usd'] = None
    unavailable = next(r for r in fee_stack(rows, positions, configs)['entries'] if r['vault']['address'] == YVUSD)
    assert unavailable['adjustedTvlUsd'] is None
    assert unavailable['performanceFee'] is unavailable['managementFee'] is None
    rows[1]['external_tvl_usd'] = '35'
    configs['vaults'][1]['performanceFee'] = 100
    product = next(r for r in fee_stack(rows, positions, configs)['entries'] if r['vault']['address'] == YVUSD)
    assert product['performanceFee'] == pytest.approx((60 * 400 + 35 * 496) / 95)


@pytest.mark.parametrize('anchor', ['absent', 'gross-unpriced', 'external-unpriced'])
@pytest.mark.parametrize('known_fee', [True, False])
def test_locker_own_fee_fallback_depends_on_yvusd_entry_presence(anchor, known_fee):
    rows = [{'chain_id': 1, 'vault': LOCKED, 'name': 'Locked yvUSD',
             'tvl_usd': '40', 'external_tvl_usd': '35'}]
    if anchor != 'absent':
        rows.append({'chain_id': 1, 'vault': YVUSD, 'name': 'yvUSD',
                     'tvl_usd': '100' if anchor == 'external-unpriced' else None,
                     'external_tvl_usd': None})
    positions = [{'chain_id': 1, 'parent': LOCKED, 'strategy': YVUSD,
                  'child': YVUSD, 'debt_usd': None, 'status': 'unavailable'}]
    configs = {'vaults': [{'chainId': 1, 'address': LOCKED,
                          'performanceFee': 125, 'managementFee': 25,
                          'fixedRateSupported': known_fee}]}
    result = fee_stack(rows, positions, configs)
    assert len(result['entries']) == 1
    locker = result['entries'][0]
    if anchor == 'external-unpriced':
        assert locker['vault']['address'] == YVUSD
        assert locker['feeRateBasis'] == 'product'
        assert locker['adjustedTvlUsd'] is None
        assert locker['performanceFee'] is locker['managementFee'] is None
        assert set(locker['memberAddresses']) == {YVUSD, LOCKED}
        locked_root = next(root for root in locker['roots'] if root['vault']['address'] == LOCKED)
        assert (locked_root['perfFee'], locked_root['mgmtFee']) == (125, 25)
        assert locked_root['feeAvailability']['state'] == ('available' if known_fee else 'unavailable')
        return
    assert locker['vault']['address'] == LOCKED
    assert locker['performanceFee'] == (125 if known_fee else None)
    assert locker['managementFee'] == (25 if known_fee else None)
    assert locker['feeRateBasis'] == 'vault'
    assert locker['adjustedTvlUsd'] == 35
    assert locker['roots'][0]['availability']['state'] == 'partial'


def product_entries(capital=(60, 35), performance=(500, 900), management=(10, 30)):
    from yearn_data.fee_views import fee_view_entries
    chains = []
    rows = []
    for address, gross, external, perf, mgmt in zip(
            (YVUSD, LOCKED), (100, 40), capital, performance, management):
        root = {'vault': {'chainId': 1, 'address': address, 'name': address},
                'capitalUsd': gross, 'perfFee': 0, 'mgmtFee': 0, 'children': [],
                'feeAvailability': {'state': 'available'}, 'availability': {'state': 'available'}}
        chains.append({'root': root, 'effectivePerfFee': perf, 'effectiveMgmtFee': mgmt})
        rows.append({'chain_id': 1, 'vault': address,
                     'external_tvl_usd': str(external) if external is not None else None})
    entries = fee_view_entries(chains, rows)
    assert len(entries) == 1
    return entries[0], chains


def test_product_weights_member_stacks_by_external_not_gross_capital():
    product, chains = product_entries()
    assert product['performanceFee'] == pytest.approx((60 * 500 + 35 * 900) / 95)
    assert product['managementFee'] == pytest.approx((60 * 10 + 35 * 30) / 95)
    assert product['adjustedTvlUsd'] == 95
    assert product['roots'] == [chain['root'] for chain in chains]
    assert product['memberAddresses'] == [YVUSD, LOCKED]
    assert all(root['perfFee'] == root['mgmtFee'] == 0 for root in product['roots'])


@pytest.mark.parametrize(('capital', 'performance', 'expected'), [
    ((60, 35), (500, None), None),
    ((60, 35), (None, 900), None),
    ((60, None), (500, 900), None),
    ((0, 0), (500, 900), None),
    ((60, 0), (500, None), 500),
    ((0, 35), (None, 900), 900),
    ((60, 35), (0, 0), 0),
])
def test_product_rate_preserves_unavailable_zero_and_unfunded_members(capital, performance, expected):
    product, _ = product_entries(capital=capital, performance=performance)
    if expected is None:
        assert product['performanceFee'] is None
    else:
        assert product['performanceFee'] == pytest.approx(expected)


def test_product_missing_management_rate_does_not_erase_known_performance_rate():
    product, _ = product_entries(management=(10, None))
    assert product['performanceFee'] == pytest.approx((60 * 500 + 35 * 900) / 95)
    assert product['managementFee'] is None


def test_product_keeps_every_visible_locker_without_repeating_its_capital():
    from yearn_data.fee_views import fee_view_entries
    from yearn_data.yvusd_fees import ACCOUNTANTS
    _, chains = product_entries()
    second = next(address for address in ACCOUNTANTS if address != LOCKED)
    root = dict(chains[1]['root'], vault={'chainId': 1, 'address': second, 'name': 'Second locker'})
    chains.append({'root': root, 'effectivePerfFee': 1500, 'effectiveMgmtFee': 50})
    rows = [{'chain_id': 1, 'vault': address, 'external_tvl_usd': str(capital)}
            for address, capital in [(YVUSD, 60), (LOCKED, 35), (second, 5)]]
    entries = fee_view_entries(chains, rows)
    assert len(entries) == 1
    product = entries[0]
    assert product['adjustedTvlUsd'] == 100
    assert product['performanceFee'] == pytest.approx(690)
    assert product['managementFee'] == pytest.approx(19)
    assert set(product['memberAddresses']) == {YVUSD, LOCKED, second}
    assert len(product['roots']) == 3


def test_missing_downstream_fee_is_not_replaced_with_yvusd_own_zero():
    rows = [{'chain_id': 1, 'vault': address, 'name': address,
             'tvl_usd': str(gross), 'external_tvl_usd': str(external)}
            for address, gross, external in [(YVUSD, 100, 60), (LOCKED, 40, 40), (LOAN, 80, 0)]]
    positions = [{'chain_id': 1, 'parent': parent, 'strategy': child, 'child': child,
                  'debt_usd': str(debt), 'status': 'ok'}
                 for parent, child, debt in [(LOCKED, YVUSD, 40), (YVUSD, LOAN, 80)]]
    configs = {'vaults': [{'chainId': 1, 'address': address, 'performanceFee': fee, 'managementFee': 0}
                         for address, fee in [(YVUSD, 0), (LOCKED, 0), (LOAN, None)]]}
    product = next(entry for entry in fee_stack(rows, positions, configs)['entries']
                   if entry['vault']['address'] == YVUSD)
    assert product['performanceFee'] is None
    assert product['adjustedTvlUsd'] == 100
    assert len(product['roots']) == 2


def reader(monkeypatch, address, code_hash, response):
    import eth_utils
    monkeypatch.setattr(eth_utils, 'keccak', lambda code: bytes.fromhex(code_hash[2:]))
    block = b'1' * 32
    def code(target, block_identifier):
        assert target.lower() == address
        assert block_identifier == block
        return b'code'
    return SimpleNamespace(block={'hash': block}, w3=SimpleNamespace(eth=SimpleNamespace(get_code=code)), call=lambda calls: response)


def test_custom_ybold_accountant_requires_verified_recipient(monkeypatch):
    r = reader(monkeypatch, ACCOUNTANT, CODE_HASH, [(STAKER,)])
    config = {'chainId': 1, 'address': YBOLD, 'accountant': ACCOUNTANT}
    assert custom_accountant_config(r, config)['performanceFee'] == 0
    r.call = lambda calls: [(LOAN,)]
    with pytest.raises(ValueError, match='recipient'):
        custom_accountant_config(r, config)
    with pytest.raises(ValueError, match='accountant'):
        custom_accountant_config(r, config | {'accountant': LOAN})


def test_yvusd_reads_actual_fee_config_separately_from_locker_bonus(monkeypatch):
    code_hash = '0x237ca0cef5a652adde023f9a80e5dbd8535e4a36cfd216bd42bfa7af31e8ebf1'
    r = reader(monkeypatch, LOCKED, code_hash, [(YVUSD,), (25, 50, 1000)])
    config = {'chainId': 1, 'address': YVUSD, 'accountant': LOCKED}
    amounts = custom_accountant_config(r, config)
    assert (amounts['managementFee'], amounts['performanceFee'], amounts['lockerBonus']) == (25, 50, 1000)
    r.call = lambda calls: [(YVUSD,), None]
    with pytest.raises(ValueError, match='configuration'):
        custom_accountant_config(r, config)
    r.call = lambda calls: [(LOAN,), (25, 50, 1000)]
    with pytest.raises(ValueError, match='asset'):
        custom_accountant_config(r, config)
    r = reader(monkeypatch, LOCKED, '0x' + '0' * 64, [])
    with pytest.raises(ValueError, match='implementation'):
        custom_accountant_config(r, config)


@pytest.mark.parametrize('positions', [
    [],
    [{'chain_id': 1, 'parent': LOAN, 'strategy': '0x' + '2' * 40,
      'child': '0x' + '2' * 40, 'debt_usd': '100', 'status': 'ok'}],
])
def test_standalone_vaults_have_direct_deposit_entries_without_a_nested_stack(positions):
    rows = [{'chain_id': 1, 'vault': LOAN, 'name': 'Curve DOLA-sUSDe Factory yVault',
             'tvl_usd': '100', 'external_tvl_usd': '100'}]
    configs = {'vaults': [{'chainId': 1, 'address': LOAN, 'performanceFee': 1000,
                           'managementFee': 0, 'fixedRateSupported': True}]}
    result = fee_stack(rows, positions, configs)
    assert result['chains'] == []  # Legacy stack statistics still describe nesting.
    assert result['totalStackedCapital'] == 0
    assert len(result['entries']) == 1
    entry = result['entries'][0]
    assert entry['vault']['address'] == LOAN
    assert entry['performanceFee'] == pytest.approx(1000)
    assert entry['managementFee'] == 0
    assert entry['adjustedTvlUsd'] == 100
    assert entry['roots'][0]['children'] == []
    configs['vaults'][0]['performanceFee'] = None
    assert fee_stack(rows, positions, configs)['entries'][0]['performanceFee'] is None


def test_adding_standalone_entries_preserves_nested_stacks_and_counts_each_vault_once():
    from copy import deepcopy
    rows = [{'chain_id': 1, 'vault': a, 'name': a, 'tvl_usd': str(gross),
             'external_tvl_usd': str(external)}
            for a, gross, external in [(YVUSD, 100, 100), (LOAN, 60, 0)]]
    configs = {'vaults': [{'chainId': 1, 'address': a, 'performanceFee': fee, 'managementFee': 0}
                         for a, fee in [(YVUSD, 0), (LOAN, 500)]]}
    positions = [{'chain_id': 1, 'parent': YVUSD, 'strategy': LOAN, 'child': LOAN,
                  'debt_usd': '60', 'status': 'ok'}]
    previous = fee_stack(rows, positions, configs)
    standalone = '0x' + '3' * 40
    extended_rows = deepcopy(rows) + [{'chain_id': 1, 'vault': standalone, 'name': 'Standalone',
                                     'tvl_usd': '40', 'external_tvl_usd': '40'}]
    extended_configs = deepcopy(configs)
    extended_configs['vaults'].append({'chainId': 1, 'address': standalone,
                                     'performanceFee': 1000, 'managementFee': 200})
    result = fee_stack(extended_rows, positions, extended_configs)
    for field in ('chains', 'maxDepth', 'maxEffectivePerfFee', 'avgEffectivePerfFee',
                  'totalStackedCapital', 'availability'):
        assert result[field] == previous[field]
    entries = {r['vault']['address']: r for r in result['entries']}
    assert len(entries) == len(result['entries']) == 3
    assert entries[standalone]['performanceFee'] == pytest.approx(1000)
    assert entries[standalone]['managementFee'] == 200
    assert entries[LOAN]['adjustedTvlUsd'] == 0  # Upstream-owned capital remains deducted.
    assert entries[YVUSD]['performanceFee'] == pytest.approx(300)
    assert entries[YVUSD]['roots'][0]['children'][0]['perfFee'] == 500


@pytest.mark.parametrize('tvl', [None, '0'])
def test_standalone_entries_keep_existing_tvl_eligibility(tvl):
    rows = [{'chain_id': 1, 'vault': LOAN, 'name': 'Standalone',
             'tvl_usd': tvl, 'external_tvl_usd': tvl}]
    configs = {'vaults': [{'chainId': 1, 'address': LOAN, 'performanceFee': 1000, 'managementFee': 0}]}
    assert fee_stack(rows, [], configs)['entries'] == []


@pytest.mark.parametrize('failure', ['code_timeout', 'call_timeout', 'rpc_value_error', 'code_mismatch', 'recipient_mismatch', 'recipient_unavailable'])
def test_observed_product_config_records_failure_kind(monkeypatch, failure):
    from yearn_data import analytics_collect
    from yearn_data.analytics import SCHEMA, put_input, selected_input
    r=reader(monkeypatch, ACCOUNTANT, CODE_HASH, [(STAKER,)])
    r.block.update(number=123, timestamp=456)
    def calls(batch):
        if not batch:return []
        if len(batch)==4:return [None,None,None,(ACCOUNTANT,)]
        if failure=='call_timeout':raise TimeoutError('private provider URL')
        if failure=='recipient_unavailable':return [None]
        return [(LOAN if failure=='recipient_mismatch' else STAKER,)]
    r.call=calls
    if failure in ('code_timeout','rpc_value_error'):
        def unavailable(*args, **kwargs):
            raise (TimeoutError if failure=='code_timeout' else ValueError)('private provider URL')
        r.w3.eth.get_code=unavailable
    if failure=='code_mismatch':
        import eth_utils
        monkeypatch.setattr(eth_utils,'keccak',lambda code:bytes(32))
    monkeypatch.setattr(analytics_collect,'Reader',lambda chain:r)
    configs=analytics_collect.observed_configs(1,[{'address':YBOLD,'version':'v3'}],[])
    conn=connect('staging')
    try:
        conn.executescript(SCHEMA)
        put_input(conn,'fee-config',{'vaults':configs},status='partial',observed_at=456)
        stored=selected_input(conn,'fee-config')['body']['vaults'][0]
        assert stored['failureKind'] == ('verification_rejected' if failure.endswith('mismatch') else 'acquisition_failed')
        assert stored['failureReason'] and 'private provider URL' not in str(stored)
        assert stored['fixedRateSupported'] is False and stored['availability']=='unavailable'
    finally:conn.close()


@pytest.mark.parametrize('failed_address', [YVUSD, LOCKED, LOAN])
def test_product_discloses_retained_fee_observations(failed_address):
    from yearn_data.analytics import SCHEMA, put_input, selected_input
    with connect('staging') as conn:
        conn.executescript(SCHEMA)
        configs = [{'chainId': 1, 'address': a, 'performanceFee': fee, 'managementFee': 0,
                    'fixedRateSupported': True, 'observedAt': 1}
                   for a, fee in [(YVUSD, 0), (LOCKED, 100), (LOAN, 500)]]
        original = put_input(conn, 'fee-config', {'vaults': configs}, observed_at=1)
        failed = next(c for c in configs if c['address'] == failed_address) | {
            'performanceFee': None, 'managementFee': None, 'fixedRateSupported': False,
            'observedAt': 2, 'failureKind': 'acquisition_failed'}
        put_input(conn, 'fee-config', {'vaults': [failed]}, status='partial', observed_at=2)
        rows = [{'chain_id': 1, 'vault': a, 'tvl_usd': 100, 'external_tvl_usd': capital}
                for a, capital in [(YVUSD, 60), (LOCKED, 40), (LOAN, 0)]]
        positions = [{'chain_id': 1, 'parent': YVUSD, 'strategy': LOAN, 'child': LOAN,
                      'debt_usd': 100, 'status': 'ok'}]
        def product():
            result = fee_stack(rows, positions, selected_input(conn, 'fee-config')['body'])
            return next(e for e in result['entries'] if e['vault']['address'] == YVUSD)
        entry = product()
        assert entry['performanceFee'] == pytest.approx(340)
        assert entry['retainedFeeConfigurations'] == [{
            'chainId': 1, 'address': failed_address, 'observedAt': 1,
            'retainedFromInputId': original, 'retryObservedAt': 2,
            'failureKind': 'acquisition_failed'}]
        put_input(conn, 'fee-config', {'vaults': configs}, observed_at=3)
        assert product()['retainedFeeConfigurations'] == []
