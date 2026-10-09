"""Isolated fixtures for a code-task composition spike (constructed events only).

Nothing here runs a native child, provider, database or service. The main-line module
scripts/trial_native_observation.py (PublicActions, measure) is imported read-only so the
composition reuses the existing action metadata instead of re-implementing it.
Run through run_composition.sh, which stages: frames.py (candidate), frames_orig.py (accepted
native-frames-v3), evidence.py (accepted), test_evidence.py (reviewed builders).
"""
import hashlib
from pathlib import Path
import importlib.util
import itertools
import json
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .. import evidence
from .. import frames
import trial_native_observation as tno
from ..evidence import PULL, SEARCH
from ..frames import FrameError, Frames, Profile
from .test_selected_evidence import RECEIPT, HANDLE, PULL_REQUEST, REQUEST, search


S, M, V = '70000000-0000-4000-8000-000000000001', 'claude-opus-5-5', '2.1.295'
ACTIONS = {'Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash'}   # names from scripts/trial_task_permissions.py ALLOW
CODE_TOOLS = sorted(ACTIONS | {SEARCH, PULL})
WORKSPACE = str(Path(__file__).resolve().parents[3])
COMMANDS = {'go_test': 'go test ./localapi'}
# CONSTRUCTED: the real permissionMode of the prospective harness is not retained anywhere; reviewers must pin it.
PROFILE = dict(name='code-task/spike-1', init_tools=set(CODE_TOOLS), action_tools=ACTIONS, permission_modes={'dontAsk'})


def profile(**override):
    return Profile(**dict(PROFILE, **override))


def init(tools=CODE_TOOLS, mode='dontAsk'):
    return {'type': 'system', 'subtype': 'init', 'session_id': S, 'model': M, 'claude_code_version': V,
            'permissionMode': mode, 'tools': list(tools),
            'mcp_servers': [{'name': 'cairn', 'status': 'connected', 'source': 'dynamic'}]}


def use(key, name, inp):
    return {'type': 'assistant', 'session_id': S, 'message': {'content': [{'type': 'tool_use', 'id': key, 'name': name, 'input': inp}]}}


def result(key, content, error=False, native=None):
    ev = {'type': 'user', 'session_id': S, 'message': {'content': [{'type': 'tool_result', 'tool_use_id': key,
          'content': content, 'is_error': error}]}}
    if native is not None:
        ev['tool_use_result'] = native
    return ev


