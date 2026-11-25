"""Research judgments round-trip without becoming proof, consensus or compulsory context."""
from __future__ import annotations

import json

from verifylab.cli import main
from verifylab.repo import Repo
from conftest import commit_all
from test_cmd_show import make_item


def call(capsys, *args):
    rc = main(list(args))
    captured = capsys.readouterr()
    assert rc == 0, captured.err + captured.out
    return captured.out


def judge(root, capsys, item='add-zero', author='agent:reviewer', text=None, **changes):
    payload = {'schema': 'vl.appraisal.v1', 'subject': 'connection',
               'ratings': {'novelty': {'value': 'high'}, 'usefulness': {'value': 'medium'}},
               'goal': 'Find a useful bridge', 'input_context': 'Lemma and selected related results; no web search.',
               'provenance': {'model': 'external-classifier', 'role': 'reviewer'},
               'sources': [{'locator': 'https://example.org/prior', 'access': 'supplied abstract'}], **changes}
    path = root / 'assessment-input.json'
    path.write_text(json.dumps(payload))
    args = ['review', item, '--kind', 'appraisal', '--author', author, '--assessment', str(path), '--json']
    if text is not None:
        args += ['--text', text]
    call(capsys, *args)
    return next(r.data for r in reversed(Repo.open(root).reviews(item)) if r.data['author'] == author)


def test_score_only_and_opposed_judgments_survive_full_brief_and_export(research_repo, monkeypatch, capsys, tmp_path):
    root = research_repo
    monkeypatch.chdir(root)
    first = judge(root, capsys, ratings={'novelty': {'value': 'high', 'distribution': {'low': .1, 'medium': .2, 'high': .7}}},
                  raw_output={'native_signal': 'actual-producer-output'})
    judge(root, capsys, author='agent:critic', text='The earlier source may already contain this bridge.',
          ratings={'novelty': {'value': 'low'}}, review_references=[first['review_id']])
    commit_all(root, 'admit opposed judgments')
    out = call(capsys, 'show', 'add-zero', '--json')
    data = json.loads(out)['items'][0]
    appraisals = [r for r in data['reviews'] if r['kind'] == 'appraisal']
    assert len(appraisals) == 2
    assert {r['author'] for r in appraisals} == {'agent:reviewer', 'agent:critic'}
    assert next(r for r in appraisals if r['author'] == 'agent:reviewer')['text'] == ''
    assert all(r['applicability']['state'] == 'current' for r in appraisals)
    brief = call(capsys, 'show', 'add-zero', '--brief', '--budget', '16000')
    assert 'novelty=high' in brief and 'novelty=low' in brief and 'earlier source' in brief
    assert first['review_id'] in brief and 'supplied abstract' in brief
    output = tmp_path / 'catalog'
    call(capsys, 'export', 'catalog', str(output))
    exported = '\n'.join(p.read_text() for p in output.rglob('*') if p.is_file())
    assert 'agent:critic' in exported and 'earlier source' in exported
    assert 'novelty' in exported and 'supplied abstract' in exported
    assert 'native_signal' in exported and 'actual-producer-output' in exported
    catalog = json.loads((tmp_path / 'catalog' / 'catalog.json').read_text())
    entry = next(x for x in catalog['items'] if x['id'] == 'add-zero')
    actual = next(x for x in entry['reviews'] if x['author'] == 'agent:reviewer')
    assert actual['assessment']['ratings']['novelty']['distribution']['high'] == .7


def test_sparse_context_is_explicit_and_keeps_corrections(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    judge(research_repo, capsys, text='distinctive appraisal prose')
    call(capsys, 'review', 'add-zero', '--kind', 'correction', '--author', 'agent:critic', '--text', 'Only naturals.')
    out = call(capsys, 'show', 'add-zero', '--brief', '--no-appraisals')
    assert 'distinctive appraisal prose' not in out and 'novelty=high' not in out
    assert '1 appraisals omitted by request' in out and 'Only naturals.' in out and 'ladder' in out
    data = json.loads(call(capsys, 'show', 'add-zero', '--no-appraisals', '--json'))['items'][0]
    assert data['appraisals_omitted'] == 1 and not data['reviews'] and data['corrections']


def test_stale_basis_is_shown_as_stale(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    (root / 'goal.md').write_text('Goal one')
    judge(root, capsys, context_files=['goal.md'])
    commit_all(root, 'admit')
    (root / 'goal.md').write_text('Goal two')
    commit_all(root, 'change goal')
    shown = json.loads(call(capsys, 'show', 'add-zero', '--json'))['items'][0]['reviews']
    assert len(shown) == 1 and shown[0]['applicability']['state'] == 'stale'
    assert 'goal.md' in str(shown[0]['applicability']['reasons'])
    assert 'stale' in call(capsys, 'show', 'add-zero', '--brief')


def test_brief_preserves_ordinary_insights_and_relations(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, 'related', kind='intuition', uses=['add-zero'])
    call(capsys, 'review', 'add-zero', '--kind', 'understanding', '--author', 'agent:reader', '--text', 'A reusable connection.')
    out = call(capsys, 'show', 'add-zero', '--brief')
    assert 'A reusable connection.' in out and 'used by: related' in out


def test_untrusted_text_cannot_control_terminal_but_json_retains_data(research_repo, monkeypatch, capsys):
    root = research_repo
    monkeypatch.chdir(root)
    make_item(root, 'hostile-note', kind='intuition', title='Name\x1b]0;false-title\x07\rOverwrite', body='Good\rbad\x9b31m')
    text = call(capsys, 'show', 'hostile-note')
    assert '\x1b' not in text and '\x07' not in text and '\r' not in text and '\x9b' not in text
    assert r'\x1b' in text and r'\x07' in text and r'\x0d' in text
    item = json.loads(call(capsys, 'show', 'hostile-note', '--json'))['items'][0]
    assert '\x1b' in item['title'] and '\r' in item['body']


def test_fractional_score_is_preserved_without_inventing_a_label(research_repo, monkeypatch, capsys):
    monkeypatch.chdir(research_repo)
    judge(research_repo, capsys, ratings={'novelty': {'score': 1.5}})
    judge(research_repo, capsys, author='agent:categorical', ratings={'novelty': {'value': 'medium'}})
    commit_all(research_repo, 'mixed native outputs')
    shown = json.loads(call(capsys, 'show', 'add-zero', '--json'))['items'][0]['reviews']
    scored = next(r for r in shown if r['author'] == 'agent:reviewer')
    assert scored['assessment']['ratings']['novelty'] == {'score': 1.5} and scored['text'] == ''
    brief = call(capsys, 'show', 'add-zero', '--brief', '--budget', '16000')
    assert 'novelty=1.5' in brief and 'input context:' in brief
