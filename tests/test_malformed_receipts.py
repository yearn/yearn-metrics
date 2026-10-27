"""Each product rejects noncanonical StrategyReported lengths before accounting."""
import copy
import json
from pathlib import Path

import pytest

from yearn_data import ybold_fees, yeth_fees, yvusd_fees


@pytest.mark.parametrize('product', [yvusd_fees, ybold_fees, yeth_fees],
                         ids=['yvusd', 'ybold', 'yeth'])
@pytest.mark.parametrize('length', [0, 191, 193, 224])
def test_malformed_report_encoding_is_rejected(product, length):
    name = product.__name__.rsplit('.', 1)[-1].removesuffix('_fees')
    case = copy.deepcopy(json.loads((Path(__file__).parent / f'fixtures/fees/{name}.json').read_text())[-1])
    report, evidence = case['report'], case['evidence']
    log = next(log for log in evidence['receipt']['logs'] if log['logIndex'] == report['log_index'])
    original = log['data'][2:]
    assert len(original) == 192 * 2
    log['data'] = '0x' + (original[:length * 2] if length < 192 else original + '00' * (length - 192))
    with pytest.raises(ValueError, match='^invalid StrategyReported encoding$'):
        product.accounting(report, evidence)
