"""Validate an installation and render its user-service files for explicit install."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def quote(value):
    value = str(value)
    if any(char in value for char in ('\n', '\r', '\0')):
        raise ValueError('Service paths must fit on one line')
    # systemd expands percent specifiers even inside quoted values.
    return json.dumps(value.replace('%', '%%'), ensure_ascii=False)


def render(repo, python, config, env_file, output):
    repo, config, env_file = (Path(p).expanduser().resolve() for p in (repo, config, env_file))
    # Preserve the virtual-environment executable path, including its symlink.
    python = Path(python).expanduser().absolute()
    output = Path(output).expanduser().absolute()
    source = repo / 'src/yearn_data/nightly.py'
    for path in (source, python, config, env_file):
        if not path.is_file():
            raise ValueError(f'Required installation file is missing: {path}')
    # WorkingDirectory consumes the whole value; ExecStart/Environment use quoted words.
    values = dict(ROOT=str(repo).replace('%', '%%'), PYTHONPATH=quote(f'PYTHONPATH={repo / "src"}'),
                  PYTHON=quote(python), CONFIG=quote(config), ENV=quote(env_file))
    templates = Path(__file__).resolve().parents[1] / 'ops'
    units = {
        'yearn-data-nightly.service': re.sub(
            r'@([A-Z_]+)@', lambda match: values[match[1]],
            (templates / 'yearn-data-nightly.service.in').read_text()),
        'yearn-data-nightly.timer': (templates / 'yearn-data-nightly.timer').read_text(),
    }
    check = subprocess.run(
        [str(python), '-m', 'yearn_data.nightly', '--config', str(config),
         '--env', str(env_file), '--check'],
        env=dict(os.environ, PYTHONPATH=str(repo / 'src')), cwd=repo,
        capture_output=True, text=True)
    if check.returncode:
        raise ValueError('Nightly configuration check failed; validate the selected config with --check')
    result = json.loads(check.stdout)
    if (result.get('status') != 'configuration-valid'
            or Path(result.get('package', '')).resolve() != source.resolve()):
        raise ValueError('The selected Python environment must load this checkout')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, text in units.items():
        temporary = output / (name + '.tmp')
        temporary.write_text(text)
        temporary.replace(output / name)
    return dict(status='rendered', directory=str(output), scope=result['scope'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir or args.repo / '.deployment/nightly/units'
    try:
        result = render(args.repo, args.python, args.config, args.env, output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
