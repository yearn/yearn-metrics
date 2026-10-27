"""Symmetric, evidence-backed scope and overlap comparison for a pinned TVL close."""
from decimal import Decimal, InvalidOperation
import math


def finance_replay_routes(replays):
    """Only complete replay contributions can support an overlap verdict."""
    recognized = set()
    for replay in replays:
        known = {row['address'] for row in replay['records'] if row.get('adjustedRaw') is not None}
        for edge in replay['edges']:
            if edge['parent'] not in known or edge.get('contributionVault', edge['parent']) not in known:
                continue
            recognized.add((replay['chainId'], edge['parent'], edge.get('strategy'),
                            edge.get('childChainId', replay['chainId']), edge['child'], edge['method']))
    return recognized


def finance_overlap_status(position, recognized):
    """Match a stored position to dated Finance deduction evidence."""
    identity = (position['chain_id'], position['parent'],
                position.get('child_chain_id', position['chain_id']), position['child'])
    if any((chain, parent, child_chain, child) == identity
           and (strategy == position['strategy'] or method == 'vault-asset')
           for chain, parent, strategy, child_chain, child, method in recognized):
        return 'handled'
    return None


def discrepancy_details(rows, evidence, dataset_id):
    """Do not mix inventory/adapter evidence from another published dataset."""
    if not isinstance(evidence, dict) or evidence.get('tvlDatasetId') != dataset_id:
        return {'state': 'unavailable', 'reason': 'Bidirectional evidence is not prepared for this TVL dataset'}
    required = {'vaults', 'asOfTimestamp', 'coverage', 'sources'}
    def records_complete(records, fields):
        return isinstance(records, list) and all(
            isinstance(record, dict) and fields <= record.keys() for record in records)
    def known_number(number):
        try:
            return not isinstance(number, bool) and math.isfinite(float(Decimal(str(number))))
        except (InvalidOperation, ValueError, OverflowError):
            return False
    if (not required <= evidence.keys()
            or type(evidence['asOfTimestamp']) is not int or evidence['asOfTimestamp'] < 0
            or not isinstance(evidence['coverage'], dict)
            or not isinstance(evidence['sources'], dict)
            or not isinstance(evidence['coverage'].get('incompleteChains', []), list)
            or not records_complete(evidence['vaults'], {'protocol', 'chainId', 'address'})
            or any(not isinstance(v['protocol'], str) or not isinstance(v['address'], str)
                   or not v['address'] or type(v['chainId']) is not int or v['chainId'] <= 0
                   or (v.get('quote') is not None and not isinstance(v['quote'], dict))
                   or any(v.get(field) is not None and
                          (not isinstance(v[field], str) or not known_number(v[field]))
                          for field in ('units', 'replayTvlUsd'))
                   for v in evidence['vaults'])
            or not records_complete(evidence.get('overlapChecks', []), {'protocol', 'status'})
            or any(not isinstance(check['protocol'], str)
                   or check['status'] not in ('missed', 'handled', 'unverified')
                   for check in evidence.get('overlapChecks', []))
            or any(check['status'] in ('missed', 'handled') and not known_number(check.get('ourDeductionUsd'))
                   for check in evidence.get('overlapChecks', []))):
        return {'state': 'unavailable', 'reason': 'Comparison evidence is incomplete for this TVL dataset'}
    local = {(r['chain_id'], r['vault'].lower()): r for r in rows}
    external = {(v['protocol'], v['chainId'], v['address'].lower()): v for v in evidence['vaults']}
    categories = []
    def value(number):
        return None if number is None else float(Decimal(str(number)))
    def entry(row, address=None):
        return {'chainId': row['chain_id'], 'vaultAddress': address or row['vault'],
                'name': row.get('name'), 'category': row.get('category'),
                'oursUsd': value(row.get('external_tvl_usd'))}
    for protocol, curation in [('yearn-finance', False), ('yearn-curating', True)]:
        ours_only, theirs_only, unresolved = [], [], []
        for identity, row in local.items():
            if (row.get('category') == 'curation') != curation:
                continue
            amount = value(row.get('external_tvl_usd'))
            if amount is None or amount <= 0 or (protocol, *identity) in external:
                continue
            record = entry(row)
            if curation and identity[0] in evidence.get('coverage', {}).get('incompleteChains', []):
                unresolved.append(record | {'reason': 'External factory inventory incomplete; exclusion unverified'})
            else:
                ours_only.append(record | {'defillamaUsd': 0, 'reason': 'Outside the independently checked adapter scope'})
        for (target, chain, address), observation in external.items():
            units = observation.get('units')
            if target != protocol or (units is not None and Decimal(units) <= 0):
                continue
            row = local.get((chain, address))
            # A zero net contribution is not a scope exclusion: the parent and child
            # attribution differs between the two implementations.
            if row is not None and row.get('tvl_usd') is not None:
                continue
            amount = value(observation.get('replayTvlUsd')) if units is not None else None
            record = {'chainId': chain, 'vaultAddress': address, 'name': observation.get('name'),
                      'category': observation.get('category'), 'oursUsd': None if row else 0,
                      'defillamaUsd': amount,
                      'reason': 'Missing local price' if row else 'Missing from our snapshot',
                      'valuationBasis': 'Adapter replay with a historical DefiLlama token quote',
                      'priceTimestamp': (observation.get('quote') or {}).get('timestamp')}
            if amount is None:
                unresolved.append(record | {'reason': 'In adapter scope; a priced external contribution is not verified'})
            elif amount > 0:
                theirs_only.append(record)
        checks = [r for r in evidence.get('overlapChecks', []) if r['protocol'] == protocol]
        missed = sorted((r | {'ourDeductionUsd': value(r['ourDeductionUsd'])} for r in checks if r['status'] == 'missed'),
                        key=lambda r: -r['ourDeductionUsd'])
        handled = [r | {'ourDeductionUsd': value(r['ourDeductionUsd'])} for r in checks if r['status'] == 'handled']
        categories.append({'protocol': protocol,
                           'oursOnly': sorted(ours_only, key=lambda r: -r['oursUsd']),
                           'theirsOnly': sorted(theirs_only, key=lambda r: -r['defillamaUsd']),
                           'missedOverlaps': missed, 'handledOverlaps': handled,
                           'unresolvedVaults': unresolved,
                           'unverifiedOverlaps': [r for r in checks if r['status'] == 'unverified']})
    return {'state': 'partial', 'datasetId': dataset_id, 'asOfTimestamp': evidence['asOfTimestamp'],
            'categories': categories, 'coverage': evidence['coverage'], 'sources': evidence['sources']}
