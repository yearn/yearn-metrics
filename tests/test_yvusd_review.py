"""Regression coverage for yvUSD evidence and published fee outputs."""
from yearn_data.storage import table_columns
import copy
import csv
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from test_yvusd_fees import CASES
from yearn_data import fees, fee_valuation as valuation
from yearn_data.exports import export_analysis
from yearn_data.fee_recompute import recompute_canonical_fees
from yearn_data.storage import connect, init_db


def insert(conn, report):
    conn.execute('INSERT INTO vaults(chain_id,address,version,api_version,updated_at) VALUES (1,?,?,?,1) ON CONFLICT DO NOTHING',
                 (report['vault_address'], report['version'], report['api_version']))
    fields = [row for row in table_columns(conn,'strategy_reports') if row in report]
    conn.execute('INSERT INTO strategy_reports ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',
                 [report[f] for f in fields])
    conn.commit()


@pytest.fixture
def case_db(tmp_path, monkeypatch):
    case = copy.deepcopy(CASES[-1])
    monkeypatch.setattr(fees, 'web3_for', lambda chain: SimpleNamespace(eth=SimpleNamespace(
        call=lambda *args, **kwargs: bytes.fromhex('00'*12+case['evidence']['accountant'][2:]))))
    with closing(connect('staging')) as conn:
        init_db(conn); insert(conn, case['report'])
        yield conn, case


def index(conn, case, **kwargs):
    return fees.index_canonical_fees(conn, receipt_fetcher=lambda *args: case['evidence']['receipt'], **kwargs)


def rows(conn, run, name):
    return [json.loads(r[0]) for r in conn.execute('SELECT row_json FROM analysis_outputs WHERE run_id=? AND name=?', (run,name))]


@pytest.mark.parametrize('priced', [False, True])
def test_locker_only_valuation_preserves_zero_and_distribution_completeness(case_db, priced):
    conn, case = case_db; assert index(conn, case) == 1
    report = case['report']
    day = valuation.pricing.normalize_yearn_price_timestamp(report['block_timestamp'])
    if priced:
        valuation.save_price(conn, (1,report['asset'].lower(),day), ('2','ok',{'provider':'yearn-prices','normalized_timestamp':day}))
    run = valuation.run_fee_usd(conn, before_timestamp=day+1)
    event = rows(conn, run, 'fee_usd_events')[0]
    assert event['total_fees_paid_usd'] == '0'
    assert event['locker_bonus_usd'] == ('50.071976' if priced else None)
    assert event['valuation_status'] == ('zero_fee' if priced else 'zero_fee_unpriced_distribution')
    for group in ('family','day','vault','asset','chain'):
        total = rows(conn, run, 'fee_usd_by_'+group)[0]
        assert total['fee_usd_complete'] is priced
        assert total['pricing_complete_for_accepted_fees'] is priced
        assert total['zero_fee_events'] == 1
        assert total['unpriced_distribution_events'] == (0 if priced else 1)


def test_canonical_csv_keeps_distribution_columns_when_normal_report_is_first(case_db, tmp_path):
    conn, case = case_db
    normal = dict(case['report'], vault_address='0x'+'55'*20, tx_hash='0x'+'66'*32,
                  block_number=case['report']['block_number']-1)
    insert(conn, normal); assert index(conn, case) == 2
    run = fees.run_canonical_fees(conn)
    export_analysis(conn, 'canonical-fees', tmp_path/'export', run)
    with (tmp_path/'export/canonical_fee_events.csv').open() as stream:
        reader=csv.DictReader(stream)
        assert {'locker_bonus_raw','reported_total_fees_raw'} <= set(reader.fieldnames)
        normal_row, corrected = list(reader)
    assert normal_row['locker_bonus_raw'] == normal_row['reported_total_fees_raw'] == ''
    assert corrected['locker_bonus_raw'] == corrected['reported_total_fees_raw'] == '25035988'
    assert corrected['total_fees_paid_raw'] == '0'


@pytest.mark.parametrize('length', [0,191,193,224])
@pytest.mark.parametrize('offline', [False,True])
def test_malformed_report_data_stays_unresolved(case_db, length, offline):
    conn, case = case_db
    if offline: assert index(conn,case) == 1
    log=next(l for l in case['evidence']['receipt']['logs'] if l['logIndex']==case['report']['log_index'])
    original=log['data'][2:]
    log['data']='0x'+(original[:length*2] if length<192 else original+'00'*(length-192))
    if offline:
        evidence=json.loads(conn.execute('SELECT evidence_json FROM canonical_fee_reports').fetchone()[0])
        evidence['yvusd']=case['evidence']
        conn.execute('UPDATE canonical_fee_reports SET evidence_json=?',(json.dumps(evidence),));conn.commit()
        assert recompute_canonical_fees(conn,'formulas')['unavailable'] == 1
    else:
        assert index(conn,case) == 1
    result=conn.execute('SELECT status,accounting_json FROM canonical_fee_reports').fetchone()
    assert result['status'] == 'unresolved' and result['accounting_json'] is None


@pytest.mark.parametrize('invalid_receipt', [False, True])
def test_receipt_failure_keeps_reason_and_other_reports_available(case_db, invalid_receipt):
    conn, case = case_db
    insert(conn, dict(case['report'], vault_address='0x'+'55'*20, log_index=case['report']['log_index']+1))
    def fetch(*args):
        if invalid_receipt: return dict(case['evidence']['receipt'], status=0)
        raise TimeoutError('provider unavailable')
    assert fees.index_canonical_fees(conn, receipt_fetcher=fetch) == 2
    results={r['report_log_index']:r for r in conn.execute('SELECT * FROM canonical_fee_reports')}
    target=results[case['report']['log_index']]
    assert target['status']=='unresolved' and target['reason']=='receipt_unavailable_or_invalid'
    assert results[case['report']['log_index']+1]['status']=='ok'


