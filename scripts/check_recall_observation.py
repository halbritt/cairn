"""Real hook -> CLI -> API -> store -> use-report check on an explicitly disposable store.

Runs the actual lifecycle engine for Claude, Codex and OpenCode configurations
against a live authenticated API with a stand-in selector executable that
reports usage and cost in the provider's JSON shape. Then reads the protected
report through the trusted CLI and checks that each prompt's latency, selector
work and injected bytes appear per receipt and in the daily per-harness rollup,
that unreported values stay unknown, and that the profile boundaries hold.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recall_observation_engine', ROOT / 'integrations/lifecycle/memory.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

SELECTOR = '''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps(dict(type="result", is_error=False, structured_output=dict(index=0), total_cost_usd=0.0042,
    duration_ms=321, duration_api_ms=300,
    usage=dict(input_tokens=1800, output_tokens=12, cache_creation_input_tokens=0, cache_read_input_tokens=500),
    modelUsage={"fake-selector-model-1": dict(costUSD=0.0042)})))
'''
SESSIONS = dict(claude='caed9473-b01a-41e7-95ce-c3c1f28d66b3', codex='9a1c2f77-8f3b-4b5e-9a47-0b7e5d3a1c10',
                opencode='ses_0123456789abcdefABCDEF')


def check(binary, root):
    assert os.environ.get('CAIRN_TEST_DATABASE_URL')
    assert os.environ['CAIRN_DATABASE_URL'] == os.environ['CAIRN_TEST_DATABASE_URL']
    root = Path(root)
    root.mkdir(mode=0o700)
    repo = 'recall-observation-probe:' + str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    (root / 'agent.token').write_text(token)
    (root / 'agent.token').chmod(0o600)
    identities = [dict(principal='recall-observation-probe/agent', role='agent', repo=repo, destination='hosted',
                       token_sha256=hashlib.sha256(token.encode()).hexdigest())]
    (root / 'identities.json').write_text(json.dumps(identities))
    (root / 'identities.json').chmod(0o600)
    selector = root / 'selector'
    selector.write_text(SELECTOR)
    selector.chmod(0o700)
    work = root / 'work'
    (work / '.git').mkdir(parents=True)
    env = dict(os.environ, CAIRN_HOME=str(root))
    api = subprocess.Popen([binary, 'serve'], env=env, stdout=subprocess.DEVNULL, stderr=(root / 'api.log').open('w'))
    try:
        for _ in range(100):
            if (root / 'api.sock').exists():
                break
            time.sleep(0.1)
        assert (root / 'api.sock').exists(), 'API socket never appeared'
        socket = str(root / 'api.sock')

        def agent(operation, body, expected='OK'):
            result = subprocess.run([binary, 'agent', '--socket', socket, '--token-file', str(root / 'agent.token'),
                                     operation], input=json.dumps(body), text=True, capture_output=True, timeout=15)
            envelope = json.loads(result.stdout)
            assert envelope['status'] == expected, (operation, envelope, result.stderr)
            return envelope.get('data')

        def report(*args, expected='OK'):
            result = subprocess.run([binary, 'use-report', *args], env=env, text=True, capture_output=True, timeout=30)
            envelope = json.loads(result.stdout)
            assert envelope['status'] == expected, (args, envelope)
            return envelope.get('data')

        note = agent('create', dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='Lease expiry fixture: renew the lease before it expires.',
            scope=dict(repo=repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
        assert note['record_id']

        def run_hook(harness, selector_on, session=None):
            config = dict(cairn=binary, claude=str(selector), socket=socket, token_file=str(root / 'agent.token'),
                          repo=repo, state_dir=str(root / ('state-%s-%s' % (harness, selector_on))),
                          context_bytes=9500, semantic_fallback=selector_on)
            if harness != 'claude':
                config['harness'] = harness
            event = dict(hook_event_name='UserPromptSubmit', cwd=str(work), session_id=session or SESSIONS[harness],
                         prompt='Repair lease expiry before it expires')
            return engine.handle(config, event)

        for harness in ('claude', 'codex', 'opencode'):
            for selector_on in (False, True):
                result = run_hook(harness, selector_on)
                context = result.get('hookSpecificOutput', {}).get('additionalContext', '')
                assert 'Lease expiry fixture' in context, (harness, selector_on, result)

        first = report('--limit', '200', repo)
        by_harness = {}
        for rollup in first['recall_rollups']['rows']:
            by_harness.setdefault(rollup['harness'], []).append(rollup)
        assert set(by_harness) == {'claude', 'codex', 'opencode'}, first['recall_rollups']
        for harness, rows in by_harness.items():
            assert len(rows) == 1, rows  # one UTC day
            row = rows[0]
            assert row['observations'] == 2 and row['completed'] == 2 and row['timeouts'] == 0 and row['errors'] == 0, row
            for metric in ('elapsed_ms', 'search_ms', 'selector_ms', 'pulls_ms', 'injected_bytes'):
                assert row[metric]['known'] == 2 and row[metric]['unknown'] == 0, (harness, metric, row[metric])
                assert row[metric]['median'] is not None and row[metric]['p95'] is not None, (harness, metric)
            assert row['injected_bytes']['median'] > 0 and row['elapsed_ms']['median'] > 0, row
            selector = row['selector']
            # One prompt ran the selector, one did not: both are known, the cost is only what was reported.
            assert selector['observations_reporting_calls'] == 2 and selector['observations_not_reporting_calls'] == 0, selector
            assert selector['observations_with_calls'] == 1 and selector['calls'] == 1, selector
            assert selector['calls_with_cost'] == 1 and selector['calls_without_cost'] == 0, selector
            assert abs(selector['cost_usd'] - 0.0042) < 1e-9, selector
            assert (selector['input_tokens'], selector['output_tokens']) == (1800, 12), selector
            assert selector['cache_creation_input_tokens'] == 0 and selector['cache_read_input_tokens'] == 500, selector
            assert [m['model'] for m in selector['models']] == ['fake-selector-model-1'], selector
        print('Claude, Codex and OpenCode prompts each appear in the daily rollup with phases, selector work and cost')

        observed = [r for r in first['rows'] if r['recall'] is not None]
        # Six prompts: each selector prompt also searched semantically, so it produced a second receipt that the
        # same observation covers; the three plain prompts produced one receipt each.
        assert len(first['rows']) == len(observed) == 9, [r['receipt_id'] for r in first['rows']]
        selector_rows = [r for r in observed if r['recall']['selector_calls']]
        assert len(selector_rows) == 6, selector_rows
        assert {r['recall']['receipts'] for r in selector_rows} == {2}
        assert {r['recall']['receipts'] for r in observed if not r['recall']['selector_calls']} == {1}
        for row in observed:
            recall = row['recall']
            assert recall['status'] == 'completed' and recall['harness'] in by_harness and recall['receipts'] >= 1, recall
            assert recall['method'] == 'cairn-lifecycle/recall-meter/1', recall
            assert recall['elapsed_ms'] is not None and recall['injected_bytes'] > 0, recall
            # Latency is not delivery, use or outcome: those streams stay unknown. Any usage shown is the service's
            # own instrumented body pull, never something the hook's latency report supplied.
            assert row['best_observed_delivery'] == 'unknown' and row['task_outcome'] == 'unknown', row
            assert row['process_state'] == 'unknown' and row['duration_ms'] is None, row
            assert row['usage'] in ('unknown', 'expanded') and row['usage_method'] in ('', 'authorized-body-pull/1'), row
        for row in selector_rows:
            call, = row['recall']['selector_calls']
            assert (call['stage'], call['outcome'], call['model'], call['model_source']) == (
                'recall', 'completed', 'fake-selector-model-1', 'reported'), call
            assert abs(row['recall']['selector_cost_usd'] - 0.0042) < 1e-9, row['recall']
            assert row['recall']['selector_calls_without_cost'] == 0, row['recall']
        for row in observed:
            if not row['recall']['selector_calls']:
                assert row['recall']['selector_calls'] == [] and row['recall']['selector_cost_usd'] is None
        print('use-report shows each receipt\'s hook latency by phase, selector calls, model, tokens and reported cost')

        # Boundaries: the hosted profile cannot read the protected report and cannot report outside its repository.
        agent('use-report', dict(repo=repo, limit=1), expected='AUTHORITY_DENIED')
        before = len(report('--limit', '200', repo)['recall_rollups']['rows'])
        agent('recall-observation', dict(request_id=str(uuid.uuid4()), repo=repo + ':other', harness='claude',
              hook_event='UserPromptSubmit', method='probe/1', status='completed'), expected='AUTHORITY_DENIED')
        agent('recall-observation', dict(request_id=str(uuid.uuid4()), repo=repo, harness='nobody',
              hook_event='UserPromptSubmit', method='probe/1', status='completed'), expected='INVALID_REQUEST')
        assert len(report('--limit', '200', repo)['recall_rollups']['rows']) == before
        # A failure with nothing observed is unknown: a hook that cannot reach the API records nothing, not zeros.
        config = dict(cairn=binary, socket=str(root / 'absent.sock'), token_file=str(root / 'agent.token'), repo=repo,
                      state_dir=str(root / 'state-unreachable'), context_bytes=9500)
        try:
            engine.handle(config, dict(hook_event_name='UserPromptSubmit', cwd=str(work), session_id=SESSIONS['claude'],
                                       prompt='Repair lease expiry'))
            raise AssertionError('an unreachable API should fail the hook')
        except engine.HookError:
            pass
        after = report('--limit', '200', repo)
        assert sum(r['observations'] for r in after['recall_rollups']['rows']) == 6
        for days in ('0', '91'):
            report('--days', days, repo, expected='INVALID_REQUEST')
        print('Reporting stays within the profile boundary; an unreachable API leaves the observation unknown')
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()


if __name__ == '__main__':
    check(sys.argv[1], sys.argv[2])
