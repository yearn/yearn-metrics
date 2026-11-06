"""Harvest counts over the selected publication, independent of fee valuation."""
from collections import Counter
import re
from urllib.parse import parse_qs, urlsplit

from .pairing import FAMILIES

WEEK_SECONDS = 7 * 86400


def harvest_response(store, url):
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        if set(query) - {'datasetId', 'chainId'}:
            raise ValueError('unsupported filter')
        if any(len(values) != 1 for values in query.values()):
            raise ValueError('filters must appear only once')
        identity = query.get('datasetId', [None])[0]
        if identity is not None and not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('invalid datasetId')
        raw_chain = query.get('chainId', [None])[0]
        if raw_chain is not None and not re.fullmatch('[1-9][0-9]*', raw_chain):
            raise ValueError('chainId must be a positive integer')
        dataset = store.get(identity)
        chains = (int(raw_chain),) if raw_chain else dataset.chains
        if any(chain not in dataset.chains for chain in chains):
            raise ValueError('unsupported chain for this dataset')
        end = dataset.cutoff
        start = end - WEEK_SECONDS
        if start < 0:
            raise ValueError('publication does not cover a seven-day window')
        # Published fee rows have one row per canonical report identity, including
        # zero, loss and unpriced reports. Do not add earnings rows: allocator
        # reports occur in both cohorts. Tokenized Strategy Reported events are
        # independent contract reports, including those underlying allocators.
        fees, _ = dataset.view_rows(start, end, chains, None)
        families = Counter(row['contract_family'] for row in fees)
        return 200, {
            'datasetId': dataset.id,
            'reportCount': len(fees),
            'startTimestamp': start,
            'endTimestamp': end,
            'chainIds': list(chains),
            'byContractFamily': {family: families[family] for family in FAMILIES},
            'methodology': 'published-contract-reports-v1',
        }
    except ValueError as error:
        return 400, {'error': str(error)}
