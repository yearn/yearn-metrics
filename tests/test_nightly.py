"""Scheduler recovery contracts at the worker/collector/HTTP boundaries; no DB."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from yearn_data import nightly

EXTRA_TVL = nightly.extra_tvl
VERIFY_API = nightly.verify_api


@pytest.fixture
def setup(monkeypatch, tmp_path):
    settings = nightly.Settings('neon', ('eth',), (100,), {}, 'baseline',
                               'https://example.test', tmp_path / 'publication', tmp_path / 'state',
                               max_invocations=4, retry_seconds=0)
    def receipt(identity='baseline', cutoff=86400, status='selected'):
        return dict(jobId=identity, status=status, context=dict(database='neon', chains=['eth'], cutoff=cutoff),
                    result=dict(publishReady=True, consolidation='complete', releaseId='release-' + identity,
                                feeDatasetId='fees', tvlDatasetId='tvl', analyticsPublicationId='analytics'))
    receipts = {'baseline': receipt()}
    calls = []
    monkeypatch.setattr(nightly, 'read_receipt', lambda db, identity: receipts[identity])
    monkeypatch.setattr(nightly, 'Registry', lambda db: SimpleNamespace(
        selection=lambda: dict(releaseId=receipts[list(receipts)[-1]]['result']['releaseId'])))
    def extra(config, pending, persist):
        calls.append(('extra', pending['start'], pending['cutoff']))
        pending['phase'] = 'refresh'
        persist()
        return True
    monkeypatch.setattr(nightly, 'extra_tvl', extra)
    def worker(options, *, mode, resume, progress):
        identity = resume or 'job-' + str(options.cutoff)
        calls.append(('worker', options.cutoff, options.baseline_job, resume, mode))
        progress(dict(event='stage-started', jobId=identity))
        receipts[identity] = receipt(identity, options.cutoff)
        return receipts[identity]
    monkeypatch.setattr(nightly, 'run_refresh', worker)
    monkeypatch.setattr(nightly, 'verify_api', lambda config, rec: dict(status='verified'))
    return settings, receipts, calls, receipt


def test_cutoff_and_missed_days_continue_from_accepted_baseline(setup):
    settings, _, calls, _ = setup
    assert nightly.cycle(settings, now=4 * 86400 + 7200) == 0
    assert calls == [('extra', 86400, 345600), ('worker', 345600, 'baseline', None, 'publish')]
    state = json.loads((settings.state_dir / 'state.json').read_text())
    assert state['pending'] is None
    assert state['lastJob'] == 'job-345600'
    assert nightly.cycle(settings, now=345601) == 0
    assert len(calls) == 2


def test_budget_and_cleanup_resumes_keep_original_cutoff_and_job(setup, monkeypatch):
    settings, receipts, calls, receipt = setup
    original = nightly.run_refresh
    statuses = iter(['work-deferred', 'selected-cleanup-failed', 'selected'])
    def worker(*args, **kwargs):
        result = original(*args, **kwargs)
        result['status'] = next(statuses)
        return result
    monkeypatch.setattr(nightly, 'run_refresh', worker)
    assert nightly.cycle(replace(settings, max_invocations=1), now=172801) == 3
    # Restart on the following day: recover yesterday, then catch up to today.
    assert nightly.cycle(replace(settings, max_invocations=2), now=259201) == 3
    assert calls[1:] == [('worker', 172800, 'baseline', None, 'publish'),
                         ('worker', 172800, 'baseline', 'job-172800', 'publish'),
                         ('worker', 172800, 'baseline', 'job-172800', 'publish')]
    monkeypatch.setattr(nightly, 'run_refresh', original)
    assert nightly.cycle(settings, now=259201) == 0
    assert calls[-1] == ('worker', 259200, 'job-172800', None, 'publish')
    assert receipts['job-259200'] == receipt('job-259200', 259200)


@pytest.mark.parametrize('error,reason', [
    (nightly.requests.ConnectionError('secret provider details'), 'http-request-failed'),
    (nightly.VerificationError('selection-mismatch'), 'selection-mismatch'),
    (nightly.requests.exceptions.JSONDecodeError('secret payload', 'x', 0), 'invalid-response'),
])
def test_verification_failure_is_diagnosable_without_secrets_or_republication(setup, monkeypatch, capsys, error, reason):
    settings, _, calls, _ = setup
    def unavailable(*args):
        raise error
    monkeypatch.setattr(nightly, 'verify_api', unavailable)
    assert nightly.cycle(replace(settings, max_invocations=1), now=172801) == 3
    saved = (settings.state_dir / 'state.json').read_text()
    pending = json.loads(saved)['pending']
    output = capsys.readouterr().err
    assert 'secret' not in saved + output
    assert pending['phase'] == 'verify'
    assert pending['verification']['reason'] == reason
    assert pending['verification']['failureClass'] == type(error).__name__
    events = [json.loads(line) for line in output.splitlines()]
    assert events[-1]['event'] == 'hosted-verification-failed'
    assert events[-1]['reason'] == reason
    monkeypatch.setattr(nightly, 'verify_api', lambda *args: dict(status='verified'))
    assert nightly.cycle(settings, now=172801) == 0
    assert len(calls) == 2


def test_interrupted_worker_recovers_saved_job_and_scope_changes_stop(setup, monkeypatch):
    settings, _, calls, _ = setup
    original = nightly.run_refresh
    def interrupted(*args, **kwargs):
        kwargs['progress'](dict(jobId='interrupted', event='stage-started'))
        raise KeyboardInterrupt
    monkeypatch.setattr(nightly, 'run_refresh', interrupted)
    with pytest.raises(KeyboardInterrupt):
        nightly.cycle(settings, now=172801)
    monkeypatch.setattr(nightly, 'run_refresh', original)
    with pytest.raises(ValueError, match='scope changed'):
        nightly.cycle(replace(settings, confirmations={'eth': 64}), now=172801)
    assert nightly.cycle(settings, now=172801) == 0
    assert calls[-1][3] == 'interrupted'


def test_missing_job_id_never_starts_a_replacement_job(setup, monkeypatch):
    settings, _, calls, _ = setup
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(nightly, 'run_refresh', interrupted)
    with pytest.raises(KeyboardInterrupt):
        nightly.cycle(settings, now=172801)
    with pytest.raises(ValueError, match='before job ID'):
        nightly.cycle(settings, now=172801)
    assert calls == [('extra', 86400, 172800)]


def test_changed_native_checkpoint_keeps_job_id_for_operator_recovery(setup, monkeypatch):
    settings, _, _, _ = setup
    original = nightly.run_refresh
    def deferred(*args, **kwargs):
        result = original(*args, **kwargs)
        result['status'] = 'work-deferred'
        return result
    monkeypatch.setattr(nightly, 'run_refresh', deferred)
    assert nightly.cycle(replace(settings, max_invocations=1), now=172801) == 3
    def reject(options, *, resume, **kwargs):
        assert resume == 'job-172800'
        raise ValueError('refresh code or configuration changed; start a new job')
    monkeypatch.setattr(nightly, 'run_refresh', reject)
    with pytest.raises(ValueError, match='configuration changed'):
        nightly.cycle(settings, now=259201)
    pending = json.loads((settings.state_dir / 'state.json').read_text())['pending']
    assert pending['jobId'] == 'job-172800'
    assert pending['cutoff'] == 172800


def test_wrong_or_incomplete_baseline_and_overlap_fail_before_acquisition(setup, monkeypatch):
    settings, receipts, calls, _ = setup
    receipts['baseline']['status'] = 'prepared'
    with pytest.raises(ValueError, match='completed selected'):
        nightly.cycle(settings, now=172801)
    receipts['baseline']['status'] = 'selected'
    monkeypatch.setattr(nightly, 'Registry', lambda db: SimpleNamespace(selection=lambda: dict(releaseId='manual-release')))
    with pytest.raises(ValueError, match='current selection'):
        nightly.cycle(settings, now=172801)
    with nightly.local_lock(settings.state_dir):
        with pytest.raises(nightly.RefreshBusy):
            nightly.cycle(settings, now=172801)
    assert calls == []


def test_extra_tvl_uses_native_missing_close_plan_and_releases_writer_lock(setup, monkeypatch):
    settings, _, _, _ = setup
    class Connection:
        def execute(self, query, args):
            locks.append(query)
            return SimpleNamespace(fetchone=lambda: [True])
        def close(self):
            pass
    locks = []; collected = []; discovery = []
    monkeypatch.setattr(nightly, 'connect', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(nightly.tvl_discovery, 'DiscoveryReader', lambda chain: SimpleNamespace(block_at=lambda ts: ts // 10))
    def discover(conn, **kwargs):
        discovery.append(kwargs)
        return dict(status='incomplete', run_id='catalog')
    monkeypatch.setattr(nightly.tvl_discovery, 'discover_catalog', discover)
    plans = iter([dict(targets=[(100, 172799)]), dict(targets=[], unresolvedTargets=[], deferredRepairs=['missing-price'])])
    monkeypatch.setattr(nightly, 'tvl_plan', lambda *args: next(plans))
    monkeypatch.setattr(nightly.tvl_batch, 'reader', lambda *args: object())
    monkeypatch.setattr(nightly.tvl_sources, 'collect_tvl', lambda *args, **kwargs: collected.append((args[1:3], kwargs['chain_ids'])))
    pending = dict(start=86400, cutoff=172800, phase='extra')
    assert EXTRA_TVL(settings, pending, lambda: None)
    assert collected == [((172799, 172799), [100])]
    assert discovery[0]['from_block'] == 8640
    assert discovery[0]['to_block'] == 17279
    assert pending['extraCatalogs']['100']['status'] == 'incomplete'
    assert pending['extraTvl']['deferredRepairs'] == ['missing-price']
    assert 'pg_advisory_unlock' in locks[-1]


@pytest.mark.parametrize('failure', ['release', 'analytics-cutoff', 'tvl-close', 'fees', 'invalid-response', None])
def test_http_verification_checks_consumer_ids_and_closed_cutoff(setup, monkeypatch, failure):
    settings, _, _, receipt = setup
    rec = receipt('new', 172800)
    publication = dict(releaseId='release-new', feesDatasetId='fees', tvlDatasetId='tvl', analyticsPublicationId='analytics')
    bodies = [publication, dict(publicationId='analytics', feesDatasetId='fees', tvlDatasetId='tvl', cutoffTimestamp=172800),
              dict(datasetId='tvl', asOfTimestamp=172799), dict(datasetId='fees'), publication]
    if failure == 'release': bodies[0] = {}
    if failure == 'analytics-cutoff': bodies[1]['cutoffTimestamp'] = 86400
    if failure == 'tvl-close': bodies[2]['asOfTimestamp'] = 86399
    if failure == 'fees': bodies[3]['datasetId'] = 'old'
    if failure == 'invalid-response': bodies[1] = []
    responses = iter(bodies)
    monkeypatch.setattr(nightly.requests, 'get', lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: next(responses)))
    if failure:
        with pytest.raises(ValueError): VERIFY_API(settings, rec)
    else:
        assert VERIFY_API(settings, rec)['status'] == 'verified'


def test_shipped_configuration_and_check_have_no_network_database_or_writes(monkeypatch, tmp_path, capsys):
    root = Path(__file__).parents[1]
    def unexpected(*args, **kwargs):
        pytest.fail('configuration check crossed a write or external boundary')
    monkeypatch.setattr(nightly, 'cycle', unexpected)
    monkeypatch.setattr(nightly, 'connect', unexpected)
    monkeypatch.setattr(nightly.requests, 'get', unexpected)
    env = tmp_path / 'env'; env.write_text('')
    assert nightly.main(['--config', str(root / 'ops/nightly-refresh.json'), '--env', str(env), '--check']) == 0
    body = json.loads(capsys.readouterr().out)
    assert body['scope']['chains'] == ['arb', 'base', 'eth', 'ftm', 'kat', 'op', 'polygon']
    assert body['scope']['extraTvlChains'] == [100, 999, 4663]
    assert body['scope']['confirmations'] == {'ftm': 64}


@pytest.mark.parametrize('failure', ['setup-outage', 'late-finality', 'range-outage', 'collection-outage', 'all-unavailable'])
def test_unavailable_extra_chain_preserves_gap_and_allows_other_chains_and_publication(setup, monkeypatch, failure):
    settings, _, calls, _ = setup
    settings = replace(settings, extra_tvl_chains=(100, 999))
    collected = []; locks = []
    class Connection:
        def execute(self, query, params):
            locks.append(query)
            return SimpleNamespace(fetchone=lambda: [True])
        def close(self):
            pass
        def rollback(self):
            pass
    monkeypatch.setattr(nightly, 'connect', lambda *args, **kwargs: Connection())
    def discovery_reader(chain):
        if (chain == 100 and failure == 'setup-outage') or failure == 'all-unavailable':
            raise nightly.requests.ConnectionError('https://secret@provider.invalid')
        def block_at(timestamp):
            if chain == 100 and failure == 'range-outage':
                raise nightly.requests.Timeout('https://secret@provider.invalid')
            if chain == 100 and failure == 'late-finality' and timestamp == 172799:
                raise ValueError('requested timestamp is newer than finalized chain state')
            return timestamp // 10
        return SimpleNamespace(block_at=block_at)
    monkeypatch.setattr(nightly.tvl_discovery, 'DiscoveryReader', discovery_reader)
    monkeypatch.setattr(nightly.tvl_discovery, 'discover_catalog', lambda *args, **kwargs: dict(status='complete', run_id='catalog'))
    def batch_reader(conn, chain):
        if chain == 100:
            raise nightly.requests.ConnectionError('https://secret@provider.invalid')
        return object()
    monkeypatch.setattr(nightly.tvl_batch, 'reader', batch_reader)
    def plan(conn, options):
        assert options.chain_ids == ((100, 999) if failure == 'collection-outage' else (999,))
        return dict(targets=[(c, 172799) for c in options.chain_ids],
                    unresolvedTargets=[(100, 172799)] if 100 in options.chain_ids else [], deferredRepairs=[])
    monkeypatch.setattr(nightly, 'tvl_plan', plan)
    monkeypatch.setattr(nightly.tvl_sources, 'collect_tvl', lambda *args, **kwargs: collected.extend(kwargs['chain_ids']))
    monkeypatch.setattr(nightly, 'extra_tvl', EXTRA_TVL)
    assert nightly.cycle(settings, now=172801) == 0
    assert calls == [('worker', 172800, 'baseline', None, 'publish')]
    assert collected == ([] if failure == 'all-unavailable' else [999])
    evidence = json.loads((settings.state_dir / 'job-172800-verification.json').read_text())
    gap = evidence['extraTvl']['unavailableChains']['100']
    assert gap['reason'] == ('finalized-head-behind-cutoff' if failure == 'late-finality' else 'rpc-transport-unavailable')
    assert 'secret' not in json.dumps(evidence)
    if failure == 'all-unavailable':
        assert set(evidence['extraTvl']['unavailableChains']) == {'100', '999'}
    assert 'pg_advisory_unlock' in locks[-1]


@pytest.mark.parametrize('stage', ['discovery', 'collection'])
@pytest.mark.parametrize('failure', ['wrong-chain', 'http-auth', 'tls-config', 'price-auth', 'price-config'])
def test_extra_chain_integrity_and_configuration_errors_still_block_publication(setup, monkeypatch, stage, failure):
    from yearn_data.pricing_clients import YearnPricesAuthenticationError, YearnPricesConfigurationError
    settings, _, calls, _ = setup
    response = nightly.requests.Response(); response.status_code = 401
    error = {'wrong-chain': ValueError('RPC chain mismatch'),
             'http-auth': nightly.requests.HTTPError('secret', response=response),
             'tls-config': nightly.requests.exceptions.SSLError('secret'),
             'price-auth': YearnPricesAuthenticationError('secret'),
             'price-config': YearnPricesConfigurationError('secret')}[failure]
    locks = []
    connection = SimpleNamespace(execute=lambda query, args: (locks.append(query) or SimpleNamespace(fetchone=lambda: [True])), close=lambda: None)
    monkeypatch.setattr(nightly, 'connect', lambda *args, **kwargs: connection)
    def fail(*args):
        raise error
    monkeypatch.setattr(nightly.tvl_discovery, 'DiscoveryReader', fail if stage == 'discovery' else lambda chain: SimpleNamespace(block_at=lambda timestamp: timestamp // 10))
    monkeypatch.setattr(nightly.tvl_discovery, 'discover_catalog', lambda *args, **kwargs: dict(status='complete', run_id='catalog'))
    monkeypatch.setattr(nightly.tvl_batch, 'reader', fail)
    monkeypatch.setattr(nightly, 'tvl_plan', lambda *args: dict(targets=[(100, 172799)], unresolvedTargets=[], deferredRepairs=[]))
    monkeypatch.setattr(nightly, 'extra_tvl', EXTRA_TVL)
    with pytest.raises(type(error)):
        nightly.cycle(settings, now=172801)
    assert calls == []
    assert 'pg_advisory_unlock' in locks[-1]


def test_http_diagnostic_keeps_status_without_url_or_response_body():
    response = nightly.requests.Response()
    response.status_code = 503
    response.url = 'https://secret@provider.invalid'
    response._content = b'secret upstream response'
    result = nightly.verification_failure(nightly.requests.HTTPError('secret', response=response))
    assert result['httpStatus'] == 503
    assert result['reason'] == 'http-request-failed'
    assert 'secret' not in json.dumps(result)


def test_extra_chain_warning_survives_budget_resume(setup, monkeypatch):
    settings, _, _, _ = setup
    settings = replace(settings, extra_tvl_chains=(100, 999))
    connection = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: [True]),
                                 close=lambda: None, rollback=lambda: None)
    monkeypatch.setattr(nightly, 'connect', lambda *args, **kwargs: connection)
    monkeypatch.setattr(nightly.tvl_discovery, 'DiscoveryReader', lambda chain: SimpleNamespace(block_at=lambda timestamp: timestamp // 10))
    monkeypatch.setattr(nightly.tvl_discovery, 'discover_catalog', lambda *args, **kwargs: dict(status='complete', run_id='catalog'))
    def reader(conn, chain):
        if chain == 100:
            raise nightly.requests.ConnectionError('secret')
        return object()
    monkeypatch.setattr(nightly.tvl_batch, 'reader', reader)
    monkeypatch.setattr(nightly.tvl_sources, 'collect_tvl', lambda *args, **kwargs: None)
    # One failed chain plus 30 known missing closes on the other chain.
    dates = [(999, day * 86400 - 1) for day in range(1, 31)]
    plans = iter([
        ((100, 999), [(100, 86399), *dates]),
        ((100, 999), [(100, 86399), dates[-1]]),
        ((999,), [dates[-1]]),
        ((999,), []),
    ])
    def plan(conn, options):
        expected, targets = next(plans)
        assert options.chain_ids == expected
        return dict(targets=targets, unresolvedTargets=targets, deferredRepairs=[])
    monkeypatch.setattr(nightly, 'tvl_plan', plan)
    pending = dict(start=0, cutoff=31 * 86400, phase='extra')
    assert not EXTRA_TVL(settings, pending, lambda: None)
    assert EXTRA_TVL(settings, pending, lambda: None)
    assert pending['phase'] == 'refresh'
    assert pending['extraTvl']['unavailableChains']['100']['reason'] == 'rpc-transport-unavailable'


@pytest.mark.parametrize('changes', [
    {'initial_baseline_job': ''}, {'max_invocations': 0}, {'max_invocations': 1.5},
    {'retry_seconds': -1}, {'retry_seconds': True},
    {'api_origin': 'http://example.test'}, {'api_origin': 'https://example.test/path'},
    {'api_origin': 'https://example.test?query=1'}, {'api_origin': 'https://example.test#fragment'},
    {'api_origin': 'https://:secret@example.test'}, {'api_origin': 'https://@example.test'},
    {'api_origin': 'https://user:secret@example.test'},
    {'extra_tvl_chains': [100, 100]}, {'extra_tvl_chains': [0]},
    {'extra_tvl_chains': [True]}, {'extra_tvl_chains': [1]},
])
def test_invalid_operator_configuration_is_rejected_before_external_effects(tmp_path, monkeypatch, changes):
    body = json.loads((Path(__file__).parents[1] / 'ops/nightly-refresh.json').read_text())
    body.update(changes)
    config = tmp_path / 'config.json'; config.write_text(json.dumps(body))
    env = tmp_path / 'env'; env.write_text('')
    monkeypatch.setattr(nightly, 'cycle', lambda *args: pytest.fail('invalid configuration reached execution'))
    with pytest.raises(ValueError):
        nightly.main(['--config', str(config), '--env', str(env), '--check'])
