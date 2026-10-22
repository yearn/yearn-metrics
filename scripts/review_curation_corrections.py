"""Build a separate single-close review publication from saved, dated evidence.

No production writes or price substitutions. Additional V2 evidence must be
collected at the same block as its selected parent snapshot.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from yearn_data.tvl_review import ReviewDataset


def review(frame_path, positions_path, hyper_path, out):
    out=Path(out)
    if out.exists():
        raise ValueError('review output already exists; choose a new output directory')
    inputs=[Path(frame_path),Path(positions_path),Path(hyper_path)]
    frame, additions, hyper=[json.loads(p.read_text()) for p in inputs]
    timestamp=frame['timestamp']
    rows={(r['chain_id'],r['vault'].lower()):r for r in frame['rows']}
    original_rows=list(rows.values())
    identity=lambda p:(p['chain_id'],p['parent'],p['strategy'],p['child'])
    positions={identity(p):p for p in frame['positions']}
    for item in additions:
        p=item['position']
        parent=rows[(p['chain_id'],p['parent'])]
        if p['timestamp']!=timestamp or p['block_number']!=parent['block_number'] or p['status']!='ok':
            raise ValueError('additional position is not verified at the selected close')
        positions[identity(p)]=p
        fallback=(p['chain_id'],p['parent'],p['strategy'],p['strategy'])
        if fallback!=identity(p):
            positions.pop(fallback,None)
    if hyper['snapshot']['timestamp']!=timestamp or hyper['position']['timestamp']!=timestamp:
        raise ValueError('HyperEVM evidence must use the selected close')
    rows[(999,hyper['snapshot']['vault'])]=hyper['snapshot']
    positions[identity(hyper['position'])]=hyper['position']
    out.mkdir(parents=True,exist_ok=True)
    from yearn_data.tvl_policy import VAULT_POLICIES, TARGET_CORRECTIONS
    from yearn_data.tvl_api import BRIDGE_MIGRATIONS, PRICE_REJECTIONS
    manifest={'database':'neon','runs':[{'id':1}], 'catalog':frame['catalog']+[hyper['catalog']],
              'deductInternalCuration':frame.get('deductInternalCuration',True),
              'allocatorKeys':frame.get('allocatorKeys',[]),'currentBridgePolicy':frame.get('currentBridgePolicy','none'),
              'bridgeMigrations':frame.get('bridgeMigrations',BRIDGE_MIGRATIONS),
              'priceRejections':frame.get('priceRejections',PRICE_REJECTIONS),
              'vaultPolicies':frame.get('vaultPolicies',VAULT_POLICIES),
              'targetCorrections':frame.get('targetCorrections',TARGET_CORRECTIONS)}
    dataset=ReviewDataset(manifest,timestamp,list(rows.values()),list(positions.values()))
    dataset.save(out)
    _,corrected,edges,diagnostics=dataset.frames((timestamp,))[0]
    def totals(points):
        result={}
        for r in points:
            if r['external_tvl_usd'] is not None:
                c=r.get('category',r['version'])
                result[c]=result.get(c,Decimal(0))+Decimal(r['external_tvl_usd'])
        return {c:str(v) for c,v in result.items()}
    before,after=totals(original_rows),totals(corrected)
    changed=[]
    new={(r['chain_id'],r['vault']):r for r in corrected}
    for old in original_rows:
        r=new.get((old['chain_id'],old['vault']))
        if r is None or r['external_tvl_usd']!=old['external_tvl_usd']:
            changed.append({'chainId':old['chain_id'],'vault':old['vault'],'name':old.get('name'),
                'before':old['external_tvl_usd'],'after':r['external_tvl_usd'] if r else None,
                'excluded':r is None})
    result={'timestamp':timestamp,'reviewDatasetId':dataset.id,'before':before,'after':after,
        'changes':changed,'diagnostics':diagnostics,'additionalV2Positions':[i['position'] for i in additions],
        'inputSha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        'scope':'Single-close local review; missing prices remain unavailable; no active publication changed'}
    (out/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'graph.json').write_text(json.dumps(dataset.graph(),indent=2)+'\n')
    (out/'summary.json').write_text(json.dumps(dataset.summary(),indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frame',required=True)
    parser.add_argument('--positions',required=True)
    parser.add_argument('--hyper',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    result=review(args.frame,args.positions,args.hyper,args.out)
    print(json.dumps({k:result[k] for k in ('reviewDatasetId','before','after')},indent=2))
