"""Actual Source and checked Go Claude recall with a disposable Unix API."""
import copy
import json
import shutil
import unittest

import test_f02_checked_ordinary as ordinary


class ClaudeUnboundRecallTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass = classmethod(ordinary.CheckedOrdinaryTests.tearDownClass.__func__)
    setUp = ordinary.CheckedOrdinaryTests.setUp
    close = ordinary.CheckedOrdinaryTests.close
    source = ordinary.CheckedOrdinaryTests.source
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    state = ordinary.CheckedOrdinaryTests.state
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin

    def claude(self, name, prompt):
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config['harness'] = 'claude'
            self.overwrite(path, ordinary.encoded(config))
        self.repin()
        self.f.event.update(hook_event_name=name, session_id='11111111-1111-4111-8111-111111111111',
                            prompt=prompt, prompt_id='native-fixture-prompt')
        self.f.raw = ordinary.encoded(self.f.event)

    def pair(self):
        rows = []
        for launch in (self.source, self.go):
            shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
            self.calls.clear()
            result = launch()
            calls = copy.deepcopy(self.calls)
            for operation, body, _ in calls:
                body.pop('request_id', None)
                for key in ('elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms'):
                    if operation == 'recall-observation':
                        self.assertGreaterEqual(body.pop(key), 0)
            rows.append(dict(exit=result.returncode, stdout=result.stdout.decode(),
                             stderr=result.stderr.decode(), state=self.state(),
                             requests=[(op, body) for op, body, _ in calls]))
        print('CLAUDE_UNBOUND_PAIR='+json.dumps(dict(event=self.f.event['hook_event_name'],
              source=rows[0], go=rows[1]), sort_keys=True), flush=True)
        self.assertEqual(rows[1], rows[0])
        return rows[1]

    def test_start_and_prompt_reach_real_checked_recall_body(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect the current retention policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stderr']), (0, ''))
                self.assertEqual([op for op, _ in row['requests']], ['version', 'index'])

    def test_start_and_prompt_query_refusal_keeps_exact_public_diagnostics(self):
        for name in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=name):
                self.claude(name, 'Inspect "alpha\x00beta" policy.')
                row = self.pair()
                self.assertEqual((row['exit'], row['stdout']), (1, ''))
                self.assertEqual(row['requests'], [])
                self.assertEqual(row['stderr'], 'Cairn lifecycle diagnostic: phase=handle error_class=ValueError\n'
                    'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n')
