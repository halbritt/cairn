"""Fixtures for the V4 corrections requested by root's independent native-v2 source review.

(1) classify_attempt must keep every independent terminal fact and bind streams/attempts explicitly;
(2) TerminalProbe line boundary and pending tail; (3) terminal denials reconcile the matched action;
(4) closed hook outcome vocabulary, no fabricated exit code; (5) JSON-object final, 8192 unchanged;
(6) stream digest. Constructed events only; main-line modules are imported read-only.
"""
import copy
from pathlib import Path
import hashlib
import json
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .. import frames
from .. import terminal_seam as seam

from . import test_selected_coding as t
import trial_native_observation as tno
import trial_source_delivery as tsd
from ..frames import FrameError, Frames, PINNED_BUDGETS

S = t.S
ATTEMPT = '90000000-0000-4000-8000-000000000001'
BINDING = {'attempt_id': ATTEMPT, 'session_id': S, 'custody_bound': True}
CUSTODY = {'schema': 'cairn.native-process-custody/1', 'classification': 'completed', 'legacy_exit_code': 0,
           'observed_returncode': 0, 'observed_wait_status': 0, 'input_bytes': 100, 'input_bytes_written': 100,
           'cleanup': {'status': 'observed_clear'}, 'leader_reaped': True}


def success_events(final=None):
    return [t.init(), t.message('assistant', [t.use_block('a', 'Read', {'file_path': 'x'})]),
            t.message('user', [t.result_block('a', 'ok')]), final or t.final()]


def drive(events, prof=None, projector=None):
    """Feed the same bytes to the success-only reader and to a TerminalProbe, like the external owner would."""
    reader, sink = t.composed(None, prof=prof, projector=projector)
    probe, error = seam.TerminalProbe(S), None
    for event in events:
        raw = t.line(event)
        for start in range(0, len(raw), 65536):
            chunk = raw[start:start + 65536]
            probe.feed(chunk)
            if error is None:
                try:
                    reader.feed(chunk)
                except FrameError as exc:
                    error = str(exc)
    done = None
    if error is None:
        try:
            done = reader.finish(0)
        except FrameError as exc:
            error = str(exc)
    status = {'status': 'ok' if error is None else 'refused', 'code': error}
    return reader, sink, done, dict(status, **reader.stream_digest()), probe


def route(frames_outcome, probe_report, custody=CUSTODY, binding=BINDING):
    return seam.classify_attempt(custody, frames_outcome, probe_report, binding)


