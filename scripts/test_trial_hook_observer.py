"""Evaluator observer preserves each hook's output and recall measurements."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


OBSERVER = Path(__file__).with_name("trial_hook_observer.py")
ENGINE = '''import json, sys
from pathlib import Path
def recall(config, event, state):
    state['last_recall'] = dict(outcome='recalled', bytes=40,
        preview_seconds=event['seconds'], preview_reported_cost_usd=event['cost'],
        preview_process={'outcome':'completed','elapsed_ms':event['seconds'] * 1000})
    return {'hookSpecificOutput': {'additionalContext': 'chosen note'}}
def handle(config, event):
    path = Path(config['state'])
    state = json.loads(path.read_text()) if path.exists() else {}
    result = recall(config, event, state) if event['hook_event_name'] != 'PostToolUse' else {}
    path.write_text(json.dumps(state))
    return result
def main():
    config = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(handle(config, json.load(sys.stdin))))
    print('fixture warning 日本語',file=sys.stderr)
    return 0
if __name__ == '__main__':
    sys.exit(main())
'''


class HookObserverTest(unittest.TestCase):
    def test_bounded_prompt_metadata_keeps_output_and_distinguishes_absent_origin(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); engine=root/'memory.py';engine.write_text(ENGINE)
            config=root/'config.json';config.write_text(json.dumps(dict(state=str(root/'state.json'))))
            observations=root/'observations.jsonl'
            frozen='PRIVATE external task'
            events=[dict(prompt=frozen,prompt_id='native-one',source='user'),
                    dict(prompt='PRIVATE automatic text',prompt_id='native-two'),
                    dict(prompt=frozen,prompt_id={'bad':'PRIVATE'},source='PRIVATE future origin')]
            for fields in events:
                event=dict(hook_event_name='UserPromptSubmit',seconds=0,cost=0,**fields)
                direct=subprocess.run([sys.executable,str(engine),'--config',str(config)],input=json.dumps(event),text=True,capture_output=True)
                observed=subprocess.run([sys.executable,str(OBSERVER),'--engine',str(engine),'--config',str(config),
                    '--observations',str(observations),'--frozen-prompt-sha256',hashlib.sha256(frozen.encode()).hexdigest()],input=json.dumps(event),text=True,capture_output=True)
                self.assertEqual((observed.returncode,observed.stdout),(direct.returncode,direct.stdout))
            rows=[json.loads(line)['prompt_metadata'] for line in observations.read_text().splitlines()]
            self.assertEqual([x['origin'] for x in rows],['user','absent','unknown'])
            self.assertEqual([x['matches_frozen_prompt'] for x in rows],[True,False,True])
            self.assertNotEqual(rows[0]['prompt_id_sha256'],rows[1]['prompt_id_sha256'])
            self.assertEqual(rows[2]['prompt_id_state'],'invalid')
            self.assertNotIn('PRIVATE',observations.read_text())
            self.assertNotIn('native-one',observations.read_text())

    def test_bound_discovery_reaches_observer_without_changing_delivery(self):
        from test_inbox_recall_bridge import InboxRecallBridgeTests
        from test_claude_inbox_recall import ClaudeInboxRecallTests
        ready = dict(state='ready', coverage=dict(indexed=2, eligible=3),
                     algorithm='bge-original-passages/1', model_sha256='a' * 64,
                     scores_sha256='b' * 64)
        cases = [(ready, True, False, 'semantic'),
                 (dict(state='unavailable'), True, False, 'lexical_fallback'),
                 (dict(state='invalid_result'), True, False, 'lexical_fallback'),
                 (dict(state='not_needed'), True, False, 'not_needed'),
                 (dict(state=[]), True, False, 'unknown'),
                 (None, False, False, 'lexical'),
                 (None, True, False, 'unknown'),
                 (None, True, True, 'deferred')]
        for fixture in (InboxRecallBridgeTests, ClaudeInboxRecallTests):
            for discovery, semantic, startup, expected in cases:
                with self.subTest(harness=fixture.__name__, discovery=discovery, startup=startup):
                    case = fixture(); case.setUp(); self.addCleanup(case.doCleanups)
                    case.mc['semantic_fallback'] = semantic
                    if discovery is not None:
                        case.fixture['search']['discovery'] = discovery
                    case.freeze()
                    event = dict(case.event, prompt='Inspect fixture safeguards.')
                    if startup:
                        event.update(hook_event_name='SessionStart', source='startup', prompt='')
                    observations = case.root / 'observations.jsonl'
                    result = subprocess.run([sys.executable, str(OBSERVER),
                        '--engine', str(case.root / 'memory.py'), '--config', str(case.root / 'memory.json'),
                        '--observations', str(observations)], input=json.dumps(event),
                        text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    row = json.loads(observations.read_text())
                    self.assertEqual(row['recall']['discovery'], expected)
                    if discovery is None:
                        self.assertNotIn('search_discovery', row['recall'])
                    else:
                        self.assertEqual(row['recall']['search_discovery'],
                                         dict(state='unknown') if expected == 'unknown' else discovery)
                    self.assertEqual(row['stdout_bytes'], len(result.stdout.encode()))
                    if not startup:
                        view_text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
                        self.assertIn('Preserve the fixture scope', view_text)
                    calls = [c for c in case.calls() if c['op'] == 'search']
                    self.assertEqual(len(calls), 1)
                    self.assertEqual('--semantic' in calls[0]['args'], semantic and not startup)

    def test_discovery_observation_rejects_free_text_and_malformed_fields(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('observer_projection', OBSERVER)
        observer = importlib.util.module_from_spec(spec); spec.loader.exec_module(observer)
        raw = dict(state='ready', reason='PRIVATE query', query='PRIVATE',
                   algorithm='PRIVATE algorithm text', model_sha256='PRIVATE', scores_sha256='f'*64,
                   coverage=dict(indexed=True, eligible=-1, body='PRIVATE'))
        projected = observer.bounded_search_discovery(raw)
        self.assertEqual(projected, dict(state='ready', scores_sha256='f'*64))
        for invalid in ('PRIVATE', None, [], dict(state='PRIVATE')):
            self.assertEqual(observer.bounded_search_discovery(invalid), dict(state='unknown'))
        self.assertEqual(raw['query'], 'PRIVATE')

    def test_actual_engine_search_failure_records_final_failed_status_without_payloads(self):
        engine = OBSERVER.parent.parent / 'integrations/lifecycle/memory.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'cairn'
            cli.write_text('#!' + sys.executable + '\nimport sys\nsys.exit(1)\n')
            cli.chmod(0o700)
            config = root / 'config.json'
            config.write_text(json.dumps(dict(cairn=str(cli), socket='unused', token_file='unused',
                repo='trial', state_dir=str(root / 'state'))))
            observations = root / 'observations.jsonl'
            event = json.dumps(dict(hook_event_name='UserPromptSubmit', cwd=str(root),
                session_id='da3fddc4-431d-4c40-acdd-d4323d73a2db', prompt='private task'))
            direct = subprocess.run([sys.executable, str(engine), '--config', str(config)],
                                    input=event, text=True, capture_output=True)
            observed = subprocess.run([sys.executable, str(OBSERVER), '--engine', str(engine),
                '--config', str(config), '--observations', str(observations)],
                input=event, text=True, capture_output=True)
            self.assertEqual(direct.returncode, 1)
            self.assertEqual((observed.returncode, observed.stdout, observed.stderr),
                             (direct.returncode, direct.stdout, direct.stderr))
            row = json.loads(observations.read_text())
            self.assertEqual(row['recall']['outcome'], 'failed')
            self.assertEqual(row['recall']['error_type'], 'HookError')
            self.assertGreaterEqual(row['recall']['duration_ms'], 0)
            self.assertEqual(row['exit_code'], 1)
            self.assertEqual(row['stderr_bytes'],len(direct.stderr.encode()))
            self.assertGreater(row['stderr_bytes'],0)
            self.assertNotIn('private', observations.read_text())

    def test_actual_engine_empty_search_preserves_output(self):
        engine = OBSERVER.parent.parent / 'integrations/lifecycle/memory.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.git').mkdir()
            cli = root / 'cairn'
            cli.write_text('#!' + sys.executable + '\nimport json\nprint(json.dumps(' + repr(dict(
                ok=True, data=dict(status='SCOPE_EMPTY', destination=dict(name='hosted'), selected=[], index=[]))) + '))\n')
            cli.chmod(0o700)
            config = root / 'config.json'
            config.write_text(json.dumps(dict(cairn=str(cli), socket='unused', token_file='unused',
                repo='trial', harness='claude', state_dir=str(root / 'state'), semantic_fallback=False)))
            observations = root / 'observations.jsonl'
            for name in ('SessionStart', 'UserPromptSubmit'):
                event = json.dumps(dict(hook_event_name=name, source='startup', cwd=str(root),
                    session_id='da3fddc4-431d-4c40-acdd-d4323d73a2db', prompt='check saved preferences'))
                direct = subprocess.run([sys.executable, str(engine), '--config', str(config)],
                                        input=event, text=True, capture_output=True)
                observed = subprocess.run([sys.executable, str(OBSERVER), '--engine', str(engine),
                    '--config', str(config), '--observations', str(observations)],
                    input=event, text=True, capture_output=True)
                self.assertEqual(direct.returncode, 0, direct.stderr)
                self.assertEqual((observed.returncode, observed.stdout, observed.stderr),
                                 (direct.returncode, direct.stdout, direct.stderr))
            rows = [json.loads(line) for line in observations.read_text().splitlines()]
            self.assertEqual([r['recall']['outcome'] for r in rows], ['empty', 'empty'])
            self.assertTrue(all(r['recall_seconds'] >= 0 for r in rows))

    def test_failure_before_new_metrics_does_not_reuse_old_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine, config, observations = (root / n for n in ('memory.py', 'config.json', 'observations.jsonl'))
            engine.write_text(ENGINE + '\ndef recall(*args):\n    raise ValueError("fixture failure")\n')
            config.write_text(json.dumps(dict(state=str(root / 'session.json'))))
            (root / 'session.json').write_text(json.dumps(dict(last_recall=dict(model_reported_cost_usd=999))))
            result = subprocess.run([sys.executable, str(OBSERVER), '--engine', str(engine),
                '--config', str(config), '--observations', str(observations)],
                input=json.dumps(dict(hook_event_name='UserPromptSubmit')), text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('fixture failure', result.stderr)
            row = json.loads(observations.read_text())
            self.assertIsNone(row['recall'])
            self.assertTrue(row['recall_attempted'])
            self.assertIsNone(row['exit_code'])
            self.assertNotIn('preview_reported_cost_usd', row.get('recall') or {})

    def test_keeps_both_recalls_without_counting_unchanged_tool_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine, config, observations = (root / n for n in ('memory.py', 'config.json', 'observations.jsonl'))
            engine.write_text(ENGINE)
            config.write_text(json.dumps(dict(state=str(root / 'session.json'))))
            for name, seconds, cost in [('SessionStart', 2, .01), ('UserPromptSubmit', 3, .02), ('PostToolUse', 0, 0)]:
                event = json.dumps(dict(hook_event_name=name, seconds=seconds, cost=cost))
                direct = subprocess.run([sys.executable, str(engine), '--config', str(config)], input=event,
                                        text=True, capture_output=True)
                observed = subprocess.run([sys.executable, str(OBSERVER), '--engine', str(engine),
                    '--config', str(config), '--observations', str(observations)], input=event,
                    text=True, capture_output=True)
                self.assertEqual(observed.returncode, direct.returncode, observed.stderr)
                self.assertEqual(observed.stdout, direct.stdout)
                self.assertEqual(observed.stderr, direct.stderr)
                row=json.loads(observations.read_text().splitlines()[-1])
                self.assertEqual(row['stderr_bytes'],len(direct.stderr.encode()))
                self.assertGreater(row['stderr_bytes'],0)
            rows = [json.loads(line) for line in observations.read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows], ['SessionStart', 'UserPromptSubmit', 'PostToolUse'])
            self.assertEqual([r['recall']['preview_reported_cost_usd'] for r in rows[:2]], [.01, .02])
            self.assertEqual([r['recall']['preview_process']['elapsed_ms'] for r in rows[:2]], [2000, 3000])
            self.assertIsNone(rows[2]['recall'])
            self.assertFalse(rows[2]['recall_attempted'])
            self.assertNotIn('chosen note', observations.read_text())


if __name__ == '__main__':
    unittest.main()
