"""Updater rejects incompatible time bounds before any write stage."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('timestamp', [172799, 172800, 172801])
def test_cutoff_checked_before_writes(monkeypatch, tmp_path, timestamp):
    spec = importlib.util.spec_from_file_location('update_postgres', Path(__file__).parents[1] / 'scripts/update_postgres.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from yearn_data import chains, config, storage
    monkeypatch.setattr(config, 'load_environment', lambda paths: None)
    monkeypatch.setattr(chains, 'web3_for', lambda chain: SimpleNamespace(eth=SimpleNamespace(
        chain_id=1, get_block=lambda block: {'number': 100, 'timestamp': timestamp})))
    def unexpected(*args, **kwargs):
        if timestamp < 172800:
            raise RuntimeError('valid range reached catch-up')
        pytest.fail('write stage reached before cutoff validation')
    monkeypatch.setattr(module.subprocess, 'run', unexpected)
    monkeypatch.setattr(storage, 'connect', unexpected)
    monkeypatch.setattr('sys.argv', ['update_postgres', '--db', 'staging', '--env', str(tmp_path / 'env'),
        '--chain', 'eth', '--from-block', '1', '--to-block', '100', '--cutoff', '172800',
        '--publication-root', str(tmp_path)])
    if timestamp < 172800:
        with pytest.raises(RuntimeError, match='valid range reached catch-up'):
            module.main()
    else:
        with pytest.raises(SystemExit, match='2'):
            module.main()