class ClassifierKeepsTerminalFacts(unittest.TestCase):
    def happy(self):
        reader, sink, done, outcome, probe = drive(success_events())
        return outcome, probe.report()


    def test_fix_consistent_trio_is_the_only_route_to_grading(self):
        outcome, report = self.happy()
        routed = route(outcome, report)
        self.assertEqual((routed['attempt_class'], routed['observations']), ('graded_candidate', []))
        self.assertEqual(routed['schema'], 'cairn.attempt-route/2')
        self.assertIn('not derived here', routed['basis'])

    def test_fix_each_defective_terminal_report_becomes_a_typed_unknown(self):
        outcome, report = self.happy()
        cases = [('terminal=None', None, 'terminal_report_missing'),
                 ('wrong schema', dict(report, schema='x'), 'terminal_report_invalid'),
                 ('overflow', dict(report, overflow=True), 'terminal_overflow'),
                 ('malformed line', dict(report, malformed_lines=1), 'terminal_malformed_lines'),
                 ('two results', dict(report, result_events=2), 'terminal_multiple_results'),
                 ('no result', dict(report, result_seen=False, result_events=0), 'terminal_result_missing'),
                 ('pending tail', dict(report, pending_bytes=7), 'terminal_unterminated_tail'),
                 ('result without session id', dict(report, session_match=None), 'terminal_session_unobserved')]
        for label, terminal, observation in cases:
            with self.subTest(case=label):
                routed = route(outcome, terminal)
                self.assertEqual(routed['attempt_class'], 'harness_unknown')
                self.assertIn(observation, routed['observations'])

    def test_fix_stream_and_attempt_binding_must_be_explicit_and_consistent(self):
        outcome, report = self.happy()
        no_session = {k: v for k, v in BINDING.items() if k != 'attempt_id'}
        cases = [('different bytes', dict(outcome, stream_sha256='0' * 64), report, BINDING, 'stream_binding_mismatch'),
                 ('different byte count', dict(outcome, stream_bytes=outcome['stream_bytes'] + 1), report, BINDING, 'stream_binding_mismatch'),
                 ('frames without digest', {'status': 'ok', 'code': None}, report, BINDING, 'frames_binding_missing'),
                 ('terminal for another session', outcome, dict(report, session_match=False), BINDING, 'terminal_session_mismatch'),
                 ('no binding', outcome, report, None, 'attempt_binding_missing'),
                 ('binding without attempt', outcome, report, no_session, 'attempt_binding_missing'),
                 ('binding for another session', outcome, report, dict(BINDING, session_id='70000000-0000-4000-8000-000000000099'), 'attempt_binding_mismatch'),
                 ('custody binding not asserted', outcome, report, dict(BINDING, custody_bound=False), 'custody_binding_unasserted'),
                 ('custody binding absent', outcome, report, {k: v for k, v in BINDING.items() if k != 'custody_bound'}, 'custody_binding_unasserted'),
                 ('success stream with an error result', outcome, dict(report, is_error=True), BINDING, 'frames_terminal_inconsistent')]
        for label, o, terminal, binding, observation in cases:
            with self.subTest(case=label):
                routed = route(o, terminal, binding=binding)
                self.assertEqual(routed['attempt_class'], 'harness_unknown')
                self.assertIn(observation, routed['observations'])

    def test_fix_all_applicable_observations_are_listed_together(self):
        outcome, report = self.happy()
        routed = route(outcome, dict(report, overflow=True, malformed_lines=3, result_events=2), binding=None)
        for observation in ('terminal_overflow', 'terminal_malformed_lines', 'terminal_multiple_results', 'attempt_binding_missing'):
            self.assertIn(observation, routed['observations'])

    def test_fix_process_failure_and_provider_stop_keep_their_routes(self):
        outcome, report = self.happy()
        timeout = dict(CUSTODY, classification='timeout', legacy_exit_code=124, observed_returncode=-15)
        killed = dict(report, result_seen=False, result_events=0, session_match=None)     # no result is expected after a kill
        routed = route({'status': 'refused', 'code': 'incomplete_stream_or_exit', **{k: outcome[k] for k in ('stream_sha256', 'stream_bytes', 'session_id')}},
                       killed, custody=timeout)
        self.assertEqual(routed['attempt_class'], 'process_failure')
        self.assertIn('terminal_result_missing', routed['observations'])
        weekly = "You've hit your weekly limit · resets Oct 12, 3am"
        events = [t.init(), dict(t.message('assistant', [t.block('text', text=weekly)]), error='rate_limit'),
                  {'type': 'result', 'session_id': S, 'subtype': 'success', 'is_error': True, 'terminal_reason': 'api_error',
                   'api_error_status': 429, 'result': weekly, 'num_turns': 1}]
        reader, sink, done, o429, probe429 = drive(events)
        stop = route(o429, probe429.report(), custody=dict(CUSTODY, observed_returncode=1))
        self.assertEqual((stop['attempt_class'], stop['admission_stop_observed']), ('provider_admission_stop', True))
        broken = dict(probe429.report(), malformed_lines=1)
        degraded = route(o429, broken, custody=dict(CUSTODY, observed_returncode=1))
        self.assertEqual(degraded['attempt_class'], 'process_failure')            # exit 1 is still a process failure ...
        self.assertTrue(degraded['admission_stop_observed'])                      # ... and the stop signal is never lost
        degraded = route(o429, broken)
        self.assertEqual(degraded['attempt_class'], 'harness_unknown')
        self.assertTrue(degraded['admission_stop_observed'])

    def test_guard_custody_unknowns_still_win(self):
        outcome, report = self.happy()
        for custody, observation in [(None, 'custody_missing'), (dict(CUSTODY, input_bytes_written=99), 'input_delivery_incomplete'),
                                     (dict(CUSTODY, cleanup={'status': 'unknown'}), 'process_custody_unknown'),
                                     (dict(CUSTODY, observed_returncode=None), 'exit_unobserved')]:
            routed = route(outcome, report, custody=custody)
            self.assertEqual(routed['attempt_class'], 'harness_unknown')
            self.assertIn(observation, routed['observations'])

    def test_guard_zero_exit_and_frames_ok_alone_never_grade(self):
        routed = route({'status': 'ok', 'code': None}, None, binding=BINDING)
        self.assertEqual(routed['attempt_class'], 'harness_unknown')
        self.assertEqual(route({'status': 'not_run', 'code': None}, self.happy()[1])['attempt_class'], 'frames_refused')   # nothing to grade


