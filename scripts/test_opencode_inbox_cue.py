"""Exercise native/MCP tool-result cues through the real plugin and cached hook."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from integrations.lifecycle import coordination

ROOT = Path(__file__).resolve().parents[1]


class OpenCodeInboxCue(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.api_calls = self.root / 'api-calls'
        executable = self.root / 'forbidden-api'
        executable.write_text(f'#!{sys.executable}\nfrom pathlib import Path\n'
                              f'Path({str(self.api_calls)!r}).touch()\nraise SystemExit(1)\n')
        executable.chmod(0o700)
        self.config = dict(harness='opencode', binding='fixture', repo='fixture', native_delivery=True,
                           cairn=str(executable), socket=str(self.root / 'absent.sock'),
                           token_file=str(self.root / 'absent.token'), state_dir=str(self.root / 'state'))
        self.hook = self.root / 'hook.py'
        self.hook.write_text(f'import json,os,sys,time\nfrom pathlib import Path\n'
                            f'sys.path.insert(0, {str(ROOT / "integrations/lifecycle")!r})\n'
                            'import coordination\n'
                            'event=json.load(sys.stdin)\n'
                            'if event["hook_event_name"] == "PostToolUse": time.sleep(float(os.environ.get("CUE_DELAY", "0")))\n'
                            # Presence setup is unrelated to the cue; its cached path is real.
                            'if event["hook_event_name"] in ("TurnStart", "SessionEnd"): print("{}")\n'
                            'else: print(json.dumps(coordination.handle(coordination.load_config(Path(sys.argv[-1])), event)))\n')

    def run_cue(self, calls, *, environment=None, pending=None, state_change=None):
        config_path = self.root / 'coordination.json'
        config_path.write_text(json.dumps(self.config))
        plugin_config = self.root / 'plugin.json'
        plugin_config.write_text(json.dumps(dict(python=sys.executable,
            script=str(self.hook), config=str(config_path))))
        env = dict(os.environ, CAIRN_COORDINATION_CONFIG=str(plugin_config), CUE_WORKSPACE=str(self.root))
        env.update(environment or {})
        process = subprocess.Popen(['node', str(ROOT / 'scripts/run_opencode_cue_fixture.mjs')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertEqual(process.stdout.readline().strip(), f'READY:{process.pid}')
        state = dict(schema='cairn.native-session/1', process=coordination.process_reference(process.pid),
                     workspace=str(self.root), agent=dict(execution_id='fixture-execution',
                     metadata=dict(state='busy', delivery_mode='existing-session', workspace=str(self.root))),
                     inbox_pending=dict(requests=1, notices=2, responses=0, latest_position=42,
                                        execution_id='fixture-execution', observed_at=time.time()))
        state['inbox_pending'].update(pending or {})
        if state_change:
            state_change(state)
        state_path = coordination.state_path(self.config, 'ses_fixture')
        state_path.with_suffix('.cue').unlink(missing_ok=True)
        coordination.write_state(state_path, state)
        output, error = process.communicate(json.dumps(calls), timeout=8)
        self.assertEqual(process.returncode, 0, error)
        result = json.loads(output)
        self.assertEqual(result['sdkCalls'], 0)
        self.assertFalse(self.api_calls.exists(), 'a tool cue must not contact the API')
        return result['outputs'], state_path.with_suffix('.cue')

    def test_native_and_mcp_results_preserve_content_with_cue_first(self):
        native = dict(title='kept', output='original native output', metadata=dict(kept=True))
        mcp = dict(content=[dict(type='text', text='original MCP output'),
                            dict(type='image', data='AA==', mimeType='image/png')],
                   isError=False, structuredContent=dict(kept=True), output='unused MCP extension')
        for shape, value in (('native', native), ('mcp', mcp)):
            with self.subTest(shape=shape):
                outputs, marker = self.run_cue([dict(session='ses_fixture', output=value)])
                result = outputs[0]
                if shape == 'native':
                    self.assertTrue(result['output'].startswith('Cairn inbox: 1 request, 2 notices waiting'))
                    self.assertTrue(result['output'].endswith(value['output']))
                    self.assertEqual((result['title'], result['metadata']), (value['title'], value['metadata']))
                else:
                    self.assertEqual(result['content'][0]['type'], 'text')
                    self.assertTrue(result['content'][0]['text'].startswith('Cairn inbox: 1 request, 2 notices waiting'))
                    self.assertEqual(result['content'][1:], value['content'])
                    self.assertFalse(result['isError'])
                    self.assertEqual(result['structuredContent'], value['structuredContent'])
                    self.assertEqual(result['output'], value['output'])
                self.assertTrue(marker.exists())

    def test_only_supported_tracked_result_consumes_cue_then_deduplicates(self):
        native = dict(output='original')
        calls = [dict(session='ses_fixture', output=dict(unknown='kept')),
                 dict(session='ses_other', output=native),
                 dict(session='ses_fixture', output=native),
                 dict(session='ses_fixture', output=native)]
        outputs, marker = self.run_cue(calls)
        self.assertEqual(outputs[:2], [calls[0]['output'], native])
        self.assertTrue(outputs[2]['output'].startswith('Cairn inbox:'))
        self.assertEqual(outputs[3], native)
        self.assertTrue(marker.exists())

    def test_concurrent_completions_do_not_repeat_the_same_arrival(self):
        calls = [dict(session='ses_fixture', output=dict(output='original')) for _ in range(4)]
        outputs, _ = self.run_cue(calls, environment=dict(CUE_CONCURRENT='1'))
        self.assertEqual(sum(value['output'].startswith('Cairn inbox:') for value in outputs), 1)
        self.assertTrue(all(value['output'].endswith('original') for value in outputs))

    def test_suppression_preserves_output_and_marker(self):
        native = dict(output='original')
        cases = [dict(environment={name: '1'}) for name in
                 ('CAIRN_COORDINATION_DISABLED', 'CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD')]
        cases += [dict(environment=dict(CAIRN_WAKE_CONTEXT=str(self.root / 'unread-wake-context'))),
                  dict(pending=dict(observed_at=time.time() - 100)),
                  dict(pending=dict(execution_id='other-execution')),
                  dict(state_change=lambda state: state['process'].update(start=0))]
        for case in cases:
            with self.subTest(case=case):
                outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)], **case)
                self.assertEqual(outputs, [native])
                self.assertFalse(marker.exists())
        for name in ('.cairn-no-memory', '.cairn-no-coordination'):
            with self.subTest(marker=name):
                opt_out = self.root / name
                opt_out.touch()
                try:
                    outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)])
                    self.assertEqual(outputs, [native])
                    self.assertFalse(marker.exists())
                finally:
                    opt_out.unlink()

    def test_cue_timeout_preserves_successful_tool_output(self):
        native = dict(output='original')
        outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)],
                                      environment=dict(CUE_DELAY='3'))
        self.assertEqual(outputs, [native])
        self.assertFalse(marker.exists())

    def test_disposal_during_cue_lookup_preserves_tool_output(self):
        native = dict(output='original')
        outputs, _ = self.run_cue([dict(session='ses_fixture', output=native)],
                                 environment=dict(CUE_DISPOSE='1', CUE_DELAY='.3'))
        self.assertEqual(outputs, [native])


if __name__ == '__main__':
    unittest.main()
