"""Offline formula and acceptance recomputation from retained fee evidence."""
import json

from .fee_acceptance import assess_candidate, normalize_candidate, POLICY_VERSION
from .fee_reconstruction import reconstruct_candidate
from .fees import _amounts, _save, normalize_allocator_fees, report_inputs, allocator_inputs
from .config import CHAINS


def recompute_canonical_fees(conn, mode, chains=None, limit=None, version=None, report_keys=None):
    """Never fetch evidence. Missing/changed inputs remain explicit and retryable."""
    if mode not in ('policy','formulas'):
        raise ValueError('mode must be policy or formulas')
    if limit is not None and limit<=0:
        raise ValueError('limit must be positive')
    if version not in (None,'v2','v3'):
        raise ValueError('version must be v2 or v3')
    params=[];scope=''
    if chains:
        ids=[CHAINS[c].chain_id for c in chains]
        scope=' AND r.chain_id IN ('+','.join('?' for _ in ids)+')';params.extend(ids)
    if version is not None:
        scope+=' AND r.version=?';params.append(version)
    if report_keys is not None:
        if not report_keys:
            return {'processed':0,'accepted':0,'conditional':0,'rejected':0,'unavailable':0}
        scope += ' AND (r.chain_id,r.tx_hash,r.log_index) IN (VALUES '+','.join('(?,?,?)' for _ in report_keys)+')'
        params.extend(value for key in report_keys for value in key)
    sql='''SELECT r.*,v.api_version,f.event_log_index,f.evidence_json,f.candidate_json FROM canonical_fee_reports f
        JOIN strategy_reports r ON r.chain_id=f.chain_id AND r.tx_hash=f.tx_hash AND r.log_index=f.report_log_index
        LEFT JOIN vaults v ON v.chain_id=r.chain_id AND v.address=r.vault_address
        WHERE (f.candidate_json IS NOT NULL OR json_extract(f.evidence_json,'$.source')='filtered_v2_logs' OR
            (r.version='v3' AND json_extract(f.evidence_json,'$.source')='strategy_reports'))'''+scope+' ORDER BY r.chain_id,r.block_number,r.log_index'
    if limit is not None:sql+=' LIMIT ?';params.append(limit)
    counts={'processed':0,'accepted':0,'conditional':0,'rejected':0,'unavailable':0}
    for r in conn.execute(sql,params).fetchall():
        evidence=json.loads(r['evidence_json'])
        if r['version'] == 'v3' and evidence.get('source') == 'strategy_reports':
            failure_reason = 'cached_allocator_inputs_missing_or_changed'
            try:
                if evidence.get('allocator_inputs') != allocator_inputs(r):
                    raise ValueError('allocator inputs missing or changed')
                from .yvusd_fees import ACCOUNTANTS, applies, accounting
                from .ybold_fees import ACCOUNTANT as YBOLD_ACCOUNTANT, applies as is_ybold, accounting as ybold_accounting
                from .yeth_fees import ACCOUNTANT as YETH_ACCOUNTANT, applies as is_yeth, accounting as yeth_accounting
                if applies(dict(r)) or is_ybold(dict(r)) or is_yeth(dict(r)):
                    failure_reason = 'cached_distribution_evidence_missing_or_invalid'
                if applies(dict(r)) and evidence['yvusd']['accountant'].lower() not in ACCOUNTANTS:
                    failure_reason = 'rejected_yvusd_accountant'
                if is_ybold(dict(r)) and evidence['ybold']['accountant'].lower() != YBOLD_ACCOUNTANT:
                    failure_reason = 'rejected_ybold_accountant'
                if is_yeth(dict(r)) and evidence['yeth']['accountant'].lower() != YETH_ACCOUNTANT:
                    failure_reason = 'rejected_yeth_accountant'
                amounts = (yeth_accounting(r, evidence['yeth']) if is_yeth(dict(r)) else
                           ybold_accounting(r, evidence['ybold']) if is_ybold(dict(r)) else
                           accounting(r, evidence['yvusd']) if applies(dict(r)) else normalize_allocator_fees(r))
                reason = None
            except (ValueError, KeyError, TypeError):
                amounts, reason = None, failure_reason
            _save(conn, r, amounts, reason, evidence, r['log_index'])
            counts['processed'] += 1
            counts['accepted' if amounts is not None else 'unavailable'] += 1
            continue
        if evidence.get('source') == 'filtered_v2_logs':
            from .fee_log_evidence import load
            try:
                value = load(conn, r)
                if value is None:
                    raise ValueError('filtered log evidence missing')
                amounts, event_index, evidence = value
                reason = None
            except (ValueError, KeyError, TypeError):
                amounts, event_index, reason = None, r['event_log_index'], 'invalid_filtered_log_evidence'
            _save(conn, r, amounts, reason, evidence, event_index)
            counts['processed'] += 1
            counts['accepted' if amounts is not None else 'unavailable'] += 1
            continue
        candidate=json.loads(r['candidate_json']);amounts=None
        try:
            snapshot=evidence.get('report_inputs')
            if snapshot is not None and snapshot!=report_inputs(r):
                raise ValueError('source report changed')
            if candidate['method']=='execution-fee-mint':
                from .fee_trace import verify_cached_execution
                verification=verify_cached_execution(conn,r,evidence)
                evidence['execution_verification']=verification
                candidate={**candidate,'total_fees_paid_raw':verification['fee_operand_raw'],
                           'calculation_version':verification['trace_version']}
                if mode=='formulas' and 'direct_execution_asset' not in evidence:
                    baseline=normalize_candidate(reconstruct_candidate(evidence['fee_shares'],evidence['historical_state']))
                    candidate={**candidate,'baseline_candidate':baseline}
            else:
                if candidate['method']=='receipt-no-mint-zero':
                    from .fee_trace import verify_cached_no_mint
                    verify_cached_no_mint(conn,r,evidence)
                if mode=='formulas':
                    if snapshot is None:
                        raise ValueError('legacy source binding unavailable')
                    candidate=normalize_candidate(reconstruct_candidate(evidence['fee_shares'],evidence['historical_state']))
            decision=assess_candidate(candidate,evidence)
            if decision['status']=='accepted':
                amounts=_amounts(r['gain_raw'],r['loss_raw'],candidate['total_fees_paid_raw'])
                amounts.update(amount_source='derived-contract',component_coverage='unavailable')
        except (ValueError,KeyError,TypeError):
            decision={'policy_version':POLICY_VERSION,'status':'unavailable','reason':'cached_fee_inputs_missing_or_changed',
                      'state_scope':None,'state_suitability':'unverified'}
        _save(conn,r,amounts,decision['reason'],evidence,r['event_log_index'],candidate,decision)
        counts['processed']+=1;counts[decision['status']]+=1
    conn.commit()
    return counts
