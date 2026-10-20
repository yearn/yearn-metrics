import pytest
from yearn_data.finance_membership import capped_debt

def test_converter_cap_normalizes_child_units():
    route=dict(parentAsset='usdc',childAsset='usds',parentDecimals=6,childDecimals=18)
    assert capped_debt(100_000_000,200*10**18,1,4,route,'usdc','usds')==50_000_000
    assert capped_debt(10_000_000,200*10**18,1,4,route,'usdc','usds')==10_000_000
    with pytest.raises(ValueError,match='assets changed'):
        capped_debt(100,100,1,2,route,'dai','usds')

def test_router_zero_supply_and_invalid_shares():
    assert capped_debt(100,100,0,0,{},'asset','asset')==0
    with pytest.raises(ValueError,match='ownership'):
        capped_debt(100,100,2,1,{},'asset','asset')


def test_wrapper_and_unavailable_reads_are_distinct():
    from yearn_data.finance_membership import contributions
    parent='0x'+'11'*20; child='0x'+'22'*20; token='0x'+'33'*20
    class Reader:
        def call(self,calls):
            return [(child,) if a==parent else (token,) for a,*_ in calls]
    active={parent:{'asset':{'address':child}},child:{'asset':{'address':token}}}
    result,edges=contributions(Reader(),10,active,{parent:(50,),child:(100,)},{parent:[],child:None},{'cross':{},'routes':{}})
    assert result[parent]==0
    assert result[child] is None
    assert edges[0]['method']=='vault-asset'


PARENT = '0x' + '11' * 20
CHILD = '0x' + '22' * 20
ASSET = '0x' + '33' * 20
STRATEGY = '0x' + '44' * 20
OTHER = '0x' + '55' * 20


class FixtureReader:
    block = {'number': 100, 'timestamp': 1234}

    def __init__(self, values):
        self.values = values

    def call(self, calls):
        return [self.values.get((address, signature)) for address, signature, *_ in calls]


def policy():
    return {'cross': {}, 'routes': {}, 'blacklist': set(), 'v1': set(),
            'explicit': {10: set()}, 'owners': {10: set()}, 'factories': []}


@pytest.mark.parametrize('metadata', [None, {}, {'address': ''}, {'address': None}])
def test_missing_catalog_asset_is_unavailable_for_membership_and_replay(monkeypatch, tmp_path, metadata):
    import importlib.util
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from yearn_data import analytics_collect
    from yearn_data.finance_membership import contributions

    candidates = [{'chainId': 10, 'address': PARENT, 'asset': metadata}]
    reader = FixtureReader({(PARENT, 'totalAssets()'): (100,), (PARENT, 'asset()'): (ASSET,)})
    monkeypatch.setattr(analytics_collect, 'Reader', lambda chain: reader)
    local = [{'address': PARENT}]
    observed = analytics_collect.membership_chain(10, local, candidates, policy(), {})
    assert observed[0]['included'] is None

    active = {PARENT: candidates[0], CHILD: {'asset': metadata}}
    net, edges = contributions(reader, 10, active, {PARENT: (100,), CHILD: (60,)},
                               {PARENT: [(CHILD, 60)], CHILD: []}, policy())
    assert net == {PARENT: None, CHILD: None}
    assert edges == []

    path = Path(__file__).resolve().parents[1] / 'scripts' / 'collect_finance_comparison.py'
    spec = importlib.util.spec_from_file_location('collect_finance_comparison', path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    monkeypatch.setattr(replay, 'FINANCE_CHAINS', {10: 'optimism'})
    monkeypatch.setattr(replay, 'Reader', lambda chain: reader)
    monkeypatch.setattr(replay, 'ArchiveReader', lambda chain: SimpleNamespace(block_at=lambda timestamp: 100))
    reader.w3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda number: reader.block))
    monkeypatch.setattr(replay, 'fetch', lambda url: SimpleNamespace(json=lambda: candidates))
    monkeypatch.setattr(replay, 'rules', lambda *args: policy())
    for name in ('projects_yearn_index.js', 'registries_curators.js'):
        (tmp_path / name).write_text('fixture')
    replay.collect(tmp_path, 1234)
    records = json.loads((tmp_path / 'finance-replay.json').read_text())[0]['records']
    assert records[0]['rawAssets'] == '100'
    assert records[0]['adjustedRaw'] is None


