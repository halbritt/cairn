"""Real request/preparation/runner, synthetic source and inert executor only."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import unittest
import unittest.mock

import trial_candidate_request as candidate
import trial_candidate_run_bridge as bridge
import trial_paired_request as paired
import trial_weekly_run as weekly
from trial_capacity_adapter import adapt
from test_trial_capacity_adapter import Inputs
import test_trial_candidate_request as candidate_fixtures
from test_trial_candidate_request import FakeCompiler, PREPARE_NOW, git
from test_trial_paired_capacity import write_pin
from test_trial_weekly_run import FAKE_HARNESS, FakeExecutor, embedding_fixture


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.f = candidate_fixtures.CandidateRequestTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        repo = self.root/'repo'
        # Match the shared inert harness's retained baseline attempt identity.
        cases_path = self.f.fixture.source/'cases.json'
        cases = json.loads(cases_path.read_bytes())
        for case in cases['cases']:
            case.update(cwd='project', copy_to='project')
        write_pin(cases_path, cases)
        self.f.fixture.freeze()
        self.f.profile['weekly_source']['frozen_sha256'] = pin(self.f.fixture.source/'FROZEN.json')['sha256']
        shutil.copytree(self.f.fixture.source, repo/'trials/task-eval')
        harness = repo/'scripts/trial-task-eval.sh'
        harness.parent.mkdir()
        # The shared inert harness must render this fixture's exact synthetic prompt.
        harness.write_text(FAKE_HARNESS.replace(
            "return f'Synthetic task for {case}.\\n'", "return 'synthetic task ' + case"))
        harness.chmod(0o700)
        shutil.copy(Path(weekly.selection.__file__), harness.parent/'trial_account_selection.py')
        shutil.copy(Path(weekly.limits.__file__), harness.parent/'trial_legacy_limits.py')
        (harness.parent/'trial_task_eval.py').write_text(
            '# synthetic\nselected = credentials_source()\na.add_argument("--original-limits")\n')
        # The original-execution seam prepares the declared config and launcher through trial_embedding_launch.
        config, launcher = embedding_fixture(self.root)
        self.f.profile['embedding'].update(config=config, worker=pin(launcher))
        # The original-execution seam reads a structured model-identity declaration.
        self.f.profile['embedding']['model_identity'] = write_pin(self.root/'model-identity.json', dict(
            schema='cairn.embedding-model-identity/1', model_sha256='a'*64, algorithm='synthetic-embedding/1',
            dimensions=384))
        git(repo, 'add', '-A'); git(repo, 'commit', '-qm', 'synthetic harness')
        self.harness = git(repo, 'rev-parse', 'HEAD')
        queued = self.f.run_request()
        self.entry = self.f.queue/queued['request_id']
        _, reviews, _, _ = self.f.reviewed(queued)
        candidate.prepare(self.f.profile, queued['request_id'], now=PREPARE_NOW, **reviews)
        evidence = self.root/'adapter'; evidence.mkdir()
        inputs = Inputs(evidence)
        document = inputs.document()
        provider = self.f.fixture.provider
        document.update(plan=provider.plan, accounts=provider.accounts, routes=provider.routes,
            spend_request=pin(provider.root/'spend-request.json'),
            spend_binding=provider.mapping['spend_binding'], prices=provider.mapping['prices'],
            quotes=provider.mapping['quotes'])
        adapt(document, evidence/'adapted', now='2026-10-09T20:02:00Z')
        self.runs = self.root/'runs'; self.runs.mkdir(mode=0o700)
        writer_cgroup = self.root/'writer-cgroup'; writer_cgroup.mkdir(mode=0o700)
        writer_controls = dict(schema='cairn.writer-controls/1',
            controls=dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32,
                          cpu_max='100000 100000'),
            reviewed_identity=dict(schema='cairn.writer-reviewed-identity/2',
                kernel=dict(release='synthetic-kernel', version='synthetic-build'),
                bwrap=dict(path='/usr/bin/bwrap', sha256='a'*64),
                source_scope=dict(test='synthetic'), execution='sealed_memfd'))
        writer_controls_pin = write_pin(self.root/'writer-controls.json', writer_controls)
        self.profile = dict(schema=weekly.PROFILE_SCHEMA, repo=str(repo), current_ref='main',
            candidate_ref='feature/recall', harness_commit=self.harness, runs_root=str(self.runs),
            campaign_id=self.f.profile['campaign_id'], schedule=self.f.profile['schedule'],
            weekly_source={k:self.f.profile['weekly_source'][k] for k in ('label_version','frozen_sha256')},
            embedding=dict(worker=self.f.profile['embedding']['worker']), postgres=self.f.profile['postgres'],
            capacity=dict(receipt=pin(evidence/'adapted/receipt.json'),
                plan=write_pin(evidence/'plan.json', provider.plan),
                models=dict.fromkeys(('CURRENT','CAND','primary','secondary'), 'fixture-model')),
            executor='fake', selection=None, account=None,
            execution=paired.read_json(self.entry/'proposal.json')['weekly_input']['execution'],
            writer=dict(cgroup=str(writer_cgroup), controls=writer_controls_pin))
        config_home = self.root/'accounts/weekly'
        config_home.mkdir(parents=True, mode=0o700)
        (config_home/'.credentials.json').write_text('{"synthetic": "never read"}')
        self.profile['account'] = dict(account_id='account-A', config_home=str(config_home))
        self.profile['selection'] = write_pin(self.root/'live-selection.json', dict(
            schema=weekly.SELECTION_SCHEMA, campaign_id=self.profile['campaign_id'],
            scheduled_at=queued['scheduled_at'], current=self.harness, candidate=self.f.cand,
            harness_commit=self.harness, executor='fake',
            capacity_receipt_sha256=self.profile['capacity']['receipt']['sha256'],
            writer_sha256=paired.canonical_sha(self.profile['writer']),
            selected_by=self.f.profile['proposal']['reply_recipient_id']))
        self.selection = dict(schema='cairn.candidate-run-bridge/1', entry=pin(self.entry/'entry.json'),
            prepared=pin(self.entry/'prepared.json'),
            runner_profile=write_pin(self.root/'runner-profile.json', self.profile))
        self.output = self.root/'bridge'
        self.executor = FakeExecutor(self.profile['account'])

    def run_bridge(self, **kwargs):
        return bridge.run(write_pin(self.root/'bridge-selection.json', self.selection), self.output,
                          now=kwargs.pop('now', PREPARE_NOW), executors=kwargs.pop('executors', {'fake':self.executor}),
                          compiler=kwargs.pop('compiler', FakeCompiler()), **kwargs)

    def select_default_executor(self, account):
        self.profile.update(executor='legacy-harness', account=account)
        live = paired.read_json_pin(self.profile['selection'])
        live['executor'] = 'legacy-harness'
        self.profile['selection'] = write_pin(self.root/'live-selection.json', live)
        self.selection['runner_profile'] = write_pin(self.root/'runner-profile.json', self.profile)

    def test_default_executor_gets_selected_account_and_keeps_capacity_gate(self):
        self.select_default_executor(self.profile['account'])
        # Production construction reaches capacity checking with its one selected
        # account. This fixture deliberately reserves two; no executor may run.
        with self.assertRaisesRegex(paired.Refusal, '^capacity_account_mismatch$'):
            self.run_bridge(executors=None)
        self.assertEqual(list(self.runs.iterdir()), [])
        self.assertEqual(self.executor.executed, [])

    def test_default_executor_without_account_keeps_original_refusal(self):
        self.select_default_executor(None)
        with self.assertRaisesRegex(paired.Refusal, '^weekly_executor_blocked$') as caught:
            self.run_bridge(executors=None)
        self.assertEqual(caught.exception.blockers, ['dedicated_account_unbound'])
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_executor_spec_cannot_change_selected_account(self):
        def changed_account(profile, **kwargs):
            return weekly.run(dict(profile, account=dict(profile['account'], account_id='other')), **kwargs)
        result = self.run_bridge(runner=changed_account)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['runner']['code'], 'executor_failed')
        self.assertEqual(self.executor.executed, [])

    def test_unchanged_preparation_covers_slot_but_cannot_extend_it(self):
        prepared = paired.read_json(self.entry/'prepared.json')['preparation']
        retained = self.entry/'preparation'
        request = paired.read_json(retained/'request.json')
        start, end = map(paired.utc, (request['scheduled_at'], request['expires_at']))
        spend_end = paired.utc(paired.read_json(retained/'provider-spend-request.json')['valid_until'])
        self.assertLessEqual(end, spend_end)
        names = ('companion', 'review', 'mapping', 'capacity', 'spend-binding')
        docs = [paired.read_json(retained/f'provider-{name}.json') for name in names]
        docs.extend(paired.read_json(retained/'provider-prices.json')['quotes'].values())
        for document in docs:
            self.assertLessEqual(paired.utc(document['observed_at']), start)
            self.assertGreaterEqual(paired.utc(document['valid_until']), spend_end)
        before = {name: pin(retained/name) for name in prepared['files']}
        bridge._load(write_pin(self.root/'bridge-selection.json', self.selection),
                     end - dt.timedelta(microseconds=1))
        with self.assertRaisesRegex(paired.Refusal, '^outside_weekly_window$'):
            self.run_bridge(now=end)
        self.assertEqual(before, {name: pin(retained/name) for name in prepared['files']})
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_different_selected_review_cannot_borrow_preparation_validity(self):
        prepared = paired.read_json(self.entry/'prepared.json')
        review = paired.read_json_pin(prepared['review'])
        review['valid_until'] = '2026-10-09T20:01:00Z'
        prepared['review'] = write_pin(self.root/'other-review.json', review)
        self.selection['prepared'] = write_pin(self.entry/'prepared.json', prepared)
        with self.assertRaisesRegex(paired.Refusal, '^bridge_provider_binding$'):
            self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_prepared_candidate_reaches_exact_weekly_runner_once(self):
        result = self.run_bridge()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['runner']['attempts']['records'], 126)
        self.assertEqual(result['runner']['baseline_custody'],
                         dict(verified=126, proof='harness_retained_baseline'))
        self.assertEqual(len(self.executor.executed), 1)
        mapping = result['binding']
        self.assertNotEqual(mapping['candidate']['occurrence_id'], mapping['runner']['occurrence_id'])
        self.assertNotEqual(mapping['candidate']['schedule_sha256'], mapping['runner']['schedule_sha256'])
        self.assertEqual(self.executor.executed[0]['harness']['commit'], self.harness)
        self.assertEqual(self.run_bridge(), result)
        self.assertEqual(len(self.executor.executed), 1)

    def test_moved_candidate_ref_refuses_before_bridge_or_runner_writes(self):
        git(self.root/'repo', 'branch', '-f', 'feature/recall', self.harness)
        with self.assertRaisesRegex(paired.Refusal, '^bridge_ref_drift$'):
            self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])
        self.assertEqual(self.executor.executed, [])

    def test_wrong_slot_refuses_before_mutation(self):
        self.profile['schedule']['time'] = '20:00'
        self.selection['runner_profile'] = write_pin(self.root/'runner-profile.json', self.profile)
        with self.assertRaisesRegex(paired.Refusal, '^bridge_wrong_slot$'):
            self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_altered_prepared_artifact_refuses_before_mutation(self):
        execution = json.loads((self.entry/'execution.json').read_bytes())
        Path(execution['memory_arms']['CURRENT']['binary']['path']).write_text('altered inert binary')
        with self.assertRaisesRegex(paired.Refusal, '^pin_mismatch$'):
            self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_rebuilt_binary_mismatch_never_reaches_executor(self):
        def changed_compiler(source, output):
            FakeCompiler()(source, output)
            output.write_bytes(b'other inert binary')
        result = self.run_bridge(compiler=changed_compiler)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['runner']['code'], 'executor_failed')
        self.assertEqual(self.executor.executed, [])
        # Existing runner conservatively retains the consumed execution marker.
        self.assertEqual(self.run_bridge(), result)
        self.assertEqual(self.executor.executed, [])

    def test_existing_slot_from_other_harness_refuses_before_bridge_write(self):
        self.run_bridge()
        original_output = self.output
        self.output = self.root/'other-bridge'
        self.profile['harness_commit'] = self.f.cand
        self.selection['runner_profile'] = write_pin(self.root/'other-profile.json', self.profile)
        with self.assertRaisesRegex(paired.Refusal, '^bridge_stored_slot_mismatch$'):
            self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertTrue((original_output/'receipt.json').is_file())
        self.assertEqual(len(self.executor.executed), 1)

    def test_lost_runner_return_resumes_existing_receipt_without_second_execution(self):
        def interrupted_return(*args, **kwargs):
            weekly.run(*args, **kwargs)
            raise OSError('synthetic lost return')
        with self.assertRaisesRegex(paired.Refusal, '^bridge_invalid_or_unavailable$'):
            self.run_bridge(runner=interrupted_return)
        self.assertTrue((self.output/'intent.json').is_file())
        self.assertFalse((self.output/'receipt.json').exists())
        result = self.run_bridge()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(self.executor.executed), 1)

    def test_unknown_executor_completion_stays_held_and_is_not_replayed(self):
        class Interrupted(FakeExecutor):
            def execute(self, spec, run_dir):
                self.executed.append(spec)
                raise KeyboardInterrupt()
        self.executor = Interrupted(self.profile['account'])
        with self.assertRaises(KeyboardInterrupt): self.run_bridge()
        with self.assertRaisesRegex(paired.Refusal, '^weekly_run_in_progress$'): self.run_bridge()
        self.assertEqual(len(self.executor.executed), 1)
        self.assertFalse((self.output/'receipt.json').exists())

    def test_receipt_status_cannot_upgrade_incomplete_runner_result(self):
        self.executor = FakeExecutor(self.profile['account'], harness_mode='cap')
        result = self.run_bridge()
        self.assertEqual(result['status'], 'incomplete')
        result['status'] = 'complete'
        (self.output/'receipt.json').write_text(json.dumps(result))
        with self.assertRaisesRegex(paired.Refusal, '^bridge_receipt_changed$'): self.run_bridge()
        self.assertEqual(len(self.executor.executed), 1)

    def test_tampered_baseline_cannot_gain_a_complete_bridge_binding(self):
        self.executor = FakeExecutor(self.profile['account'], harness_mode='baseline_tampered')
        result = self.run_bridge()
        self.assertEqual((result['status'], result['runner']['code']),
                         ('rejected', 'attempt_baseline_invalid'))
        self.assertIsNone(result['runner']['pair_binding'])
        self.assertEqual(self.run_bridge(), result)
        self.assertEqual(len(self.executor.executed), 1)

    def test_altered_retained_schedule_refuses_before_mutation(self):
        path = self.entry/'preparation/schedule.json'
        schedule = json.loads(path.read_bytes())
        schedule['runs'][0]['seed'] = 99
        path.write_text(json.dumps(schedule))
        with self.assertRaisesRegex(paired.Refusal, '^bridge_preparation_changed$'): self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_git_replacement_cannot_substitute_the_pinned_harness(self):
        git(self.root/'repo', 'replace', self.harness, self.f.cand)
        with self.assertRaisesRegex(paired.Refusal, '^bridge_git_replacements$'): self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_claim_for_another_pair_is_not_relabelled_by_the_bridge(self):
        self.run_bridge()
        self.output = self.root/'other-bridge'
        slot = next(self.runs.iterdir())
        claim = json.loads((slot/'claim.json').read_bytes())
        claim['candidate']['commit'] = self.harness
        (slot/'claim.json').write_text(json.dumps(claim))
        with self.assertRaisesRegex(paired.Refusal, '^bridge_stored_slot_mismatch$'): self.run_bridge()
        self.assertFalse(self.output.exists())
        self.assertEqual(len(self.executor.executed), 1)


    def test_runner_profile_must_carry_the_reviewed_execution_declaration(self):
        for execution in (None, write_pin(self.root/'other-execution.json', {'schema': 'other'})):
            with self.subTest(execution=execution):
                self.profile['execution'] = execution
                self.selection['runner_profile'] = write_pin(self.root/'runner-profile.json', self.profile)
                with self.assertRaisesRegex(paired.Refusal, '^bridge_execution_mismatch$'):
                    self.run_bridge()
                self.assertFalse(self.output.exists())
                self.assertEqual(list(self.runs.iterdir()), [])

    def test_complete_bridge_receipt_requires_the_observed_native_sha_and_effort(self):
        result = self.run_bridge()
        execution = paired.read_json(self.entry/'execution.json')
        observed = result['runner']['original_execution']['observed']
        self.assertEqual(observed['native']['sha256'], execution['native']['binary']['sha256'])
        self.assertEqual(observed['reasoning_effort'], execution['native']['effort'])
        slot = next(self.runs.iterdir())
        receipt = json.loads((slot/'receipt.json').read_bytes())
        receipt['original_execution']['observed']['native']['sha256'] = '0'*64
        (slot/'receipt.json').write_text(json.dumps(receipt))
        self.output = self.root/'other-bridge'
        with self.assertRaisesRegex(paired.Refusal, '^bridge_runner_execution_mismatch$'): self.run_bridge()

    def test_default_executor_carries_the_full_declared_contract_to_the_capacity_gate(self):
        self.select_default_executor(self.profile['account'])
        with self.assertRaisesRegex(paired.Refusal, '^capacity_account_mismatch$'):
            self.run_bridge(executors=None)
        self.assertEqual(list(self.runs.iterdir()), [])

    def test_runner_spec_embedding_must_be_the_reviewed_declaration(self):
        def changed_embedding(profile, **kwargs):
            real = weekly._prepare
            def prepare(*args, **inner):
                spec = real(*args, **inner)
                spec['embedding']['selected_config'] = dict(spec['embedding']['selected_config'], sha256='0'*64)
                return spec
            with unittest.mock.patch.object(weekly, '_prepare', side_effect=prepare):
                return weekly.run(profile, **kwargs)
        result = self.run_bridge(runner=changed_embedding)
        self.assertEqual((result['status'], result['runner']['code']), ('incomplete', 'executor_failed'))
        self.assertEqual(self.executor.executed, [])

if __name__ == '__main__':
    unittest.main()
