"""A changed route declaration must not become verified positive membership."""
import pytest

from yearn_data.analytics_collect import FINANCE_CHAINS, CURATION_CHAINS, rules
from yearn_data.finance_membership import contributions
from test_finance_membership import FixtureReader, PARENT, CHILD, STRATEGY, OTHER, ASSET


def finance_source(entries):
    return '''buildNestedDebtByVault subtractNestedDebt get_default_queue withdrawalQueue
list/vaults/${api.chainId}?origin=yearn
const v1Vaults = [];
const blacklist = [];
'''+'const chains = ['+','.join(repr(v) for v in FINANCE_CHAINS.values())+'];\n'+ \
        'const registeredNestedVaults = [\n'+entries+'\n];'


def registry():
    return 'const config = {\n  "yearn-curating": { chains: {\n'+''.join(
        f'{name}: {{morphoVaultOwners: []}},\n' for name in CURATION_CHAINS.values())+'}\n  },\n}'


def route(**overrides):
    fields={'chainId':'10','parent':repr(PARENT),'strategy':repr(STRATEGY),'child':repr(CHILD)}
    fields.update(overrides)
    return '{'+','.join(k+': '+v for k,v in fields.items())+'}'


def net(policy,debt=30):
    active={a:{'asset':{'address':ASSET}} for a in (PARENT,CHILD)}
    reader=FixtureReader({(a,'token()'):(a,) for a in active})
    return contributions(reader,10,active,{PARENT:(100,),CHILD:(100,)},
                         {PARENT:[(STRATEGY,debt)],CHILD:[]},policy)


def test_chain_key_reordering_retains_the_registered_deduction():
    entry='{parent: '+repr(PARENT)+',strategy: '+repr(STRATEGY)+',child: '+repr(CHILD)+',chainId: 10}'
    policy=rules(finance_source(entry),registry())
    values,evidence=net(policy)
    assert values=={PARENT:70,CHILD:100}
    assert evidence[0]['method']=='registered' and evidence[0]['deduction']=='30'


@pytest.mark.parametrize('broken', ['nested','spread','missing-field','unclosed','missing-declaration','duplicate'])
def test_unparsed_route_entries_fail_closed(broken):
    good=route()
    entries=good+','+route(parent=repr(OTHER))
    if broken=='nested':entries=good+','+route(metadata='{changed: true}')
    elif broken=='spread':entries=good+', ...additionalRoutes'
    elif broken=='missing-field':entries=good+', {chainId: 10, parent: '+repr(OTHER)+'}'
    elif broken=='unclosed':entries=good+','+route(parent=repr(OTHER))[:-1]
    elif broken=='duplicate':entries=good+','+route(child=repr(OTHER))
    source=finance_source(entries)
    if broken=='missing-declaration':source=source.replace('registeredNestedVaults','renamedRoutes')
    with pytest.raises(ValueError,match='finance.*route'):
        rules(source,registry())


@pytest.mark.parametrize('debt',[0,30])
def test_unresolved_asset_constants_do_not_verify_the_affected_contribution(debt):
    policy=rules(finance_source(route(parentAsset='ADDRESSES.ethereum.USDC',
                                      childAsset='ADDRESSES.ethereum.USDS')),registry())
    values,evidence=net(policy,debt)
    assert values[PARENT]==(100 if debt==0 else None)
    assert values[CHILD]==100
    assert evidence==[]
