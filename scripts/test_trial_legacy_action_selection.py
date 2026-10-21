"""Synthetic streams through conversion/draft only; no actor or grader calls."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_blind_packet_draft as packet
import trial_legacy_blind_selection as legacy
import trial_task_eval as task_eval
import trial_task_permissions as permissions
import trial_legacy_grading_bundle as bundle
import test_trial_legacy_grading_bundle as bundle_fixture
from test_trial_legacy_blind_selection import Fixture, C, pin, NOW


def reference(path):
    raw = Path(path).read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


class ActionSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.f = Fixture(self.root, outcomes={('case-a', 0): ('correct', 'mistake')},
                         tasks={'case-a': 'Synthetic task.\n'})
        self.output = self.root/'conversion'

    def request(self, channels, *, raw=None, harness='claude', answer=None):
        for contract in self.f.contracts.values():
            contract['required_channels'] = channels
        self.f.save()
        if raw is not None:
            for record in self.f.records:
                record.update(stream_sha256=hashlib.sha256(raw).hexdigest(), harness=harness)
                if answer is not None:
                    record['answer_chars'] = len(answer)
                base = self.f.run/'runs'/record['run_id']
                (base/'stream.jsonl').write_bytes(raw)
                (base/'result.json').write_text(json.dumps(record))
            agent_path = self.f.run/'agent.json'
            agent = json.loads(agent_path.read_bytes()); agent['records'] = self.f.records
            self.f.request['agent'] = pin(agent_path, json.dumps(agent).encode())
        self.f.request.update(schema='cairn.legacy-blind-conversion/2', action_parser=dict(
            schema='cairn.legacy-action-parser/1', task_eval=reference(task_eval.__file__),
            task_permissions=reference(permissions.__file__)))
        plan_path = self.f.run/'plan.json'
        plan = json.loads(plan_path.read_bytes())
        plan['evaluator_sha256'] = self.f.request['action_parser']['task_eval']['sha256']
        self.f.request['plan'] = pin(plan_path, json.dumps(plan).encode())
        binding_path = Path(self.f.request['binding']['path'])
        binding = json.loads(binding_path.read_bytes())
        binding['plan_sha256'] = self.f.request['plan']['sha256']
        binding['agent_sha256'] = self.f.request['agent']['sha256']
        self.f.request['binding'] = pin(binding_path, C(binding))
        return self.f.resave_request()

    def reseal_plan(self, plan):
        self.f.request['plan'] = pin(self.f.run/'plan.json', json.dumps(plan).encode())
        path = Path(self.f.request['binding']['path'])
        binding = json.loads(path.read_bytes()); binding['plan_sha256'] = self.f.request['plan']['sha256']
        self.f.request['binding'] = pin(path, C(binding))
        return self.f.resave_request()

    def refused(self, request, code):
        with self.assertRaisesRegex(legacy.paired.Refusal, '^'+code+'$'):
            legacy.convert(request, self.output)
        self.assertFalse(self.output.exists())

    def draft(self, receipt):
        subject = packet.approval_subject(receipt['selection'])
        approval = dict(schema='cairn.blind-declared-approval/2', state='approved',
                        subject_sha256=hashlib.sha256(C(subject)).hexdigest(),
                        observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
                        issuer_role=pin(self.root/'issuer', b'synthetic'))
        packet.draft(receipt['selection'], pin(self.root/'approval.json', C(approval)), self.root/'draft', now=NOW)
        return [json.loads(path.read_bytes()) for path in sorted((self.root/'draft/public/samples').glob('*.json'))]

    def test_action_only_contract_composes_without_answer_or_extra_channels(self):
        request = self.request(['task_statement', 'grading_instructions', 'action_trace'])
        receipt = legacy.convert(request, self.output)
        for sample in self.draft(receipt):
            self.assertEqual([c['kind'] for c in sample['channels']],
                             ['task_statement', 'grading_instructions', 'action_trace'])
            action = json.loads(base64.b64decode(sample['channels'][2]['data']))
            self.assertEqual(action, dict(schema='cairn.legacy-action-trace/1', commands=[], tool_outputs=[]))
        self.assertFalse((self.output/'answers').exists())
        self.assertEqual(legacy.convert(request, self.output), receipt)

    def test_original_claude_command_output_error_repeat_and_background_semantics(self):
        def use(ident, command, **extra):
            return dict(type='tool_use', id=ident, name='Bash', input=dict(command=command, **extra))
        def result(ident, output, error=False):
            return dict(type='tool_result', tool_use_id=ident, content=output, is_error=error)
        events = [
            dict(type='assistant', message=dict(content=[use('toolu_a', 'false'), use('toolu_b', 'repeat'),
                use('toolu_c', 'repeat'), use('toolu_d', 'background', run_in_background=True),
                use('toolu_e', 'pending'), dict(type='tool_use', id='memory', name='mcp__cairn__pull',
                    input={'handle': 'PRIVATE-MEMORY'}), dict(type='tool_use', name='Write',
                    input={'file_path': 'PRIVATE-WRITE'}), dict(type='text', text='PRIVATE-ANSWER')])),
            dict(type='user', message=dict(content=[result('toolu_a', 'failure', True), result('toolu_b', 'first'),
                result('toolu_c', 'second'), result('toolu_d', 'background result'), result('memory', 'PRIVATE-MEMORY')])),
        ]
        raw = b''.join(C(event) for event in events)
        receipt = legacy.convert(self.request(['action_trace', 'task_statement', 'grading_instructions'], raw=raw), self.output)
        expected = dict(schema='cairn.legacy-action-trace/1',
            commands=['false', 'repeat', 'repeat', 'background', 'pending'], tool_outputs=[
                dict(command='false', output='failure', is_error=True),
                dict(command='repeat', output='first', is_error=False),
                dict(command='repeat', output='second', is_error=False),
                dict(command='background', output='background result', is_error=False)])
        for sample in self.draft(receipt):
            self.assertEqual([c['kind'] for c in sample['channels']],
                             ['action_trace', 'task_statement', 'grading_instructions'])
            projected = base64.b64decode(sample['channels'][0]['data'])
            self.assertEqual(json.loads(projected), expected)
            for leak in (b'PRIVATE-MEMORY', b'PRIVATE-WRITE', b'PRIVATE-ANSWER', b'ORACLE-SECRET-CHECK'):
                self.assertNotIn(leak, projected)

    def test_codex_dedup_and_shell_unwrapping_remain_the_existing_distinct_parsers(self):
        item = dict(type='item.completed', item=dict(id='command-1', type='command_execution',
                    command="/bin/bash -lc 'echo test'", aggregated_output='nonzero output', exit_code=7))
        raw = C(item)+C(item)+C(dict(type='item.completed', item=dict(type='agent_message', text='PRIVATE-ANSWER')))
        receipt = legacy.convert(self.request(['task_statement', 'action_trace', 'grading_instructions'],
                                              raw=raw, harness='codex'), self.output)
        expected = dict(schema='cairn.legacy-action-trace/1', commands=["/bin/bash -lc 'echo test'"],
                        tool_outputs=[dict(command='echo test', output='nonzero output', is_error=True)]*2)
        for sample in self.draft(receipt):
            self.assertEqual(json.loads(base64.b64decode(sample['channels'][1]['data'])), expected)

    def test_v2_answer_only_adds_no_action_evidence(self):
        request = self.request(['task_statement', 'grading_instructions', 'final_answer'])
        receipt = legacy.convert(request, self.output)
        for sample in self.draft(receipt):
            self.assertEqual([c['kind'] for c in sample['channels']],
                             ['task_statement', 'grading_instructions', 'final_answer'])
        for name in ('actions', 'subjects', 'observations'):
            self.assertFalse((self.output/name).exists())

    def test_answer_and_actions_follow_selected_channel_order(self):
        answer = 'Selected synthetic answer.'
        raw = C(dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Bash',
                    input=dict(command='false')), dict(type='text', text=answer)])))
        channels = ['task_statement', 'final_answer', 'action_trace', 'grading_instructions']
        receipt = legacy.convert(self.request(channels, raw=raw, answer=answer), self.output)
        for sample in self.draft(receipt):
            self.assertEqual([c['kind'] for c in sample['channels']], channels)
            self.assertEqual(base64.b64decode(sample['channels'][1]['data']), answer.encode())
            action = json.loads(base64.b64decode(sample['channels'][2]['data']))
            self.assertEqual(action['commands'], ['false'])
            self.assertEqual(action['tool_outputs'], [])

    def test_no_changed_pairs_writes_only_receipt(self):
        for record in self.f.records:
            record['outcome'] = 'correct'
        receipt = legacy.convert(self.request(['task_statement', 'grading_instructions', 'action_trace']), self.output)
        self.assertIsNone(receipt['selection'])
        self.assertEqual(receipt['changed_pair_grading']['state'], 'no_grading_needed')
        self.assertEqual([p.name for p in self.output.iterdir()], ['receipt.json'])

    def test_v1_still_refuses_actions_and_v2_refuses_artifact_or_evaluator_channels(self):
        self.f.contracts['case-a']['required_channels'] = ['task_statement', 'grading_instructions', 'action_trace']
        self.refused(self.f.save(), 'legacy_channel_unbound')
        for channel in ('artifact_bundle', 'evaluator_observation'):
            with self.subTest(channel=channel):
                self.refused(self.request(['task_statement', 'grading_instructions', channel]), 'legacy_channel_unbound')

    def test_required_channel_and_parser_selection_are_not_optional(self):
        self.refused(self.request(['task_statement', 'action_trace']), 'blind_required_channels')
        self.request(['task_statement', 'grading_instructions', 'action_trace'])
        del self.f.request['action_parser']
        self.refused(self.f.resave_request(), 'legacy_request_shape')

    def test_parser_bytes_and_original_plan_source_must_match(self):
        for name in ('task_eval', 'task_permissions'):
            with self.subTest(name=name):
                self.request(['task_statement', 'grading_instructions', 'action_trace'])
                self.f.request['action_parser'][name] = pin(self.root/(name+'.py'), b'# other source\n')
                self.refused(self.f.resave_request(), 'legacy_action_parser_mismatch')
        self.request(['task_statement', 'grading_instructions', 'action_trace'])
        plan = json.loads((self.f.run/'plan.json').read_bytes()); plan['evaluator_sha256'] = '0'*64
        self.refused(self.reseal_plan(plan), 'legacy_action_parser_mismatch')

    def test_same_length_stream_and_attempt_substitution_refuse(self):
        request = self.request(['task_statement', 'grading_instructions', 'action_trace'])
        base = self.f.run/'runs'/self.f.records[0]['run_id']
        original = (base/'stream.jsonl').read_bytes()
        (base/'stream.jsonl').write_bytes(original.replace(b'Synthetic', b'Alteredxx'))
        self.refused(request, 'legacy_attempt_content_mismatch')
        (base/'stream.jsonl').write_bytes(original)
        record = json.loads((base/'result.json').read_bytes()); record['run_id'] = self.f.records[1]['run_id']
        (base/'result.json').write_text(json.dumps(record))
        self.refused(request, 'legacy_attempt_mismatch')

    def test_subject_binds_exact_attempt_source_parser_and_retained_stream(self):
        request = self.request(['task_statement', 'grading_instructions', 'action_trace'])
        receipt = legacy.convert(request, self.output)
        selection = json.loads(Path(receipt['selection']['path']).read_bytes())
        for sample in selection['samples']:
            channel = sample['channels'][2]
            observation = json.loads(Path(channel['observation']['path']).read_bytes())
            subject_pin = observation['subject']
            self.assertEqual(reference(subject_pin['path']), subject_pin)
            subject = json.loads(Path(subject_pin['path']).read_bytes())
            self.assertEqual(subject['source'], sample['source'])
            self.assertEqual(subject['parser'], self.f.request['action_parser'])
            self.assertEqual(subject['attempt'], reference(self.f.run/'runs'/sample['source']['run_id']/'result.json'))
            self.assertEqual(subject['stream'], reference(self.f.run/'runs'/sample['source']['run_id']/'stream.jsonl'))
            self.assertEqual(subject['stream_representation'], 'legacy-retained-utf8/1')
            self.assertEqual(subject['custody'], 'harness_recorded_hash')
        subjects = sorted((self.output/'subjects').glob('*.json'))
        original = subjects[0].read_bytes()
        subjects[0].write_bytes(subjects[1].read_bytes())
        with self.assertRaisesRegex(legacy.paired.Refusal, '^blind_pin_mismatch$'):
            packet.approval_subject(receipt['selection'])
        with self.assertRaisesRegex(legacy.paired.Refusal, '^legacy_output_conflict$'):
            legacy.convert(request, self.output)
        subjects[0].write_bytes(original)
        self.assertEqual(legacy.convert(request, self.output), receipt)

    def test_action_budget_overflow_refuses_without_truncation(self):
        raw = C(dict(type='assistant', message=dict(content=[dict(type='tool_use', name='Bash',
                  input=dict(command='x'*32000))])))
        self.refused(self.request(['task_statement', 'grading_instructions', 'action_trace'], raw=raw), 'legacy_action_bound')

    def test_resealed_wrong_attempt_subject_still_refuses_converter_and_bundle(self):
        request = self.request(['task_statement', 'grading_instructions', 'action_trace'])
        receipt = legacy.convert(request, self.output)
        selection = json.loads(Path(receipt['selection']['path']).read_bytes())
        first, second = selection['samples']
        channel = first['channels'][2]
        observation_path = Path(channel['observation']['path'])
        observation = json.loads(observation_path.read_bytes())
        other = json.loads(Path(second['channels'][2]['observation']['path']).read_bytes())
        observation['subject'] = other['subject']
        channel['observation'] = pin(observation_path, C(observation))
        receipt['selection'] = pin(Path(receipt['selection']['path']), C(selection))
        receipt_pin = pin(self.output/'receipt.json', C(receipt))
        # The generic draft checks pin correspondence, not the subject body's
        # legacy attempt semantics. The converter/bundle must reproduce them.
        packet.approval_subject(receipt['selection'])
        with self.assertRaisesRegex(legacy.paired.Refusal, '^legacy_output_conflict$'):
            legacy.convert(request, self.output)
        with self.assertRaisesRegex(legacy.paired.Refusal, '^legacy_output_conflict$'):
            bundle.build(receipt_pin, bundle_fixture.POLICY)

    def test_retained_invalid_utf8_uses_the_existing_replacement_policy(self):
        raw = b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo \xff"}}]}}\n'
        receipt = legacy.convert(self.request(['task_statement', 'grading_instructions', 'action_trace'], raw=raw), self.output)
        for sample in self.draft(receipt):
            self.assertEqual(json.loads(base64.b64decode(sample['channels'][2]['data']))['commands'], ['echo \ufffd'])

    def test_full_synthetic_shape_composes_with_unchanged_bundle(self):
        tasks = bundle_fixture.SHAPE_TASKS
        outcomes = {(case, seed): ('correct', 'mistake') if (case, seed) == ('task-00', 0) else ('correct', 'correct')
                    for case in tasks for seed in range(3)}
        self.f = Fixture(self.root/'full', outcomes=outcomes, tasks=tasks, arms=('CURRENT', 'CAND'))
        planned = sorted((dict(run_id=r['run_id'], case=r['case'], arm=r['arm'], seed=r['seed']) for r in self.f.records),
                         key=lambda row: row['run_id'])
        self.f.binding['schedule_sha256'] = legacy.paired.canonical_sha(planned)
        receipt = legacy.convert(self.request(['task_statement', 'grading_instructions', 'action_trace']), self.output)
        self.draft(receipt)
        joined, result = bundle.build(reference(self.output/'receipt.json'), bundle_fixture.POLICY,
                                     draft_receipt=reference(self.root/'draft/private/receipt.json'))
        self.assertEqual(len(joined['inventory']), 126)
        self.assertEqual(receipt['pairs'], dict(total=63, changed=1, unchanged=62, indeterminate=0))
        self.assertEqual(len(result['delivery_pairs']), 1)
        self.assertEqual(result['status'], 'awaiting_grades')


if __name__ == '__main__':
    unittest.main()
