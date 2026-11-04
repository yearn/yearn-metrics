"""VM scheduler adapter around the maintained refresh worker.

Local state owns orchestration only; Postgres refresh receipts own financial work.
No scheduler installation or production work occurs without an explicit --run.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import requests
from web3.exceptions import ProviderConnectionError, TimeExhausted

from .config import load_environment
from .refresh import run_refresh
from .refresh_scope import DAY, RefreshOptions, tvl_plan
from .refresh_state import LOCK, RefreshBusy, read_receipt
from .storage import connect
from .hosted_publications import Registry
from . import tvl_batch, tvl_discovery, tvl_sources


@dataclass(frozen=True)
class Settings:
    database: str
    chains: tuple
    extra_tvl_chains: tuple
    confirmations: dict
    initial_baseline_job: str
    api_origin: str
    publication_root: Path
    state_dir: Path
    max_invocations: int = 12
    retry_seconds: int = 60

    @classmethod
    def read(cls, path):
        path = Path(path).resolve()
        body = json.loads(path.read_text())
        for key in ('publication_root', 'state_dir'):
            body[key] = (path.parent / body[key]).resolve()
        body['chains'] = tuple(body['chains'])
        body['extra_tvl_chains'] = tuple(body['extra_tvl_chains'])
        settings = cls(**body)
        settings.options(DAY, None)  # Validate native scope before any writes.
        if (not settings.initial_baseline_job or type(settings.max_invocations) is not int
                or settings.max_invocations < 1 or type(settings.retry_seconds) is not int
                or settings.retry_seconds < 0):
            raise ValueError('baseline and positive invocation budget are required')
        url = urlsplit(settings.api_origin)
        if url.scheme != 'https' or not url.netloc or url.path not in ('', '/') or url.query or url.fragment or url.username is not None or url.password is not None:
            raise ValueError('api_origin must be an HTTPS origin')
        if (len(set(settings.extra_tvl_chains)) != len(settings.extra_tvl_chains)
                or any(type(c) is not int or c <= 0 for c in settings.extra_tvl_chains)
                or set(settings.extra_tvl_chains) & set(settings.options(DAY, None).chain_ids)):
            raise ValueError('extra TVL chains must be distinct positive IDs outside report scope')
        return settings

    def options(self, cutoff, baseline):
        return RefreshOptions(self.database, self.chains, cutoff, self.publication_root,
                              baseline_job=baseline, confirmations=self.confirmations)

    def identity(self):
        return dict(database=self.database, chains=list(self.chains),
                    extraTvlChains=list(self.extra_tvl_chains), confirmations=self.confirmations,
                    publicationRoot=str(self.publication_root), apiOrigin=self.api_origin)


def save(path, body):
    """Durable replacement: SIGTERM cannot leave a partially written checkpoint."""
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        os.chmod(temporary, 0o600)
        json.dump(body, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def local_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'nightly.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RefreshBusy('another nightly wrapper is running') from None
        yield


def accepted_baseline(settings, identity):
    receipt = read_receipt(settings.database, identity)
    result = receipt['result']
    if (receipt['status'] != 'selected' or not result.get('publishReady')
            or result.get('consolidation') != 'complete'
            or not set(settings.chains) <= set(receipt['context']['chains'])
            or receipt['context']['database'] != settings.database):
        raise ValueError('baseline must be a completed selected refresh covering this scope')
    if Registry(settings.database).selection()['releaseId'] != result['releaseId']:
        raise ValueError('baseline differs from current selection; review manual publication before continuing')
    return receipt


def source_gap(exc):
    """Recognize availability failures; integrity/configuration errors stay fatal."""
    if isinstance(exc, requests.exceptions.SSLError):
        return None
    if isinstance(exc, (requests.ConnectionError, requests.Timeout, ProviderConnectionError, TimeExhausted)):
        return 'rpc-transport-unavailable'
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status >= 500 or status in (408, 429):
            return 'rpc-http-unavailable'
    if isinstance(exc, ValueError) and str(exc) == 'requested timestamp is newer than finalized chain state':
        return 'finalized-head-behind-cutoff'
    return None


def extra_tvl(settings, pending, persist):
    """Retain the production TVL-only acquisition phase under the worker lock.

