import copy
import json
from pathlib import Path

import pytest
from yearn_data.yvusd_fees import accounting, FEE_TOPIC
from yearn_data.fees import normalize_allocator_fees
from yearn_data.fee_valuation import usd_amount

CASES = json.loads((Path(__file__).parent/'fixtures/fees/yvusd.json').read_text())

@pytest.mark.parametrize('case,expected', list(zip(CASES, [(78666,624907,624907),(0,0,7462307),(0,0,25035988)])))
def test_historical_fee_splits(case, expected):
    result = accounting(case['report'], case['evidence'])
    management, performance, bonus = expected
    assert result['management_fee_raw'] == str(management)
    assert result['performance_fee_raw'] == str(performance)
    assert result['locker_bonus_raw'] == str(bonus)
    assert result['total_fees_paid_raw'] == str(management + performance)
    assert result['manager_fee_raw'] == str(management + performance)
    assert result['reported_total_fees_raw'] == str(sum(expected))
    assert usd_amount(result['locker_bonus_raw'], 6, '1') == str(bonus / 1000000)

@pytest.mark.parametrize('mutation', ['accountant','transaction','amount','missing','duplicate','protocol','prior_report'])
def test_rejects_unbound_or_unreconciled_splits(mutation):
    case = copy.deepcopy(CASES[-1]); r,e = case['report'],case['evidence']; receipt=e['receipt']
    fee=next(l for l in receipt['logs'] if l['topics'][0]==FEE_TOPIC)
    if mutation=='accountant': e['accountant']='0x'+'1'*40
    if mutation=='transaction': receipt['transactionHash']='0x'+'1'*64
    if mutation=='amount': fee['topics'][3]='0x'+format(1,'064x')
    if mutation=='missing': receipt['logs'].remove(fee)
    if mutation=='duplicate': receipt['logs'].append(copy.deepcopy(fee))
    if mutation=='protocol':
        from eth_abi import decode, encode
        r['protocol_fees_raw']='1'
        log=next(l for l in receipt['logs'] if l['logIndex']==r['log_index'])
        values=list(decode(['uint256']*6,bytes.fromhex(log['data'][2:])))
        values[3]=1;log['data']='0x'+encode(['uint256']*6,values).hex()
    if mutation=='prior_report':
        previous=copy.deepcopy(next(l for l in receipt['logs'] if l['logIndex']==r['log_index']))
        for log in receipt['logs']:
            if log['logIndex']>=r['log_index']:log['logIndex']+=1
        r['log_index']+=1;receipt['logs'].append(previous)
        assert len({l['logIndex'] for l in receipt['logs']})==len(receipt['logs'])
    expected='unreconciled yvUSD fee split' if mutation in ('protocol','prior_report') else None
    with pytest.raises(ValueError, match=expected): accounting(r,e)

def test_target_cannot_silently_fall_back_to_raw_deductions():
    with pytest.raises(ValueError): normalize_allocator_fees(CASES[0]['report'])

