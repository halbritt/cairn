"""Synthetic legacy harness output only. No cases, oracles, providers or grading."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_blind_packet_draft as packet
import trial_legacy_blind_selection as legacy
import trial_legacy_case_materials as case_materials
import trial_paired_request as paired
import trial_task_eval as task_eval

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
BASE, CAND = 'baseline-mem', 'candidate-mem'
SECRET = 'ORACLE-SECRET-CHECK'
# (case, seed) -> (baseline outcome, candidate outcome)
OUTCOMES = {('case-a', 0): ('correct', 'mistake'), ('case-a', 1): ('correct', 'correct'),
            ('case-b', 0): ('incomplete', 'correct'), ('case-b', 1): ('undetermined', 'correct')}
TASKS = {'case-a': 'Fix the synthetic thing.\n', 'case-b': 'Explain the synthetic other thing.\n'}


def C(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def pin(path, content):
    path.write_bytes(content)
    return dict(path=str(path), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def stream(answer):
    return json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)])))+'\n'


class Fixture:
    def __init__(self, root, outcomes=None, tasks=None, arms=(BASE, CAND)):
        self.arms = tuple(arms)
        self.root = root
        self.run = root/'run'; (self.run/'runs').mkdir(parents=True)
        self.materials = root/'materials'; self.materials.mkdir()
        self.records, self.answers = [], {}
        self.original_tasks = dict(tasks or TASKS)
        for (case, seed), outcomes in (outcomes or OUTCOMES).items():
            for arm, outcome in zip(self.arms, outcomes):
                self.add(case, arm, seed, outcome)
        self.contracts = {case: dict(schema='cairn.blind-case-contract/2', case_key=case, case_class='synthetic',
                                     required_channels=['task_statement', 'grading_instructions', 'final_answer'],
                                     artifact_inventory=[]) for case in self.original_tasks}
        self.tasks = dict(self.original_tasks)
        self.binding = dict(schema='cairn.legacy-pair-binding/1', request_id='request-A', request_sha256='1'*64,
                            campaign_id='campaign-A', occurrence_id='occurrence-A', schedule_sha256='2'*64,
                            arm_roles={self.arms[0]: 'CURRENT', self.arms[1]: 'CAND'})
        self.admission = None
        self.wording = 'task'

    def add(self, case, arm, seed, outcome):
        run_id = f'{case}.{arm}.s{seed}'
        answer = f'Synthetic answer for {case} seed {seed} by {"one" if arm == self.arms[0] else "two"}.'
        record = dict(run_id=run_id, case=case, category='synthetic', primary=True, provenance='synthetic', arm=arm,
                      seed=seed, order=self.arms.index(arm), harness='claude', model='synthetic-model', wording='task', exit=0,
                      seconds=1.0, prompt_bytes=len(self.original_tasks[case].encode()), outcome=outcome,
                      correct=[True], mistake=[False], stratum='completion',
                      check_log=[dict(run=SECRET, outcome='ok')], trace=dict(turns=1), commands=0,
                      answer_chars=len(answer), memory=dict(delivered=['fixture-note']),
                      stream_sha256=hashlib.sha256(stream(answer).encode()).hexdigest(),
                      prompt_sha256=hashlib.sha256(self.original_tasks[case].encode()).hexdigest())
        self.records.append(record); self.answers[run_id] = answer

    def save(self, *, binding=True):
        for record in self.records:
            base = self.run/'runs'/record['run_id']
            base.mkdir(exist_ok=True)
            (base/'result.json').write_text(json.dumps(record, indent=2))
            (base/'stream.jsonl').write_text(stream(self.answers[record['run_id']]))
        plan = dict(arms=list(self.arms), wording=self.wording,
                    runs=[[r['case'], r['arm'], r['seed'], r['order']] for r in self.records])
        plan_pin = pin(self.run/'plan.json', json.dumps(plan, indent=2).encode())
        admission = self.admission or dict(state='complete', stop=None, planned=len(self.records),
                                            admitted=len(self.records), not_started=[])
        agent = dict(schema='cairn.task-eval.agent/1', frozen=dict(version=12, cases_sha256='3'*64),
                     arms=list(self.arms), model='synthetic-model', wording=self.wording, harness='claude',
                     distractors=1900, admission=admission,
                     records=sorted(self.records, key=lambda r: r['run_id']))
        agent_pin = pin(self.run/'agent.json', json.dumps(agent, indent=2).encode())
        policy = dict(schema='cairn.blind-policy/2', **{
            name: dict(version='synthetic-v1', pin=pin(self.materials/(name+'.bin'), ('PRIVATE '+name).encode()))
            for name in packet.POLICIES})
        review = dict(schema='cairn.blind-review-measurement/1', unit='bytes', semantics='comparison_plus_control',
                      graders=[dict(slot=i, limit=100000, control=pin(self.materials/f'control-{i}', b'SYNTHETIC CONTROL'))
                               for i in (1, 2)])
        cases = [dict(case=case, contract=pin(self.materials/f'contract-{case}.json', C(self.contracts[case])),
                      task_statement=pin(self.materials/f'task-{case}.txt', self.tasks[case].encode()),
                      grading_instructions=pin(self.materials/f'grading-{case}.txt', b'Selected synthetic rubric.\n'))
                 for case in sorted(self.tasks)]
        bound = None
        if binding:
            bound = pin(self.materials/'binding.json', C(dict(self.binding, agent_sha256=agent_pin['sha256'],
                                                                 plan_sha256=plan_pin['sha256'])))
        request = dict(schema='cairn.legacy-blind-conversion/1', agent=agent_pin, plan=plan_pin, binding=bound,
                       namespace='namespace-A', policy=policy,
                       policy_ref=dict(schema='cairn.blind-policy-ref/1', id='public-policy-A'),
                       review_measurement=review, cases=cases)
        self.request = request
        return pin(self.root/'request.json', C(request))

    def resave_request(self):
        return pin(self.root/'request.json', C(self.request))


class Case(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.f = Fixture(self.root)

    def refused(self, code, request, output=None):
        output = output or self.root/'out'
        with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
            legacy.convert(request, output)
        self.assertFalse(output.exists())


class ConversionTests(Case):
    def test_changed_db_coverage_pair_converts_with_manifest_bound_safe_action_facts(self):
        case = case_materials.case_source('db-coverage')
        task = case['wordings']['task']
        fixture = Fixture(self.root/'db-run', outcomes={('db-coverage', 0): ('correct', 'mistake')},
                          tasks={'db-coverage': task})
        fixture.save()
        answer = 'I ran the integration checks and reviewed the database coverage.'
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command='make test-integration')),
                dict(type='tool_use', id='toolu_memory', name='mcp__cairn__search',
                     input={'query': 'MEMORY-SECRET ARM-CAND'}),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_test',
                     content='initdb: warning: /private/MEMORY-SECRET\nok example.com/cairnmini/core 0.01s\n',
                     is_error=False),
            ]))),
            json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)]))),
            json.dumps(dict(type='result', result=answer)),
        ]) + '\n'
        stream_bytes = raw.encode()
        for record in fixture.records:
            record['prompt_bytes'] = len(task.encode())
            record['prompt_sha256'] = hashlib.sha256(task.encode()).hexdigest()
            record['stream_sha256'] = hashlib.sha256(stream_bytes).hexdigest()
            record['answer_chars'] = len(answer)
            base = fixture.run/'runs'/record['run_id']
            (base/'stream.jsonl').write_bytes(stream_bytes)
            (base/'result.json').write_text(json.dumps(record, indent=2))

        issued_path = self.root/'selected-materials'
        issued = case_materials.issue(issued_path)
        material_pin = {key: issued[key] for key in ('schema', 'path', 'bytes', 'sha256')}
        material_binding = dict(schema=issued['schema'], sha256=issued['sha256'])
        evaluator_sha = hashlib.sha256(Path(task_eval.__file__).read_bytes()).hexdigest()

        agent_path = Path(fixture.request['agent']['path'])
        agent = json.loads(agent_path.read_bytes())
        agent['records'] = sorted(fixture.records, key=lambda row: row['run_id'])
        agent['frozen'] = dict(version=12, label_version=12,
                               cases_sha256=case_materials.EXPECTED_CASES_SHA256)
        agent['case_materials'] = material_binding
        agent_pin = pin(agent_path, json.dumps(agent, indent=2).encode())

        plan_path = Path(fixture.request['plan']['path'])
        plan = json.loads(plan_path.read_bytes())
        plan['evaluator_sha256'] = evaluator_sha
        plan['case_materials'] = material_binding
        plan_pin = pin(plan_path, json.dumps(plan, indent=2).encode())

        binding_path = Path(fixture.request['binding']['path'])
        binding = json.loads(binding_path.read_bytes())
        binding.update(agent_sha256=agent_pin['sha256'], plan_sha256=plan_pin['sha256'])
        binding_pin = pin(binding_path, C(binding))

        def source_pin(path):
            content = Path(path).read_bytes()
            return dict(path=str(path), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())

        fixture.request = dict(
            schema=legacy.SELECTED_REQUEST_SCHEMA, agent=agent_pin, plan=plan_pin,
            binding=binding_pin, namespace='namespace-A', policy=fixture.request['policy'],
            policy_ref=fixture.request['policy_ref'], review_measurement=fixture.request['review_measurement'],
            action_parser=dict(schema='cairn.legacy-action-parser/1',
                               task_eval=source_pin(task_eval.__file__),
                               task_permissions=source_pin(legacy.permissions.__file__)),
            case_materials=material_pin)
        request_pin = fixture.resave_request()

        snapshot_pin = dict(schema='cairn.legacy-pregrade-snapshot-pin/1',
                            record_path='final-artifacts/synthetic.json', bytes=1, sha256='a'*64)
        artifact = C(dict(schema='cairn.legacy-blind-final-artifacts/1', workspace_entries=[],
                          deletions=[], git_index_refs=[]))
        with patch.object(legacy, '_pregrade_snapshot', return_value=(b'{}', {}, artifact, snapshot_pin)):
            receipt = legacy.convert(request_pin, self.root/'converted')
            before = {path.relative_to(self.root/'converted').as_posix(): path.read_bytes()
                      for path in (self.root/'converted').rglob('*') if path.is_file()}
            replayed = legacy.convert(request_pin, self.root/'converted')
            after = {path.relative_to(self.root/'converted').as_posix(): path.read_bytes()
                     for path in (self.root/'converted').rglob('*') if path.is_file()}
        self.assertEqual(receipt, replayed)
        self.assertEqual(before, after)
        selection = json.loads(Path(receipt['selection']['path']).read_bytes())
        self.assertEqual(len(selection['comparisons']), 1)
        action_refs = []
        for sample in selection['samples']:
            channel = next(row for row in sample['channels'] if row['name'] == 'action_trace')
            ref = channel['entries'][0]['input']
            action_refs.append(json.loads(Path(ref['path']).read_bytes()))
        self.assertEqual(len(action_refs), 2)
        for projection in action_refs:
            self.assertEqual(projection['schema'], legacy.DB_COVERAGE_ACTION_PROJECTION)
            self.assertEqual(projection['events'], [dict(action='make_test_integration', status='completed',
                output_facts=dict(initdb_diagnostic=True, core_test_ok=True,
                                  forbidden=dict(failure=False, skip=False, cached=False, no_test_files=False)))])
        subjects = []
        for file in (self.root/'converted'/'subjects').glob('*.json'):
            subjects.append(json.loads(file.read_bytes()))
        self.assertEqual(len(subjects), 2)
        self.assertTrue(all(subject['schema'] == 'cairn.legacy-db-coverage-action-subject/1' and
                            subject['selected_materials'] == material_binding and
                            subject['projection'] == legacy.DB_COVERAGE_ACTION_PROJECTION
                            for subject in subjects))
        converted = b''.join(before.values())
        for secret in (b'MEMORY-SECRET', b'ARM-CAND', b'/private/', b'0.01s', b'initdb: warning'):
            self.assertNotIn(secret, converted)

        bad_request = copy.deepcopy(fixture.request)
        bad_request['action_parser']['task_eval']['sha256'] = '0'*64
        bad_pin = pin(self.root/'bad-request.json', C(bad_request))
        with patch.object(legacy, '_pregrade_snapshot', return_value=(b'{}', {}, artifact, snapshot_pin)):
            with self.assertRaisesRegex(paired.Refusal, '^legacy_pin_mismatch$'):
                legacy.convert(bad_pin, self.root/'refused')
        self.assertFalse((self.root/'refused').exists())

    def test_db_coverage_action_trace_is_an_exact_allowlisted_projection(self):
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_ok', name='Bash', input=dict(command='make test-integration')),
                dict(type='tool_use', id='toolu_bad', name='Bash', input=dict(command='cat /tmp/ARM-CAND')),
                dict(type='tool_use', id='toolu_secret', name='mcp__cairn__search', input={'query': 'MEMORY-SECRET'}),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_ok', content='OUTPUT-SECRET ARM-CAND', is_error=False),
                dict(type='tool_result', tool_use_id='toolu_bad', content='RAW-OUTPUT-SECRET', is_error=False),
            ]))),
            json.dumps(dict(type='result', result='FINAL-ANSWER-SECRET')),
        ]) + '\n'
        trace = legacy.task_eval.parse_stream(raw, 'claude')
        projection = legacy._db_coverage_action_projection(trace, raw, 'claude')
        value = json.loads(projection)
        self.assertEqual(value, dict(schema='cairn.legacy-db-coverage-action-trace/1',
            events=[dict(action='make_test_integration', status='completed', output_facts=dict(
                initdb_diagnostic=False, core_test_ok=False,
                forbidden=dict(failure=False, skip=False, cached=False, no_test_files=False)))]))
        for secret in (b'OUTPUT-SECRET', b'ARM-CAND', b'MEMORY-SECRET', b'RAW-OUTPUT-SECRET',
                       b'FINAL-ANSWER-SECRET', b'go test ./...; echo'):
            self.assertNotIn(secret, projection)

    def test_db_coverage_action_trace_preserves_coarse_failure_and_started_states(self):
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_fail', name='Bash', input=dict(command='go test ./...')),
                dict(type='tool_use', id='toolu_wait', name='Bash', input=dict(command='make test', run_in_background=True)),
                dict(type='tool_use', id='toolu_other', name='Bash', input=dict(command='git status')),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_fail', content='PRIVATE', is_error=True),
                dict(type='tool_result', tool_use_id='toolu_wait', content='Command running in background', is_error=False),
            ]))),
            json.dumps(dict(type='result', result='done')),
        ]) + '\n'
        trace = legacy.task_eval.parse_stream(raw, 'claude')
        projection = json.loads(legacy._db_coverage_action_projection(trace, raw, 'claude'))
        self.assertEqual(projection, dict(schema='cairn.legacy-db-coverage-action-trace/1', events=[
            dict(action='go_test_all', status='failed'), dict(action='make_test', status='not_completed')]))

    def test_db_coverage_action_trace_normalizes_codex_wrapper_without_copying_output(self):
        raw = '\n'.join([
            json.dumps(dict(type='item.completed', item=dict(id='1', type='command_execution',
                command="/bin/bash -lc 'make test'", aggregated_output='PRIVATE-MEMORY-ARM', exit_code=0))),
            json.dumps(dict(type='item.completed', item=dict(id='2', type='command_execution',
                command="/bin/bash -lc 'go test ./...'", aggregated_output='FAIL-SECRET', exit_code=2))),
        ]) + '\n'
        trace = legacy.task_eval.parse_stream(raw, 'codex')
        projection = legacy._db_coverage_action_projection(trace, raw, 'codex')
        self.assertEqual(json.loads(projection), dict(schema='cairn.legacy-db-coverage-action-trace/1', events=[
            dict(action='make_test', status='completed'), dict(action='go_test_all', status='failed')]))
        for secret in (b'PRIVATE-MEMORY-ARM', b'FAIL-SECRET', b'/bin/bash', b'go test'):
            self.assertNotIn(secret, projection)

    def test_db_coverage_action_trace_accepts_only_the_supported_simple_command_classes(self):
        for command, expected in (('bash scripts/test-postgres.sh', 'test_postgres_script'),
                                  ('go test', 'go_test'), ('go test ./core', 'go_test_core')):
            raw = '\n'.join([
                json.dumps(dict(type='assistant', message=dict(content=[
                    dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command=command)),
                ]))),
                json.dumps(dict(type='user', message=dict(content=[
                    dict(type='tool_result', tool_use_id='toolu_test', content='safe output', is_error=False),
                ]))),
            ]) + '\n'
            trace = legacy.task_eval.parse_stream(raw, 'claude')
            projection = json.loads(legacy._db_coverage_action_projection(trace, raw, 'claude'))
            event = projection['events'][0]
            self.assertEqual(event['action'], expected)
            self.assertEqual(event['status'], 'completed')
            if expected.startswith('go_test'):
                self.assertNotIn('output_facts', event)
            else:
                self.assertEqual(event['output_facts'], dict(
                    initdb_diagnostic=False, core_test_ok=False,
                    forbidden=dict(failure=False, skip=False, cached=False, no_test_files=False)))

    def test_db_coverage_action_trace_projects_only_allowlisted_output_facts(self):
        output = ('initdb: warning: private path /tmp/MEMORY-SECRET ARM-CAND\n'
                  'ok example.com/cairnmini/core 0.03s\n'
                  '--- SKIP: TestPrivate (0.00s)\n(cached)\nno test files\nFAIL private-package\n')
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command='make test-integration')),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_test', content=output, is_error=False),
            ]))),
        ]) + '\n'
        trace = legacy.task_eval.parse_stream(raw, 'claude')
        projection = legacy._db_coverage_action_projection(trace, raw, 'claude')
        self.assertEqual(json.loads(projection), dict(schema='cairn.legacy-db-coverage-action-trace/1', events=[
            dict(action='make_test_integration', status='completed', output_facts=dict(
                initdb_diagnostic=True, core_test_ok=True,
                forbidden=dict(failure=True, skip=True, cached=True, no_test_files=True)))
        ]))
        for secret in (b'MEMORY-SECRET', b'ARM-CAND', b'/tmp/', b'0.03s', b'private-package',
                       b'TestPrivate', b'initdb: warning', b'no test files'):
            self.assertNotIn(secret, projection)

    def test_db_coverage_action_trace_refuses_compound_wrapped_and_unparseable_evidence(self):
        commands = ('make test-integration && echo MEMORY-SECRET',
                    'env make test', 'bash -lc \'make test-integration\'',
                    'go test ./core -run TestSecret')
        for command in commands:
            raw = json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_bad', name='Bash', input=dict(command=command)),
            ]))) + '\n'
            with self.subTest(command=command), self.assertRaisesRegex(
                    paired.Refusal, '^legacy_db_action_command_ambiguous$'):
                trace = legacy.task_eval.parse_stream(raw, 'claude')
                legacy._db_coverage_action_projection(trace, raw, 'claude')

        for output in ('x' * (legacy._DB_COVERAGE_OUTPUT_LIMIT + 1), 'bad\ufffdoutput'):
            raw = '\n'.join([
                json.dumps(dict(type='assistant', message=dict(content=[
                    dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command='make test-integration')),
                ]))),
                json.dumps(dict(type='user', message=dict(content=[
                    dict(type='tool_result', tool_use_id='toolu_test', content=output, is_error=False),
                ]))),
            ]) + '\n'
            with self.subTest(output_size=len(output)), self.assertRaisesRegex(
                    paired.Refusal, '^legacy_db_action_output_unparseable$'):
                trace = legacy.task_eval.parse_stream(raw, 'claude')
                legacy._db_coverage_action_projection(trace, raw, 'claude')

        duplicate_summaries = ('ok example.com/cairnmini/core 0.01s\n'
                               'ok example.com/cairnmini/core 0.02s\n')
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command='make test-integration')),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_test', content=duplicate_summaries, is_error=False),
            ]))),
        ]) + '\n'
        with self.assertRaisesRegex(paired.Refusal, '^legacy_db_action_output_ambiguous$'):
            trace = legacy.task_eval.parse_stream(raw, 'claude')
            legacy._db_coverage_action_projection(trace, raw, 'claude')

    def test_non_integration_command_output_is_not_scanned_or_size_limited(self):
        output = 'x' * (legacy._DB_COVERAGE_OUTPUT_LIMIT + 1)
        raw = '\n'.join([
            json.dumps(dict(type='assistant', message=dict(content=[
                dict(type='tool_use', id='toolu_test', name='Bash', input=dict(command='make test')),
            ]))),
            json.dumps(dict(type='user', message=dict(content=[
                dict(type='tool_result', tool_use_id='toolu_test', content=output, is_error=False),
            ]))),
        ]) + '\n'
        trace = legacy.task_eval.parse_stream(raw, 'claude')
        projection = legacy._db_coverage_action_projection(trace, raw, 'claude')
        self.assertEqual(json.loads(projection), dict(schema=legacy.DB_COVERAGE_ACTION_PROJECTION,
            events=[dict(action='make_test', status='completed')]))
        self.assertNotIn(b'x', projection)

    def test_v12_evaluator_projection_redacts_check_names_logs_verdicts_arm_and_memory(self):
        case = case_materials.case_source('deploy-stamp')
        checks = case_materials.shell_checks(case)
        self.assertTrue(checks)
        record = dict(check_log=[dict(run=check['run'][:200], exit=0, seconds=0.01,
                                      output='MEMORY-BODY ARM-CAND ORACLE-VERDICT')
                                 for check in checks],
                      baseline_custody=dict(sha256='1'*64))
        pin = lambda name: dict(path='/private/'+name, bytes=1, sha256='2'*64)
        chosen = dict(evaluator_contract=dict(evaluator_source_sha256='3'*64,
            selected_assets_sha256='4'*64,
            precedence=dict(version='cairn.task-eval.grade-precedence/v12', rules=['selected'])),
            manifest=dict(schema=case_materials.SCHEMA, sha256='5'*64),
            source_case_sha256='6'*64)
        public, subject = legacy._evaluator_projection(case,
            record, dict(schema='cairn.blind-source/2', arm='CAND', run_id='private-run'),
            pin('task'), pin('stream'), pin('artifact'), pin('answer'), None, pin('snapshot'),
            pin('attempt'), chosen)
        for forbidden in (b'MEMORY-BODY', b'ARM-CAND', b'ORACLE-VERDICT',
                          checks[0]['run'].encode(), b'private-run', b'/private/'):
            self.assertNotIn(forbidden, public)
        self.assertIn(b'process_exit_codes', public)
        self.assertEqual(subject['selected_materials'], dict(schema=case_materials.SCHEMA, sha256='5'*64))

    def test_changed_pairs_compose_into_blind_draft_without_arm_or_oracle_leaks(self):
        receipt = legacy.convert(self.f.save(), self.root/'out')
        selection = json.loads(Path(receipt['selection']['path']).read_bytes())
        # Only (case-a, 0) and (case-b, 0) have deterministic outcomes that differ.
        keys = sorted((c['pair_key']['case'], c['pair_key']['seed']) for c in selection['comparisons'])
        self.assertEqual(keys, [('case-a', 0), ('case-b', 0)])
        self.assertEqual(receipt['pairs'], dict(total=4, changed=2, unchanged=1, indeterminate=1))
        for flag in ('runtime_admitted', 'release_authority_verified', 'delivery_available', 'grading_performed'):
            self.assertIs(receipt[flag], False)
        self.assertEqual(receipt['pair_identity'], 'declared_binding')
        samples = {s['sample_id']: s for s in selection['samples']}
        for comparison in selection['comparisons']:
            sources = [samples[i]['source'] for i in comparison['samples']]
            self.assertEqual({s['arm'] for s in sources}, {'CURRENT', 'CAND'})
            self.assertEqual(comparison['samples'], sorted(comparison['samples']))
            self.assertEqual(comparison['pair_key']['request_id'], 'request-A')
        # Arm roles come from the declared binding, run ids from the exact legacy rows.
        by_run = {s['source']['run_id']: s['source']['arm'] for s in selection['samples']}
        self.assertEqual(by_run, {'case-a.baseline-mem.s0': 'CURRENT', 'case-a.candidate-mem.s0': 'CAND',
                                  'case-b.baseline-mem.s0': 'CURRENT', 'case-b.candidate-mem.s0': 'CAND'})
        # Composition with the unchanged blind packet draft.
        subject = packet.approval_subject(receipt['selection'])
        approval = dict(schema='cairn.blind-declared-approval/2', state='approved',
                        subject_sha256=hashlib.sha256(C(subject)).hexdigest(),
                        observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
                        issuer_role=pin(self.root/'issuer.bin', b'PRIVATE declared role'))
        draft = packet.draft(receipt['selection'], pin(self.root/'approval.json', C(approval)), self.root/'draft', now=NOW)
        self.assertEqual(draft['status'], 'declared_correspondence_valid')
        public = b''.join(p.read_bytes() for p in sorted((self.root/'draft/public').rglob('*')) if p.is_file())
        answer = self.f.answers['case-a.candidate-mem.s0']
        self.assertIn(__import__('base64').b64encode(answer.encode()), public)
        for row in self.f.request['cases']:
            self.assertNotIn(row['contract']['path'].encode(), public)
            self.assertNotIn(row['task_statement']['path'].encode(), public)
            self.assertNotIn(row['grading_instructions']['path'].encode(), public)
        for leak in (b'CURRENT', b'CAND', BASE.encode(), CAND.encode(), b'.s0', SECRET.encode(), b'fixture-note'):
            self.assertNotIn(leak, public)
        # The converter's own output never copies oracle-derived record fields.
        converted = b''.join(p.read_bytes() for p in (self.root/'out').rglob('*') if p.is_file())
        self.assertNotIn(SECRET.encode(), converted)
        self.assertNotIn(b'fixture-note', converted)

    def test_without_identity_binding_refuses_with_exact_blocker(self):
        self.refused('legacy_pair_identity_unbound', self.f.save(binding=False))

    def test_binding_for_other_output_or_arms_refuses(self):
        for change in (dict(agent_sha256='9'*64), dict(plan_sha256='9'*64),
                       dict(arm_roles={BASE: 'CURRENT', CAND: 'CURRENT'}),
                       dict(arm_roles={BASE: 'CURRENT', 'other': 'CAND'})):
            with self.subTest(change=change):
                request = self.f.save()
                binding = json.loads(Path(self.f.request['binding']['path']).read_bytes())
                binding.update(change)
                self.f.request['binding'] = pin(self.root/'materials/binding.json', C(binding))
                self.refused('legacy_binding_mismatch', self.f.resave_request())

    def test_missing_failed_or_incomplete_attempts_refuse(self):
        mutations = [
            ('legacy_attempt_missing', lambda f: f.records.pop()),
            ('legacy_attempt_not_graded', lambda f: f.records[0].update(outcome='harness_error')),
            ('legacy_attempt_not_graded', lambda f: f.records[0].update(outcome='provider_error')),
            ('legacy_run_incomplete', lambda f: setattr(f, 'admission', dict(state='stopped_provider_failure',
                stop=dict(run_id='x'), planned=8, admitted=6, not_started=[]))),
        ]
        for index, (code, mutate) in enumerate(mutations):
            with self.subTest(code=code, index=index):
                f = Fixture(self.root/str(index)); mutate(f)
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    legacy.convert(self.save_plan_mismatch(f) if code == 'legacy_attempt_missing' else f.save(),
                                   f.root/'out')
                self.assertFalse((f.root/'out').exists())

    def save_plan_mismatch(self, f):
        """The plan keeps a row whose attempt record is absent."""
        request = f.save()
        plan = json.loads((f.run/'plan.json').read_bytes())
        plan['runs'].append(['case-b', CAND, 1, 0])
        f.request['plan'] = pin(f.run/'plan.json', json.dumps(plan, indent=2).encode())
        binding = json.loads(Path(f.request['binding']['path']).read_bytes())
        binding['plan_sha256'] = f.request['plan']['sha256']
        f.request['binding'] = pin(f.materials/'binding.json', C(binding))
        return f.resave_request()

    def test_retained_attempt_that_differs_from_agent_record_refuses(self):
        request = self.f.save()
        path = self.f.run/'runs/case-a.baseline-mem.s0/result.json'
        record = json.loads(path.read_text()); record['outcome'] = 'mistake'
        path.write_text(json.dumps(record, indent=2))
        self.refused('legacy_attempt_mismatch', request)

    def test_retained_answer_that_differs_from_recorded_answer_refuses(self):
        request = self.f.save()
        (self.f.run/'runs/case-b.candidate-mem.s0/stream.jsonl').write_text(stream('A different answer.'))
        self.refused('legacy_attempt_content_mismatch', request)

    def test_ambiguous_legacy_attempt_identity_refuses(self):
        # run_id is case.arm.sSEED; with dotted names "a.b"+"c" and "a"+"b.c" collide.
        f = self.f
        for record in f.records:
            if record['case'] == 'case-a':
                f.answers[record['run_id'].replace('case-a', 'case.x')] = f.answers.pop(record['run_id'])
                record.update(case='case.x', run_id=record['run_id'].replace('case-a', 'case.x'))
        self.refused('legacy_attempt_identity_ambiguous', f.save())

    def test_duplicate_or_misnamed_run_id_refuses(self):
        f = self.f
        f.records[1]['run_id'] = 'case-a.candidate-mem.s9'
        f.answers['case-a.candidate-mem.s9'] = f.answers['case-a.candidate-mem.s0']
        self.refused('legacy_attempt_identity_ambiguous', f.save())

    def test_unbound_channels_refuse(self):
        self.f.contracts['case-a']['required_channels'].append('artifact_bundle')
        self.refused('legacy_channel_unbound', self.f.save())

    def test_same_length_stream_substitution_refuses(self):
        # agent-341's reproduction: pins unchanged, answer swapped for one of equal length.
        request = self.f.save()
        original = self.f.answers['case-a.baseline-mem.s0']
        (self.f.run/'runs/case-a.baseline-mem.s0/stream.jsonl').write_text(stream('X'*len(original)))
        self.refused('legacy_attempt_content_mismatch', request)

    def test_same_length_task_substitution_refuses(self):
        self.f.tasks['case-b'] = 'Y'*(len(TASKS['case-b'].encode())-1)+'\n'
        self.refused('legacy_task_statement_mismatch', self.f.save())

    def test_legacy_output_without_recorded_attempt_content_refuses_release(self):
        # Output from before the harness recorded stream and prompt hashes has
        # no original-attempt provenance; lengths alone never stand in for it.
        for field in ('stream_sha256', 'prompt_sha256'):
            with self.subTest(field=field):
                f = Fixture(self.root/field)
                for record in f.records:
                    del record[field]
                with self.assertRaisesRegex(paired.Refusal, '^original_attempt_provenance_unavailable$'):
                    legacy.convert(f.save(), f.root/'out')
                self.assertFalse((f.root/'out').exists())

    def test_indeterminate_pairs_block_changed_pair_completion(self):
        receipt = legacy.convert(self.f.save(), self.root/'out')
        coverage = receipt['changed_pair_coverage']
        self.assertEqual(coverage['state'], 'blocked_indeterminate_pairs')
        self.assertIs(coverage['complete_possible'], False)
        self.assertEqual(coverage['indeterminate'], [dict(case='case-b', seed=1)])
        for record in self.f.records:
            if (record['case'], record['seed']) == ('case-b', 1):
                record['outcome'] = 'incomplete'
        receipt = legacy.convert(self.f.save(), self.root/'out2')
        self.assertEqual(receipt['changed_pair_coverage'],
                         dict(state='all_pairs_classified', complete_possible=True, indeterminate=[]))

    def test_declared_binding_and_observed_content_stay_distinct(self):
        receipt = legacy.convert(self.f.save(), self.root/'out')
        self.assertEqual(receipt['custody'], dict(pair_identity='declared_binding',
            attempt_content='harness_recorded_hash', independent_attempt_custody=False))
        attempt = receipt['attempts'][0]
        self.assertEqual(set(attempt), {'run_id', 'sample_id', 'declared_by_harness', 'observed_now'})
        self.assertEqual(attempt['declared_by_harness']['stream_sha256'], attempt['observed_now']['stream_sha256'])

    def test_task_statement_that_is_not_the_attempt_prompt_refuses(self):
        self.f.tasks['case-b'] = 'Some other task.\n'
        self.refused('legacy_task_statement_mismatch', self.f.save())

    def test_non_task_wording_refuses(self):
        self.f.wording = 'terse'
        self.refused('legacy_wording_unsupported', self.f.save())

    def test_small_all_unchanged_run_emits_no_grading_needed_receipt(self):
        for record in self.f.records:
            record['outcome'] = 'correct'
        receipt = legacy.convert(self.f.save(), self.root/'out')
        self.assertEqual(receipt['changed_pair_grading']['state'], 'no_grading_needed')
        self.assertEqual(sorted(p.name for p in (self.root/'out').iterdir()), ['receipt.json'])


SHAPE_TASKS = {f'task-{i:02d}': f'Synthetic task number {i}.\n' for i in range(21)}


def full_shape(root, state_of):
    """21 synthetic tasks x 3 seeds x 2 arms = 126 attempts, 63 pairs."""
    outcomes = {(case, seed): state_of(index, seed) for index, case in enumerate(sorted(SHAPE_TASKS))
                for seed in range(3)}
    return Fixture(root, outcomes=outcomes, tasks=SHAPE_TASKS)


class ZeroChangedTests(Case):
    def test_full_shape_all_unchanged_is_classified_with_no_grading_needed(self):
        # agent-341's reproduction: 126 attempts, all correct/correct, hashes kept.
        f = full_shape(self.root/'full', lambda i, s: ('correct', 'correct'))
        self.assertEqual(len(f.records), 126)
        receipt = legacy.convert(f.save(), self.root/'out')
        self.assertEqual(receipt['pairs'], dict(total=63, changed=0, unchanged=63, indeterminate=0))
        self.assertEqual(receipt['changed_pair_grading'], dict(
            state='no_grading_needed', complete=True, deliveries_required=0,
            reason='all_pairs_determinate_unchanged'))
        self.assertIsNone(receipt['selection'])
        self.assertEqual(receipt['comparisons'], [])
        self.assertEqual(receipt['changed_pair_coverage'],
                         dict(state='all_pairs_classified', complete_possible=True, indeterminate=[]))
        self.assertEqual(len(receipt['pair_classification']), 63)
        self.assertEqual({row['state'] for row in receipt['pair_classification']}, {'unchanged'})
        # No blind selection, answers or packets are fabricated.
        self.assertEqual(sorted(p.name for p in (self.root/'out').iterdir()), ['receipt.json'])
        self.assertEqual(json.loads((self.root/'out/receipt.json').read_bytes()), receipt)
        for flag in ('runtime_admitted', 'release_authority_verified', 'delivery_available', 'grading_performed'):
            self.assertIs(receipt[flag], False)
        self.assertEqual(legacy.convert(f.save(), self.root/'out'), receipt)

    def test_full_shape_all_indeterminate_stays_incomplete(self):
        f = full_shape(self.root/'full', lambda i, s: ('undetermined', 'needs_review'))
        receipt = legacy.convert(f.save(), self.root/'out')
        self.assertEqual(receipt['pairs'], dict(total=63, changed=0, unchanged=0, indeterminate=63))
        self.assertEqual(receipt['changed_pair_grading'], dict(
            state='incomplete_all_pairs_indeterminate', complete=False, deliveries_required=None,
            reason='no_pair_classified'))
        self.assertEqual(receipt['changed_pair_coverage']['state'], 'blocked_indeterminate_pairs')
        self.assertEqual({row['state'] for row in receipt['pair_classification']}, {'undetermined'})
        self.assertIsNone(receipt['selection'])

    def test_full_shape_mixed_unchanged_and_indeterminate_stays_blocked(self):
        f = full_shape(self.root/'full', lambda i, s: ('correct', 'undetermined') if (i, s) == (20, 2) else ('mistake', 'mistake'))
        receipt = legacy.convert(f.save(), self.root/'out')
        self.assertEqual(receipt['pairs'], dict(total=63, changed=0, unchanged=62, indeterminate=1))
        self.assertEqual(receipt['changed_pair_grading'], dict(
            state='blocked_indeterminate_pairs', complete=False, deliveries_required=None,
            reason='indeterminate_pairs_present'))
        self.assertEqual(receipt['changed_pair_coverage']['indeterminate'], [dict(case='task-20', seed=2)])
        self.assertIsNone(receipt['selection'])

    def test_full_shape_with_one_changed_pair_keeps_the_selection_path(self):
        f = full_shape(self.root/'full', lambda i, s: ('correct', 'mistake') if (i, s) == (7, 1) else ('correct', 'correct'))
        receipt = legacy.convert(f.save(), self.root/'out')
        self.assertEqual(receipt['pairs'], dict(total=63, changed=1, unchanged=62, indeterminate=0))
        self.assertEqual(receipt['changed_pair_grading'], dict(
            state='awaiting_grading', complete=False, deliveries_required=2, reason='changed_pairs_selected'))
        selection = json.loads(Path(receipt['selection']['path']).read_bytes())
        self.assertEqual([(c['pair_key']['case'], c['pair_key']['seed']) for c in selection['comparisons']],
                         [('task-07', 1)])
        packet.approval_subject(receipt['selection'])

    def test_every_zero_changed_outcome_still_refuses_missing_provenance_or_coverage(self):
        shapes = dict(unchanged=lambda i, s: ('correct', 'correct'),
                      indeterminate=lambda i, s: ('undetermined', 'undetermined'),
                      mixed=lambda i, s: ('correct', 'undetermined') if s == 0 else ('incomplete', 'incomplete'),
                      changed=lambda i, s: ('correct', 'mistake') if (i, s) == (0, 0) else ('correct', 'correct'))
        breaks = dict(
            original_attempt_provenance_unavailable=lambda f: f.records[5].pop('stream_sha256'),
            legacy_attempt_missing=lambda f: f.records.pop(),
            legacy_run_incomplete=lambda f: setattr(f, 'admission', dict(state='stopped_provider_failure',
                stop=dict(run_id='x'), planned=126, admitted=125, not_started=[])),
            legacy_attempt_not_graded=lambda f: f.records[3].update(outcome='harness_error'))
        for name, state_of in shapes.items():
            for code, damage in breaks.items():
                with self.subTest(shape=name, code=code):
                    f = full_shape(self.root/name/code, state_of); damage(f)
                    request = f.save()
                    if code == 'legacy_attempt_missing':
                        plan = json.loads((f.run/'plan.json').read_bytes())
                        plan['runs'].append(['task-20', CAND, 2, 0])
                        f.request['plan'] = pin(f.run/'plan.json', json.dumps(plan, indent=2).encode())
                        binding = json.loads(Path(f.request['binding']['path']).read_bytes())
                        binding['plan_sha256'] = f.request['plan']['sha256']
                        f.request['binding'] = pin(f.materials/'binding.json', C(binding))
                        request = f.resave_request()
                    with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                        legacy.convert(request, f.root/'out')
                    self.assertFalse((f.root/'out').exists())

    def test_empty_plan_and_records_refuse_instead_of_completing(self):
        # agent-341's reproduction: every check passed vacuously on zero attempts.
        self.f.records = []
        self.refused('legacy_run_empty', self.f.save())

    def test_empty_records_with_a_nonempty_plan_refuse(self):
        f = self.f
        planned = [[r['case'], r['arm'], r['seed'], r['order']] for r in f.records]
        f.records = []
        f.save()
        f.request['plan'] = pin(f.run/'plan.json', json.dumps(dict(arms=[BASE, CAND], wording='task',
                                                                   runs=planned), indent=2).encode())
        binding = json.loads(Path(f.request['binding']['path']).read_bytes())
        binding['plan_sha256'] = f.request['plan']['sha256']
        f.request['binding'] = pin(f.materials/'binding.json', C(binding))
        self.refused('legacy_run_empty', f.resave_request())

    def test_zero_changed_run_still_requires_identity_binding(self):
        f = full_shape(self.root/'full', lambda i, s: ('correct', 'correct'))
        with self.assertRaisesRegex(paired.Refusal, '^legacy_pair_identity_unbound$'):
            legacy.convert(f.save(binding=False), self.root/'out')
        self.assertFalse((self.root/'out').exists())


class CliTests(Case):
    def test_cli_reports_fixed_refusal_and_conversion(self):
        import io
        from contextlib import redirect_stdout
        for binding, status, code in ((False, 'refused', 2), (True, 'converted', 0)):
            request = self.f.save(binding=binding)
            output = self.root/('cli-'+status)
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(legacy.main(['--request', request['path'], '--request-sha256', request['sha256'],
                                              '--output', str(output)]), code)
            result = json.loads(printed.getvalue())
            self.assertEqual(result['status'], status)
            if binding:
                self.assertIs(result['runtime_admitted'], False)
            else:
                self.assertEqual(result['code'], 'legacy_pair_identity_unbound')
                self.assertFalse(output.exists())


class DeterminismTests(Case):
    def test_conversion_is_deterministic_and_idempotent(self):
        request = self.f.save()
        first = legacy.convert(request, self.root/'out')
        files = {p: p.read_bytes() for p in (self.root/'out').rglob('*') if p.is_file()}
        again = legacy.convert(request, self.root/'out')
        self.assertEqual(first, again)
        self.assertEqual(files, {p: p.read_bytes() for p in (self.root/'out').rglob('*') if p.is_file()})
        other = legacy.convert(request, self.root/'other')
        one = json.loads(Path(first['selection']['path']).read_bytes())
        two = json.loads(Path(other['selection']['path']).read_bytes())
        ids = lambda s: ([x['sample_id'] for x in s['samples']], [x['comparison_id'] for x in s['comparisons']],
                         [x['samples'] for x in s['comparisons']])
        self.assertEqual(ids(one), ids(two))

    def test_changed_output_is_refused_not_overwritten(self):
        request = self.f.save()
        receipt = legacy.convert(request, self.root/'out')
        answer = next((self.root/'out/answers').iterdir())
        answer.chmod(0o600); answer.write_bytes(b'tampered')
        with self.assertRaisesRegex(paired.Refusal, '^legacy_output_conflict$'):
            legacy.convert(request, self.root/'out')
        self.assertEqual(answer.read_bytes(), b'tampered')
        self.assertEqual(receipt['selection']['sha256'],
                         hashlib.sha256(Path(receipt['selection']['path']).read_bytes()).hexdigest())

    def test_inputs_are_not_mutated_and_pins_are_checked(self):
        request = self.f.save()
        before = copy.deepcopy(self.f.request)
        legacy.convert(request, self.root/'out')
        self.assertEqual(before, json.loads(Path(request['path']).read_bytes()))
        (self.f.run/'agent.json').write_bytes((self.f.run/'agent.json').read_bytes()+b' ')
        self.refused('legacy_pin_mismatch', request, self.root/'out2')


if __name__ == '__main__':
    unittest.main()
