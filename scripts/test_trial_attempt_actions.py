"""Constructed failed streams; no provider, process launch or grading."""
import hashlib
import json
import unittest
from unittest.mock import patch

from selected_native.frames import Frames, Profile, FrameError
from selected_native.evidence import SEARCH, PULL
from trial_attempt_actions import AttemptActions
from trial_native_observation import PublicActions


SESSION = '00000000-0000-4000-8000-000000000001'
TOOLS = {'Bash', 'Read', 'Edit', 'Write', 'Glob', 'Grep', SEARCH, PULL}
PRIVATE = 'PRIVATE_ATTEMPT_PAYLOAD_SENTINEL'


def event(kind, **values):
    return dict(type=kind, session_id=SESSION, **values)


def init():
    return event('system', subtype='init', model='fixture-model', claude_code_version='fixture-cli',
                 permissionMode='dontAsk', tools=sorted(TOOLS), mcp_servers=[dict(name='cairn', status='connected')])


def call(command='make check', ident='toolu_check'):
    return event('assistant', message=dict(content=[dict(type='tool_use', id=ident,
                 name='Bash', input=dict(command=command))]))


def result(denials=None):
    return event('result', subtype='error_max_turns', is_error=True, result=PRIVATE,
                 permission_denials=[] if denials is None else denials)


def wire(value):
    return json.dumps(value).encode()+b'\n'


