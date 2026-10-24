"""Codex weekly selection and executor contracts; synthetic, no provider use."""
import copy
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
            spec=dict(harness=dict(root=str(harness)),original_execution=fragment,
                arms={n:dict(binary=fixture.component,hook=fixture.component) for n in ('CURRENT','CAND')},
                distractors=1900,seeds=[0,1,2],embedding=dict(worker=fixture.component),actor_model='gpt-6-luna',
                postgres=dict(bin_dir='/disposable/pg'),original_arms=dict(path=str(controls),sha256='a'*64))
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
            profile=dict(campaign_id='campaign',selection=None,account=account_ref)
            preview=dict(scheduled_at='2026-10-24T12:00:00Z',current='1'*40,candidate='2'*40,harness='3'*40,
                         original_execution=execution)
            capacity=dict(receipt_sha256='c'*64)
            document=dict(schema=weekly.SELECTION_SCHEMA,campaign_id='campaign',scheduled_at=preview['scheduled_at'],
                current=preview['current'],candidate=preview['candidate'],harness_commit=preview['harness'],
                executor=weekly.CodexWeeklyExecutor.name,capacity_receipt_sha256=capacity['receipt_sha256'],
                selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107b7a1e07',codex_account_binding_sha256=paired.canonical_sha(account_ref),
                provider_model='gpt-6-luna',provider_binary_sha256='b'*64)
            profile['selection']=write_pin(root/'selection.json',document)
            weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,capacity)
            profile['account']=dict(account_ref,auth_file=str(root/'another-auth.json'))
            with self.assertRaisesRegex(paired.Refusal,'^live_selection_mismatch$'):
                weekly.selection_gate(profile,preview,weekly.CodexWeeklyExecutor.name,capacity)


if __name__=='__main__':
    unittest.main()
