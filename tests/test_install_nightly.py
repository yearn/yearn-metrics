"""Portable unit generation validates the selected runtime before creating files."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def installation(tmp_path):
    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location('install_nightly', root / 'scripts/install_nightly.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    repo = tmp_path / 'checkout with spaces % $USER'
    repo.mkdir()
    shutil.copytree(root / 'src', repo / 'src', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(root / 'ops', repo / 'ops')
    runtime = tmp_path / 'runtime with spaces'
    runtime.symlink_to(Path(sys.prefix), target_is_directory=True)
    python = runtime / 'bin/python'
    local = repo / '.deployment/nightly'
    local.mkdir(parents=True)
    config = local / 'config.json'
    body = json.loads((repo / 'ops/nightly-refresh.example.json').read_text())
    body.update(initial_baseline_job='reviewed-baseline', api_origin='https://example.test')
    config.write_text(json.dumps(body))
    env = repo / '.env'
    env.touch()
    return helper, repo, python, config, env, local / 'units'


def test_renders_quoted_installation_paths_without_starting_work(installation):
    helper, repo, python, config, env, output = installation
    original = config.read_bytes()
    result = helper.render(repo, python, config, env, output)
    unit = (output / 'yearn-data-nightly.service').read_text()
    assert f'WorkingDirectory={str(repo).replace("%", "%%")}\n' in unit
    assert f'ExecStart=:"{python}" -m yearn_data.nightly' in unit
    assert f'--config "{str(config).replace("%", "%%")}"' in unit
    assert result['scope']['publicationRoot'] == str(repo / 'artifacts/nightly/publication')
    assert config.read_bytes() == original
    assert not (repo / 'artifacts').exists()
    assert sorted(p.name for p in output.iterdir()) == ['yearn-data-nightly.service', 'yearn-data-nightly.timer']
    assert 'OnCalendar=*-*-* 02:00:00 UTC' in (output / 'yearn-data-nightly.timer').read_text()


@pytest.mark.parametrize('failure', ['missing-environment', 'unconfigured-baseline'])
def test_invalid_installation_keeps_existing_rendered_units(installation, failure):
    helper, repo, python, config, env, output = installation
    output.mkdir()
    old = output / 'yearn-data-nightly.service'
    old.write_text('previous service')
    if failure == 'missing-environment':
        env.unlink()
    else:
        body = json.loads(config.read_text())
        body['initial_baseline_job'] = ''
        config.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        helper.render(repo, python, config, env, output)
    assert old.read_text() == 'previous service'
    assert not (output / 'yearn-data-nightly.timer').exists()


def test_rejects_a_runtime_loading_another_checkout(installation, monkeypatch):
    helper, repo, python, config, env, output = installation
    monkeypatch.setattr(helper.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(dict(status='configuration-valid', package='/other/checkout/nightly.py'))))
    with pytest.raises(ValueError, match='must load this checkout'):
        helper.render(repo, python, config, env, output)
    assert not output.exists()
