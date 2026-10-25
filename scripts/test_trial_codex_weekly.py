"""Codex weekly selection and executor contracts; synthetic, no provider use."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_codex_account_selection as account
import trial_original_execution as original
import trial_paired_request as paired
import trial_weekly_input as weekly_input
import trial_weekly_run as weekly
import trial_weekly_accounts as weekly_accounts
from test_trial_weekly_request import Fixture, write_pin


def codex_execution(fixture, root):
    execution=copy.deepcopy(fixture.execution)
    execution.pop('native')
    execution['schema']=weekly_input.CODEX_EXECUTION_SCHEMA
    execution['provider']=dict(harness='codex',binary=fixture.component,model='gpt-6-luna',effort='high')
    identity=write_pin(root/'identity.json',dict(schema='cairn.embedding-model-identity/1',
        model_sha256='a'*64,algorithm='synthetic-embedding/1',dimensions=384))
    for arm in execution['memory_arms'].values(): arm['embedding_model_identity']=identity
    return execution


def codex_account(auth):
    raw=auth.read_bytes()
    return dict(account_id='codex-owner-account',auth_file=str(auth),auth_sha256=hashlib.sha256(raw).hexdigest())


def writer_fixture(root):
    cgroup=root/'writer-cgroup'; cgroup.mkdir(mode=0o700)
    controls=dict(schema='cairn.writer-controls/1',
        controls=dict(memory_max=512*1024*1024,memory_swap_max=0,pids_max=32,cpu_max='100000 100000'),
        reviewed_identity=dict(schema='cairn.writer-reviewed-identity/2',
            kernel=dict(release='synthetic-kernel',version='synthetic-build'),
            bwrap=dict(path='/usr/bin/bwrap',sha256='a'*64),
            source_scope=dict(test='synthetic'),execution='sealed_memfd'))
    controls_pin=write_pin(root/'writer-controls.json',controls)
    return dict(cgroup=str(cgroup),controls=controls_pin)


class CodexWeeklyContractTests(unittest.TestCase):
    def test_weekly_execution_v2_keeps_codex_actor_distinct_and_validates_pin(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); fixture=Fixture(root)
            execution=codex_execution(fixture,root)
            ref=dict(fixture.reference)
            ref['execution']=write_pin(root/'weekly-execution-v2.json',execution)
            selected=weekly_input.load_weekly_input(ref)
            self.assertEqual(weekly_input.actor(selected['execution']),execution['provider'])
            profile=dict(weekly_source={k:ref[k] for k in ('label_version','frozen_sha256')},
                         execution=ref['execution'],embedding=dict(worker=fixture.component))
            fragment=original.declared(profile,current='2'*40,candidate='2'*40,
                models=dict.fromkeys(('CURRENT','CAND'),'gpt-6-luna'))
            self.assertEqual(fragment['harness'],'codex')
            self.assertNotIn('native',fragment)
            self.assertEqual(fragment['provider'],fixture.component)

    def test_selected_codex_account_requires_pinned_chatgpt_subscription_auth(self):
        with tempfile.TemporaryDirectory() as d:
            auth=Path(d)/'auth.json'; auth.write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'synthetic'})))
            selected=account.selected_auth(codex_account(auth))
            self.assertEqual(selected,auth)
            for wrong in (dict(codex_account(auth),extra=True),
                          dict(codex_account(auth),auth_file='relative')):
                with self.assertRaises(paired.Refusal): account.selected_auth(wrong)
            auth.write_text(json.dumps(dict(auth_mode='api_key',tokens={'access_token':'synthetic'})))
            with self.assertRaisesRegex(paired.Refusal,'^codex_account_auth_changed$'):
                account.selected_auth(codex_account(auth) | {'auth_sha256':'0'*64})
            with self.assertRaisesRegex(paired.Refusal,'^codex_account_subscription_required$'):
                account.selected_auth(codex_account(auth))

    def test_codex_executor_passes_selected_model_account_and_binary_without_provider_call(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); auth=root/'auth.json'; auth.write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'synthetic'})))
            account_ref=codex_account(auth) | {'account_id':'codex-account'}
            (root/'fixture').mkdir()
            fixture=Fixture(root/'fixture')
            execution=codex_execution(fixture,root)
            ref=dict(fixture.reference); ref['execution']=write_pin(root/'v2.json',execution)
            profile=dict(weekly_source={k:ref[k] for k in ('label_version','frozen_sha256')},execution=ref['execution'],
                         embedding=dict(worker=fixture.component),account=account_ref)
            fragment=original.declared(profile,current='2'*40,candidate='2'*40,
                models=dict.fromkeys(('CURRENT','CAND'),'gpt-6-luna'))
            executor=weekly.CodexWeeklyExecutor(account_ref)
            executor.check(dict(original_execution=fragment))
            harness=root/'harness'; (harness/'scripts').mkdir(parents=True)
            script=harness/'scripts/trial-task-eval.sh'; script.write_text('#!/bin/sh\nexit 0\n')
            run_dir=root/'run'; run_dir.mkdir()
            controls=root/'controls.json'; controls.write_text('{}')
            writer=writer_fixture(root)
            spec=dict(harness=dict(root=str(harness)),original_execution=fragment,
                arms={n:dict(binary=fixture.component,hook=fixture.component) for n in ('CURRENT','CAND')},
                distractors=1900,seeds=[0,1,2],embedding=dict(worker=fixture.component),actor_model='gpt-6-luna',
                postgres=dict(bin_dir='/disposable/pg'),original_arms=dict(path=str(controls),sha256='a'*64),
                writer=writer)
            with patch.object(weekly.subprocess,'run') as run:
                run.return_value.returncode=2
                result=executor.execute(spec,run_dir)
            argv=run.call_args.args[0]
            self.assertIn('--harness',argv); self.assertEqual(argv[argv.index('--harness')+1],'codex')
            self.assertEqual(argv[argv.index('--model')+1],'gpt-6-luna')
            self.assertEqual(argv[argv.index('--codex-auth-file')+1],str(auth))
            self.assertEqual(argv[argv.index('--codex-auth-sha256')+1],account_ref['auth_sha256'])
            self.assertEqual(argv[argv.index('--parallel')+1],'1')
            self.assertIn('--codex-weekly-limits',argv)
            self.assertNotIn('-c',argv)  # Codex configuration belongs to the provider command, not this CLI.
            with patch.object(weekly.te,'cmd_agent',return_value=0):
                self.assertEqual(weekly.te.main(argv[1:]),0)  # Wrapper argv parses through the actual CLI.
            self.assertNotIn('OPENAI_API_KEY',run.call_args.kwargs['env'])
            self.assertNotIn('OPENROUTER_API_KEY',run.call_args.kwargs['env'])
            self.assertEqual(result['exit_code'],2)

    def test_codex_actor_command_pins_the_subscription_provider_explicitly(self):
        argv=weekly.te.codex_weekly_command('/sandbox/.codex/codex','gpt-6-luna','high')
        config=argv[argv.index('-c')+1]
        self.assertEqual(config,'model_provider="openai"')
        self.assertIn('--ignore-user-config',argv)
        self.assertEqual(argv[argv.index('-m')+1],'gpt-6-luna')

    def test_codex_live_selection_binds_account_path_model_and_provider_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); auth=root/'auth.json'; auth.write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'access_token':'synthetic'})))
            account_ref=codex_account(auth) | {'account_id':'codex-account'}
            execution=dict(harness='codex',provider=dict(sha256='b'*64),model='gpt-6-luna')
            profile=dict(campaign_id='campaign',selection=None,account=account_ref,writer=writer_fixture(root))
            preview=dict(scheduled_at='2026-10-24T12:00:00Z',current='1'*40,candidate='2'*40,harness='3'*40,
                         original_execution=execution)
            capacity=dict(receipt_sha256='c'*64)
            document=dict(schema=weekly.SELECTION_SCHEMA,campaign_id='campaign',scheduled_at=preview['scheduled_at'],
                current=preview['current'],candidate=preview['candidate'],harness_commit=preview['harness'],
                executor=weekly.CodexWeeklyExecutor.name,capacity_receipt_sha256=capacity['receipt_sha256'],
                writer_sha256=paired.canonical_sha(profile['writer']),
                selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107b7a1e07',codex_account_binding_sha256=paired.canonical_sha(account_ref),
                codex_account_assignment_sha256=paired.canonical_sha(account_ref),
                provider_model='gpt-6-luna',provider_binary_sha256='b'*64)
            profile['selection']=write_pin(root/'selection.json',document)
            weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,capacity)
            profile['account']=dict(account_ref,auth_file=str(root/'another-auth.json'))
            with self.assertRaisesRegex(paired.Refusal,'^live_selection_mismatch$'):
                weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,capacity)

    def test_multi_account_executor_pins_all_auth_files_and_assignment(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'source').mkdir(); fixture=Fixture(root/'source')
            accounts=[]
            for index, account_id in enumerate(('codex-A','codex-B','codex-C')):
                home=root/f'account-{index}';home.mkdir()
                auth=home/'auth.json';auth.write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'account_id':account_id})))
                accounts.append(dict(account_id=account_id,auth_file=str(auth),config_home=str(home),
                                     auth_sha256=hashlib.sha256(auth.read_bytes()).hexdigest()))
            cases=[f'case-{i:02d}' for i in range(21)];seeds=[1,2,3]
            allocation=weekly_accounts.balanced(cases,seeds,accounts)
            allocation_ref=write_pin(root/'account-assignment.json',allocation)
            loaded,_,counts=weekly_accounts.validate(allocation_ref,
                dict(cases=cases,arms=['CURRENT','CAND'],seeds=seeds))
            assignment=dict(document=loaded,reference=allocation_ref,pair_counts=counts)
            execution=codex_execution(fixture,root)
            ref=dict(fixture.reference);ref['execution']=write_pin(root/'v2.json',execution)
            profile=dict(weekly_source={k:ref[k] for k in ('label_version','frozen_sha256')},
                         execution=ref['execution'],embedding=dict(worker=fixture.component))
            fragment=original.declared(profile,current='2'*40,candidate='2'*40,
                models=dict.fromkeys(('CURRENT','CAND'),'gpt-6-luna'))
            executor=weekly.CodexWeeklyExecutor(account_assignment=assignment)
            self.assertEqual(set(executor.account_ids),set(account['account_id'] for account in accounts))
            executor.check(dict(original_execution=fragment))
            with patch.object(weekly.codex_account,'subscription_preflight',return_value=dict(
                    ordinary_usage_allowed=True,used_percent=20,absolute_allowance_observed=False)) as read:
                statuses=executor.subscription_preflight(dict(original_execution=fragment),
                                                         dt.datetime(2026,10,24,tzinfo=dt.timezone.utc))
            self.assertEqual(read.call_count,3)
            self.assertEqual(len(statuses),3)
            postflight_rows=[dict(account_id=account['account_id'],checked_at='2026-10-25T00:00:00Z',
                                  status=dict(primary=dict(used_percent=40,window_duration_mins=10080,
                                                           resets_at='2026-10-31T00:00:00Z')),
                                  error=None) for account in accounts]
            with patch.object(weekly.codex_account,'subscription_postflight',side_effect=postflight_rows) as read:
                observed=executor.subscription_postflight(dict(original_execution=fragment))
            self.assertEqual(read.call_count,3)
            self.assertEqual([row['account_id'] for row in observed], [a['account_id'] for a in accounts])
            self.assertEqual(observed,postflight_rows)
            harness=root/'harness';(harness/'scripts').mkdir(parents=True)
            (harness/'scripts/trial-task-eval.sh').write_text('#!/bin/sh\nexit 0\n')
            run_dir=root/'run';run_dir.mkdir();controls=root/'controls.json';controls.write_text('{}')
            writer=writer_fixture(root)
            spec=dict(harness=dict(root=str(harness)),original_execution=fragment,
                arms={n:dict(binary=fixture.component,hook=fixture.component) for n in ('CURRENT','CAND')},
                distractors=1900,seeds=[1,2,3],embedding=dict(worker=fixture.component),actor_model='gpt-6-luna',
                postgres=dict(bin_dir='/disposable/pg'),original_arms=dict(path=str(controls),sha256='a'*64),
                account_assignment=allocation_ref,writer=writer)
            with patch.object(weekly.subprocess,'run') as launched:
                launched.return_value.returncode=0
                executor.execute(spec,run_dir)
            argv=launched.call_args.args[0]
            self.assertEqual(argv[argv.index('--codex-account-assignment')+1],allocation_ref['path'])
            self.assertEqual(argv[argv.index('--codex-account-assignment-sha256')+1],allocation_ref['sha256'])
            self.assertEqual(argv[argv.index('--parallel')+1],'1')
            with patch.object(weekly.te,'cmd_agent',return_value=0):
                self.assertEqual(weekly.te.main(argv[1:]),0)

    def test_multi_account_live_selection_binds_assignment_pin_and_members(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); accounts=[]
            for i, account_id in enumerate(('codex-A','codex-B','codex-C')):
                home=root/f'home-{i}';home.mkdir();auth=home/'auth.json'
                auth.write_text(json.dumps(dict(auth_mode='chatgpt',tokens={'account_id':account_id})))
                accounts.append(dict(account_id=account_id,auth_file=str(auth),config_home=str(home),
                                     auth_sha256=hashlib.sha256(auth.read_bytes()).hexdigest()))
            cases=[f'case-{i:02d}' for i in range(21)];seeds=[1,2,3]
            assignment=weekly_accounts.balanced(cases,seeds,accounts)
            assignment_ref=write_pin(root/'assignment.json',assignment)
            execution=dict(harness='codex',provider=dict(sha256='b'*64),model='gpt-6-luna')
            preview=dict(scheduled_at='2026-10-24T12:00:00Z',current='1'*40,candidate='2'*40,harness='3'*40,
                         original_execution=execution)
            profile=dict(campaign_id='campaign',selection=None,account=None,writer=writer_fixture(root))
            capacity=dict(receipt_sha256='c'*64,account_assignment=assignment_ref)
            document=dict(schema=weekly.SELECTION_SCHEMA,campaign_id='campaign',scheduled_at=preview['scheduled_at'],
                current=preview['current'],candidate=preview['candidate'],harness_commit=preview['harness'],
                executor=weekly.CodexWeeklyExecutor.name,capacity_receipt_sha256=capacity['receipt_sha256'],
                writer_sha256=paired.canonical_sha(profile['writer']),
                selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107a7b9a1f',
                codex_account_binding_sha256=paired.canonical_sha(assignment),
                codex_account_assignment_sha256=assignment_ref['sha256'],
                provider_model='gpt-6-luna',provider_binary_sha256='b'*64)
            profile['selection']=write_pin(root/'selection.json',document)
            weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,capacity)
            changed=dict(capacity,account_assignment=dict(assignment_ref,sha256='0'*64))
            with self.assertRaises(paired.Refusal):
                weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,changed)

    def test_same_slot_retry_and_replay_require_the_original_assignment_pin(self):
        plan=dict(sha256='a'*64)
        assignment=dict(path='/private/assignment.json',sha256='b'*64)
        claim=dict(capacity=dict(plan_sha256=plan['sha256'],account_assignment=assignment,
                                 accounts=['codex-A','codex-B','codex-C']))
        weekly.assert_account_slot_binding(claim,plan,assignment,('codex-C','codex-A','codex-B'),
                                           'retry_account_assignment_mismatch')
        for changed in (
                dict(capacity=dict(plan_sha256='c'*64,account_assignment=assignment,
                                   accounts=['codex-A','codex-B','codex-C'])),
                dict(capacity=dict(plan_sha256=plan['sha256'],account_assignment=dict(assignment,sha256='d'*64),
                                   accounts=['codex-A','codex-B','codex-C'])),
                dict(capacity=dict(plan_sha256=plan['sha256'],account_assignment=assignment,
                                   accounts=['codex-A','codex-B']))):
            with self.assertRaisesRegex(paired.Refusal,'^retry_account_assignment_mismatch$'):
                weekly.assert_account_slot_binding(changed,plan,assignment,
                                                   ('codex-A','codex-B','codex-C'),
                                                   'retry_account_assignment_mismatch')


if __name__=='__main__':
    unittest.main()
