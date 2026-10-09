"""Fixtures for the final code-task composition candidate (constructed events only).

Covers the five review points: runner exit status is handled by run_v2.sh/prove_red.sh; here:
(2) transactional sink, (3) pinned hook channel policy with a reviewed projection, (4) final-answer
contract and pinned budgets, (5) process exit and provider 401/429 metadata outside the Frames path.
No native child, provider, database or service is touched. Main-line modules are imported read-only.
"""
import copy
from pathlib import Path
import hashlib
import importlib.util
import inspect
import itertools
import json
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .. import evidence
from .. import frames
from .. import terminal_seam as seam
import trial_native_observation as tno
import trial_source_delivery as tsd
import trial_task_eval as tte
from ..evidence import CURRENT_READ_POLICIES, PULL, SEARCH
from ..frames import FrameError, Frames, PINNED_BUDGETS, Profile
from .test_selected_evidence import BODY, HANDLE, PULL_REQUEST, RECEIPT, REQUEST, partial, search


S, M, V = '70000000-0000-4000-8000-000000000001', 'claude-opus-5-5', '2.1.295'
ACTIONS = {'Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash'}
CODE_TOOLS = sorted(ACTIONS | {SEARCH, PULL})
WORKSPACE = str(Path(__file__).resolve().parents[3])
COMMANDS = {'go_test': 'go test ./localapi'}
BASE = dict(name='code-task/final-spike', init_tools=set(CODE_TOOLS), action_tools=ACTIONS,
            permission_modes={'dontAsk'},   # CONSTRUCTED mode: the real one must come from a reviewed capture
            inspection_policies=CURRENT_READ_POLICIES)
FINAL_TEXT = 'worktree=/w\ncommit=0123456789abcdef0123456789abcdef01234567\ndesign=/w/DESIGN.md'


def profile(**override):
    return Profile(**dict(BASE, **override))


def init(tools=CODE_TOOLS, mode='dontAsk'):
    return {'type': 'system', 'subtype': 'init', 'session_id': S, 'model': M, 'claude_code_version': V,
            'permissionMode': mode, 'tools': list(tools),
            'mcp_servers': [{'name': 'cairn', 'status': 'connected', 'source': 'dynamic'}]}


def block(kind, **fields):
    return dict(type=kind, **fields)


def message(role, blocks):
    return {'type': role, 'session_id': S, 'message': {'content': blocks}}


def use_block(key, name, inp):
    return block('tool_use', id=key, name=name, input=inp)


def result_block(key, content, error=False):
    return block('tool_result', tool_use_id=key, content=content, is_error=error)