FINAL = {'type': 'result', 'session_id': S, 'subtype': 'success', 'is_error': False, 'result': json.dumps({'ok': True}),
         'num_turns': 4, 'total_cost_usd': 0.1, 'permission_denials': [],
         'modelUsage': {M: {'provider': 'firstParty', 'inputTokens': 1, 'outputTokens': 1,
                            'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}}}
TASK = {'type': 'user', 'session_id': S, 'message': {'role': 'user', 'content': 'task prompt'}}
HOUSEKEEPING = [{'type': 'system', 'subtype': 'thinking_tokens'}, {'type': 'system', 'subtype': 'ui_invalidate'},
                {'type': 'rate_limit_event'}]


def memory_pair(search_key='m1', pull_key='m2'):
    body = 'new decision: preserve the failed attempt; next source review only'
    ledger_search = {'request_id': REQUEST, 'query': 'q', 'memory_budget_bytes': 9500, 'min_pull_bytes': 5000}
    pull_args = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE, 'span': {'offset': 14, 'length': 12}}
    from .test_selected_evidence import partial
    return [use(search_key, SEARCH, ledger_search), result(search_key, [{'type': 'text', 'text': json.dumps(search(body))}]),
            use(pull_key, PULL, pull_args), result(pull_key, [{'type': 'text', 'text': json.dumps(partial(body))}])]


def line(event):
    return (json.dumps(event, ensure_ascii=False) + '\n').encode()


def run(reader, events):
    for event in events:
        raw = line(event)
        for start in range(0, len(raw), 65536):     # the reader accepts at most 64 KiB per feed()
            reader.feed(raw[start:start + 65536])
    return reader.finish(0)


def coding_events(denials=None):
    final = dict(FINAL, permission_denials=denials if denials is not None else [])
    return ([init(), TASK, use('toolu_01', 'Read', {'file_path': 'DESIGN.md'}), result('toolu_01', 'file text')]
            + memory_pair() + [HOUSEKEEPING[0], use('toolu_02', 'Bash', {'command': 'go test ./localapi'}),
                               result('toolu_02', 'ok', native={'exit_code': 0}), HOUSEKEEPING[2],
                               use('toolu_03', 'Write', {'file_path': 'DESIGN.md', 'content': 'x'}),
                               result('toolu_03', 'written'), final])


def composed(events=None, prof=None, clock=None):
    ticks = itertools.count(1)
    sink = tno.PublicActions(WORKSPACE, COMMANDS)
    reader = Frames(S, M, V, profile=prof or profile(), actions=sink, clock=clock or (lambda: float(next(ticks))))
    return reader, run(reader, events if events is not None else coding_events())


class DefaultStaysMemoryOnly(unittest.TestCase):
    def test_guard_default_reader_still_rejects_coding_surface(self):
        for events, code in [([init()], 'tool_allowlist'),
                             ([init([SEARCH, PULL]), use('t', 'Read', {'file_path': 'x'})], 'tool_not_allowed'),
                             ([init([SEARCH, PULL], 'default')], 'init_identity')]:
            with self.subTest(code=code):
                with self.assertRaises(FrameError) as caught:
                    run(Frames(S, M, V), events)
                self.assertEqual(str(caught.exception), code)

    def test_guard_profile_and_sink_must_be_supplied_together(self):
        with self.assertRaises(FrameError):
            Frames(S, M, V, profile=profile())
        with self.assertRaises(FrameError):
            Frames(S, M, V, actions=tno.PublicActions(WORKSPACE, {}))


class Composition(unittest.TestCase):
    def test_fix_mixed_stream_keeps_memory_ledger_and_collects_action_metadata(self):
        reader, done = composed()
        self.assertEqual(done['init']['profile']['name'], 'code-task/spike-1')
        self.assertEqual(len(done['init']['profile']['sha256']), 64)
        actions = done['actions']
        self.assertEqual([(a['tool'], a['state']) for a in actions['actions']],
                         [('Read', 'tool_success'), (SEARCH, 'tool_success'), (PULL, 'tool_success'),
                          ('Bash', 'tool_success'), ('Write', 'tool_success')])
        bash = [a for a in actions['actions'] if a['tool'] == 'Bash'][0]
        self.assertEqual(bash['exit_code'], 0)
        self.assertEqual(bash['validation_label'], 'go_test')
        self.assertEqual(done['evidence']['responses']['m2']['source']['coverage'], 'returned_byte_range')
        self.assertEqual(done['final']['reported_permission_denials'], 0)

    def test_fix_action_rows_equal_the_main_line_measure_rows(self):
        events = coding_events()
        _, done = composed(events)
        lines = [json.dumps(e, ensure_ascii=False) + '\n' for e in events]
        timings = [dict(line=i + 1, received_seconds=float(i), bytes=len(x.encode()),
                        sha256=hashlib.sha256(x.encode()).hexdigest()) for i, x in enumerate(lines)]
        measured = tno.measure(''.join(lines), timings, [], input_bytes=1000, expects_hooks=False, model=M,
                               prompt='task prompt', workspace=WORKSPACE, public_validation_commands=COMMANDS)
        keys = ('tool', 'state', 'exit_code', 'path', 'command_category', 'shell_form', 'validation_label')
        project = lambda rows: [{k: r.get(k) for k in keys} for r in rows]
        self.assertEqual(project(done['actions']['actions']), project(measured['public_actions']['actions']))

    def test_known_main_line_marks_public_actions_unknown_for_the_observed_rate_limit_event(self):
        events = coding_events()
        lines = [json.dumps(e) + '\n' for e in events]
        timings = [dict(line=i + 1, received_seconds=float(i), bytes=len(x.encode()),
                        sha256=hashlib.sha256(x.encode()).hexdigest()) for i, x in enumerate(lines)]
        measured = tno.measure(''.join(lines), timings, [], input_bytes=1000, expects_hooks=False, model=M, prompt='task prompt',
                               workspace=WORKSPACE, public_validation_commands=COMMANDS)
        self.assertEqual(measured['public_actions']['status'], 'unknown')
        self.assertIn('native_action_format_unsupported', measured['public_actions']['unknown'])
        self.assertEqual(measured['status'], 'within_observed_limits')
        _, done = composed(events)
        self.assertEqual(done['actions']['status'], 'observed')   # the composition labels the event instead

    def test_fix_surface_and_tools_stay_explicit(self):
        wider = [init(CODE_TOOLS + ['WebFetch'])]
        with self.assertRaises(FrameError) as caught:
            composed(wider)
        self.assertEqual(str(caught.exception), 'tool_allowlist')
        for name in ('WebFetch', 'MultiEdit', SEARCH + '_x', 'mcp__cairn__cairn_remember'):
            with self.subTest(name=name), self.assertRaises(FrameError) as caught:
                composed([init(), use('t', name, {})])
            self.assertEqual(str(caught.exception), 'tool_not_allowed')
        with self.assertRaises(FrameError) as caught:
            composed([init(mode='default')])
        self.assertEqual(str(caught.exception), 'init_identity')

    def test_fix_action_result_discipline(self):
        cases = {
            'duplicate_action_result': [init(), use('a', 'Read', {'file_path': 'x'}), result('a', 'one'), result('a', 'two')],
            'call_identity': [init(), use('a', 'Read', {'file_path': 'x'}), use('a', 'Read', {'file_path': 'y'})],
            'response_identity': [init(), result('ghost', 'orphan')],
            'unanswered_tool_call': [init(), use('a', 'Read', {'file_path': 'x'}), FINAL],
        }
        for code, events in cases.items():
            with self.subTest(code=code), self.assertRaises((FrameError, evidence.EvidenceError)) as caught:
                composed(events)
            self.assertEqual(str(caught.exception), code)

    def test_fix_hooks_are_never_swallowed_as_housekeeping(self):
        for sub in ('hook_started', 'hook_response'):
            event = {'type': 'system', 'subtype': sub, 'hook_id': 'h', 'hook_event': 'SessionStart', 'stdout': 'SENTINEL'}
            with self.subTest(sub=sub, reader='default'), self.assertRaises(FrameError) as caught:
                run(Frames(S, M, V), [init([SEARCH, PULL]), event])
            self.assertEqual(str(caught.exception), 'hook_event_unexpected')
            with self.subTest(sub=sub, reader='profile'), self.assertRaises(FrameError) as caught:
                composed([init(), event])
            self.assertEqual(str(caught.exception), 'hook_event_unexpected')

    def test_fix_benign_and_unreviewed_labels_are_separated(self):
        _, done = composed()
        self.assertEqual(done['unreviewed_event_labels'], [])
        events = coding_events()
        events.insert(3, {'type': 'system', 'subtype': 'surprise'})
        _, done = composed(events)
        self.assertEqual(done['unreviewed_event_labels'], ['["system", "surprise"]'])
        self.assertNotIn('SENTINEL', json.dumps(done))

    def test_fix_permission_denials_are_counted_not_dropped(self):
        _, done = composed(coding_events(denials=[{'tool_name': 'WebFetch', 'tool_use_id': 'toolu_09', 'tool_input': {'url': 'SENTINEL'}}]))
        self.assertEqual(done['final']['reported_permission_denials'], 1)
        self.assertNotIn('SENTINEL', json.dumps(done['final']))

    def test_fix_profile_stream_caps_are_explicit_and_default_stays_small(self):
        big = 'x' * (2 * 1024 * 1024)
        events = [init(), use('a', 'Read', {'file_path': 'x'}), result('a', big), FINAL]
        _, done = composed(events)                       # 2 MiB line fits the profile cap
        self.assertEqual(done['actions']['actions'][0]['state'], 'tool_success')
        with self.assertRaises(FrameError) as caught:    # same bytes exceed the memory-only 1 MiB line cap
            run(Frames(S, M, V), [init([SEARCH, PULL]), {'type': 'rate_limit_event', 'pad': big}])
        self.assertEqual(str(caught.exception), 'line_bounds')
        with self.assertRaises(FrameError):
            composed(events, prof=profile(max_line_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024))

    def test_guard_profile_validation(self):
        bad = [dict(action_tools={SEARCH}), dict(action_tools={'Read', 'Elsewhere'}), dict(permission_modes=set()),
               dict(init_tools={'Read'}), dict(max_line_bytes=0), dict(max_frames=100001), dict(name='')]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(FrameError):
                profile(**override)
        self.assertEqual(profile().sha256, profile().sha256)
        self.assertNotEqual(profile().sha256, profile(permission_modes={'dontAsk', 'default'}).sha256)

    def test_guard_timing_unknown_without_clock_is_labelled_not_invented(self):
        sink = tno.PublicActions(WORKSPACE, COMMANDS)
        reader = Frames(S, M, V, profile=profile(), actions=sink)
        done = run(reader, coding_events())
        self.assertEqual(done['actions']['status'], 'unknown')
        self.assertIn('action_timing_unknown', done['actions']['unknown'])


if __name__ == '__main__':
    unittest.main()
