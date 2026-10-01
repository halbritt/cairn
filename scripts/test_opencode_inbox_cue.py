"""Exercise native/MCP tool-result cues through the real plugin and live-lookup hook."""
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
AGENT_ID, EXECUTION_ID = '11111111-1111-4111-8111-111111111111', 'fixture-execution'


def counts(**values):
    return dict(response=dict(ok=True, data=dict(dict(requests=0, notices=0, responses=0), **values)))


def refused(status):
    return dict(response=dict(ok=False, status=status), exit=1)


UNREACHABLE = dict(raw='socket closed', exit=1)


class OpenCodeInboxCue(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.api_calls = self.root / 'api-calls'
        self.api_script = self.root / 'api-script.json'
        # A disposable stand-in for the authenticated CLI: it records every request
        # and plays the scripted replies in order, repeating the last one.
        executable = self.root / 'fake-cairn'
        executable.write_text(f'#!{sys.executable}\nimport json, sys, time\nfrom pathlib import Path\n'
                              f'root = Path({str(self.root)!r})\nbody = sys.stdin.read()\n'
                              'log = root / "api-calls"\n'
                              'index = len(log.read_text().splitlines()) if log.exists() else 0\n'
                              'with log.open("a") as file:\n'
                              '    file.write(json.dumps(dict(argv=sys.argv[1:], body=json.loads(body or "null"))) + "\\n")\n'
                              'steps = json.loads((root / "api-script.json").read_text())\n'
                              'step = steps[min(index, len(steps) - 1)]\n'
                              'time.sleep(step.get("delay", 0))\n'
                              'print(step["raw"] if "raw" in step else json.dumps(step["response"]))\n'
                              'sys.exit(step.get("exit", 0))\n')
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
        self.lookups = []
        self.elapsed = 0

    def run_cue(self, calls, *, environment=None, pending=None, cache=True, state_change=None, api=None):
        """Run tool completions through the real plugin; `api` scripts the lookup replies."""
        self.api_calls.unlink(missing_ok=True)
        self.api_script.write_text(json.dumps(api or [counts(requests=1, notices=2, latest_position=42)]))
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
                     workspace=str(self.root), agent=dict(agent_id=AGENT_ID, execution_id=EXECUTION_ID,
                     metadata=dict(state='busy', delivery_mode='existing-session', workspace=str(self.root))))
        if cache:
            state['inbox_pending'] = dict(requests=1, notices=2, responses=0, latest_position=42,
                                          execution_id=EXECUTION_ID, observed_at=time.time())
            state['inbox_pending'].update(pending or {})
        if state_change:
            state_change(state)
        state_path = coordination.state_path(self.config, 'ses_fixture')
        state_path.with_suffix('.cue').unlink(missing_ok=True)
        coordination.write_state(state_path, state)
        started = time.monotonic()
        output, error = process.communicate(json.dumps(calls), timeout=8)
        self.elapsed = time.monotonic() - started
        self.assertEqual(process.returncode, 0, error)
        result = json.loads(output)
        self.assertEqual(result['sdkCalls'], 0)
        self.lookups = [json.loads(line) for line in self.api_calls.read_text().splitlines()] if self.api_calls.exists() else []
        for lookup in self.lookups:  # The cue may only ask the read-only counting operation for this session.
            self.assertEqual(lookup['argv'][-1], 'session-inbox-pending')
            self.assertEqual(lookup['body'], dict(agent_id=AGENT_ID, execution_id=EXECUTION_ID))
        self.api_calls.unlink(missing_ok=True)
        return result['outputs'], state_path.with_suffix('.cue')

    def check_shapes(self, **run):
        native = dict(title='kept', output='original native output', metadata=dict(kept=True))
        mcp = dict(content=[dict(type='text', text='original MCP output'),
                            dict(type='image', data='AA==', mimeType='image/png')],
                   isError=False, structuredContent=dict(kept=True), output='unused MCP extension')
        for shape, value in (('native', native), ('mcp', mcp)):
            with self.subTest(shape=shape):
                outputs, marker = self.run_cue([dict(session='ses_fixture', output=value)], **run)
                result = outputs[0]
                self.assertEqual(len(self.lookups), 1)
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

    def test_native_and_mcp_results_preserve_content_with_cue_first(self):
        self.check_shapes()

    def test_arrival_after_the_last_refresh_cues_at_the_next_tool_boundary(self):
        # The watcher has not recorded the arrival: no record, or one too old to trust.
        self.check_shapes(cache=False)
        self.check_shapes(pending=dict(requests=0, notices=0, latest_position=0, observed_at=time.time() - 100))

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
        self.assertEqual(len(self.lookups), 2, 'unsupported or untracked results must not look anything up')
        self.assertTrue(marker.exists())

    def test_cue_follows_new_arrivals_not_the_unchanged_backlog(self):
        calls = [dict(session='ses_fixture', output=dict(output=f'original {index}')) for index in range(6)]
        api = [counts(requests=1, notices=2, latest_position=42),
               counts(requests=1, notices=2, latest_position=42),  # unchanged backlog
               counts(requests=2, notices=2, latest_position=51),  # a request arrived
               counts(requests=1, notices=2, latest_position=42),  # it was delivered meanwhile
               counts(requests=1, notices=2, latest_position=42),
               counts(responses=1, requests=1, notices=2, latest_position=60)]
        outputs, _ = self.run_cue(calls, api=api, cache=False)
        cued = [value['output'].startswith('Cairn inbox:') for value in outputs]
        self.assertEqual(cued, [True, False, True, False, False, True])
        self.assertTrue(outputs[2]['output'].startswith('Cairn inbox: 2 requests, 2 notices waiting'))
        self.assertTrue(outputs[5]['output'].startswith('Cairn inbox: 1 request, 2 notices, 1 response waiting'))
        self.assertTrue(all(value['output'].endswith(f'original {index}') for index, value in enumerate(outputs)))
        self.assertEqual(len(self.lookups), 6)

    def test_concurrent_completions_do_not_repeat_the_same_arrival(self):
        calls = [dict(session='ses_fixture', output=dict(output='original')) for _ in range(4)]
        outputs, _ = self.run_cue(calls, environment=dict(CUE_CONCURRENT='1'),
                                  api=[dict(counts(requests=1, latest_position=5), delay=.3)], cache=False)
        self.assertEqual(sum(value['output'].startswith('Cairn inbox:') for value in outputs), 1)
        self.assertTrue(all(value['output'].endswith('original') for value in outputs))
        self.assertEqual(len(self.lookups), 1, 'concurrent completions must not queue behind the lookup')

    def test_suppression_preserves_output_and_marker(self):
        native = dict(output='original')
        cases = [dict(environment={name: '1'}) for name in
                 ('CAIRN_COORDINATION_DISABLED', 'CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD')]
        cases += [dict(environment=dict(CAIRN_WAKE_CONTEXT=str(self.root / 'unread-wake-context'))),
                  dict(state_change=lambda state: state['process'].update(start=0)),
                  dict(state_change=lambda state: state['agent']['metadata'].update(state='idle')),
                  dict(state_change=lambda state: state['agent']['metadata'].update(delivery_mode='fresh-worker')),
                  dict(state_change=lambda state: state.update(retired=True))]
        for case in cases:
            with self.subTest(case=case):
                outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)], **case)
                self.assertEqual(outputs, [native])
                self.assertFalse(marker.exists())
                self.assertEqual(self.lookups, [], 'a suppressed callback must not look anything up')
        for name in ('.cairn-no-memory', '.cairn-no-coordination'):
            with self.subTest(marker=name):
                opt_out = self.root / name
                opt_out.touch()
                try:
                    outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)])
                    self.assertEqual(outputs, [native])
                    self.assertFalse(marker.exists())
                    self.assertEqual(self.lookups, [])
                finally:
                    opt_out.unlink()

    def test_lookup_failure_preserves_output_and_leaves_no_marker(self):
        native = dict(output='original')
        stale = dict(observed_at=time.time() - 100)
        other_execution = dict(execution_id='other-execution')
        cases = dict(
            unreachable_without_record=dict(api=[UNREACHABLE], cache=False),
            unreachable_with_stale_record=dict(api=[UNREACHABLE], pending=stale),
            unreachable_with_other_execution_record=dict(api=[UNREACHABLE], pending=other_execution),
            refused_stale_execution=dict(api=[refused('STALE_SESSION')]),
            refused_unknown_operation=dict(api=[refused('INVALID_REQUEST')]),
            malformed=dict(api=[counts(requests='many', latest_position=1)]),
            negative=dict(api=[counts(requests=-1, latest_position=1)]),
        )
        for name, case in cases.items():
            with self.subTest(name):
                outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)], **case)
                self.assertEqual(outputs, [native])
                self.assertFalse(marker.exists())
                self.assertEqual(len(self.lookups), 1)

    def test_unreachable_lookup_never_uses_a_fresh_watcher_record(self):
        outputs, marker = self.run_cue([dict(session='ses_fixture', output=dict(output='original'))],
                                       api=[UNREACHABLE])
        self.assertEqual(outputs, [dict(output='original')])
        self.assertFalse(marker.exists())

    def test_slow_lookup_is_bounded_and_preserves_successful_tool_output(self):
        native = dict(output='original')
        outputs, marker = self.run_cue([dict(session='ses_fixture', output=native)], cache=False,
                                       api=[dict(counts(requests=1, latest_position=5), delay=20)])
        self.assertEqual(outputs, [native])
        self.assertFalse(marker.exists())
        self.assertLess(self.elapsed, 4)

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