def test_child_without_asset_metadata_cannot_support_a_deduction():
    from yearn_data.finance_membership import contributions

    active = {PARENT: {'asset': {'address': ASSET}}, CHILD: {'asset': None}}
    reader = FixtureReader({(a, 'asset()'): (ASSET,) for a in active})
    net, edges = contributions(reader, 10, active, {PARENT: (100,), CHILD: (60,)},
                               {PARENT: [(CHILD, 60)], CHILD: []}, policy())
    assert net == {PARENT: None, CHILD: None}
    assert edges == []


@pytest.mark.parametrize('failure', [None, 'destination', 'assets', 'ownership'])
def test_registered_route_is_checked_without_invalidating_other_vaults(monkeypatch, failure):
    from yearn_data import analytics_collect
    from yearn_data.finance_membership import contributions

    active = {a: {'chainId': 10, 'address': a, 'asset': {'address': ASSET}}
              for a in (PARENT, CHILD, OTHER)}
    values = {(a, signature): (ASSET,) for a in active for signature in ('token()', 'asset()')}
    values.update({(a, 'totalAssets()'): (100,) for a in active})
    values.update({(STRATEGY, 'vault()'): (OTHER if failure == 'destination' else CHILD,),
                   (CHILD, 'balanceOf(address)'): (11 if failure == 'ownership' else 5,),
                   (CHILD, 'totalSupply()'): (10,)})
    reader = FixtureReader(values)
    rules = policy()
    key = (10, PARENT, STRATEGY)
    rules['cross'][key] = CHILD
    rules['routes'][key] = {'getter': 'vault'}
    if failure == 'assets':
        rules['routes'][key].update(parentAsset=OTHER, childAsset=ASSET, parentDecimals=6, childDecimals=18)
    debts = {PARENT: [(STRATEGY, 100)], CHILD: [], OTHER: []}
    net, edges = contributions(reader, 10, active, {a: (100,) for a in active}, debts, rules)
    assert net == {PARENT: None if failure else 50, CHILD: 100, OTHER: 100}
    assert edges == ([] if failure else [dict(parent=PARENT, strategy=STRATEGY, child=CHILD,
                                             method='registered', deduction='50')])

    # A bad Finance route cannot erase independent explicit curation membership.
    monkeypatch.setattr(analytics_collect, 'Reader', lambda chain: reader)
    monkeypatch.setattr(analytics_collect, 'default_debts', lambda *args: debts)
    monkeypatch.setattr(analytics_collect, 'CURATION_CHAINS', {10: 'optimism'})
    rules['explicit'][10].add(PARENT)
    curated = '0x' + '66' * 20
    rules['owners'][10].add(OTHER)
    local = list(active.values()) + [{'address': curated, 'tvl_category': 'curation', 'version': 'morpho-v2'}]
    rows = analytics_collect.membership_chain(10, local, list(active.values()), rules,
                                              {(10, curated): {'owner': OTHER}})
    assert {r['address']: r['included'] for r in rows} == {PARENT: True, CHILD: True, OTHER: True, curated: True}


