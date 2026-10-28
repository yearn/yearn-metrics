"""Report-time strategy earnings attribution over a pinned, offline ownership ledger.

Fees are deliberately untouched. Unknown ownership is eligible for omission only
when its gross annual activity is priced and strictly below the materiality limit.
"""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import json
import re

from .fee_products import product_for

POLICY = 'report-time-strategy-ownership-v1'
ANNUAL_THRESHOLD_USD = Decimal('1000')
FAMILY = 'yearn-v3-tokenized-strategy'
HASH = re.compile(r'0x[0-9a-f]{64}')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def identity(row):
    return row['chain_id'], row['tx_hash'].lower(), row['log_index']


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid ownership integer')
    return value


def amount(value):
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError('invalid strategy valuation') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('invalid strategy valuation')
    return result


def redistributed(row):
    product = product_for(row['chain_id'], row['vault_address'])
    return product is not None and row['vault_address'].lower() != product['earningsAddress']


def apply_ownership(fees, ledger):
    """Return supplemental earnings rows and annual omission disclosures.

    The ledger is opt-in and covers explicit full-year UTC periods. Missing
    reports inside those periods are unresolved, never presumed external.
    Reports outside coverage retain the legacy policy and are disclosed as such.
    """
    if ledger.get('policy') != POLICY or ledger.get('sourceFeeRowsSha256') != digest(fees):
        raise ValueError('strategy ownership policy or source fee rows mismatch')
    periods = ledger.get('periods')
    if not isinstance(periods, list) or not periods:
        raise ValueError('strategy ownership requires annual coverage periods')
    bounds = []
    for period in periods:
        start, end = integer(period['since']), integer(period['until'])
        date = datetime.fromtimestamp(start, timezone.utc)
        try:
            expected = int(date.replace(year=date.year + 1).timestamp())
        except ValueError as exc:
            raise ValueError('unsupported annual coverage start') from exc
        if start % 86400 or end != expected or (bounds and start < bounds[-1][1]):
            raise ValueError('ownership periods must be ordered, nonoverlapping full UTC years')
        bounds.append((start, end))
    source = {}
    for row in fees:
        key = identity(row)
        if key in source:
            raise ValueError('duplicate fee event identity')
        source[key] = row
    evidence = {}
    for report in ledger.get('reports', []):
        chain = integer(report['chainId'], 1)
        tx, index = report['txHash'], integer(report['logIndex'])
        if not isinstance(tx, str) or not HASH.fullmatch(tx):
            raise ValueError('invalid ownership transaction hash')
        key = chain, tx, index
        if key in evidence or key not in source:
            raise ValueError('duplicate or unknown ownership report')
        row = source[key]
        if (row['contract_family'] != FAMILY or redistributed(row)
                or report['vaultAddress'] != row['vault_address'].lower()
                or integer(report['blockTimestamp']) != row['block_timestamp']
                or not any(a <= row['block_timestamp'] < b for a, b in bounds)):
            raise ValueError('ownership report does not match its source or coverage')
        parts = []
        seen_paths = set()
        for part in report['fractions']:
            status, path = part['classification'], part['pathId']
            if status not in ('included', 'parent-overlap', 'unresolved'):
                raise ValueError('unsupported ownership classification')
            # One stable chain/address key combines all routes to that beneficiary.
            if not isinstance(path, str) or not re.fullmatch(r'[1-9]\d*:0x[0-9a-f]{40}', path):
                raise ValueError('invalid ownership path identity')
            if (status, path) in seen_paths:
                raise ValueError('duplicate ownership path')
            seen_paths.add((status, path))
            numerator, denominator = part['numerator'], part['denominator']
            if not all(isinstance(v, str) and re.fullmatch(r'0|[1-9]\d*', v)
                       and len(v) <= 160 for v in (numerator, denominator)):
                raise ValueError('invalid ownership fraction')
            if int(denominator) == 0:
                raise ValueError('invalid ownership denominator')
            weight = Fraction(int(numerator), int(denominator))
            if not 0 < weight <= 1:
                raise ValueError('invalid ownership weight')
            parts.append((status, path, weight))
        if sum((p[2] for p in parts), Fraction()) != 1:
            raise ValueError('ownership fractions must sum to one')
        evidence[key] = parts

    annual = defaultdict(lambda: [Decimal(0), Decimal(0)])
    selected = []
    outside = 0
    redistributed_count = 0
    with localcontext() as ctx:
        ctx.prec = 400
        for row in fees:
            if row['contract_family'] != FAMILY:
                continue
            if redistributed(row):
                redistributed_count += 1
                continue
            period = next((i for i, (a, b) in enumerate(bounds)
                           if a <= row['block_timestamp'] < b), None)
            if period is None:
                outside += 1
                continue
            gain, loss = amount(row.get('gross_gain_usd')), amount(row.get('loss_usd'))
            parts = evidence.get(identity(row), [('unresolved',
                f"{row['chain_id']}:{row['vault_address'].lower()}", Fraction(1))])
            for status, path, weight in parts:
                if status != 'unresolved':
                    continue
                if gain is None or loss is None:
                    raise ValueError(f'unpriced unresolved strategy ownership: {path}')
                for i, value in enumerate((gain, loss)):
                    annual[period, path][i] += value * weight.numerator / weight.denominator
            selected.append((row, gain, loss, parts))
        for (_, path), (gain, loss) in annual.items():
            if gain + loss >= ANNUAL_THRESHOLD_USD:
                raise ValueError(f'unresolved strategy ownership reaches annual $1000 threshold: {path}')
        rows = []
        for row, gain, loss, parts in selected:
            included = sum((w for s, _, w in parts if s == 'included'), Fraction())
            def scale(value):
                if not included:
                    return '0'
                return (None if value is None else
                        format(value * included.numerator / included.denominator, 'f'))
            counted_gain, counted_loss = scale(gain), scale(loss)
            net = (None if counted_gain is None or counted_loss is None else
                   format(Decimal(counted_gain) - Decimal(counted_loss), 'f'))
            raw_net = None if gain is None or loss is None else format(gain - loss, 'f')
            rows.append({key: row[key] for key in ('chain_id', 'vault_address', 'tx_hash',
                                                  'log_index', 'block_timestamp', 'asset')} | {
                'version': 'v3', 'contract_family': FAMILY,
                'gross_gain_usd': counted_gain, 'loss_usd': counted_loss, 'net_yield_usd': net,
                'raw_gross_gain_usd': None if gain is None else format(gain, 'f'),
                'raw_loss_usd': None if loss is None else format(loss, 'f'),
                'raw_net_yield_usd': raw_net,
                'strategy_earnings_policy': POLICY,
            })
        disclosures = []
        for i, (start, end) in enumerate(bounds):
            paths = [{'pathId': path, 'grossGainsUsd': format(values[0], 'f'),
                      'lossesUsd': format(values[1], 'f'),
                      'grossActivityUsd': format(sum(values), 'f')}
                     for (period, path), values in sorted(annual.items()) if period == i]
            disclosures.append({'since': start, 'until': end, 'omittedPaths': paths,
                'omittedGrossActivityUsd': format(sum((Decimal(p['grossActivityUsd']) for p in paths), Decimal(0)), 'f')})
    return rows, {'policy': POLICY, 'annualPathThresholdUsd': '1000',
        'ownershipSha256': digest(ledger), 'coveragePeriods': disclosures,
        'includedReportCount': len(rows), 'outsideCoverageReportCount': outside,
        'redistributedReportCount': redistributed_count,
        'timing': 'strategy-report-time; parent earnings retain their existing recognition dates',
        'omissionPolicy': 'conservative omission; unresolved is not observed zero'}
