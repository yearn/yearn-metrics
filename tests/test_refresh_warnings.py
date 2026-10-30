"""Warnings must expose partial evidence without converting missing values to zero."""
import json

from yearn_data.refresh_warnings import findings,write_report


def test_missing_prices_retained_evidence_and_history_gaps_are_reported(tmp_path):
    result=dict(cutoff=172800,gaps=dict(reportPrices={'ok':10,'missing':2},
        feePrices={'ok':4},unpriceableFeeEvents=1,unresolvedTvlChainDates=[{'chain':250,'day':86399}]),
        analyticsSources={'membership':dict(availability='available',retainedRows=1,observedAt=1)},
        history={'unavailableHistoryDates':[dict(timestamp=86399,unavailableRows=3)]})
    warnings=findings([],result)
    assert {w['stage'] for w in warnings}=={'reportPrices','unpriceableFeeEvents',
        'unresolvedTvlChainDates','membership','history'}
    assert next(w for w in warnings if w['stage']=='reportPrices')['details']=={'missing':2}
    assert next(w for w in warnings if w['stage']=='history')['details']['unavailableRows']==3
    # Provider-derived labels cannot inject HTML into the standalone report.
    warnings[0]['details']['label']='<script>alert(1)</script>'
    write_report(tmp_path,'job',result|dict(warnings=warnings,publishReady=True))
    assert json.loads((tmp_path/'warnings.json').read_text())['warnings']==warnings
    rendered=(tmp_path/'warnings.html').read_text()
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