class ProbeBoundaries(unittest.TestCase):
    def line_of(self, size):
        pad = size - len(b'{"type":"x","p":""}')
        return b'{"type":"x","p":"' + b'a' * pad + b'"}'

    def feed(self, probe, data):
        for start in range(0, len(data), 65536):
            probe.feed(data[start:start + 65536])


    def test_fix_line_limit_is_exact_even_when_the_newline_is_in_the_same_or_last_chunk(self):
        for size, overflow in [(seam.LINE, False), (seam.LINE + 1, True)]:
            for tail in (b'\n', b'\n' + t.line({'type': 'rate_limit_event'})):
                probe = seam.TerminalProbe(S)
                self.feed(probe, self.line_of(size) + tail)
                with self.subTest(size=size, tail=len(tail)):
                    self.assertEqual(probe.report()['overflow'], overflow)

    def test_fix_pending_unterminated_tail_is_reported_not_ignored(self):
        probe = seam.TerminalProbe(S)
        probe.feed(t.line(t.final()) + b'{"type":"x"')
        report = probe.report()
        self.assertEqual((report['result_events'], report['pending_bytes']), (1, len(b'{"type":"x"')))
        outcome = {'status': 'ok', 'code': None, **{'stream_sha256': report['stream_sha256'], 'stream_bytes': report['stream_bytes'], 'session_id': S}}
        self.assertIn('terminal_unterminated_tail', route(outcome, report)['observations'])

    def test_fix_total_and_chunk_bounds_set_overflow_and_the_digest_still_covers_accepted_chunks(self):
        probe = seam.TerminalProbe(S)
        probe.feed(b'x' * 65537)
        self.assertTrue(probe.report()['overflow'])
        self.assertEqual(probe.report()['stream_bytes'], 0)
        with self.assertRaises(ValueError):
            seam.TerminalProbe('not-a-uuid')

    def test_guard_malformed_and_nonobject_lines_are_counted(self):
        probe = seam.TerminalProbe(S)
        probe.feed(b'garbage\n[1]\n' + t.line(t.final()))
        report = probe.report()
        self.assertEqual((report['malformed_lines'], report['result_events']), (2, 1))


class StreamDigest(unittest.TestCase):
    def test_fix_digest_is_available_after_a_refusal_and_matches_the_probe_for_the_same_bytes(self):
        events = [t.init(), t.message('assistant', [t.use_block('a', 'WebFetch', {})])]
        reader, sink, done, outcome, probe = drive(events)
        self.assertEqual(outcome['status'], 'refused')
        self.assertEqual({k: outcome[k] for k in ('stream_sha256', 'stream_bytes')},
                         {k: probe.report()[k] for k in ('stream_sha256', 'stream_bytes')})
        raw = b''.join(t.line(e) for e in events)
        self.assertEqual(outcome['stream_sha256'], hashlib.sha256(raw).hexdigest())

    def test_fix_finish_output_carries_the_digest(self):
        reader, sink, done, outcome, probe = drive(success_events())
        self.assertEqual(done['stream_sha256'], outcome['stream_sha256'])


