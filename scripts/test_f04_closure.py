"""Authenticated whole frontends, source admission ordering and legacy coexistence."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_f04_activation import ROOT, SESSION, Fixture, encoded, module, sha, write


class F04ClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory()
        cls.binary=Path(cls.build.name)/'activation'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/cairn-lifecycle'],cwd=ROOT,check=True)
        cls.legacy=module('f04_legacy_bundle',ROOT/'integrations/lifecycle/lifecycle_bundle.py')

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)

    def fixture(self,name,**kwargs): return Fixture(self.root/name,self.binary,**kwargs)

    def assert_preflight(self,f,role,result):
        prefix='Cairn coordination' if role=='coordination' else 'Cairn lifecycle'
        self.assertEqual((result.returncode,result.stdout,result.stderr),(1,b'',
            (prefix+' diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n').encode()))
        self.assertFalse(Path(f.memory['state_dir']).exists())
        self.assertEqual(f.api_calls(),[])

    def test_each_missing_or_changed_member_including_bootstrap_loader_alias_refuses(self):
        for role in ('memory','coordination'):
            f=self.fixture(role)
            for name in f.receipt['members']:
                with self.subTest(role=role,member=name):
                    path=Path(f.receipt['snapshot_root'])/name
                    original=path.read_bytes()
                    path.unlink()
                    self.assert_preflight(f,role,f.public(role))
                    write(path,original+b'\n# deliberate checked-closure change\n')
                    self.assert_preflight(f,role,f.public(role))
                    write(path,original)

    def test_direct_execution_of_every_staged_member_refuses_before_adjacent_import(self):
        f=self.fixture('direct')
        root=Path(f.receipt['snapshot_root'])
        # An unchecked adjacent import would execute the sentinel. The /3 guard
        # must run before even trying to load this replaced adjacent loader.
        sentinel=f.root/'adjacent-loader-ran'
        original=(root/'lifecycle_bundle.py').read_bytes()
        for name in f.receipt['members']:
            with self.subTest(member=name):
                if name!='lifecycle_bundle.py':
                    write(root/'lifecycle_bundle.py',('from pathlib import Path\nPath('+repr(str(sentinel))+').touch()\n').encode())
                else: write(root/'lifecycle_bundle.py',original)
                result=subprocess.run([sys.executable,'-I','-S',str(root/name)],input=f.raw,capture_output=True,
                                      cwd=f.root,env=f.env(),timeout=5)
                role='coordination' if name in ('coordination.py','coordination-hook.py') else 'memory'
                self.assert_preflight(f,role,result)
                self.assertFalse(sentinel.exists())
        write(root/'lifecycle_bundle.py',original)

    def test_receipt_selection_and_mixed_binary_or_binding_refuse_before_state(self):
        for mode in ('receipt-pin','binary','mixed-binding'):
            f=self.fixture(mode)
            if mode=='receipt-pin': f.receipt_path.write_bytes(f.receipt_path.read_bytes()+b' ')
            elif mode=='binary':
                f.binary.chmod(0o700);f.binary.write_bytes(f.binary.read_bytes()+b'changed-image');f.binary.chmod(0o500)
            else:
                manifest=json.loads(f.binding.read_text())
                manifest['memory_config']['sha256']='0'*64
                write(f.binding,encoded(manifest))
            result=f.public('memory')
            if mode!='mixed-binding': self.assert_preflight(f,'memory',result)
            else:
                self.assertEqual((result.returncode,result.stdout),(1,b''))
                self.assertIn(b'phase=activation error_class=ValueError',result.stderr)
                self.assertFalse(Path(f.memory['state_dir']).exists())
                self.assertEqual(f.api_calls(),[])

    def test_parent_configuration_digest_detects_replacement_before_child_effect(self):
        for role in ('memory','coordination'):
            f=self.fixture('race-'+role)
            argv=f.activation_argv(role)
            config=f.memory_path if role=='memory' else f.coord_path
            original=config.read_bytes()
            replacement=config.with_suffix('.next')
            write(replacement,original+b' ')
            replacement.replace(config)
            result=subprocess.run(argv,input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=15)
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            self.assertEqual(json.loads(result.stderr)['error'],{'class':'ValueError','reason':'activation','errno':None})
            self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_receipt_provenance_type_and_new_artifact_encoding_refuse_before_bootstrap(self):
        for defect in ('provenance-type','provenance-surrogate','utf-8-sig','utf-16'):
            with self.subTest(defect=defect):
                f=self.fixture(defect)
                if defect=='provenance-type': f.receipt['source_revision']={'untrusted':'shape'}
                elif defect=='provenance-surrogate': f.receipt['source_revision']='\ud800'
                raw=encoded(f.receipt)
                if not defect.startswith('provenance'): raw=raw.decode().encode(defect)
                write(f.receipt_path,raw);f.receipt_sha=sha(raw)
                # Even an independently selected hash does not make malformed
                # new-artifact metadata a valid bootstrap selection.
                trace=f.root/'metadata-trace'
                result=subprocess.run(['/usr/bin/strace','-f','-qq','-s','64','-o',str(trace),'-e','trace=execve',
                                       str(f.launcher('memory'))],input=f.raw,capture_output=True,
                                      cwd=f.root,env=f.env(),timeout=18)
                self.assert_preflight(f,'memory',result)
                self.assertNotIn('execve("/proc/self/fd/',trace.read_text())

    def test_source_exclusions_precede_unopened_invalid_binding_data(self):
        cases=('lifecycle-disabled','child','coordination-disabled','wake-context','other-home',
               'memory-marker','coordination-marker','unsupported','noncanonical-uuid')
        for case in cases:
            f=self.fixture(case)
            env=f.env()
            if case=='lifecycle-disabled': env['CAIRN_LIFECYCLE_DISABLED']='1'
            elif case=='child': env['CAIRN_LIFECYCLE_CHILD']='1'
            elif case=='coordination-disabled': env['CAIRN_COORDINATION_DISABLED']='1'
            elif case=='wake-context': env['CAIRN_WAKE_CONTEXT']='fixture-context'
            elif case=='other-home':
                f.memory['config_home']=str(f.root/'unselected-home')
                f.coord['config_home']=f.memory['config_home']
                f.publish_configuration();env['CODEX_HOME']=str(f.root/'selected-home')
            elif case.endswith('marker'): (f.root/('.cairn-no-memory' if case=='memory-marker' else '.cairn-no-coordination')).touch()
            elif case=='unsupported': f.raw=encoded(dict(f.event,hook_event_name='Stop'))
            else: f.raw=encoded(dict(f.event,session_id=SESSION.upper().replace('11111111','AAAAAAAA',1)))
            write(f.binding,b'not JSON and must remain unread')
            for role in ('memory','coordination'):
                with self.subTest(case=case,role=role):
                    result=subprocess.run(f.activation_argv(role),input=f.raw,capture_output=True,cwd=f.root,env=env,timeout=15)
                    if case=='noncanonical-uuid':
                        # Shaped but noncanonical native IDs do not enter the
                        # activation ledger. Admission may still require pins.
                        self.assertEqual(result.returncode,1)
                    else: self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":false}\n',b''))
                    self.assertFalse(Path(f.memory['state_dir']).exists())
                    self.assertEqual(f.api_calls(),[])

    def test_approved_noninteractive_shell_ignores_env_and_bash_env_startup_code(self):
        f=self.fixture('shell')
        sentinel=f.root/'startup-ran'
        startup=f.root/'startup.sh'
        write(startup,('touch '+shlex.quote(str(sentinel))+'\n').encode())
        env=dict(f.env(),ENV=str(startup),BASH_ENV=str(startup))
        result=subprocess.run([str(f.launcher('memory'))],input=f.raw,capture_output=True,cwd=f.root,env=env,timeout=18)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{}\n',b''))
        self.assertFalse(sentinel.exists())

    def test_other_unresolved_callers_refuse_three_before_any_python_activation(self):
        driver=r'''
import importlib.util,json,sys
from pathlib import Path
root=Path(sys.argv[1]);case=sys.argv[2]
sys.path.insert(0,str(root/'scripts'))
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
config=json.loads(Path(sys.argv[3]).read_text());event=json.loads(sys.stdin.buffer.read())
expected=(ValueError,)
if case=='coordination.handle':
 coord=load('unresolved_coord',root/'integrations/lifecycle/coordination.py')
 expected+=(coord.NativePromptRefused,)
try:
 if case=='coordination.handle': coord.handle(config,event)
 elif case=='memory.handle_event': load('unresolved_memory',root/'integrations/lifecycle/memory.py').handle_event(config,event,[])
 elif case=='preflight_source_fit':
  import preflight_source_fit as preflight
  preflight.load_hook(root/'integrations/lifecycle/memory.py').handle(config,event)
 else:
  import check_recall_observation as observation
  observation.engine.handle(config,event)
except expected as error:
 cause=error.__cause__ if case=='coordination.handle' else error
 if type(cause) is not ValueError: raise
 print(type(cause).__name__+': '+str(cause),file=sys.stderr);sys.exit(1)
raise SystemExit('unresolved /3 was accepted')
'''
        for case in ('coordination.handle','memory.handle_event','preflight_source_fit','check_recall_observation'):
            f=self.fixture('unresolved-'+case)
            config=f.coord_path if case.startswith('coordination') else f.memory_path
            result=subprocess.run([sys.executable,'-I','-S','-c',driver,str(ROOT),case,str(config)],input=f.raw,
                                  capture_output=True,cwd=f.root,env=f.env(),timeout=10)
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            self.assertEqual(result.stderr,b'ValueError: unsupported inbox recall activation state\n',result.stderr.decode(errors='replace'))
            self.assertFalse(Path(f.memory['state_dir']).exists())
            self.assertEqual(f.api_calls(),[])

    def test_whole_frontend_uses_one_checked_fd_go_exec_and_one_parent_config_read(self):
        for role in ('memory','coordination'):
            f=self.fixture('trace-'+role)
            # F04 retains its activation-only cell. F02 separately checks the
            # migrated SessionStart body with a real private API fixture.
            f.raw=encoded(dict(f.event,hook_event_name='UserPromptSubmit' if role=='memory' else 'SessionStart',prompt='',host_pid=os.getpid()))
            log=f.root/'trace'
            result=subprocess.run(['/usr/bin/strace','-f','-qq','-o',str(log),'-s','1024','-e','trace=execve,openat',str(f.launcher(role))],
                                  input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=18)
            self.assertEqual((result.returncode,result.stderr),(0,b''),result.stdout)
            rows=log.read_text().splitlines()
            selected=[r for r in rows if 'execve("/proc/self/fd/' in r and '"activation"' in r]
            self.assertEqual(len(selected),1,'\n'.join(r for r in rows if 'execve(' in r))
            config=str(f.memory_path if role=='memory' else f.coord_path)
            opens=[r for r in rows if 'openat(' in r and '"'+config+'"' in r]
            self.assertEqual(len(opens),2,'parent and Go each consume one current-config snapshot: '+'\n'.join(opens))
            for path in Path(f.memory['state_dir']).glob('*.json'):
                self.assertNotIn('inbox_activation',json.loads(path.read_text()),'non-wake may not enter activation')

    def test_legacy_complete_frontends_coexist_and_refuse_three_without_python_activation(self):
        for harness in ('codex','claude'):
            for role in ('memory','coordination'):
                for enabled in (False,True):
                    with self.subTest(harness=harness,role=role,enabled=enabled):
                        f=self.fixture(harness+role+str(enabled),harness=harness,enabled=enabled)
                        f.raw=encoded(dict(f.event,hook_event_name='SessionStart' if role=='coordination' else 'UserPromptSubmit',
                                           prompt='' if role=='coordination' else f.wake(),host_pid=os.getpid()))
                        new=f.public(role)
                        self.assertEqual((new.returncode,new.stderr),(0,b''),new.stdout)
                        snapshot_before={p.name:p.read_bytes() for p in Path(f.receipt['snapshot_root']).iterdir()}
                        legacy=f.root/'legacy'
                        self.legacy.stage_runtime(ROOT/'integrations/lifecycle',legacy,
                                                  {'memory.py':'memory-hook.py','coordination.py':'coordination-hook.py'})
                        argv=[sys.executable,'-I',str(legacy/(role+'-hook.py')),*(['--config',str(f.memory_path)] if role=='memory' else ['hook','--config',str(f.coord_path)])]
                        def accounting():
                            return {p.name:p.read_bytes() for p in Path(f.memory['state_dir']).glob('*')
                                    if '.memory-budget.' in p.name or '.inbox-recall.' in p.name}
                        prior_ledger=accounting()
                        refused=subprocess.run(argv,input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=18)
                        self.assertNotEqual(refused.returncode,0,'legacy /2 must not interpret /3')
                        self.assertNotIn(b'compatibility_preflight',refused.stderr,'legacy diagnostics stay legacy')
                        self.assertEqual(accounting(),prior_ledger,'a /2 caller cannot activate /3')
                        manifest=json.loads(f.binding.read_text())
                        manifest.pop('binary');manifest['schema']='cairn.inbox-recall-binding/2'
                        descriptor=self.legacy.bundle_descriptor(legacy)
                        for key,member in (('engine','memory.py'),('bridge','inbox_recall.py')):
                            manifest[key]=dict(path=str(legacy/member),sha256=sha((legacy/member).read_bytes()),bundle=descriptor)
                        write(f.binding,encoded(manifest))
                        if role=='coordination': shutil.rmtree(f.coord['state_dir'])
                        old=subprocess.run(argv,input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=18)
                        self.assertEqual((old.returncode,old.stderr),(0,b''),old.stdout)
                        self.assertEqual(new.stdout,old.stdout,'the same retained frontend result must survive the adapter')
                        self.assertEqual(accounting(),prior_ledger)
                        self.assertEqual({p.name:p.read_bytes() for p in Path(f.receipt['snapshot_root']).iterdir()},snapshot_before)


if __name__=='__main__': unittest.main()
