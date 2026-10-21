"""The custom-product layers added by PR38 obey the distribution contract."""
import copy
import csv
import json
from contextlib import closing
from pathlib import Path

import pytest

from test_yvusd_review import insert, rows
from yearn_data import fees, fee_valuation as valuation, ybold_fees, yeth_fees
from yearn_data.exports import export_analysis
from yearn_data.fee_recompute import recompute_canonical_fees
from yearn_data.storage import connect, init_db


@pytest.fixture(params=[('ybold',ybold_fees,'staker_distribution_raw'),('yeth',yeth_fees,'recovery_distribution_raw')])
def product(request,tmp_path,monkeypatch):
    name,module,field=request.param
    case=copy.deepcopy(json.loads((Path(__file__).parent/f'fixtures/fees/{name}.json').read_text())[-1])
    case['report'].setdefault('api_version',None)
    monkeypatch.setattr(fees,'web3_for',lambda chain:object())
    monkeypatch.setattr(module,'collect',lambda *args:copy.deepcopy(case['evidence']))
    with closing(connect(tmp_path/'products.sqlite')) as conn:
        init_db(conn);insert(conn,case['report'])
        yield conn,case,field,name


def index(conn,case):
    return fees.index_canonical_fees(conn,receipt_fetcher=lambda *args:case['evidence']['receipt'])


def test_positive_product_distribution_without_price_is_incomplete(product):
    conn,case,field,_=product;assert index(conn,case)==1
    day=valuation.pricing.normalize_yearn_price_timestamp(case['report']['block_timestamp'])
    run=valuation.run_fee_usd(conn,before_timestamp=day+1)
    event=rows(conn,run,'fee_usd_events')[0]
    assert int(event[field])>0 and event[field.replace('_raw','_usd')] is None
    assert event['total_fees_paid_usd']=='0'
    assert event['valuation_status']=='zero_fee_unpriced_distribution'
    for group in ('family','day','vault','asset','chain'):
        total=rows(conn,run,'fee_usd_by_'+group)[0]
        assert total['unpriced_distribution_events']==1
        assert not total['fee_usd_complete'] and not total['pricing_complete_for_accepted_fees']


def test_product_distribution_csv_is_independent_of_first_row(product,tmp_path):
    conn,case,field,_=product
    insert(conn,dict(case['report'],vault_address='0x'+'55'*20,tx_hash='0x'+'66'*32,block_number=case['report']['block_number']-1))
    assert index(conn,case)==2
    run=fees.run_canonical_fees(conn);export_analysis(conn,'canonical-fees',tmp_path/'export',run)
    with (tmp_path/'export/canonical_fee_events.csv').open() as stream:
        reader=csv.DictReader(stream);assert field in reader.fieldnames
        normal,corrected=list(reader)
    assert normal[field]=='' and corrected[field]==case['report']['total_fees_raw']


@pytest.mark.parametrize('length',[0,191,193,224])
@pytest.mark.parametrize('offline',[False,True])
def test_malformed_product_receipt_is_unresolved(product,length,offline):
    conn,case,_,name=product
    if offline: assert index(conn,case)==1
    log=next(l for l in case['evidence']['receipt']['logs'] if l['logIndex']==case['report']['log_index'])
    original=log['data'][2:];log['data']='0x'+(original[:length*2] if length<192 else original+'00'*(length-192))
    if offline:
        evidence=json.loads(conn.execute('SELECT evidence_json FROM canonical_fee_reports').fetchone()[0]);evidence[name]=case['evidence']
        conn.execute('UPDATE canonical_fee_reports SET evidence_json=?',(json.dumps(evidence),));conn.commit()
        assert recompute_canonical_fees(conn,'formulas')['unavailable']==1
    else:assert index(conn,case)==1
    result=conn.execute('SELECT status,accounting_json FROM canonical_fee_reports').fetchone()
    assert result['status']=='unresolved' and result['accounting_json'] is None


def test_product_receipt_failure_retains_reason_and_normal_report(product):
    conn,case,_,_=product
    insert(conn,dict(case['report'],vault_address='0x'+'55'*20,log_index=case['report']['log_index']+1))
    def unavailable(*args):raise TimeoutError('provider unavailable')
    assert fees.index_canonical_fees(conn,receipt_fetcher=unavailable)==2
    results={r['report_log_index']:r for r in conn.execute('SELECT * FROM canonical_fee_reports')}
    target=results[case['report']['log_index']]
    assert target['status']=='unresolved' and target['reason']=='receipt_unavailable_or_invalid'
    assert results[case['report']['log_index']+1]['status']=='ok'


@pytest.mark.parametrize('damage', ['missing', 'invalid', 'inputs'])
def test_product_offline_replay_reason_identifies_evidence_gap(product, damage):
    conn,case,_,name=product;assert index(conn,case)==1
    evidence=json.loads(conn.execute('SELECT evidence_json FROM canonical_fee_reports').fetchone()[0])
    if damage=='missing':
        evidence.pop(name)
    elif damage=='invalid':
        evidence[name]['receipt']['status']=0
    else:
        evidence['allocator_inputs']['total_fees_raw']='123'
    conn.execute('UPDATE canonical_fee_reports SET evidence_json=?',(json.dumps(evidence),));conn.commit()
    assert recompute_canonical_fees(conn,'formulas')['unavailable']==1
    reason='cached_allocator_inputs_missing_or_changed' if damage=='inputs' else 'cached_distribution_evidence_missing_or_invalid'
    assert tuple(conn.execute('SELECT accounting_json,reason FROM canonical_fee_reports').fetchone())==(None,reason)


def test_rejected_product_accountant_has_refresh_diagnostic(product):
    conn,case,_,name=product
    expected=case['evidence']['accountant'];bad='0x'+'11'*20
    case['evidence']['accountant']=bad
    assert index(conn,case)==1
    assert tuple(conn.execute('SELECT status,reason FROM canonical_fee_reports').fetchone())==('unresolved','rejected_'+name+'_accountant')
    case['evidence']['accountant']=expected
    fetch=lambda *args:case['evidence']['receipt']
    assert fees.index_canonical_fees(conn,retry_unresolved=True,receipt_fetcher=fetch)==1
    assert tuple(conn.execute('SELECT status,reason FROM canonical_fee_reports').fetchone())==('unresolved','rejected_'+name+'_accountant')
    assert json.loads(conn.execute('SELECT evidence_json FROM canonical_fee_reports').fetchone()[0])[name]['accountant']==bad
    for mode in ('formulas','policy'):
        assert recompute_canonical_fees(conn,mode)['unavailable']==1
        assert conn.execute('SELECT reason FROM canonical_fee_reports').fetchone()[0]=='rejected_'+name+'_accountant'
    assert fees.index_canonical_fees(conn,retry_unresolved=True,refresh_evidence=True,receipt_fetcher=fetch)==1
    row=conn.execute('SELECT status,reason,evidence_json FROM canonical_fee_reports').fetchone()
    assert row['status']=='ok' and row['reason'] is None
    assert json.loads(row['evidence_json'])[name]['accountant']==expected