@pytest.mark.parametrize('damage', ['missing','unreconciled','inputs'])
def test_offline_replay_distinguishes_distribution_evidence_from_inputs(case_db, damage):
    conn,case=case_db;assert index(conn,case)==1
    evidence=json.loads(conn.execute('SELECT evidence_json FROM canonical_fee_reports').fetchone()[0])
    if damage == 'missing':
        evidence.pop('yvusd')
    elif damage == 'unreconciled':
        from yearn_data.yvusd_fees import FEE_TOPIC
        log=next(l for l in evidence['yvusd']['receipt']['logs'] if l['topics'][0]==FEE_TOPIC)
        log['topics'][1]='0x'+format(123,'064x')
    else:
        evidence['allocator_inputs']['total_fees_raw']='123'
    conn.execute('UPDATE canonical_fee_reports SET evidence_json=?',(json.dumps(evidence),));conn.commit()
    assert recompute_canonical_fees(conn,'formulas')['unavailable']==1
    reason='cached_allocator_inputs_missing_or_changed' if damage=='inputs' else 'cached_distribution_evidence_missing_or_invalid'
    assert tuple(conn.execute('SELECT accounting_json,reason FROM canonical_fee_reports').fetchone()) == (None,reason)


@pytest.mark.parametrize('scope,expected', [({'version':'v2'},0), ({'chains':['arb']},0),
    ({'report_keys':[(1,'0x'+'00'*32,0)]},0), ({'version':'v3','limit':1},1), ({'target_only':True},1)])
def test_legacy_upgrade_respects_filters(case_db, monkeypatch, scope, expected):
    conn, case = case_db; first = copy.deepcopy(CASES[0]); insert(conn,first['report'])
    cases=[first,case]; by_tx={c['report']['tx_hash']:c for c in cases}
    by_block={c['evidence']['receipt']['blockHash']:c['evidence']['accountant'] for c in cases}
    monkeypatch.setattr(fees,'web3_for',lambda chain:SimpleNamespace(eth=SimpleNamespace(
        call=lambda tx,block_identifier:bytes.fromhex('00'*12+by_block[block_identifier][2:]))))
    fetch=lambda chain,tx:by_tx[tx]['evidence']['receipt']
    assert fees.index_canonical_fees(conn,receipt_fetcher=fetch)==2
    conn.execute("UPDATE canonical_fee_reports SET method_version='legacy'");conn.commit()
    kwargs=dict(scope)
    if kwargs.pop('target_only',False):kwargs.update(report_keys=[(1,case['report']['tx_hash'],case['report']['log_index'])],version='v3',limit=1)
    assert fees.index_canonical_fees(conn,receipt_fetcher=fetch,**kwargs)==expected
    upgraded=[r[0] for r in conn.execute('SELECT tx_hash FROM canonical_fee_reports WHERE method_version=?',(fees.METHOD_VERSION,))]
    assert len(upgraded)==expected
    if expected: assert upgraded==[case['report']['tx_hash'] if scope.get('target_only') else first['report']['tx_hash']]


@pytest.mark.parametrize('priced', [False,True])
def test_distribution_fields_reach_pairing_api(case_db, tmp_path, priced):
    from yearn_data.analysis import create_analysis_run,write_output,complete_analysis_run
    from yearn_data.pairing import select_pairing,PairingStore,pairing_response
    conn,case=case_db;assert index(conn,case)==1
    report=case['report'];day=valuation.pricing.normalize_yearn_price_timestamp(report['block_timestamp'])
    if priced:valuation.save_price(conn,(1,report['asset'].lower(),day),('2','ok',{'provider':'yearn-prices','normalized_timestamp':day}))
    fee_run=valuation.run_fee_usd(conn,before_timestamp=day+1)
    earnings=create_analysis_run(conn,'lifetime-yield',{'price_source':'yearn-prices','before_timestamp':day+1})
    row={k:report[k] for k in ('chain_id','vault_address','asset','tx_hash','log_index','block_timestamp','version')}
    row.update(price_usd='2' if priced else None,gross_gain_usd=None,loss_usd=None,net_yield_usd=None,
               raw_gross_gain_usd=None,raw_loss_usd=None,raw_net_yield_usd=None,valuation_status='priced' if priced else 'missing_price')
    write_output(conn,earnings,'reports',row);complete_analysis_run(conn,earnings)
    database='staging';root=tmp_path/'publication'
    select_pairing(database,earnings,fee_run,root)
    status,body=pairing_response(PairingStore(root),'/api/fees')
    assert status==200 and body['totalFeesPaidUsd']=='0'
    assert body['lockerBonusUsd']==body['reportedTotalFeesUsd']==('50.071976' if priced else None)


def test_missing_distribution_metadata_does_not_become_complete_zero(case_db):
    conn,case=case_db;assert index(conn,case)==1
    conn.execute('UPDATE strategy_reports SET asset_decimals=NULL');conn.commit()
    day=valuation.pricing.normalize_yearn_price_timestamp(case['report']['block_timestamp'])
    run=valuation.run_fee_usd(conn,before_timestamp=day+1)
    event=rows(conn,run,'fee_usd_events')[0]
    assert event['total_fees_paid_usd']=='0' and event['locker_bonus_usd'] is None
    assert event['valuation_status']=='metadata_unavailable'
    total=rows(conn,run,'fee_usd_by_family')[0]
    assert total['metadata_unavailable_events']==total['unpriced_distribution_events']==1
    assert not total['fee_usd_complete'] and not total['pricing_complete_for_accepted_fees']
