"""Owned full synthetic21/126/1900 boundary; every OS command is inert."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import test_trial_sdk_weekly_compatibility as compatibility
import test_trial_weekly_request as fixtures
import trial_task_eval as legacy
import trial_corpus_fingerprint as fingerprint
import trial_sdk_weekly_owners as owners
from trial_paired_request import canonical_sha,mcp_arguments
from trial_sdk_fixed_run import FixedRunOwner
import test_trial_sdk_fixed_run as run_fixtures


class Child:
    def __init__(self,command,**kwargs):
        self.returncode=None;self.waits=[];self.killed=False
        Path(command[command.index('--socket')+1]).touch()
    def poll(self):return self.returncode
    def terminate(self):pass
    def kill(self):self.killed=True
    def wait(self,timeout):
        self.waits.append(timeout)
        if not self.killed and len(self.waits)==1:raise subprocess.TimeoutExpired('synthetic api',timeout)
        self.returncode=-9;return -9


class Boundaries:
    def __init__(self,selected):
        self.selected=selected;self.calls=[];self.databases={};self.live=False
        self.notes=selected._runtime_selection['notes']+selected._runtime_selection['distractor_notes']
        self.ids={str(uuid.uuid5(uuid.NAMESPACE_URL,'task-eval:'+n['id'])):n for n in self.notes}
        self.fail=None;self.entities_wrong=False;self.status_override=None
    def run(self,command,*,env=None,input=None,timeout=120,cwd=None,check=True):
        cmd=[str(x) for x in command];self.calls.append(cmd)
        name=Path(cmd[0]).name
        if self.fail and self.fail(cmd):raise KeyboardInterrupt('synthetic boundary interruption')
        value={};code=0;raw=None
        if name=='initdb':Path(cmd[cmd.index('-D')+1]).mkdir()
        elif name=='pg_ctl':
            if 'start' in cmd:self.live=True
            elif 'stop' in cmd:self.live=False
            elif 'status' in cmd:code=self.status_override if self.status_override is not None else (0 if self.live else 3)
        elif name=='createdb':self.databases[cmd[-1]]={}
        elif name=='bash':
            assert env['GIT_AUTHOR_NAME']=='fixture' and env['GIT_COMMITTER_NAME']=='fixture'
            assert env['GIT_AUTHOR_DATE']==env['GIT_COMMITTER_DATE']=='2026-09-01T00:00:00Z'
            assert env['GIT_AUTHOR_EMAIL']==env['GIT_COMMITTER_EMAIL']=='fixture@example.invalid'
            (Path(cwd)/'.git').mkdir();(Path(cwd)/'setup-result.txt').write_text('synthetic setup output')
        elif name=='git':
            if cmd[1]=='rev-parse':raw=b'1111111111111111111111111111111111111111\n'
            elif cmd[1]=='for-each-ref':raw=b'refs/heads/main 1111111111111111111111111111111111111111\nrefs/tags/eval-base 1111111111111111111111111111111111111111\n'
            elif cmd[1]!='tag':raise AssertionError(cmd)
        elif name=='psql':
            db=self.databases[cmd[cmd.index('-d')+1]]
            value=dict(schema=fingerprint.SCHEMA,memory_record_columns=sorted((*fingerprint.MEMORY_FIELDS,'use_generation')),
                corpus={t:dict(rows=len(db),sha256=canonical_sha(db)) for t in fingerprint.TABLES})
            raw=json.dumps(value).encode()
        elif cmd[1]=='migrate':pass
        else:
            dbname=env['CAIRN_DATABASE_URL'].split('dbname=')[1].split(' ')[0];db=self.databases[dbname]
            if cmd[1]=='remember':
                note=self.ids[cmd[cmd.index('--request-id')+1]]
                record=str(uuid.uuid5(uuid.NAMESPACE_URL,dbname+note['id']))
                entities=[dict(kind=x.split('=',1)[0].removeprefix('--entity-'),name=x.split('=',1)[1])
                          for x in cmd if x.startswith('--entity-')]
                db[record]=dict(record_id=record,body=input.decode(),entities=entities)
                value=dict(record_id=record,version=1)
            elif cmd[1]=='get':
                value=dict(db[cmd[2]])
                if self.entities_wrong:value['entities']=[]
            elif cmd[1]=='preview-retract':
                self.assert_imports_complete(db)
                value=dict(version=1,preview_id='00000000-0000-4000-8000-000000000001')
            elif cmd[1]=='supersede':
                payload=json.loads(input);db[payload['record_id']]['superseded_by']=payload['replacement']['record_id']
            elif cmd[1]=='agent':
                value=dict(discovery=dict(state='ready',coverage=dict(indexed=len(db),eligible=len(db))))
            else:raise AssertionError(cmd)
        if raw is None:raw=json.dumps(dict(ok=True,data=value)).encode()
        return SimpleNamespace(returncode=code,stdout=raw,stderr=b'')
    def assert_imports_complete(self,db):
        assert len(db)==len(self.selected._runtime_selection['notes'])
    @contextlib.contextmanager
    def active(self):
        with patch.object(legacy,'run',side_effect=self.run),patch.object(legacy.subprocess,'Popen',Child):yield self


class OwnerTests(unittest.TestCase):
    setUp=compatibility.CompatibilityTests.setUp
    setup_binding=compatibility.CompatibilityTests.setup_binding
    config=compatibility.CompatibilityTests.config
    selected=compatibility.CompatibilityTests.selected

    def fixture(self):
        original=fixtures.Fixture
        def enriched(root):
            f=original(root)
            base=f.source/'workspaces/base'
            (base/'nested').mkdir();(base/'nested/setup.sh').write_text('nested retained')
            (base/'__pycache__').mkdir();(base/'__pycache__/omit.pyc').write_bytes(b'fixture cache')
            f.notes[0]['entities']=[dict(kind='file',name='src/synthetic.py')]
            for c in f.cases:c['workspace_commit']='1'*40
            (f.source/'corpus.json').write_text(json.dumps(dict(notes=f.notes)))
            (f.source/'cases.json').write_text(json.dumps(dict(baseline_commit='1'*40,cases=f.cases)))
            f.freeze();f.reseal_bundle();f.seal_reviews();return f
        with patch.object(fixtures,'Fixture',side_effect=enriched):result=self.selected()
        boundary=Boundaries(result[1])
        self.addCleanup(self.finish_fixture,result[1],boundary)
        return (*result,boundary)

    def finish_fixture(self,selected,boundary):
        with boundary.active():
            for run in getattr(selected._environment,'runs',()):run.cleanup_step()
            selected.close()

    def environment(self,selected):
        selected.prepare_environment(str(self.root/'environment'),'/synthetic/postgres')
        return selected._environment

    def actual_binding(self,selected):
        index=0;environment=selected._environment;row=selected.rows[index]
        workspace=environment.workspaces[index];store=environment.stores[row['arm']].store
        self.document['binding']['workspace_sha256']=selected.workspace_binding(index)
        self.document['mcp_binding'].update(socket=str(store.socket),token_file=str(store.token_file),repo=legacy.TRIAL_REPO)
        path=self.source/'mcp.json';path.chmod(0o600)
        raw=json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',command='/native/cairn',args=mcp_arguments(self.document['mcp_binding'],profile='weekly-sdk'))))).encode()
        path.write_bytes(raw);path.chmod(0o400)
        self.document['sources']['mcp']['sha256']=hashlib.sha256(raw).hexdigest()
        for role,key in (('cli','native'),('mcp_binary','arm')):
            config=selected._runtime_selection['execution']
            ref=config['native']['binary'] if key=='native' else config['memory_arms'][row['arm']]['binary']
            dest=Path(self.document['sources'][role]['path']);dest.chmod(0o600);dest.write_bytes(Path(ref['path']).read_bytes());dest.chmod(0o500)
            self.document['sources'][role]['sha256']=ref['sha256']
        self.document['binding']['source_set_sha256']=canonical_sha({k:v['sha256'] for k,v in self.document['sources'].items()})
        self.output=str(self.root/'actual-preparation');preparation,binding=self.setup_binding()
        config=self.config();config['policy'].update(self.document['mcp_binding'],executable=str(store.binary));config['cwd']=str(workspace.cwd)
        run=FixedRunOwner(binding.resources,clock=lambda:1);self.addCleanup(run.cleanup_step)
        with patch('time.monotonic_ns',return_value=1):
            selected.bind_loaded_run(run,preparation,binding,index,config,str(self.root/'fixed-loaded'),
                str(self.root/'callbacks'),run_fixtures.AGGREGATE,run_fixtures.OP_QUOTE,stream_output=str(self.root/'loaded-streams'))
        return run

    def test_full_held_selection_import_supersede_readiness_workspace_and_move(self):
        f,selected,_,_,boundary=self.fixture()
        with boundary.active():
            env=self.environment(selected)
            self.assertEqual(env.state,'ready')
            for arm,owner in env.stores.items():
                self.assertEqual(len(owner.store.ids),1904);self.assertEqual(len(owner.store.names),1904)
                self.assertEqual(owner.provenance[0]['entities'],f.notes[0]['entities'])
                self.assertEqual(owner.provenance[0]['provenance'],f.notes[0]['provenance'])
                self.assertEqual(owner.store.readiness['state'],'full_eligible_coverage')
                db=boundary.databases[owner.store.database]
                self.assertEqual(db[owner.store.ids['old']]['superseded_by'],owner.store.ids['current'])
            selected.prepare_workspace(0,str(self.root/'workspace'))
            w=env.workspaces[0]
            self.assertFalse((w.cwd/'setup.sh').exists())
            self.assertFalse((w.cwd/'__pycache__').exists())
            self.assertTrue((w.cwd/'nested/setup.sh').exists())
            setup=next(c for c in boundary.calls if c[0]=='bash')
            self.assertEqual(setup[2],str(f.source/'workspaces/base/setup.sh'))
            self.assertIn('setup-result.txt',w.prepared['initial'])
            self.assertIn('refs/tags/eval-base',w.prepared['initial_refs'])
            self.assertIn('refs/heads/main',w.prepared['initial_refs'])
            run=self.actual_binding(selected)
            self.assertFalse(env.close());self.assertTrue(boundary.live)
            self.assertEqual(run.cleanup_step(),'closed')
            self.assertTrue(env.close());self.assertFalse(boundary.live)
            for owner in env.stores.values():
                self.assertEqual(owner.wait,-9);self.assertEqual(owner.store.server.waits,[15,15])
                self.assertIsNotNone(owner.after)
            self.assertIs(run._weekly_workspace,w)

    def test_publication_retains_store_lifetime_integrity(self):
        self.assert_corpus_publication(changed=False)

    def test_publication_retains_changed_corpus(self):
        self.assert_corpus_publication(changed=True)

    def assert_corpus_publication(self, *, changed):
        _, selected, _, _, boundary = self.fixture()
        with boundary.active():
            env = self.environment(selected)
            selected.prepare_workspace(0, str(self.root/'workspace'))
            run = self.actual_binding(selected)
            owner = env.stores[run._paired_join['arm']]
            if changed:
                database = boundary.databases[owner.store.database]
                next(iter(database.values()))['body'] = 'changed after import'
            run.cleanup_step()
            self.assertTrue(env.close())
            run.publish_evidence(str(self.root/'corpus-evidence'))
            receipt = json.loads((self.root/'corpus-evidence/evidence.json').read_bytes())
            observation = receipt['store_corpus_observation']
            self.assertEqual(observation['scope'], 'store_lifetime')
            self.assertEqual(observation['arm'], run._paired_join['arm'])
            self.assertEqual(observation['state'], 'observed')
            self.assertEqual(observation['unchanged'], not changed)
            self.assertEqual(observation['before'], owner.before)
            self.assertEqual(observation['after'], owner.after)
            self.assertIsNone(receipt['observed_corpus_sha256'])
            self.assertEqual(receipt['grade'], 'ungraded')
            borrowed = env.corpus_observation(run)
            borrowed['before']['corpus'].clear()
            self.assertTrue(owner.before['corpus'])


    def test_publication_keeps_missing_final_observation_unknown(self):
        _, selected, _, _, boundary = self.fixture()
        with boundary.active():
            env = self.environment(selected)
            selected.prepare_workspace(0, str(self.root/'workspace'))
            run = self.actual_binding(selected)
            run.cleanup_step()
            run.publish_evidence(str(self.root/'pending-evidence'))
            receipt = json.loads((self.root/'pending-evidence/evidence.json').read_bytes())
            observation = receipt['store_corpus_observation']
            self.assertEqual(observation['state'], 'unknown')
            self.assertIsNone(observation['unchanged'])
            self.assertIsNone(observation['after'])
            self.assertIsNotNone(observation['before'])
            self.assertTrue(env.close())

    def test_failed_final_stamp_is_retained_as_unknown_without_requery(self):
        _, selected, _, _, boundary = self.fixture()
        with boundary.active():
            env = self.environment(selected)
            selected.prepare_workspace(0, str(self.root/'workspace'))
            run = self.actual_binding(selected)
            run.cleanup_step()
            boundary.fail = lambda cmd: Path(cmd[0]).name == 'psql'
            with self.assertRaises(KeyboardInterrupt):env.close()
            before = len(boundary.calls)
            run.publish_evidence(str(self.root/'failed-stamp-evidence'))
            receipt = json.loads((self.root/'failed-stamp-evidence/evidence.json').read_bytes())
            self.assertEqual(len(boundary.calls), before)
            observation = receipt['store_corpus_observation']
            self.assertEqual(observation['state'], 'unknown')
            self.assertIsNone(observation['unchanged'])
            self.assertIsNone(observation['after'])
            boundary.fail = None

    def test_entity_mismatch_consumes_load_keeps_partial_map_and_cleanup(self):
        _,selected,_,_,boundary=self.fixture();boundary.entities_wrong=True
        with boundary.active():
            with self.assertRaisesRegex(RuntimeError,'entity associations'):self.environment(selected)
            env=selected._environment;self.assertEqual(env.state,'unknown')
            self.assertEqual(len(env.stores),1)
            store=env.stores['CURRENT'].store
            self.assertEqual(len(store.import_results),1)
            self.assertEqual(store.ids['current'],store.import_results[0]['result']['record_id'])
            self.assertTrue(env.close());self.assertFalse(boundary.live)
            with self.assertRaisesRegex(ValueError,'weekly_environment_consumed'):self.environment(selected)

    def test_constructor_failure_after_database_creation_retains_shell(self):
        _,selected,_,_,boundary=self.fixture();boundary.fail=lambda cmd:len(cmd)>1 and cmd[1]=='migrate'
        with boundary.active():
            with self.assertRaises(KeyboardInterrupt):self.environment(selected)
            env=selected._environment;owner=env.stores['CURRENT']
            self.assertIsNotNone(owner.store.sockdir);self.assertEqual(len(boundary.databases),1)
            self.assertEqual(owner.store._constructor_socket_cleanup,'removed')
            self.assertFalse(owner.store.sockdir.exists())
            boundary.fail=None;self.assertTrue(env.close())
            self.assertFalse(owner.store.sockdir.exists())

    def test_constructor_socket_cleanup_failure_remains_unknown_without_retry(self):
        _,selected,_,_,boundary=self.fixture();boundary.fail=lambda cmd:len(cmd)>1 and cmd[1]=='migrate'
        original=shutil.rmtree;attempts=[]
        def removal(path,*args,**kwargs):
            if Path(path).name.startswith('cte-sock-'):
                attempts.append(path);raise PermissionError('synthetic socket cleanup failure')
            return original(path,*args,**kwargs)
        with boundary.active(),patch('trial_task_eval.shutil.rmtree',side_effect=removal):
            with self.assertRaises(KeyboardInterrupt):self.environment(selected)
            env=selected._environment;owner=env.stores['CURRENT'];path=owner.store.sockdir
            self.assertEqual(owner.store._constructor_socket_cleanup,'unknown')
            self.assertTrue(path.is_dir());self.assertEqual(len(attempts),1)
            boundary.fail=None
            with self.assertRaisesRegex(ValueError,'weekly_constructor_socket_cleanup_unknown'):env.close()
            self.assertEqual(owner.close_states['socket'],'unknown')
            self.assertEqual(owner.close_states['process'],'closed')
            self.assertFalse(owner.close());self.assertEqual(len(attempts),1)
        original(path)

    def test_recreated_constructor_socket_is_not_removed_as_original(self):
        _,selected,_,_,boundary=self.fixture();boundary.fail=lambda cmd:len(cmd)>1 and cmd[1]=='migrate'
        with boundary.active():
            with self.assertRaises(KeyboardInterrupt):self.environment(selected)
            env=selected._environment;owner=env.stores['CURRENT'];path=owner.store.sockdir
            path.mkdir();(path/'replacement').write_text('synthetic replacement')
            boundary.fail=None
            with self.assertRaisesRegex(ValueError,'weekly_constructor_socket_reappeared'):env.close()
            self.assertEqual(owner.close_states['socket'],'unknown')
            self.assertTrue((path/'replacement').exists())
        shutil.rmtree(path)

    def test_unknown_cluster_status_retains_original_directory(self):
        _,selected,_,_,boundary=self.fixture()
        with boundary.active():
            env=self.environment(selected);boundary.status_override=4
            self.assertFalse(env.close());self.assertTrue(env.cluster.root.exists())
            count=len(boundary.calls);self.assertFalse(env.close());self.assertEqual(len(boundary.calls),count)

    def test_destinations_cannot_overlap_selected_source(self):
        f,selected,_,_,boundary=self.fixture()
        with boundary.active():
            with self.assertRaisesRegex(ValueError,'weekly_destination_overlap'):
                selected.prepare_environment(str(f.source/'bad'),'/synthetic/postgres')
            self.assertEqual(boundary.calls,[]);self.assertFalse((f.source/'bad').exists())

    def test_workspace_overlap_refuses_before_setup(self):
        f,selected,_,_,boundary=self.fixture()
        with boundary.active():
            env=self.environment(selected);before=len(boundary.calls)
            with self.assertRaisesRegex(ValueError,'weekly_destination_overlap'):
                selected.prepare_workspace(0,str(f.source/'workspaces/base/child'))
            self.assertEqual(len(boundary.calls),before)
            self.assertFalse((f.source/'workspaces/base/child').exists())
            self.assertTrue(env.close())

    def test_held_snapshot_destination_refuses_before_cluster_effect(self):
        f,selected,_,_,boundary=self.fixture()
        target=Path(f.request['output'])/'owned-selection/nested-environment'
        with boundary.active():
            with self.assertRaisesRegex(ValueError,'weekly_destination_overlap'):
                selected.prepare_environment(str(target),'/synthetic/postgres')
            self.assertEqual(boundary.calls,[]);self.assertFalse(target.exists())

    def test_supersede_interruption_retains_completed_imports_no_replay(self):
        _,selected,_,_,boundary=self.fixture()
        boundary.fail=lambda cmd:len(cmd)>1 and cmd[1]=='supersede'
        with boundary.active():
            with self.assertRaises(KeyboardInterrupt):self.environment(selected)
            env=selected._environment;store=env.stores['CURRENT'].store
            self.assertEqual(len(store.ids),4);self.assertEqual(len(store.import_results),4)
            self.assertEqual(len(store.names),4)
            boundary.fail=None;self.assertTrue(env.close())
            with self.assertRaisesRegex(ValueError,'weekly_environment_consumed'):self.environment(selected)

    def test_readiness_interruption_keeps_all_1900_growth_and_waits(self):
        _,selected,_,_,boundary=self.fixture()
        boundary.fail=lambda cmd:len(cmd)>1 and cmd[1]=='agent'
        with boundary.active():
            with self.assertRaises(KeyboardInterrupt):self.environment(selected)
            env=selected._environment;owner=env.stores['CURRENT']
            self.assertEqual(len(owner.store.ids),1904);self.assertIsNone(owner.store.readiness)
            boundary.fail=None;self.assertTrue(env.close());self.assertEqual(owner.wait,-9)

    def test_setup_failure_keeps_partial_workspace_and_consumed_row(self):
        _,selected,_,_,boundary=self.fixture()
        with boundary.active():
            env=self.environment(selected)
            boundary.fail=lambda cmd:cmd[0]=='bash'
            with self.assertRaises(KeyboardInterrupt):selected.prepare_workspace(0,str(self.root/'failed-workspace'))
            w=env.workspaces[0];self.assertEqual(w.state,'unknown')
            self.assertTrue((w.root/'repo/private-grade.txt').exists())
            self.assertIsNone(w.prepared)
            boundary.fail=None;self.assertTrue(env.close())

    def test_legacy_collection_only_added_to_sdk_profile(self):
        binding=dict(socket='/s',token_file='/t',repo=legacy.TRIAL_REPO,task='t',run='r')
        with self.assertRaisesRegex(ValueError,'absolute_path_required'):mcp_arguments(binding)
        argv=mcp_arguments(binding,profile='weekly-sdk');self.assertEqual(argv[argv.index('--repo')+1],legacy.TRIAL_REPO)
        binding['repo']='trial:other'
        with self.assertRaisesRegex(ValueError,'absolute_path_required'):mcp_arguments(binding,profile='weekly-sdk')

    def test_weekly_workspace_is_not_prospective_empty_ref_contract(self):
        from trial_work_product import _check_prepared
        _,selected,_,_,boundary=self.fixture()
        with boundary.active():
            env=self.environment(selected);selected.prepare_workspace(0,str(self.root/'workspace'))
            with self.assertRaisesRegex(ValueError,'prepared_shape'):_check_prepared(env.workspaces[0].prepared)
            self.assertTrue(env.close())