class TerminalDenials(unittest.TestCase):
    DENIAL = {'tool_name': 'Read', 'tool_use_id': 'a', 'tool_input': {'file_path': 'SENTINEL'}}

    def states(self, sink):
        return [(row['tool'], row['state']) for row in sink.actions]


    def test_fix_matched_denial_reconciles_the_action_like_the_main_line_observer(self):
        events = success_events(t.final(permission_denials=[self.DENIAL]))
        reader, sink, done, outcome, probe = drive(events)
        self.assertEqual(self.states(sink), [('Read', 'tool_error')])
        self.assertEqual(done['final']['permission_denial_facts'],
                         [{'tool': 'Read', 'matched_call': True, 'tool_use_id_sha256': hashlib.sha256(b'a').hexdigest()}])
        self.assertEqual(done['final']['reported_permission_denials'], 1)
        self.assertNotIn('SENTINEL', json.dumps(done))
        lines = [json.dumps(e) + '\n' for e in events]
        timings = [dict(line=i + 1, received_seconds=float(i), bytes=len(x.encode()), sha256=hashlib.sha256(x.encode()).hexdigest())
                   for i, x in enumerate(lines)]
        measured = tno.measure(''.join(lines), timings, [], input_bytes=1000, expects_hooks=False, model=t.M, prompt='task prompt',
                               workspace=t.WORKSPACE, public_validation_commands=t.COMMANDS)
        self.assertEqual([(a['tool'], a['state']) for a in measured['public_actions']['actions']], self.states(sink))

    def test_fix_unmatched_nondict_and_wrong_tool_denials_are_facts_but_do_not_change_actions(self):
        denials = [{'tool_name': 'Read', 'tool_use_id': 'ghost'}, 'junk', {'tool_name': 'Bash', 'tool_use_id': 'a'},
                   {'tool_name': 'NotATool', 'tool_use_id': 'a'}]
        reader, sink, done, outcome, probe = drive(success_events(t.final(permission_denials=denials)))
        self.assertEqual(self.states(sink), [('Read', 'tool_success')])
        facts = done['final']['permission_denial_facts']
        self.assertEqual([f['tool'] for f in facts], ['Read', 'unknown', 'Bash', 'unknown'])
        self.assertEqual([f['matched_call'] for f in facts], [False, False, False, False])    # id 'a' belongs to a Read call, not Bash

    def test_fix_failed_result_frame_restores_the_sink_including_the_denial_item(self):
        events = success_events(t.final(permission_denials=[self.DENIAL], is_error=True))
        reader, sink, done, outcome, probe = drive(events)
        self.assertEqual(outcome['code'], 'native_result_failed')
        self.assertEqual(self.states(sink), [])

    def test_guard_sink_must_support_reconciliation_and_default_reader_is_unchanged(self):
        class NoDenials:
            def consume(self, *a): pass
            def finish(self): return {}
        with self.assertRaises(FrameError):
            Frames(S, t.M, t.V, profile=t.profile(), actions=NoDenials())
        reader = Frames(S, t.M, t.V)
        done = t.run(reader, [t.init([frames.SEARCH, frames.PULL]), t.final(permission_denials=[self.DENIAL])])
        self.assertIsNone(done['final']['permission_denial_facts'])
        self.assertEqual(done['final']['reported_permission_denials'], 1)


MISSING = object()


