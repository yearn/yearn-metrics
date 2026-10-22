"""Dated curation review survives backend retirement without writing a database."""
import json
from pathlib import Path
import runpy

import pytest

from test_tvl_api import source, run, DAY, PARENT, CHILD
from yearn_data.tvl_api import TvlStore
from yearn_data.tvl_review import ReviewDataset


@pytest.mark.parametrize('category,policy', [('v3',True),('curation',True),('curation',False),('curation',None)])
def test_curation_review_preserves_source_and_reopens_json(source,tmp_path,category,policy):
    conn,database,publication=source
    run(conn,1,DAY,[(PARENT,'100'),(CHILD,'100')])
    dataset=TvlStore(database,publication).get()
    _,rows,positions,_=dataset.frames((DAY,))[0]
    for row in rows:row['category']=category
    frame={'timestamp':DAY,'rows':rows,'positions':positions,**dataset.manifest}
    if policy is None:frame.pop('deductInternalCuration')
    else:frame['deductInternalCuration']=policy
    added={'chain_id':1,'parent':PARENT,'strategy':CHILD,'child':CHILD,'timestamp':DAY,
           'block_number':100+DAY,'method':'direct-strategy','debt_usd':'50','owned_usd':'80','status':'ok'}
    vault='0x'+'77'*20
    hyper={'snapshot':{'chain_id':999,'vault':vault,'timestamp':DAY,'block_number':99,
                       'version':'v3','category':'v3','asset_units':'50','tvl_usd':'50','status':'ok'},
           'catalog':{'chain_id':999,'address':vault,'version':'v3','active':1},
           'position':{'chain_id':999,'parent':vault,'strategy':vault,'child':vault,'timestamp':DAY,
                       'block_number':99,'method':'strategy-allocation','debt_usd':'0','status':'ok'}}
    files=[tmp_path/'source.json',tmp_path/'added.json',tmp_path/'hyper.json']
    for path,body in zip(files,[frame,[{'position':added}],hyper]):path.write_text(json.dumps(body))
    before=[tuple(row) for row in conn.execute('SELECT * FROM tvl_snapshots')]
    selected=(publication/'current.json').read_bytes()
    script=runpy.run_path(str(Path(__file__).parents[1]/'scripts/review_curation_corrections.py'))
    out=tmp_path/'review'
    result=script['review'](*files,out)
    net=category!='curation' or policy is not False
    assert result['after']==({'v3':'200'} if category=='v3' else {'curation':'150' if net else '200','v3':'50'})
    graph=json.loads((out/'graph.json').read_text())
    assert ReviewDataset.load(out/'frame.json').graph()==graph
    assert graph['totalTvlUsd']==(200 if net else 250)
    assert graph['totalOverlapTvlUsd']==(50 if net else 0)
    revised=ReviewDataset.load(out/'frame.json')
    assert revised.manifest['deductInternalCuration']==(policy is not False)
    _,corrected,_,_=revised.frames((DAY,))[0]
    assert {r['vault']:r['external_tvl_usd'] for r in corrected}=={
        PARENT:'100',CHILD:'50' if net else '100',vault:'50'}
    assert [tuple(row) for row in conn.execute('SELECT * FROM tvl_snapshots')]==before
    assert (publication/'current.json').read_bytes()==selected
    assert not list(out.glob('*.sqlite'))
