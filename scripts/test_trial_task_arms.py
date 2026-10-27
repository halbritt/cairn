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
from trial_task_arms import bind_ordinary_claude, copy_arms, checked_runtime_bundle, _bundle_validator


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
                self.assertEqual(json.loads((base/'binding.json').read_text())['schema'], 'cairn.inbox-recall-binding/1')
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
            _bundle_validator().stage_runtime(te.ROOT/'integrations/lifecycle', code)
            bundle_pin=dict(path=str(code/'lifecycle-bundle.json'),sha256=hashlib.sha256((code/'lifecycle-bundle.json').read_bytes()).hexdigest())
            state=root/'state';state.mkdir()
            work=root/'work';work.mkdir();(work/'.git').mkdir()
            cli=root/'cairn'
            cli.write_text('#!/usr/bin/python3\nimport json\nprint(json.dumps({"ok":True,"data":{"status":"SCOPE_EMPTY","destination":{"name":"hosted"},"selected":[],"index":[]}}))\n')
            cli.chmod(0o700)
            config=dict(harness='claude',cairn='/tmp/trial/store/cairn',socket='/tmp/trial/unused.sock',token_file='/tmp/trial/unused.token',
                        repo=te.TRIAL_REPO,state_dir='/tmp/trial/hookstate',context_bytes=5400,semantic_fallback=False)
            bindings=bind_ordinary_claude(root,config,code/'inbox_recall.py',bundle_pin=bundle_pin)
            bindings += [(code,'/tmp/trial/hook','ro'), (cli,'/tmp/trial/store/cairn','ro'),
                         (work,'/workspace','rw')]
            bindings += te.trial_hook_observer_bindings(root/'hook-config.json',state)
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


class CompleteBundleArms(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.code = self.root / 'selected'
        self.bundle = _bundle_validator()
        self.bundle.stage_runtime(te.ROOT / 'integrations/lifecycle', self.code)
        self.binary = self.root / 'binary'
        self.binary.write_bytes(b'fixture')
        self.arm = dict(binary=self.pin(self.binary), hook=self.pin(self.code / 'memory.py'),
                        bridge=self.pin(self.code / 'inbox_recall.py'), api_revision='a' * 40,
                        recall_revision='b' * 40, recall_mode='agent_tools', semantic_fallback=False,
                        bundle=self.pin(self.code / 'lifecycle-bundle.json'))

    def pin(self, path):
        return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def select_changes(self):
        (self.code / 'lifecycle-bundle.json').write_text(json.dumps(self.bundle.bundle_descriptor(self.code), indent=3))
        for role, name in (('hook', 'memory.py'), ('bridge', 'inbox_recall.py'), ('bundle', 'lifecycle-bundle.json')):
            self.arm[role] = self.pin(self.code / name)

    def copy(self):
        document = dict(arms=['selected'], memory_arms=dict(selected=self.arm))
        return copy_arms(document, self.root / 'output', lambda _: dict(vcs_revision='a' * 40, vcs_modified=False))[1]['selected']

    def test_selected_closure_is_copied_exactly_without_checkout_replacement(self):
        helper = self.code / 'grant_ledger.py'
        helper.write_bytes(helper.read_bytes() + b'\n# explicitly selected trial helper\n')
        self.select_changes()
        expected = {path.name: path.read_bytes() for path in self.code.iterdir()}
        result = self.copy()
        destination = Path(result['hook']).parent
        for name, raw in expected.items():
            self.assertEqual((destination / name).read_bytes(), raw)
        self.assertEqual(result['bundle'], dict(path=str(destination / 'lifecycle-bundle.json'),
                                              sha256=self.arm['bundle']['sha256']))
        checked_runtime_bundle(destination / 'memory.py', result['bundle'], destination / 'inbox_recall.py')

    def test_current_two_blob_input_is_refused(self):
        del self.arm['bundle']
        with self.assertRaisesRegex(ValueError, 'requires an explicit lifecycle bundle pin'):
            self.copy()
        self.assertFalse((self.root / 'output').exists())

    def test_missing_or_changed_sidecar_or_member_is_refused(self):
        for name in ('lifecycle-bundle.json', 'grant_ledger.py'):
            for missing in (True, False):
                with self.subTest(name=name, missing=missing):
                    path = self.code / name
                    raw = path.read_bytes()
                    if missing:
                        path.unlink()
                    else:
                        path.write_bytes(raw + b'\n')
                    try:
                        with self.assertRaises((OSError, ValueError)):
                            self.copy()
                        self.assertFalse((self.root / 'output').exists())
                    finally:
                        path.write_bytes(raw)

    def test_source_loader_and_mixed_schema_cannot_be_selected_as_installed_bundle(self):
        cases = [('lifecycle_bundle.py', b'INSTALLED = True', b'INSTALLED = False', 'explicitly staged'),
                 ('lifecycle_bundle.py', b'INSTALLED = True', b'INSTALLED = 1', 'explicitly staged'),
                 ('memory.py', b'LIFECYCLE_BUNDLE_VERSION = 1', b'LIFECYCLE_BUNDLE_VERSION = True', 'unsupported'),
                 ('inbox_recall.py', b'cairn.inbox-recall-binding/2', b'cairn.inbox-recall-binding/1', 'version2')]
        for name, before, after, error in cases:
            with self.subTest(name=name, after=after):
                path = self.code / name
                raw = path.read_bytes()
                self.assertIn(before, raw)
                path.write_bytes(raw.replace(before, after, 1))
                self.select_changes()
                try:
                    with self.assertRaisesRegex(ValueError, error):
                        self.copy()
                finally:
                    path.write_bytes(raw)
                    self.select_changes()
