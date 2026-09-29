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
        preview_seconds=event['seconds'], preview_reported_cost_usd=event['cost'])
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
    return 0
if __name__ == '__main__':
    sys.exit(main())
'''


class HookObserverTest(unittest.TestCase):
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
            self.assertNotIn('999', observations.read_text())

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
            rows = [json.loads(line) for line in observations.read_text().splitlines()]
            self.assertEqual([r['event'] for r in rows], ['SessionStart', 'UserPromptSubmit', 'PostToolUse'])
            self.assertEqual([r['recall']['preview_reported_cost_usd'] for r in rows[:2]], [.01, .02])
            self.assertIsNone(rows[2]['recall'])
            self.assertFalse(rows[2]['recall_attempted'])
            self.assertNotIn('chosen note', observations.read_text())


if __name__ == '__main__':
    unittest.main()