The native missing-close planner preserves missing quotes and genuine zero units.
Completed catalog stages and bounded daily work survive process interruption.
"""
    with closing(connect(settings.database, direct=True)) as conn:
        if not conn.execute('SELECT pg_try_advisory_lock(?)', (LOCK,)).fetchone()[0]:
            raise RefreshBusy('another database refresh is running')
        try:
            catalogs = pending.setdefault('extraCatalogs', {})
            unavailable = pending.setdefault('extraTvl', {}).setdefault('unavailableChains', {})
            for chain in settings.extra_tvl_chains:
                if catalogs.get(str(chain), {}).get('status') == 'complete':
                    continue
                try:
                    reader = tvl_discovery.DiscoveryReader(chain)
                    first = reader.block_at(pending['start'] - 1) + 1
                    last = reader.block_at(pending['cutoff'] - 1)
                    summary = tvl_discovery.discover_catalog(
                        conn, chain_ids=[chain], from_block=first, to_block=last,
                        reader_factory=lambda _, reader=reader: reader)
                except Exception as exc:
                    reason = source_gap(exc)
                    if reason is None:
                        raise
                    gap = dict(status='unavailable', reason=reason, failureClass=type(exc).__name__)
                    catalogs[str(chain)] = gap
                    unavailable[str(chain)] = gap
                    persist()
                    continue
                catalogs[str(chain)] = dict(status=summary['status'], runId=summary['run_id'])
                unavailable.pop(str(chain), None)
                persist()
            available = tuple(chain for chain in settings.extra_tvl_chains if str(chain) not in unavailable)
            options = SimpleNamespace(chain_ids=available,
                                      cutoff=pending['cutoff'], first_close=pending['start'] + DAY - 1,
                                      repair_history=False)
            def plan():
                return (tvl_plan(conn, options) if available else
                        dict(targets=[], unresolvedTargets=[], deferredRepairs=[]))
            work = plan()
            attempted = {tuple(item) for item in pending.setdefault('extraAttempted', [])}
            targets = [item for item in work['targets'] if tuple(item) not in attempted]
            readers = {}
            def factory(chain):
                if chain not in readers:
                    readers[chain] = tvl_batch.reader(conn, chain)
                return readers[chain]
            for chain, day in targets[:30]:
                if str(chain) in unavailable:
                    continue
                try:
                    # Validate setup here: the native collector deliberately turns
                    # reader-factory exceptions into per-vault availability gaps.
                    factory(chain)
                    tvl_sources.collect_tvl(conn, day, day, chain_ids=[chain], reader_factory=factory)
                except Exception as exc:
                    reason = source_gap(exc)
                    if reason is None:
                        raise
                    conn.rollback()
                    unavailable[str(chain)] = dict(status='unavailable', reason=reason,
                                                   failureClass=type(exc).__name__)
                pending['extraAttempted'].append([chain, day])
                persist()
            remaining = plan()
            pending['extraTvl'] = dict(unresolved=remaining['unresolvedTargets'],
                                       deferredRepairs=remaining['deferredRepairs'], unavailableChains=unavailable)
            # A completed attempt can still have unavailable source evidence.
            # Preserve that warning instead of retrying it forever or inventing zero.
            complete = len(targets) <= 30
            if complete:
                pending['phase'] = 'refresh'
            persist()
            return complete
        finally:
            conn.execute('SELECT pg_advisory_unlock(?)', (LOCK,))


class VerificationError(ValueError):
    """Owned diagnostic code, safe to persist without an upstream exception body."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def verification_failure(exc):
    reason = (exc.code if isinstance(exc, VerificationError) else
              'invalid-response' if isinstance(exc, requests.exceptions.JSONDecodeError) else
              'http-request-failed' if isinstance(exc, requests.RequestException) else 'invalid-response')
    result = dict(status='failed', checkedAt=int(time.time()), reason=reason, failureClass=type(exc).__name__)
    if isinstance(exc, requests.RequestException) and exc.response is not None:
        result['httpStatus'] = exc.response.status_code
    return result


def verify_api(settings, receipt):
    """Verify identity and cutoff, including actual financial consumer responses."""
    result = receipt['result']
    def get(path):
        response = requests.get(settings.api_origin.rstrip('/') + path, timeout=60)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise VerificationError('invalid-response')
        return body
    expected = dict(releaseId=result['releaseId'], feesDatasetId=result['feeDatasetId'],
                    tvlDatasetId=result['tvlDatasetId'], analyticsPublicationId=result['analyticsPublicationId'])
    if get('/api/publication') != expected:
        raise VerificationError('selection-mismatch')
    analytics = get('/api/analytics/publication?publicationId=' + expected['analyticsPublicationId'])
    if (any(analytics.get(k) != expected[k] for k in ('feesDatasetId', 'tvlDatasetId'))
            or analytics.get('publicationId') != expected['analyticsPublicationId']
            or analytics.get('cutoffTimestamp') != receipt['context']['cutoff']):
        raise VerificationError('analytics-identity-or-cutoff-mismatch')
    tvl = get('/api/tvl?datasetId=' + expected['tvlDatasetId'])
    fees = get('/api/fees?datasetId=' + expected['feesDatasetId'])
    if (tvl.get('datasetId') != expected['tvlDatasetId']
            or tvl.get('asOfTimestamp') != receipt['context']['cutoff'] - 1
            or fees.get('datasetId') != expected['feesDatasetId']):
        raise VerificationError('financial-identity-or-close-mismatch')
    if get('/api/publication') != expected:
        raise VerificationError('selection-changed-during-verification')
    return dict(status='verified', checkedAt=int(time.time()), **expected)