def final(text='{"ok":true}', **extra):
    return dict({'type': 'result', 'session_id': S, 'subtype': 'success', 'is_error': False, 'result': text, 'num_turns': 3,
                 'total_cost_usd': 0.1, 'permission_denials': [],
                 'modelUsage': {M: {'provider': 'firstParty', 'inputTokens': 1, 'outputTokens': 1,
                                    'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}}}, **extra)


def line(event):
    return (json.dumps(event, ensure_ascii=False) + '\n').encode()


def feed_all(reader, events):
    for event in events:
        raw = line(event)
        for start in range(0, len(raw), 65536):
            reader.feed(raw[start:start + 65536])


def memory_calls(budget=9500):
    args = {'request_id': REQUEST, 'query': 'q', 'memory_budget_bytes': budget, 'min_pull_bytes': 5000}
    if budget is None:
        del args['memory_budget_bytes']
    pull = {'request_id': PULL_REQUEST, 'receipt_id': RECEIPT, 'handle': HANDLE, 'span': {'offset': 14, 'length': 12}}
    return [message('assistant', [use_block('m1', SEARCH, args)]),
            message('user', [result_block('m1', [{'type': 'text', 'text': json.dumps(search(BODY))}])]),
            message('assistant', [use_block('m2', PULL, pull)]),
            message('user', [result_block('m2', [{'type': 'text', 'text': json.dumps(partial(BODY))}])])]


def composed(events, prof=None, clock=None, sink=None, projector=None):
    ticks = itertools.count(1)
    sink = sink if sink is not None else tno.PublicActions(WORKSPACE, COMMANDS)
    prof = prof or profile()
    reader = Frames(S, M, V, profile=prof, actions=sink, clock=clock or (lambda: float(next(ticks))),
                    hook_projector=projector)
    return reader, sink


def run(reader, events):
    feed_all(reader, events)
    return reader.finish(0)


def pristine():
    return tno.PublicActions(WORKSPACE, COMMANDS)


def sink_state(sink):
    return (list(sink.actions), dict(sink.by_id), set(sink.unknown), sink.omitted, sink.unmatched, sink.duplicates)


class TransactionalSink(unittest.TestCase):
    def two_block_frame(self):
        return [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'DESIGN.md'}),
                                              use_block('b', 'WebFetch', {'url': 'SENTINEL'})])]


    def test_fix_rejected_frame_leaves_the_sink_exactly_as_constructed(self):
        reader, sink = composed(None)
        before = sink_state(sink)
        with self.assertRaises(FrameError) as caught:
            feed_all(reader, self.two_block_frame())
        self.assertEqual(str(caught.exception), 'tool_not_allowed')
        self.assertEqual(sink_state(sink), before)
        with self.assertRaises(FrameError) as caught:
            reader.feed(line(init()))
        self.assertEqual(str(caught.exception), 'reader_unavailable')

    def test_fix_earlier_accepted_frames_do_not_escape_when_a_later_frame_is_rejected(self):
        reader, sink = composed(None)
        before = sink_state(sink)
        feed_all(reader, [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'DESIGN.md'})]),
                          message('user', [result_block('a', 'text')])])
        self.assertEqual(len(sink.actions), 1)                       # accepted frames are visible while the stream is live
        with self.assertRaises(FrameError):
            reader.feed(line(message('assistant', [use_block('c', 'WebFetch', {})])))
        self.assertEqual(sink_state(sink), before)                   # a poisoned stream leaves nothing behind

    def test_fix_rejected_result_frame_does_not_finish_a_pending_action(self):
        reader, sink = composed(None)
        feed_all(reader, [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'x'})])])
        bad = message('user', [result_block('a', 'ok'), result_block('ghost', 'orphan')])
        with self.assertRaises((FrameError, evidence.EvidenceError)):
            reader.feed(line(bad))
        self.assertEqual(sink_state(sink), sink_state(pristine()))

    def test_fix_interruption_mid_stream_restores_the_sink_and_reraises(self):
        calls = itertools.count(1)

        def clock():
            if next(calls) == 2:
                raise KeyboardInterrupt
            return 1.0
        reader, sink = composed(None, clock=clock)
        feed_all(reader, [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'x'})])])
        with self.assertRaises(KeyboardInterrupt):
            reader.feed(line(message('user', [result_block('a', 'ok')])))
        self.assertEqual(sink_state(sink), sink_state(pristine()))
        with self.assertRaises(FrameError) as caught:
            reader.feed(line(init()))
        self.assertEqual(str(caught.exception), 'reader_unavailable')

    def test_fix_sink_fault_is_all_or_nothing_and_poisons(self):
        class Faulty(tno.PublicActions):
            def consume(self, part, event, received):
                if part.get('id') == 'b':
                    raise RuntimeError('sink fault')
                return super().consume(part, event, received)
        sink = Faulty(WORKSPACE, COMMANDS)
        reader, _ = composed(None, sink=sink)
        frame = message('assistant', [use_block('a', 'Read', {'file_path': 'x'}), use_block('b', 'Glob', {'pattern': '*'})])
        feed_all(reader, [init()])
        with self.assertRaises(FrameError) as caught:
            reader.feed(line(frame))
        self.assertEqual(str(caught.exception), 'internal_refusal')
        self.assertEqual(sink_state(sink), sink_state(Faulty(WORKSPACE, COMMANDS)))

    def test_guard_successful_stream_populates_sink_once_and_finish_rejected_restores_it(self):
        reader, sink = composed(None, prof=profile(final_kind='text'))
        done = run(reader, [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'x'})]),
                            message('user', [result_block('a', 'ok')]), final(FINAL_TEXT)])
        self.assertEqual([row['tool'] for row in done['actions']['actions']], ['Read'])
        reader, sink = composed(None)                                # finish() refusal path: no final event
        feed_all(reader, [init(), message('assistant', [use_block('a', 'Read', {'file_path': 'x'})]),
                          message('user', [result_block('a', 'ok')])])
        with self.assertRaises(FrameError):
            reader.finish(0)
        self.assertEqual(sink_state(sink), sink_state(pristine()))


