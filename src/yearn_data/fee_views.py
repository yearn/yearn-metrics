"""Display policy for current fee entries, separate from historical product earnings."""
from .ybold_fees import VAULT as YBOLD, STAKER
from .yvusd_fees import VAULT as YVUSD, ACCOUNTANTS


def fee_display_name(chain_id, address, fallback):
    if chain_id == 1:
        if address.lower() == YBOLD:
            return 'Unstaked yBOLD'
        if address.lower() == STAKER:
            return 'yBOLD'
        if address.lower() == YVUSD:
            return 'yvUSD'
    return fallback


def fee_view_entries(chains, rows):
    """Preserve generic effective rates; use verified own rates for named products."""
    key = lambda vault: (vault['chainId'], vault['address'].lower())
    current = {(r['chain_id'], r['vault'].lower()): r for r in rows}
    entries = {}
    def visit(node):
        identity = key(node['vault'])
        if identity not in entries:
            supported = not node['children'] and node['availability']['state'] == 'available'
            entries[identity] = {'vault': node['vault'], 'roots': [node], 'memberAddresses': [identity[1]],
                'performanceFee': node['perfFee'] if supported else None,
                'managementFee': node['mgmtFee'] if supported else None, 'feeRateBasis': 'stack'}
        for child in node['children']:
            visit(child)
    for chain in chains:
        node = chain['root']; identity = key(node['vault'])
        entries[identity] = {'vault': node['vault'], 'roots': [node], 'memberAddresses': [identity[1]],
            'performanceFee': chain['effectivePerfFee'], 'managementFee': chain['effectiveMgmtFee'],
            'feeRateBasis': 'stack'}
    for chain in chains:
        visit(chain['root'])
    for identity, entry in entries.items():
        node = entry['roots'][0]
        if identity[0] == 1 and identity[1] in (YBOLD, STAKER):
            own = node['feeAvailability']['state'] == 'available'
            entry.update(performanceFee=node['perfFee'] if own else None,
                         managementFee=node['mgmtFee'] if own else None, feeRateBasis='vault')
        value = current.get(identity, {}).get('external_tvl_usd')
        entry['adjustedTvlUsd'] = float(value) if value is not None else None
    anchor = entries.get((1, YVUSD))
    if anchor:
        members = [entries[(1, address)] for address in sorted(ACCOUNTANTS) if (1, address) in entries]
        anchor['memberAddresses'] += [entry['vault']['address'] for entry in members]
        anchor['roots'] += [root for entry in members for root in entry['roots']]
        capital = [anchor['adjustedTvlUsd'], *[entry['adjustedTvlUsd'] for entry in members]]
        anchor['adjustedTvlUsd'] = sum(capital) if all(value is not None for value in capital) else None
        # Each member's stack already includes its downstream fees. Weight only
        # external capital so locked shares held in the unlocked vault count once.
        for field in ('performanceFee', 'managementFee'):
            rates = [anchor[field], *[entry[field] for entry in members]]
            funded = [(weight, rate) for weight, rate in zip(capital, rates) if weight is not None and weight > 0]
            total = anchor['adjustedTvlUsd']
            anchor[field] = (sum(weight * rate for weight, rate in funded) / total
                             if total is not None and total > 0 and all(rate is not None for _, rate in funded)
                             else None)
        anchor['feeRateBasis'] = 'product'
        for entry in members:
            del entries[key(entry['vault'])]
    return list(entries.values())