class HookOutcomeVocabulary(unittest.TestCase):
    VIEW = t.HookChannel.STDOUT

    def hook(self, **fields):
        base = {'type': 'system', 'subtype': 'hook_response', 'hook_id': 'h1', 'hook_event': 'SessionStart', 'session_id': S,
                'stdout': self.VIEW, 'outcome': 'success', 'exit_code': 0}
        for key, value in fields.items():
            if value is MISSING:
                base.pop(key, None)
            else:
                base[key] = value
        return base

    def run_hook(self, **fields):
        started = {'type': 'system', 'subtype': 'hook_started', 'hook_id': 'h1', 'hook_event': 'SessionStart', 'session_id': S}
        reader, sink = t.composed(None, prof=t.profile(hooks='project'), projector=tsd.hook_delivery)
        return t.run(reader, [started, t.init(), self.hook(**fields), t.final()])['hooks']

    def test_fix_outcome_strings_are_a_closed_vocabulary(self):
        for value, expected in [('success', 'success'), ('error', 'non_success'), ('x' * 31, 'non_success'),
                                ('SENTINEL-OUTCOME', 'non_success'), (None, 'missing'), (7, 'missing')]:
            hooks = self.run_hook(outcome=value)
            with self.subTest(value=value):
                self.assertEqual(hooks['rows'][0]['outcome'], expected)
                self.assertNotIn('SENTINEL-OUTCOME', json.dumps(hooks))

    def test_fix_missing_or_invalid_exit_code_is_unobserved_never_zero(self):
        for code in (MISSING, None, True, 300, -129, '0'):
            hooks = self.run_hook(exit_code=code)
            with self.subTest(code=code):
                self.assertIsNone(hooks['rows'][0]['exit_code'])
                self.assertIn('native_hook_exit_unobserved', hooks['unknown'])
                self.assertEqual(hooks['status'], 'unknown')
                self.assertIsNone(hooks['rows'][0]['delivery'])          # an unobserved exit cannot support a delivery claim
                self.assertEqual(hooks['failures'], 0)

    def test_fix_delivery_is_distinct_from_the_exit_and_never_claimed_for_a_failed_hook(self):
        ok = self.run_hook()
        self.assertEqual(ok['rows'][0]['delivery']['items'][0]['version'], 3)
        self.assertEqual((ok['status'], ok['failures']), ('observed', 0))
        failed = self.run_hook(exit_code=1)
        self.assertEqual((failed['failures'], failed['rows'][0]['exit_code'], failed['rows'][0]['delivery']), (1, 1, None))
        self.assertEqual(failed['rows'][0]['stdout_bytes'], len(self.VIEW.encode()))          # origin/bytes are still recorded
        negative = self.run_hook(exit_code=-1)
        self.assertEqual((negative['failures'], negative['rows'][0]['exit_code']), (1, -1))
        nonsuccess = self.run_hook(outcome='error', exit_code=0)
        self.assertEqual((nonsuccess['failures'], nonsuccess['rows'][0]['delivery']), (1, None))


class JsonFinalTemplate(unittest.TestCase):
    KEYS = ('commit', 'design', 'worktree')

    def prof(self, **kw):
        return t.profile(final_kind='json_object', final_required_keys=self.KEYS, **kw)

    def test_fix_json_object_is_the_template_and_required_keys_are_enforced(self):
        good = json.dumps({'worktree': '/w', 'commit': 'abc', 'design': '/w/DESIGN.md', 'extra': 1})
        reader, _ = t.composed(None, prof=self.prof())
        done = t.run(reader, [t.init(), t.final(good)])
        self.assertEqual(sorted(done['final']['work_product']), ['commit', 'design', 'extra', 'worktree'])
        for text, code in [(json.dumps({'worktree': '/w'}), 'work_product_keys'), ('worktree=/w', 'invalid_frame_json'),
                           ('[1]', 'frame_not_object'), (json.dumps({'k': 'x' * 8192}), 'work_product_bounds')]:
            reader, _ = t.composed(None, prof=self.prof())
            with self.subTest(text=text[:20]), self.assertRaises(FrameError) as caught:
                t.run(reader, [t.init(), t.final(text)])
            self.assertEqual(str(caught.exception), code)

    def test_guard_budget_stays_8192_and_keys_cannot_be_combined_with_text(self):
        self.assertEqual(PINNED_BUDGETS['final_bytes'], 8192)
        with self.assertRaises(FrameError):
            t.profile(final_max_bytes=8193)
        with self.assertRaises(FrameError):
            t.profile(final_kind='text', final_required_keys=('a',))
        with self.assertRaises(FrameError):
            t.profile(final_required_keys=('',))
        self.assertNotEqual(self.prof().sha256, t.profile().sha256)
        self.assertEqual(self.prof().document['final'], {'kind': 'json_object', 'max_bytes': 8192, 'required_keys': list(self.KEYS)})


if __name__ == '__main__':
    unittest.main()