class HookChannel(unittest.TestCase):
    VIEW = {'selected': [{'record': {'record_id': '20000000-0000-4000-8000-000000000001', 'version': 3, 'body': 'hook body'},
                          'mandatory': False}], 'index': []}
    STDOUT = json.dumps({'hookSpecificOutput': {'additionalContext': 'Cairn context:\n' + json.dumps(VIEW)}})

    def started(self, ident='h1', name='SessionStart'):
        return {'type': 'system', 'subtype': 'hook_started', 'hook_id': ident, 'hook_event': name, 'session_id': S}

    def response(self, ident='h1', name='SessionStart', stdout=None, outcome='success', code=0):
        return {'type': 'system', 'subtype': 'hook_response', 'hook_id': ident, 'hook_event': name,
                'stdout': self.STDOUT if stdout is None else stdout, 'outcome': outcome, 'exit_code': code, 'session_id': S}

    def project(self, events, **override):
        reader, sink = composed(None, prof=profile(hooks='project', **override), projector=tsd.hook_delivery)
        return run(reader, events)

    def test_guard_refuse_policy_and_default_reader_reject_hook_events(self):
        for build in (lambda: Frames(S, M, V), lambda: composed(None)[0]):
            reader = build()
            feed_all(reader, [init([SEARCH, PULL]) if reader.profile is None else init()])
            with self.assertRaises(FrameError) as caught:
                reader.feed(line(self.started()))
            self.assertEqual(str(caught.exception), 'hook_event_unexpected')

    def test_guard_policy_and_projector_must_agree(self):
        with self.assertRaises(FrameError):
            Frames(S, M, V, profile=profile(hooks='project'), actions=pristine())          # projector missing
        with self.assertRaises(FrameError):
            Frames(S, M, V, profile=profile(), actions=pristine(), hook_projector=tsd.hook_delivery)   # not allowed
        with self.assertRaises(FrameError):
            profile(hooks='drop')                                                           # no silent policy

    def test_fix_projected_hook_delivery_is_retained_without_stdout(self):
        done = self.project([self.started(), init(), self.response(), message('user', 'task prompt'), final()])
        hooks = done['hooks']
        self.assertEqual((hooks['policy'], hooks['status'], hooks['failures']), ('project', 'observed', 0))
        row = hooks['rows'][0]
        self.assertEqual(row['event'], 'SessionStart')
        self.assertEqual(row['delivery']['items'][0]['record_id'], '20000000-0000-4000-8000-000000000001')
        self.assertEqual(row['stdout_sha256'], hashlib.sha256(self.STDOUT.encode()).hexdigest())
        self.assertEqual(hooks['stdout_bytes_total'], len(self.STDOUT.encode()))
        self.assertNotIn('hook body', json.dumps(done))
        self.assertIn('no offered-handle linkage', hooks['basis'])

    def test_fix_failed_unfinished_mismatched_duplicate_and_anonymous_hooks_are_flagged_not_hidden(self):
        done = self.project([self.started(), init(), self.response(outcome='error', code=1), final()])
        self.assertEqual((done['hooks']['failures'], done['hooks']['rows'][0]['delivery']), (1, None))
        cases = {'unfinished_native_hooks': [self.started(), init(), final()],
                 'native_hook_event_mismatch': [self.started(), init(), self.response(name='UserPromptSubmit'), final()],
                 'duplicate_native_hook': [self.started(), init(), self.response(), self.response(), final()],
                 'native_hook_identity_missing': [init(), {'type': 'system', 'subtype': 'hook_response', 'session_id': S}, final()]}
        for flag, events in cases.items():
            with self.subTest(flag=flag):
                done = self.project(events)
                self.assertEqual(done['hooks']['status'], 'unknown')
                self.assertIn(flag, done['hooks']['unknown'])

    def test_guard_policy_is_part_of_the_profile_hash(self):
        self.assertNotEqual(profile().sha256, profile(hooks='project').sha256)
        self.assertEqual(profile().document['hooks'], 'refuse')


