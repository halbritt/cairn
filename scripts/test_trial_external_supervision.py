"""Real disposable processes and kernel containment; run in the owned test service."""
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import unittest
from unittest.mock import patch
import uuid

import trial_external_supervision as s
import trial_fixture_service as fs
from trial_fixture_service import service_fixture


class ExternalSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.retained = self.root/'retained'
        self.workspace = self.root/'workspace'
        self.retained.mkdir(mode=0o700)
        self.workspace.mkdir(mode=0o700)
        self.request = s.FixtureRequest(str(uuid.uuid4()), str(uuid.uuid4()), *(['a'*64]*4))
        # Missing real containment support fails the suite; it cannot yield a
        # skipped/mock pass. The public API still refuses safely before actor.
        self.delegated = s.fixture_service_delegation()

    def tearDown(self):
        self.temp.cleanup()

    def run_attempt(self, **kwargs):
        request = dataclasses.replace(self.request, **kwargs)
        started = time.monotonic()
        result = s.run_fixture(request, self.retained, self.workspace)
        self.assertLess(time.monotonic()-started, request.wall_seconds+.3)
        self.assertFalse(result.provider_executed)
        self.assertFalse(result.native_route_admitted)
        self.assertFalse(result.runtime_evidence_authenticated)
        self.assertEqual(result.task_outcome, 'not_evaluated')
        self.assertTrue(result.containment['released'])
        for wait in result.outer_waits.values():
            self.assertIsNotNone(wait)
            self.assertEqual(os.waitstatus_to_exitcode(wait['raw_status']), wait['returncode'])
        self.assertFalse(any(Path(self.delegated).glob('cairn-fixture-*')))
        return result

    def test_normal_intent_full_input_wait_release_and_selected_privacy(self):
        with patch.object(s,'sandbox',wraps=s.sandbox) as mounts:
            result = self.run_attempt()
        roles = {call.args[2][0]:call.args[0] for call in mounts.call_args_list}
        self.assertEqual({target for _,target in roles['retention']},
            {'worker.py', *('selected_native/'+name for name in s.SOURCE_PINS)})
        self.assertTrue(all(not target.startswith('selected_native/')
            for role,sources in roles.items() if role != 'retention' for _,target in sources))
        self.assertEqual(result.status, 'fixture_complete', result.failures)
        self.assertTrue(result.intent['acknowledged_before_actor'])
        self.assertTrue(result.owned_fixture_observations)
        self.assertEqual(result.actor_wait['raw_status'], 0)
        self.assertEqual(result.actor_wait['input_bytes_written'], 8500)
        self.assertEqual(result.persistence, 'returned')
        run = self.retained/self.request.run_id
        self.assertEqual({p.name for p in run.iterdir()}, {'INTENT.json','SELECTED.json','PREPARED.json','SUPERVISOR.json'})
        selected = (run/'SELECTED.json').read_bytes()
        self.assertIsNone(json.loads(selected)['init']['profile'])
        self.assertNotIn(b'PRIVATE_TRANSCRIPT_SENTINEL', selected)
        self.assertEqual(s.digest(selected), result.prepared_receipt['selected_sha256'])
        terminal = json.loads((run/'SUPERVISOR.json').read_bytes())
        self.assertEqual(terminal['persistence'], 'not_attempted')
        self.assertFalse(terminal['standalone_record_is_success'])
        intent = json.loads((run/'INTENT.json').read_bytes())
        manifest = s.source_manifest(s.reviewed_package(s.SELECTED_PACKAGE,s.SOURCE_PINS))
        self.assertEqual(intent['source_manifest_sha256'],s.digest(s.encoded(manifest)))
        self.assertEqual({key for key in manifest if key.startswith('selected_native/')},
            {'selected_native/'+name for name in s.SOURCE_PINS})

    def test_never_reading_input_8500_pipe4096_stops_on_external_deadline(self):
        result = self.run_attempt(actor='never_read', wall_seconds=2)
        self.assertEqual(result.status, 'failed')
        self.assertTrue({'wall_deadline','capture_not_completed'} & set(result.failures))
        self.assertIsNone(result.prepared_receipt)
        self.assertTrue(result.actor_wait is None or result.actor_wait['returncode'] != 0)
        self.assertTrue((self.retained/self.request.run_id/'INTENT.json').exists())

    def test_valid_looking_final_with_incomplete_input_is_failed(self):
        result = self.run_attempt(actor='early_close')
        self.assertEqual(result.status, 'failed')
        self.assertIn('input_delivery_incomplete', result.failures)
        self.assertEqual(result.actor_wait['returncode'], 0)
        self.assertLess(result.actor_wait['input_bytes_written'], 8500)
        self.assertIsNone(result.prepared_receipt)

    def test_nonzero_retains_actual_exit_without_prepared(self):
        result = self.run_attempt(actor='nonzero')
        self.assertIn('actor_nonzero', result.failures)
        self.assertEqual(result.actor_wait['returncode'], 17)
        self.assertEqual(result.actor_wait['raw_status'], 17 << 8)
        self.assertIsNone(result.prepared_receipt)

    def test_capture_report_loss_retains_outer_wait_and_unknown_actor_wait(self):
        result = self.run_attempt(actor='capture_report_loss')
        self.assertEqual(result.status,'failed')
        self.assertIsNone(result.actor_wait)
        self.assertEqual(result.outer_waits['actor']['returncode'],36)
        self.assertIn('supervisor_observation_unknown',result.failures)
        self.assertIsNone(result.prepared_receipt)

    def test_escaped_reparented_descendant_kernel_release(self):
        result = self.run_attempt(actor='descendant')
        self.assertEqual(result.status, 'fixture_complete', result.failures)
        self.assertEqual(result.containment['scope'], 'exclusive_owned_kernel_cgroup')
        self.assertTrue(result.containment['released'])

    def test_parser_hang_and_sink_fsync_hang_are_external_failures(self):
        for mode in ['parse_hang','fsync_hang']:
            with self.subTest(mode=mode):
                self.request = dataclasses.replace(self.request, run_id=str(uuid.uuid4()))
                result = self.run_attempt(sink=mode, wall_seconds=2)
                self.assertEqual(result.status, 'failed')
                self.assertIn('wall_deadline', result.failures)
                self.assertNotEqual(result.outer_waits['sink']['returncode'], 0)
                self.assertIsNone(result.prepared_receipt)

    def test_prepared_marker_without_worker_return_is_failure(self):
        result = self.run_attempt(sink='after_prepared')
        self.assertEqual(result.outer_waits['sink']['returncode'], 33)
        self.assertIn('sink_return_unconfirmed', result.failures)
        self.assertIsNone(result.prepared_receipt)
        self.assertTrue((self.retained/self.request.run_id/'PREPARED.json').exists())
        self.assertEqual(result.status, 'failed')

    def test_diagnostic_hang_preserves_primary_failure_and_release(self):
        result = self.run_attempt(actor='nonzero', diagnostic='persist_hang', wall_seconds=2)
        self.assertEqual(result.status, 'failed')
        self.assertIn('actor_nonzero', result.failures)
        self.assertEqual(result.actor_wait['returncode'], 17)
        self.assertEqual(result.persistence, 'unknown')

    def test_memory_and_pid_exhaustion_are_kernel_bounded(self):
        for mode, event, field in [('memory','memory.events','oom_kill'), ('pids','pids.events','max')]:
            with self.subTest(mode=mode):
                self.request = dataclasses.replace(self.request, run_id=str(uuid.uuid4()))
                result = self.run_attempt(actor=mode)
                self.assertEqual(result.status, 'failed')
                self.assertGreater(int(result.containment['resource_events'][event][field]),0)
                self.assertIsNone(result.prepared_receipt)

    def test_actor_cannot_read_sink_or_evaluator_origin_or_credentials(self):
        (self.root/'oracle').write_text('PRIVATE_ORACLE_SENTINEL')
        result = self.run_attempt(actor='privacy')
        self.assertEqual(result.status, 'fixture_complete', result.failures)
        work = self.workspace/self.request.run_id/'public-fixture.txt'
        self.assertEqual(work.read_text(), 'fixture work product\n')
        for path in (self.retained/self.request.run_id).iterdir():
            self.assertNotIn('PRIVATE_ORACLE_SENTINEL',path.read_text())

    def test_consumed_target_is_never_replayed(self):
        first = self.run_attempt()
        intent = (self.retained/self.request.run_id/'INTENT.json').read_bytes()
        second_workspace = self.root/'other-workspace'
        second_workspace.mkdir(mode=0o700)
        second = s.run_fixture(self.request,self.retained,second_workspace)
        self.assertEqual(second.status, 'refused_prelaunch')
        self.assertIn('target_consumed', second.failures)
        self.assertNotIn('actor',second.outer_waits)
        self.assertEqual(intent,(self.retained/self.request.run_id/'INTENT.json').read_bytes())
        self.assertEqual(first.prepared_receipt['intent_sha256'], s.digest(intent))

    def test_missing_containment_and_changed_source_refuse_before_actor(self):
        result = s.run_fixture(self.request,self.retained,self.workspace,delegated='/sys/fs/cgroup/nonexistent-cairn-fixture')
        self.assertEqual(result.status, 'refused_prelaunch')
        self.assertFalse(result.outer_waits)
        self.assertFalse((self.retained/self.request.run_id).exists())
        with patch.object(s,'SOURCE_PINS',{**s.SOURCE_PINS,'frames.py':'0'*64}):
            result = s.run_fixture(self.request,self.retained,self.workspace)
        self.assertEqual(result.status, 'refused_prelaunch')
        self.assertFalse(result.outer_waits)

    def test_namespace_verification_failure_never_admits_actor(self):
        with patch.object(s.Child,'verify_namespace',side_effect=s.Unavailable('fixture_missing_namespace')):
            result = self.run_attempt()
        self.assertEqual(result.status, 'refused_prelaunch')
        self.assertIsNone(result.intent)
        self.assertNotIn('actor',result.outer_waits)

    def test_native_has_no_command_entry_and_is_always_refused(self):
        result = s.refuse_native()
        self.assertEqual(result.status, 'refused_prelaunch')
        self.assertFalse(result.provider_executed)
        self.assertFalse(result.outer_waits)
        self.assertIn('native_capacity_reservation_unestablished',result.failures)

    def test_unobserved_release_preserves_unknown_with_actual_worker_waits(self):
        real_end = s.end_containment
        calls = []
        def unknown_release(group, children, deadline):
            released, errors = real_end(group, children, deadline)
            calls.append(released)
            return (None, errors+['containment_release_unknown']) if len(calls)==1 else (released,errors)
        with patch.object(s,'end_containment',side_effect=unknown_release):
            result=s.run_fixture(self.request,self.retained,self.workspace)
        self.assertEqual(result.status,'unknown')
        self.assertIsNone(result.containment['released'])
        self.assertIn('containment_release_unknown',result.failures)
        self.assertEqual(result.outer_waits['sink']['returncode'],0)
        self.assertEqual(result.actor_wait['returncode'],0)

    def test_controller_sigkill_at_capture_parser_and_sink_has_external_release(self):
        for stage,actor,sink in [('intent','normal','normal'),('capture','never_read','normal'),
                                ('parser','normal','parse_hang'),('sink','normal','fsync_hang')]:
            with self.subTest(stage=stage):
                self.request=dataclasses.replace(self.request,run_id=str(uuid.uuid4()),actor=actor,sink=sink,wall_seconds=6)
                result=service_fixture(self.request,self.retained,self.workspace,loss_stage=stage)
                self.assertEqual(result.status,'unknown',dataclasses.asdict(result))
                self.assertIn('controller_observation_lost',result.failures)
                self.assertTrue(result.containment['released'],dataclasses.asdict(result))
                service=result.containment['service']
                self.assertEqual(service['waitid_code'],2)
                self.assertEqual(service['waitid_status'],9)
                self.assertEqual(service['returncode'],-9)
                self.assertIsNone(result.actor_wait)
                self.assertIsNone(result.prepared_receipt)
                self.assertFalse(result.provider_executed)
                self.assertTrue((self.retained/self.request.run_id/'INTENT.json').exists())

    def test_service_owner_uses_manager_actual_status_not_proxy_exit(self):
        result=service_fixture(self.request,self.retained,self.workspace)
        self.assertEqual(result.status,'fixture_complete',dataclasses.asdict(result))
        service=result.containment['service']
        self.assertEqual(service['waitid_code'],1)
        self.assertEqual(service['waitid_status'],0)
        self.assertEqual(service['returncode'],0)
        self.assertTrue(service['release'])
        self.assertTrue(service['invocation_id'])

    def test_changed_controller_identity_is_refused_without_actor_gate(self):
        original=fs.manager_properties
        def changed(unit):
            value=original(unit)
            value['MainPID']=str(int(value['MainPID'])+100000)
            return value
        with patch.object(fs,'manager_properties',side_effect=changed):
            result=service_fixture(self.request,self.retained,self.workspace)
        self.assertEqual(result.status,'unknown')
        self.assertIn('service_owner_unknown',result.failures)
        self.assertIsNone(result.actor_wait)
        self.assertIsNone(result.containment['released'])
        self.assertFalse((self.retained/self.request.run_id).exists())

    def test_service_cleanup_faults_preserve_observed_wait_release_and_unknown(self):
        for fault in ['proxy_wait','stop','reset','post_release_query']:
            with self.subTest(fault=fault):
                self.request=dataclasses.replace(self.request,run_id=str(uuid.uuid4()),wall_seconds=6)
                original_wait=subprocess.Popen.wait
                original_run=subprocess.run
                original_properties=fs.manager_properties
                injected=[]
                exited_queries=[]
                def wait(process,*args,**kwargs):
                    if fault=='proxy_wait' and process.args[0]=='systemd-run' and len(injected)<2:
                        original_wait(process,*args,**kwargs)
                        injected.append(True)
                        raise subprocess.TimeoutExpired('PRIVATE_PROXY',.5)
                    return original_wait(process,*args,**kwargs)
                def run(argv,*args,**kwargs):
                    value=original_run(argv,*args,**kwargs)
                    selected=fault=='stop' and argv[:3]==['systemctl','--user','stop'] or fault=='reset' and argv[:3]==['systemctl','--user','reset-failed']
                    if selected:
                        injected.append(True)
                        return subprocess.CompletedProcess(argv,1)
                    return value
                def properties(unit):
                    value=original_properties(unit)
                    if value.get('ExecMainCode')=='1' and value.get('ExecMainStatus')=='0':
                        exited_queries.append(True)
                        if fault=='post_release_query' and len(exited_queries)==2:
                            injected.append(True)
                            raise ValueError('PRIVATE_MANAGER_QUERY')
                    return value
                with patch.object(subprocess.Popen,'wait',wait),patch.object(subprocess,'run',run),patch.object(fs,'manager_properties',properties):
                    result=service_fixture(self.request,self.retained,self.workspace,loss_stage='intent' if fault=='reset' else None)
                self.assertTrue(injected)
                self.assertEqual(result.status,'unknown',dataclasses.asdict(result))
                if fault=='reset':
                    self.assertIsNone(result.actor_wait)
                    self.assertEqual(result.containment['service']['returncode'],-9)
                else:
                    self.assertEqual(result.actor_wait['returncode'],0)
                    self.assertEqual(result.containment['service']['returncode'],0)
                self.assertTrue(result.containment['service']['release'])
                self.assertTrue(result.containment['released'])
                self.assertNotIn('PRIVATE',json.dumps(dataclasses.asdict(result)))

    def test_inner_worker_and_cgroup_close_faults_keep_facts_and_continue_cleanup(self):
        for fault in ['primary_child','primary_descriptor','diagnostic_child','diagnostic_group']:
            with self.subTest(fault=fault):
                self.request=dataclasses.replace(self.request,run_id=str(uuid.uuid4()))
                child_close=s.Child.close
                group_close=s.OwnedCgroup.close
                children=[]
                groups=[]
                closed=[]
                expected=[]
                original_close=os.close
                def close_child(child):
                    errors=child_close(child)
                    children.append(child.process.pid)
                    if fault=='primary_child' and len(children)==1 or fault=='diagnostic_child' and len(children)==3:
                        raise OSError('PRIVATE_CHILD_CLOSE')
                    return errors
                def close_group(group):
                    groups.append(group.identity)
                    if fault=='primary_descriptor' and len(groups)==1:
                        expected.extend([*group.files.values(),group.fd,group.parent])
                        def one_close_failure(fd):
                            closed.append(fd)
                            original_close(fd)
                            if fd==expected[0]:raise OSError('PRIVATE_DESCRIPTOR_CLOSE')
                        with patch.object(os,'close',side_effect=one_close_failure):
                            return group_close(group)
                    errors=group_close(group)
                    if fault=='diagnostic_group' and len(groups)==2:
                        raise OSError('PRIVATE_GROUP_CLOSE')
                    return errors
                with patch.object(s.Child,'close',close_child),patch.object(s.OwnedCgroup,'close',close_group):
                    result=s.run_fixture(self.request,self.retained,self.workspace)
                self.assertEqual(result.status,'unknown',dataclasses.asdict(result))
                self.assertEqual(result.actor_wait['returncode'],0)
                self.assertEqual(result.outer_waits['sink']['returncode'],0)
                self.assertEqual(result.outer_waits['diagnostic']['returncode'],0)
                self.assertTrue(result.containment['released'])
                self.assertTrue(result.containment['diagnostic']['released'])
                self.assertEqual(len(children),3)
                self.assertEqual(len(groups),2)
                if fault=='primary_descriptor':self.assertEqual(closed,expected)
                self.assertTrue(result.failures)
                self.assertNotIn('PRIVATE',json.dumps(dataclasses.asdict(result)))
                self.assertFalse(any(Path(self.delegated).glob('cairn-fixture-*')))


if __name__ == '__main__':
    unittest.main()