def test_index_and_offline_recompute_preserve_split(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from yearn_data.storage import connect, init_db
    from yearn_data.fees import index_canonical_fees
    from yearn_data.fee_recompute import recompute_canonical_fees
    from yearn_data import fees
    case = CASES[0]; r = case['report']; e = case['evidence']
    with connect(tmp_path/'fees.sqlite') as conn:
        init_db(conn)
        conn.execute('INSERT INTO vaults(chain_id,address,version,api_version,updated_at) VALUES (1,?,?,?,1)',
                     (r['vault_address'], 'v3', r['api_version']))
        fields = [row['name'] for row in conn.execute('PRAGMA table_info(strategy_reports)') if row['name'] in r]
        conn.execute('INSERT INTO strategy_reports ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',
                     [r[field] for field in fields])
        calls=[]
        def call(transaction, block_identifier):
            calls.append(block_identifier)
            return bytes.fromhex('00'*12+e['accountant'][2:])
        monkeypatch.setattr(fees, 'web3_for', lambda chain: SimpleNamespace(eth=SimpleNamespace(call=call)))
        assert index_canonical_fees(conn,receipt_fetcher=lambda chain,tx:e['receipt']) == 1
        assert calls == [e['receipt']['blockHash']]
        assert index_canonical_fees(conn) == 0
        before=conn.execute('SELECT accounting_json FROM canonical_fee_reports').fetchone()[0]
        assert json.loads(before)['total_fees_paid_raw']=='703573'
        assert recompute_canonical_fees(conn,'formulas')['accepted']==1
        assert conn.execute('SELECT accounting_json FROM canonical_fee_reports').fetchone()[0]==before

def test_zero_deduction_without_fee_event_is_known_zero():
    from eth_abi import encode
    case=copy.deepcopy(CASES[-1]); r,e=case['report'],case['evidence']
    r['total_fees_raw']='0'
    e['receipt']['logs']=[l for l in e['receipt']['logs'] if l['topics'][0]!=FEE_TOPIC]
    log=next(l for l in e['receipt']['logs'] if l['logIndex']==r['log_index'])
    from eth_abi import decode
    values=list(decode(['uint256']*6,bytes.fromhex(log['data'][2:])))
    values[4]=0;log['data']='0x'+encode(['uint256']*6,values).hex()
    result=accounting(r,e)
    assert result['total_fees_paid_raw']==result['locker_bonus_raw']=='0'


def test_multiple_reports_each_consume_their_own_fee_event():
    case=copy.deepcopy(CASES[-1]); r,e=case['report'],case['evidence']
    fee=copy.deepcopy(next(l for l in e['receipt']['logs'] if l['topics'][0]==FEE_TOPIC))
    report=copy.deepcopy(next(l for l in e['receipt']['logs'] if l['logIndex']==r['log_index']))
    fee['logIndex']-=10;report['logIndex']-=10
    e['receipt']['logs'].extend([fee,report])
    assert accounting(r,e)['locker_bonus_raw']=='25035988'


def test_explicit_refresh_reacquires_rejected_accountant(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from yearn_data.storage import connect, init_db
    from yearn_data import fees
    case = CASES[0]; r = case['report']; e = case['evidence']; calls = []
    def call(transaction, block_identifier):
        calls.append(block_identifier)
        address = '0x'+'11'*20 if len(calls) == 1 else e['accountant']
        return bytes.fromhex('00'*12+address[2:])
    monkeypatch.setattr(fees, 'web3_for', lambda chain: SimpleNamespace(eth=SimpleNamespace(call=call)))
    with connect(tmp_path/'refresh.sqlite') as conn:
        init_db(conn)
        conn.execute('INSERT INTO vaults(chain_id,address,version,api_version,updated_at) VALUES (1,?,?,?,1)',
                     (r['vault_address'], 'v3', r['api_version']))
        fields = [row['name'] for row in conn.execute('PRAGMA table_info(strategy_reports)') if row['name'] in r]
        conn.execute('INSERT INTO strategy_reports ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',
                     [r[field] for field in fields])
        fetch = lambda *args: e['receipt']
        assert fees.index_canonical_fees(conn, receipt_fetcher=fetch) == 1
        assert tuple(conn.execute('SELECT status,reason FROM canonical_fee_reports').fetchone()) == ('unresolved','rejected_yvusd_accountant')
        # Ordinary retries can replay retained evidence without another RPC call.
        assert fees.index_canonical_fees(conn, retry_unresolved=True, receipt_fetcher=fetch) == 1
        assert len(calls) == 1
        assert tuple(conn.execute('SELECT status,reason FROM canonical_fee_reports').fetchone()) == ('unresolved','rejected_yvusd_accountant')
        assert fees.index_canonical_fees(conn, retry_unresolved=True, refresh_evidence=True, receipt_fetcher=fetch) == 1
        assert calls == [e['receipt']['blockHash']] * 2
        result = conn.execute('SELECT status,accounting_json,evidence_json FROM canonical_fee_reports').fetchone()
        assert result['status'] == 'ok'
        assert json.loads(result['accounting_json'])['total_fees_paid_raw'] == '703573'
        assert json.loads(result['evidence_json'])['yvusd']['accountant'] == e['accountant']


def test_normalizer_independently_rejects_protocol_charged_locker_split():
    report=dict(CASES[-1]['report'],protocol_fees_raw='1')
    with pytest.raises(ValueError,match='unreconciled locker distribution'):
        normalize_allocator_fees(report,locker_split=(0,0,int(report['total_fees_raw'])))