class FinalContractAndBudgets(unittest.TestCase):
    def test_fix_text_final_answer_contract(self):
        reader, _ = composed(None, prof=profile(final_kind='text'))
        done = run(reader, [init(), final(FINAL_TEXT)])
        work = done['final']['work_product']
        self.assertEqual((work['kind'], work['utf8_bytes']), ('text', len(FINAL_TEXT.encode())))
        self.assertEqual(work['sha256'], hashlib.sha256(FINAL_TEXT.encode()).hexdigest())
        for text, code in [('', 'work_product_empty'), ('x' * 8193, 'work_product_bounds')]:
            reader, _ = composed(None, prof=profile(final_kind='text'))
            with self.assertRaises(FrameError) as caught:
                run(reader, [init(), final(text)])
            self.assertEqual(str(caught.exception), code)

    def test_guard_json_contract_is_the_default_and_text_is_not_accepted_as_json(self):
        reader, _ = composed(None)
        with self.assertRaises(FrameError) as caught:
            run(reader, [init(), final(FINAL_TEXT)])
        self.assertEqual(str(caught.exception), 'invalid_frame_json')

    def test_guard_original_numeric_budgets_are_constants_not_parameters(self):
        self.assertEqual(PINNED_BUDGETS, {'memory_bytes': 9500, 'final_bytes': 8192, 'tool_room_bytes': 32000,
                                          'native_searches': 2, 'pulls': 4})
        names = set(inspect.signature(Profile.__init__).parameters)
        self.assertFalse({n for n in names if 'memory' in n or 'budget' in n or 'search' in n or 'pull' in n or 'room' in n})
        with self.assertRaises(FrameError):
            profile(final_max_bytes=8193)
        wide = profile(max_total_bytes=200 * 1024 * 1024, max_line_bytes=64 * 1024 * 1024, max_frames=100000)
        self.assertEqual(wide.document['budgets'], PINNED_BUDGETS)   # wider stream caps are not budgets
        self.assertEqual(wide.document['stream_caps']['frames'], 100000)

    def test_fix_budget_check_reports_without_dropping_evidence(self):
        reader, _ = composed(None)
        done = run(reader, [init()] + memory_calls() + [final()])
        self.assertEqual(done['budget_check']['violations'], [])
        self.assertEqual(done['budget_check']['observed'], {'searches': 1, 'pulls': 1})
        reader, _ = composed(None)
        done = run(reader, [init()] + memory_calls(budget=9501) + [final()])
        self.assertEqual(done['budget_check']['violations'], ['search_budget_above_original'])
        reader, _ = composed(None)
        done = run(reader, [init()] + memory_calls(budget=None) + [final()])
        self.assertEqual(done['budget_check']['violations'], ['search_without_memory_budget'])

    def test_fix_call_count_over_the_original_limit_is_a_violation(self):
        events = [init()]
        for i in range(3):
            args = {'request_id': '40000000-0000-4000-8000-00000000010%d' % i, 'query': 'q', 'memory_budget_bytes': 9500}
            events += [message('assistant', [use_block('s%d' % i, SEARCH, args)]),
                       message('user', [result_block('s%d' % i, [{'type': 'text', 'text': json.dumps(
                           dict(search(BODY), request_id=args['request_id']))}])])]
        reader, _ = composed(None)
        done = run(reader, events + [final()])
        self.assertEqual(done['budget_check']['violations'], ['memory_call_limit'])
        reader = Frames(S, M, V)
        self.assertIsNone(run(reader, [init([SEARCH, PULL]), final()])['budget_check'])


if __name__ == '__main__':
    unittest.main()
