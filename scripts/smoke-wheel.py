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
        assert [p for p in paths if p.startswith('verifylab/skills/') and p.endswith('/SKILL.md')] == [
            'verifylab/skills/verifylab/SKILL.md']
        references = source / 'skills/verifylab/references'
        prefix = 'verifylab/skills/verifylab/references/'
        expected = {prefix + path.relative_to(references).as_posix()
                    for path in references.rglob('*') if path.is_file()}
        assert expected and {p for p in paths if p.startswith(prefix) and not p.endswith('/')} == expected
        assert 'verifylab/templates/vl.toml' in paths
        assert 'verifylab/adapters/StatementProbe.lean' in paths
        assert {'verifylab/lean/dumpx.lean', 'verifylab/lean/Ranges.lean'} <= set(paths)

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
    cli('init', '--context')
    research_map = project / 'research/map.md'
    research_map.write_text('A reusable bridge for the package smoke test.\n')
    git('add', '-A')
    git('commit', '-qm', 'commit question and candidate')
    cli('check', 'nth-prime')
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'pending-admission'
    git('add', 'research')
    git('commit', '-qm', 'admit receipt')
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'verified'
    context = cli('show', 'nth-prime')['items'][0]
    cli('review', 'nth-prime', '--kind', 'fidelity', '--expected-meaning-digest', context['meaning_digest'],
        '--verdict', 'faithful', '--author', 'agent:smoke', '--text', 'The evaluator checks the listed prime cases.')
    git('add', 'research')
    git('commit', '-qm', 'admit review')
    assert cli('show', 'nth-prime')['items'][0]['fidelity']['label'] == 'faithful'
    assert cli('find', 'reusable bridge', '--scope', 'notes')['notes'][0]['path'] == 'research/map.md'
    assessment = base / 'assessment.json'
    assessment.write_text(json.dumps({
        'schema': 'vl.appraisal.v1', 'subject': 'application',
        'ratings': {'usefulness': {'score': 2}}, 'goal': 'Reuse the prime computation',
        'input_context': 'Package smoke fixture: result and research map; no literature search.',
        'context_files': ['research/map.md'], 'raw_output': {'fixture_score': 2},
    }))
    cli('review', 'nth-prime', '--kind', 'appraisal', '--author', 'agent:smoke-rater', '--assessment', str(assessment))
    git('add', 'research')
    git('commit', '-qm', 'admit contextual appraisal')
    rated = next(r for r in cli('show', 'nth-prime')['items'][0]['reviews'] if r['kind'] == 'appraisal')
    assert rated['assessment']['ratings']['usefulness'] == {'score': 2}
    assert rated['applicability']['state'] == 'current' and rated['text'] == ''
    exported = base / 'catalog'
    cli('export', 'catalog', str(exported))
    catalog = json.loads((exported / 'catalog.json').read_text())
    entry = next(item for item in catalog['items'] if item['id'] == 'nth-prime')
    appraisal = next(review for review in entry['reviews'] if review['kind'] == 'appraisal')
    assert appraisal['assessment']['raw_output'] == {'fixture_score': 2}
    research_map.write_text('A changed application context.\n')
    git('add', 'research/map.md')
    git('commit', '-qm', 'change appraisal context only')
    stale = next(r for r in cli('show', 'nth-prime')['items'][0]['reviews'] if r['kind'] == 'appraisal')
    assert stale['applicability']['state'] == 'stale'
    current = cli('show', 'nth-prime')['items'][0]
    assert current['status']['label'] == 'verified' and current['fidelity']['label'] == 'faithful'
    assert cli('validate')['ok'] is True
    shutil.copytree(source / 'fixtures/python-planted/defects/wrong/experiments', project / 'experiments', dirs_exist_ok=True)
    cli('check', 'nth-prime', expected=1)
    assert cli('show', 'nth-prime')['items'][0]['status']['label'] == 'verified-stale'
    print('Installed-wheel smoke passed: package resources, context init/search, protected check, admission, '
          'fidelity review, score-only appraisal/export, appraisal-only staleness, validation, '
          'wrong-answer rejection and stale proof status.')