class AttemptActionTests(unittest.TestCase):
    def observer(self, **kwargs):
        return AttemptActions(SESSION, None, {'make_check':'make check'}, **kwargs)

    def observe(self, *events):
        observer = self.observer()
        for number, value in enumerate(events):
            observer.feed(wire(value), number)
        return observer.finish()

    def test_failure_facts_survive_success_reader_rollback(self):
        actions = PublicActions(None, {'make_check':'make check'})
        profile = Profile('fixture', TOOLS, TOOLS-{SEARCH, PULL}, {'dontAsk'})
        success = Frames(SESSION, 'fixture-model', 'fixture-cli', profile=profile, actions=actions)
        failure = self.observer()
        denied = dict(tool_name='Bash', tool_use_id='toolu_check', reason=PRIVATE)
        values = [init(), call(), event('user', message=dict(content=[dict(type='tool_result',
                    tool_use_id='toolu_check', is_error=False, content=PRIVATE)])), result([denied])]
        stream = b''.join(map(wire, values))
        for number, value in enumerate(values):
            chunk = wire(value)
            failure.feed(chunk, number)
            if value['type'] == 'result':
                with self.assertRaisesRegex(FrameError, 'native_result_failed'):
                    success.feed(chunk)
            else:
                success.feed(chunk)
        self.assertEqual(actions.actions, [])
        report = failure.finish()
        self.assertEqual(report['coverage'], 'observed')
        self.assertEqual(report['stream_sha256'], hashlib.sha256(stream).hexdigest())
        self.assertEqual(report['stream_sha256'], success.stream_digest()['stream_sha256'])
        self.assertEqual(report['stream_bytes'], len(stream))
        row = report['actions']['actions'][0]
        self.assertEqual(row['state'], 'tool_error')
        self.assertEqual(report['native_reported_denials'][0]['declared_required_operation'], 'make_check')
        self.assertEqual(report['native_reported_denials'][0]['command_sha256'], hashlib.sha256(b'make check').hexdigest())
        self.assertEqual(report['native_reported_denials'][0]['prior_reported_action_state'], 'tool_success')
        self.assertNotIn(PRIVATE, json.dumps(report))
        self.assertFalse(report['runtime_authenticated'])
        self.assertFalse(report['validity_decided'])
        self.assertFalse(report['task_acceptance'])

    def test_compound_command_category_cannot_match_required_operation(self):
        denied = dict(tool_name='Bash', tool_use_id='toolu_check')
        report = self.observe(init(), call('make check && '+PRIVATE), result([denied]))
        self.assertIsNone(report['native_reported_denials'][0]['declared_required_operation'])
        self.assertEqual(report['actions']['actions'][0]['command_category'], 'make_check')
        self.assertEqual(report['actions']['actions'][0]['shell_form'], 'compound_or_redirect')
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_conflicting_native_denial_input_cannot_supply_required_match(self):
        for data in ({'command':PRIVATE}, {}, None):
            with self.subTest(data=data):
                denial = dict(tool_name='Bash', tool_use_id='toolu_check', tool_input=data)
                report = self.observe(init(), call(), result([denial]))
                self.assertEqual(report['coverage'], 'unknown')
                fact = report['native_reported_denials'][0]
                self.assertTrue(fact['matched_call'])
                self.assertEqual(fact['reported_command_consistency'], 'conflicting')
                self.assertIsNone(fact['declared_required_operation'])
                self.assertNotIn(PRIVATE, json.dumps(report))
        denial = dict(tool_name='Bash', tool_use_id='toolu_check', tool_input=dict(command='make check'))
        fact = self.observe(init(), call(), result([denial]))['native_reported_denials'][0]
        self.assertEqual(fact['reported_command_consistency'], 'matched')
        self.assertEqual(fact['declared_required_operation'], 'make_check')

    def test_exact_declared_compound_is_a_declaration_match_without_authority(self):
        command = 'make check && true'
        observer = AttemptActions(SESSION, None, {'make_check':command})
        for number, value in enumerate((init(), call(command),
                result([dict(tool_name='Bash', tool_use_id='toolu_check')]))):
            observer.feed(wire(value), number)
        report = observer.finish()
        self.assertEqual(report['native_reported_denials'][0]['declared_required_operation'], 'make_check')
        self.assertEqual(report['actions']['actions'][0]['shell_form'], 'compound_or_redirect')
        self.assertFalse(report['runtime_authenticated'])
        self.assertFalse(report['validity_decided'])

    def test_wrong_tool_unknown_id_and_ambiguous_id_never_match(self):
        for calls, denial in (([call()], dict(tool_name='Read', tool_use_id='toolu_check')),
                              ([call()], dict(tool_name='Bash', tool_use_id='toolu_other')),
                              ([call(), call()], dict(tool_name='Bash', tool_use_id='toolu_check'))):
            with self.subTest(denial=denial, calls=len(calls)):
                report = self.observe(init(), *calls, result([denial]))
                self.assertEqual(report['coverage'], 'unknown')
                self.assertFalse(report['native_reported_denials'][0]['matched_call'])
                self.assertIsNone(report['native_reported_denials'][0]['declared_required_operation'])

    def test_actor_absence_and_unfinished_action_do_not_imply_observer_loss(self):
        empty = self.observe(init(), result())
        self.assertEqual(empty['coverage'], 'observed')
        self.assertIn('no_observed_actions', empty['actions']['unknown'])
        incomplete = self.observe(init(), call())
        self.assertEqual(incomplete['coverage'], 'observed')
        self.assertEqual(incomplete['terminal_events'], 0)
        self.assertIn('unfinished_actions', incomplete['actions']['unknown'])

    def test_malformed_frame_preserves_prior_facts_and_marks_coverage_unknown(self):
        observer = self.observer()
        observer.feed(wire(init())+wire(call()), 0)
        observer.feed(b'{"secret":"'+PRIVATE.encode()+b'"\n', 1)
        report = observer.finish()
        self.assertEqual(len(report['actions']['actions']), 1)
        self.assertIn('malformed_event', report['unknown'])
        self.assertNotIn(PRIVATE, json.dumps(report))

    def test_bad_part_rolls_back_whole_event_not_prior_frames(self):
        observer = self.observer()
        observer.feed(wire(init())+wire(call()), 0)
        bad = call('go test ./...', 'toolu_new')
        bad['message']['content'].append(dict(type='unexpected', text=PRIVATE))
        observer.feed(wire(bad), 1)
        report = observer.finish()
        self.assertEqual(len(report['actions']['actions']), 1)
        self.assertIn('projection_event_unknown', report['unknown'])

    def test_duplicate_json_keys_nonfinite_numbers_and_surrogates_refuse(self):
        for raw in (b'{"type":"assistant","type":"user"}\n',
                    b'{"value":NaN}\n', b'{"value":"\\ud800"}\n'):
            with self.subTest(raw=raw):
                observer = self.observer()
                observer.feed(wire(init()), 0)
                observer.feed(raw, 1)
                self.assertIn('malformed_event', observer.finish()['unknown'])

    def test_byte_line_and_frame_bounds_stop_projection_keep_digest(self):
        for limits in ({'total_limit':400, 'line_limit':400},
                       {'line_limit':10}, {'frame_limit':1}):
            with self.subTest(limits=limits):
                observer = self.observer(**limits)
                first = wire(init())+wire(call())
                observer.feed(first, 0)
                observer.feed(wire(result()), 1)
                report = observer.finish()
                self.assertEqual(report['coverage'], 'unknown')
                self.assertEqual(report['stream_sha256'], hashlib.sha256(first+wire(result())).hexdigest())
                self.assertLessEqual(len(report['actions']['actions']), 1)

    def test_chunk_bound_unterminated_tail_and_missing_init_are_unknown(self):
        for chunk in (b'x'*65537, b'{}', wire(call())):
            with self.subTest(size=len(chunk)):
                observer = self.observer()
                observer.feed(chunk, 0)
                self.assertEqual(observer.finish()['coverage'], 'unknown')

    def test_denial_bounds_do_not_publish_a_partial_denial_batch(self):
        report = self.observe(init(), call(), result([dict(tool_name='Bash', tool_use_id='toolu_check')]*257))
        self.assertEqual(report['native_reported_denials'], [])
        self.assertIn('projection_event_unknown', report['unknown'])

    def test_duplicate_denials_and_prior_reported_exit_remain_observations(self):
        completion = event('user', message=dict(content=[dict(type='tool_result',
                           tool_use_id='toolu_check', is_error=False, content=PRIVATE)]),
                           tool_use_result=dict(exit_code=0))
        denied = dict(tool_name='Bash', tool_use_id='toolu_check')
        report = self.observe(init(), call(), completion, result([denied, denied]))
        self.assertIn('duplicate_denial', report['unknown'])
        self.assertEqual(len(report['native_reported_denials']), 2)
        for fact in report['native_reported_denials']:
            self.assertEqual(fact['prior_reported_action_state'], 'tool_success')
            self.assertEqual(fact['prior_reported_exit_code'], 0)
        self.assertEqual(report['actions']['actions'][0]['state'], 'tool_error')
        self.assertIsNone(report['actions']['actions'][0]['exit_code'])

    def test_session_mismatch_and_backwards_timing_preserve_prior_evidence(self):
        observer = self.observer()
        observer.feed(wire(init())+wire(call()), 5)
        wrong = result()
        wrong['session_id'] = '00000000-0000-4000-8000-000000000002'
        observer.feed(wire(wrong), 4)
        report = observer.finish()
        self.assertEqual(len(report['actions']['actions']), 1)
        self.assertIn('session_binding_unknown', report['unknown'])
        self.assertIn('capture_timing_unknown', report['unknown'])

    def test_event_after_result_remains_an_unknown_with_all_observed_facts(self):
        report = self.observe(init(), result(), call())
        self.assertIn('event_after_result', report['unknown'])
        self.assertEqual(len(report['actions']['actions']), 1)

    def test_projection_fault_and_interrupt_do_not_mutate_committed_state(self):
        for fault in (ValueError('private fault'), KeyboardInterrupt(), RuntimeError(PRIVATE)):
            observer = self.observer()
            observer.feed(wire(init())+wire(call()), 0)
            with patch.object(PublicActions, 'consume', side_effect=fault):
                if isinstance(fault, (KeyboardInterrupt, RuntimeError)):
                    with self.assertRaises(type(fault)):
                        observer.feed(wire(call('go test ./...', 'toolu_new')), 1)
                else:
                    observer.feed(wire(call('go test ./...', 'toolu_new')), 1)
            report = observer.finish()
            self.assertEqual(len(report['actions']['actions']), 1)
            self.assertEqual(report['coverage'], 'unknown')
            self.assertNotIn(PRIVATE, json.dumps(report))

    def test_configuration_is_bounded_and_finish_is_one_use(self):
        for commands in ({'secret':'make check'}, {'make_check':'\ud800'},
                         {'make_check':'same', 'go_test':'same'}):
            with self.assertRaises(ValueError):
                AttemptActions(SESSION, None, commands)
        with self.assertRaises(ValueError):
            self.observer(total_limit=10, line_limit=11)
        observer = self.observer()
        observer.finish()
        with self.assertRaises(ValueError):
            observer.feed(b'', 0)
        with self.assertRaises(ValueError):
            observer.finish()


if __name__ == '__main__':
    unittest.main()
