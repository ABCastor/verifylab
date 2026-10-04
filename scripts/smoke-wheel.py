#!/usr/bin/env python3
"""Exercise a built wheel in a temporary installation and protected Python research project.

Requires uv, git and working bubblewrap isolation. No research data is used or preserved.
The wheel has no runtime dependencies; uv installs that local file with --no-deps.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("wheel", type=Path)
args = parser.parse_args()
wheel = args.wheel.resolve(strict=True)
source = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="verifylab-wheel-") as scratch:
    base = Path(scratch)
    with zipfile.ZipFile(wheel) as archive:
        paths = archive.namelist()
        assert len([p for p in paths if p.startswith('verifylab/skills/') and p.endswith('/SKILL.md')]) == 7
        assert 'verifylab/templates/vl.toml' in paths
        assert 'verifylab/adapters/StatementProbe.lean' in paths

    runtime = base / 'installed'
    subprocess.run(['uv', 'venv', '--python', sys.executable, str(runtime)], check=True, capture_output=True)
    subprocess.run(['uv', 'pip', 'install', '--no-deps', '--python', str(runtime / 'bin/python'), str(wheel)], check=True, capture_output=True)
    project = base / 'demo'
    shutil.copytree(source / 'fixtures/python-planted/repo', project)
    (project / 'research/vl.toml').unlink()

    def run(command, expected=0):
        result = subprocess.run(command, cwd=project, capture_output=True, text=True)
        if result.returncode != expected:
            raise AssertionError((command, result.returncode, result.stdout, result.stderr))
        return result.stdout

    def git(*args):
        return run(['git', *args])

    def cli(*args, expected=0):
        return json.loads(run([str(runtime / 'bin/vl'), *args, '--json'], expected))

    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'VerifyLab package smoke')
    git('config', 'user.email', 'smoke@example.invalid')
    cli('init')
    git('add', '-A')
    git('commit', '-qm', 'commit question and candidate')
    cli('check', 'nth-prime')
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'pending-admission'
    git('add', 'research')
    git('commit', '-qm', 'admit receipt')
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'verified'
    cli('review', 'nth-prime', '--kind', 'fidelity', '--verdict', 'faithful', '--author', 'agent:smoke', '--text', 'The evaluator checks the listed prime cases.')
    git('add', 'research')
    git('commit', '-qm', 'admit review')
    assert cli('show', 'nth-prime')['items'][0]['fidelity']['label'] == 'faithful'
    assert cli('validate')['ok'] is True
    shutil.copytree(source / 'fixtures/python-planted/defects/wrong/experiments', project / 'experiments', dirs_exist_ok=True)
    cli('check', 'nth-prime', expected=1)
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'verified-stale'
    print('Installed-wheel smoke passed: package resources, init, protected check, admission, fidelity review, validation, wrong-answer rejection and stale status.')
