"""Public ordinary-hook configuration without native host/provider execution."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import trial_task_eval as te
from trial_task_arms import bind_ordinary_claude, copy_arms


class ArmContractTest(unittest.TestCase):
    def test_declared_clean_binary_identity_is_checked_not_inferred_from_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            binary=root/'binary'; binary.write_text('fixture')
            hook=root/'memory.py'; hook.write_text('fixture')
            desc=lambda p:dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            config=dict(binary=desc(binary),hook=desc(hook),api_revision='a'*40,recall_revision='b'*40,
                        recall_mode='ambient',semantic_fallback=False)
            document=dict(arms=['baseline'],memory_arms={'baseline':config},baseline_commit='a'*40)
            with self.assertRaisesRegex(ValueError,'does not attest'):
                copy_arms(document,root/'out',lambda _:{'vcs_revision':'c'*40,'vcs_modified':False})
            binary.write_text('changed')
            with self.assertRaisesRegex(ValueError,'missing or changed'):
                copy_arms(document,root/'out2',lambda _:{})

    def test_launcher_task_binding_is_fresh_once_and_requires_engine_support(self):
        import uuid
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); code=root/'code';code.mkdir()
            engine=code/'memory.py';bridge=code/'inbox_recall.py'
            engine.write_text('RECALL_TASK_VERSION = 1\n');bridge.write_text('# fixture bridge\n')
            keys=[]
            for label in ('first','second'):
                base=root/label;base.mkdir()
                config=dict(harness='claude',cairn='cairn',socket='socket',token_file='token',repo='fixture')
                bind_ordinary_claude(base,config,bridge,launcher_task=True)
                retained=json.loads((base/'hook-config.json').read_text())
                key=retained['recall_task_key'];self.assertEqual(str(uuid.UUID(key)),key);keys.append(key)
                self.assertEqual(retained['repo'],'fixture')
                with self.assertRaisesRegex(ValueError,'already'):
                    bind_ordinary_claude(base,config,bridge,launcher_task=True)
            self.assertNotEqual(*keys)
            engine.write_text('# old pinned engine\n')
            base=root/'old';base.mkdir()
            with self.assertRaisesRegex(ValueError,'launcher task'):
                bind_ordinary_claude(base,dict(harness='claude'),bridge,launcher_task=True)
            self.assertFalse((base/'hook-config.json').exists())

    @unittest.skipUnless(shutil.which('bwrap'),'needs local filesystem sandbox')
    def test_actual_bound_ordinary_hook_accepts_prepared_owned_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); code=root/'hook'; code.mkdir()
            for name in ('memory.py','inbox_recall.py'):
                shutil.copy2(te.ROOT/'integrations/lifecycle'/name,code/name)
                (code/name).chmod(0o600)
            state=root/'state';state.mkdir()
            work=root/'work';work.mkdir();(work/'.git').mkdir()
            cli=root/'cairn'
            cli.write_text('#!/usr/bin/python3\nimport json\nprint(json.dumps({"ok":True,"data":{"status":"SCOPE_EMPTY","destination":{"name":"hosted"},"selected":[],"index":[]}}))\n')
            cli.chmod(0o700)
            config=dict(harness='claude',cairn='/tmp/trial/store/cairn',socket='/tmp/trial/unused.sock',token_file='/tmp/trial/unused.token',
                        repo=te.TRIAL_REPO,state_dir='/tmp/trial/hookstate',context_bytes=5400,semantic_fallback=False)
            bindings=bind_ordinary_claude(root,config,code/'inbox_recall.py')
            bindings += [(code,'/tmp/trial/hook','ro'),(state,'/tmp/trial/hookstate','rw'),
                         (root/'hook-config.json','/tmp/trial/hookconfig.json','ro'),
                         (cli,'/tmp/trial/store/cairn','ro'),(work,'/workspace','rw'),
                         (te.ROOT/'scripts/trial_hook_observer.py','/tmp/trial/hook-observer.py','ro'),
                         (te.ROOT/'scripts/trial_source_delivery.py','/tmp/trial/trial_source_delivery.py','ro')]
            command=te.runtime_sandbox()
            for source,target,mode in bindings:
                command += ['--ro-bind' if mode=='ro' else '--bind',str(source),str(target)]
            command += ['--setenv','PATH','/usr/bin:/bin','--setenv','HOME',str(Path.home()),'--chdir','/workspace',
                        '/usr/bin/python3','-B','/tmp/trial/hook-observer.py','--engine','/tmp/trial/hook/memory.py',
                        '--config','/tmp/trial/hookconfig.json','--observations','/tmp/trial/hookstate/observations.jsonl']
            event=dict(hook_event_name='UserPromptSubmit',cwd='/workspace',session_id='f9261114-9e01-4dc4-ad50-21f8b979c35a',
                       prompt_id='fresh-native-fixture',prompt='Inspect the synthetic fixture.')
            result=subprocess.run(command,input=json.dumps(event),text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('additionalContext',json.loads(result.stdout)['hookSpecificOutput'])
            metadata=json.loads((state/'observations.jsonl').read_text())
            self.assertEqual(metadata['source_delivery'],dict(schema='cairn.source-delivery/1',status='observed',items=[]))
            self.assertEqual(json.loads((state/(event['session_id']+'.json')).read_text())['last_recall']['outcome'],'delegated')