def cycle(settings, *, now=None, sleep=time.sleep):
    cutoff = int(time.time() if now is None else now) // DAY * DAY
    with local_lock(settings.state_dir):
        path = settings.state_dir / 'state.json'
        state = json.loads(path.read_text()) if path.exists() else dict(
            version=1, scope=settings.identity(), lastJob=settings.initial_baseline_job, pending=None)
        if state['version'] != 1 or state['scope'] != settings.identity():
            raise ValueError('nightly scope changed; review state before starting a new scope')
        persist = lambda: save(path, state)
        # An older interrupted cycle is completed before advancing to today's cutoff.
        for _ in range(settings.max_invocations):
            pending = state['pending']
            if pending is None:
                baseline = accepted_baseline(settings, state['lastJob'])
                start = baseline['context']['cutoff']
                if start > cutoff:
                    raise ValueError('baseline is newer than the closed cutoff')
                if start == cutoff:
                    return 0
                pending = state['pending'] = dict(cutoff=cutoff, start=start, baseline=state['lastJob'],
                                                   phase='extra', jobId=None)
                persist()
            if pending['phase'] == 'extra':
                if not extra_tvl(settings, pending, persist):
                    continue
            if pending['phase'] == 'refresh':
                if pending.get('started') and not pending['jobId']:
                    raise ValueError('interrupted before job ID was saved; inspect database jobs before recovery')
                options = settings.options(pending['cutoff'], pending['baseline'])
                pending['started'] = True
                persist()
                def progress(event):
                    if pending['jobId'] is None:
                        pending['jobId'] = event['jobId']
                        persist()
                    print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)
                receipt = run_refresh(options, mode='publish', resume=pending['jobId'], progress=progress)
                pending['jobId'] = receipt['jobId']
                save(settings.state_dir / (receipt['jobId'] + '.json'), receipt)
                pending['receiptStatus'] = receipt['status']
                if receipt['status'] == 'selected':
                    pending['phase'] = 'verify'
                persist()
                if receipt['status'] not in ('selected', 'selected-cleanup-failed', 'work-deferred'):
                    raise ValueError('refresh failed; resume only after investigating its saved receipt')
                if pending['phase'] != 'verify':
                    sleep(settings.retry_seconds)
                    continue
            # Persist phase before making HTTP calls: failures never rerun publication.
            receipt = read_receipt(settings.database, pending['jobId'])
            try:
                verified = verify_api(settings, receipt)
            except (requests.RequestException, ValueError) as exc:
                pending['verification'] = verification_failure(exc)
                persist()
                print(json.dumps(dict(event='hosted-verification-failed', jobId=pending['jobId'],
                                      **pending['verification']), sort_keys=True), file=sys.stderr, flush=True)
                sleep(settings.retry_seconds)
                continue
            pending['verification'] = verified
            save(settings.state_dir / (pending['jobId'] + '-verification.json'), pending)
            state['lastJob'] = pending['jobId']
            state['lastVerified'] = verified
            state['pending'] = None
            persist()
            if receipt['context']['cutoff'] >= cutoff:
                return 0
        return 3  # Bounded pause, never success while a cycle is unfinished.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--env', type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true', help='Validate local configuration only; no database/network/writes')
    action.add_argument('--run', action='store_true', help='Acquire and publish to the explicitly configured database')
    args = parser.parse_args(argv)
    settings = Settings.read(args.config)
    if not args.env.is_file():
        parser.error('explicit environment file does not exist')
    if args.check:
        print(json.dumps(dict(status='configuration-valid', scope=settings.identity(),
                              baseline=settings.initial_baseline_job, package=str(Path(__file__).resolve())), indent=2))
        return 0
    load_environment([args.env])
    try:
        return cycle(settings)
    except Exception as exc:
        # Provider/DB exceptions can contain credentials; keep journal diagnostics bounded.
        print(json.dumps(dict(status='failed', failureClass=type(exc).__name__)), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