@pytest.mark.parametrize('sender_present', [False, True])
@pytest.mark.parametrize('failure', [None, 'destination', 'unavailable'])
def test_ethereum_remote_deduction_and_failed_evidence(monkeypatch, sender_present, failure):
    from types import SimpleNamespace
    from yearn_data import analytics_collect, tvl_sources
    from yearn_data.finance_membership import contributions

    parent = '0x696d02db93291651ed510704c9b286841d506987'
    sender = '0xc5b16e7efe1ca05714477b8edcab4dee9b93a27c'
    holder = '0xefeebaacdc5640a123fe282652503ea4fd499d14'
    child = '0x80c34bd3a3569e126e7055831036aa7b212cb159'
    local_asset = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
    remote_asset = '0x203a662b0bd271a6ed5a60edfbd04bfce608fd36'
    active = {a: {'chainId': 1, 'address': a, 'asset': {'address': local_asset}}
              for a in [parent, OTHER] + ([sender] if sender_present else [])}
    balances = {a: (100,) for a in active}
    debts = {a: [] for a in active}
    debts[parent] = [(sender, 100)]
    values = {(a, 'asset()'): (local_asset,) for a in active}
    values.update({(a, 'totalAssets()'): (100,) for a in active})
    values.update({(sender, 'REMOTE_CHAIN_ID()'): (747474,), (sender, 'REMOTE_COUNTERPART()'): (holder,),
                   (parent, 'strategies(address)'): (0, 0, 100, 0)})
    remote = FixtureReader({(holder, 'REMOTE_COUNTERPART()'): (sender,),
                            (holder, 'vault()'): (OTHER if failure == 'destination' else child,),
                            (child, 'asset()'): (remote_asset,), (child, 'balanceOf(address)'): (3,),
                            (child, 'totalSupply()'): (5,),
                            (child, 'totalAssets()'): None if failure == 'unavailable' else (100,)})
    def remote_reader(chain):
        if chain == 1:
            return FixtureReader(values)
        assert chain == 747474
        return remote
    def block_at(timestamp):
        assert timestamp == 1234
        return 200
    remote.w3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda number: {'number': number}))
    monkeypatch.setattr(analytics_collect, 'Reader', remote_reader)
    monkeypatch.setattr(analytics_collect, 'fetch', lambda url: SimpleNamespace(json=lambda: [{'chainId': 747474, 'address': child}]))
    monkeypatch.setattr(tvl_sources, 'ArchiveReader', lambda chain: SimpleNamespace(block_at=block_at))
    net, edges = contributions(FixtureReader(values), 1, active, balances, debts, policy())
    target = sender if sender_present else parent
    assert net[target] == (None if failure else 40)
    assert net[OTHER] == 100
    remote_edges = [edge for edge in edges if edge['method'] == 'cross-chain-shares']
    if failure:
        assert remote_edges == []
    else:
        assert remote_edges == [{'parent': parent, 'contributionVault': target, 'strategy': sender, 'child': child, 'childChainId': 747474,
                                 'method': 'cross-chain-shares', 'deduction': '60', 'remoteBlock': 200}]
        from yearn_data.tvl_comparison import finance_overlap_status, finance_replay_routes
        replay = {'chainId': 1, 'edges': edges,
                  'records': [{'address': a, 'adjustedRaw': value} for a, value in net.items()]}
        recognized = finance_replay_routes([replay])
        position = {'chain_id': 1, 'parent': parent, 'strategy': sender, 'child_chain_id': 747474, 'child': child}
        assert finance_overlap_status(position, recognized) == 'handled'

    monkeypatch.setattr(analytics_collect, 'default_debts', lambda *args: debts)
    rules = policy()
    rules['explicit'][1] = set()
    rules['owners'][1] = set()
    members = analytics_collect.membership_chain(1, list(active.values()), list(active.values()), rules, {})
    observed = {row['address']: row for row in members}
    assert 'separate replay' not in observed[target]['reason']
    if failure:
        assert observed[target]['included'] is None
        assert 'unavailable' in observed[target]['reason']
    else:
        assert 'cross-chain deductions' in observed[target]['reason']
    assert 'cross-chain' not in observed[OTHER]['reason']


def test_later_failed_route_discards_earlier_parent_evidence():
    from yearn_data.finance_membership import contributions

    active = {a: {'asset': {'address': ASSET}} for a in (PARENT, CHILD, OTHER)}
    reader = FixtureReader({(a, 'asset()'): (ASSET,) for a in active})
    rules = policy()
    key = (10, PARENT, STRATEGY)
    rules['cross'][key] = OTHER
    rules['routes'][key] = {'getter': 'vault'}
    # First debt is valid, but the later route read is unavailable.
    debts = {PARENT: [(CHILD, 20), (STRATEGY, 30)], CHILD: [], OTHER: []}
    net, edges = contributions(reader, 10, active, {a: (100,) for a in active}, debts, rules)
    assert net == {PARENT: None, CHILD: 100, OTHER: 100}
    assert edges == []


@pytest.mark.parametrize('missing', ['parentAsset', 'childAsset', 'parentDecimals', 'childDecimals'])
def test_partial_converter_definition_only_invalidates_its_parent(missing):
    from yearn_data.finance_membership import contributions

    active = {a: {'asset': {'address': ASSET}} for a in (PARENT, CHILD, OTHER)}
    values = {(a, 'asset()'): (ASSET,) for a in active}
    values.update({(STRATEGY, 'vault()'): (CHILD,), (CHILD, 'balanceOf(address)'): (5,),
                   (CHILD, 'totalSupply()'): (10,)})
    route = {'getter': 'vault', 'parentAsset': ASSET, 'childAsset': ASSET,
             'parentDecimals': 6, 'childDecimals': 6}
    del route[missing]
    rules = policy()
    key = (10, PARENT, STRATEGY)
    rules['cross'][key] = CHILD
    rules['routes'][key] = route
    net, edges = contributions(FixtureReader(values), 10, active, {a: (100,) for a in active},
                               {PARENT: [(STRATEGY, 60)], CHILD: [], OTHER: []}, rules)
    assert net == {PARENT: None, CHILD: 100, OTHER: 100}
    assert edges == []
