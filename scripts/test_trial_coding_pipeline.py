"""Real fixed synthetic capture -> CodingTransport -> root CodingSink.

SDK/profile/account/hook rows are constructed. These tests observe synthetic
actor input/wait, capture clocks, owned worker waits and selected file custody.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
import uuid

from selected_native.retention import Intent, SELECTED_LIMIT, encode
from trial_external_supervision import fixture_service_delegation, sandbox
from trial_owned_workers import AttemptDomain, OwnedWorker, Unavailable
from trial_coding_transport import CodingTransport
from trial_worker_protocol import encoded
from test_trial_coding_sink_boundary import sources as sink_sources
from supervision_fixture.coding_pipeline_worker import INPUT_MESSAGE, synthetic_records


SCRIPTS=Path(__file__).resolve().parent


def sources():
    # Reuse the real sink's complete local import closure, changing only the
    # entrypoint mapping so the fixed pipeline worker can import the real sink.
    result=[(path,'supervision_fixture/coding_sink_worker.py' if target=='worker.py' else target)
            for path,target in sink_sources()]
    return result+[(SCRIPTS/'supervision_fixture/coding_pipeline_worker.py','worker.py')]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.output=root/'output';self.output.mkdir(mode=0o700)
        source_root=root/'readonly-sources';source_root.mkdir(mode=0o700)
        self.sources=[];manifest={}
        for path,target in sources():
            data=path.read_bytes();copy=source_root/target
            copy.parent.mkdir(parents=True,exist_ok=True)
            copy.write_bytes(data);copy.chmod(0o444)
            self.sources.append((copy,target));manifest[target]=hashlib.sha256(data).hexdigest()
        for name in ('trial_coding_transport.py','trial_worker_transport.py','trial_owned_workers.py',
                     'trial_external_supervision.py','trial_task_eval.py','trial_selected_native_mounts.py',
                     'supervision_fixture/launcher.py','test_trial_coding_pipeline.py',
                     'test_trial_coding_sink_boundary.py'):
            manifest['controller/'+name]=hashlib.sha256((SCRIPTS/name).read_bytes()).hexdigest()
        self.source=hashlib.sha256(encoded(manifest)).hexdigest()
        self.run=str(uuid.uuid4())
        intent=Intent(self.run,'a'*64,'b'*64,'c'*64,self.source)
        body=dict(schema='cairn.selected-retention-intent/1',**dataclasses.asdict(intent),
                  admission_basis='caller_pinned_not_verified',launch_performed_by_sink=False)
        self.intent_sha256=hashlib.sha256(encode(body)).hexdigest()
        self.domain=AttemptDomain(fixture_service_delegation())
        self.transport=CodingTransport(self.domain,self.run,self.source,self.intent_sha256)
        self.deadline=time.monotonic()+4
        self.addCleanup(self.cleanup)
        self.end=None;self.closed=None;self.selected_files={}

    def cleanup(self):
        result=self.domain.close(time.monotonic()+1)
        self.transport.forget_transient()
        self.assertFalse(self.transport.queue or self.transport.input)
        self.assertFalse(any(any(child.buffers.values()) for child in self.domain.children.values()))
        self.assertTrue(result['released'],result)
        self.assertFalse(result['errors'])
        self.assertFalse([error for role in result['roles'].values() for error in role['errors']])
        self.assertEqual(result['aggregate_resources'],dict(memory_max=512*1024*1024,
            memory_swap_max=0,pids_max=32,cpu_max='100000 100000'))
        print('SELECTED_PIPELINE_RESULT '+json.dumps(dict(case=self.id().rsplit('.',1)[-1],
            run_id=self.run,source_manifest_sha256=self.source,end=self.end,
            capture_start=self.transport.start_record,
            accepted_sink_closure=self.closed,containment=result,selected_files=self.selected_files,
            provenance=dict(sdk_records='constructed',profile='constructed',hook_rows='constructed_or_unknown',
                            account='unproved',native='unproved',validity_decided=False,task_acceptance=False)),
            sort_keys=True),flush=True)

    def pump(self,predicate):
        while not predicate() and time.monotonic()<self.deadline:self.transport.step()
        self.assertTrue(predicate(),'live coding pipeline condition missing')

    @staticmethod
    def drained(child):
        return child.wait is not None and {'stdout','stderr'}<=child.eof

    def worker(self,role,mode):
        bindings=[(self.output,'/output')] if role=='composition' else []
        child=OwnedWorker(self.domain.leaf(role),sandbox(self.sources,bindings,
            [role,mode,self.run,self.source,self.intent_sha256]))
        self.domain.bind(role,child)
        return child

    def start(self,mode='success',sink='normal'):
        self.sink=self.worker('composition',sink)
        self.pump(lambda:self.transport.ready)
        # Real root retention consumed the exclusive intent before any actor.
        intent=(self.output/self.run/'INTENT.json').read_bytes()
        self.assertEqual(hashlib.sha256(intent).hexdigest(),self.intent_sha256)
        self.assertNotIn('capture',self.domain.children)
        self.capture=self.worker('capture',mode)
        self.pump(lambda:self.transport.gate_capture(INPUT_MESSAGE) if not self.transport.gated else True)
        self.assertLessEqual(len(INPUT_MESSAGE),8500)
        self.assertEqual(self.transport.stream_limit,4*1024*1024)

    def hooks(self,unknown=False):
        rows=[dict(event=event,stdout_sha256=hashlib.sha256(b'{}\n').hexdigest(),stdout_bytes=3,
            stderr_bytes=0,process_seconds=.1,exit_code=0,
            memory_call_attempts=dict(search=0,pull=0,history=0,other=0,**{'pull-evidence':0}))
              for event in ('SessionStart','UserPromptSubmit')]
        return dict(schema='cairn.worker-hook-measurements/1',run_id=self.run,
            source_manifest_sha256=self.source,rows=None if unknown else rows,
            observations=['hook_measurements_unknown'] if unknown else [])

    def end_capture(self,mode='success',unknown_hooks=False):
        self.pump(lambda:self.drained(self.capture))
        closed=self.domain.close_role('capture',self.deadline)
        self.assertTrue(closed['released']);self.assertFalse(closed['errors'])
        if mode=='lost_wait':
            with self.assertRaisesRegex(Unavailable,'metadata_unknown'):self.transport.capture_report()
            actor=None;written=None;observations=['capture_metadata_missing']
        else:
            metadata=self.transport.capture_report()['final']
            self.assertEqual(metadata['input_bytes'],len(INPUT_MESSAGE))
            self.assertEqual(metadata['input_bytes_written'],len(INPUT_MESSAGE))
            actor=metadata['actor_wait'];written=metadata['input_bytes_written'];observations=metadata['observations']
            self.assertGreater(actor['pid'],0);self.assertGreater(actor['start_ticks'],0)
            self.assertEqual(actor['boot_id'],Path('/proc/sys/kernel/random/boot_id').read_text().strip())
            self.assertEqual(os.waitstatus_to_exitcode(actor['raw_status']),actor['returncode'])
        self.end=self.transport.end_capture(actor,input_bytes=len(INPUT_MESSAGE),input_bytes_written=written,
            observations=observations,hook_measurements=self.hooks(unknown_hooks))
        raw=b''.join(encoded(row)+b'\n' for row in synthetic_records(self.run,mode=='actor_nonzero'))
        self.assertEqual(self.end['stream_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.end['stream_bytes'],len(raw))
        self.assertEqual(self.end['observed_stream_bytes'],len(raw))
        self.assertEqual(self.end['capture_wait']['returncode'],0)
        self.assertTrue(self.end['capture_released'])
        clocks=closed['namespace_identity']['clock_domain']
        self.assertEqual(len(set(clocks.values())),1)
        self.assertGreater(self.end['timing_packets'],0)
        self.assertLessEqual(len(self.transport.queue),128*1024)
        if mode!='future_start':
            self.assertIsNotNone(self.transport.start_ns)
            self.assertGreaterEqual(self.transport.read_ns,self.transport.start_ns)

    def inspect(self,name):
        path=self.output/self.run/name
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            meta=os.fstat(fd)
            self.assertTrue(stat.S_ISREG(meta.st_mode));self.assertEqual(meta.st_uid,os.geteuid())
            self.assertEqual(stat.S_IMODE(meta.st_mode),0o600);self.assertEqual(meta.st_nlink,1)
            self.assertLessEqual(meta.st_size,SELECTED_LIMIT)
            with os.fdopen(fd,'rb',closefd=False) as source:data=source.read(SELECTED_LIMIT+1)
            self.assertEqual(len(data),meta.st_size)
            self.selected_files[name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
                dev=meta.st_dev,inode=meta.st_ino,uid=meta.st_uid,mode=stat.S_IMODE(meta.st_mode),nlink=meta.st_nlink)
            return json.loads(data)
        finally:os.close(fd)

    def finish(self):
        self.pump(lambda:self.drained(self.sink))
        closed=self.domain.close_role('composition',self.deadline)
        self.assertTrue(closed['released']);self.assertFalse(closed['errors'])
        self.closed=self.transport.closed_sink()
        return self.closed

    def assert_persistence(self,outcome):
        self.assertIsNotNone(self.closed)
        self.assertEqual(self.closed['wait']['raw_status'],0)
        self.assertEqual(self.closed['wait']['returncode'],0)
        self.assertTrue(self.closed['released']);self.assertIsNotNone(self.closed['namespace_identity'])
        self.assertEqual(self.closed['receipt']['outcome'],outcome)
        name='OBSERVATIONS.json' if outcome=='success_retention' else 'ATTEMPT-EVIDENCE.json'
        report=self.inspect(name)
        self.assertEqual(self.closed['receipt']['manifest_sha256'],self.selected_files[name]['sha256'])
        self.assertEqual(self.closed['receipt']['selected_bytes'],self.selected_files[name]['bytes'])
        self.assertEqual(report['end_observation'],self.end)
        self.assertFalse(report['validity_decided']);self.assertFalse(report['task_acceptance'])
        return report

    def test_actor_zero_live_input_timing_selected_snapshot_and_sink_custody(self):
        self.start();self.end_capture();self.finish()
        report=self.assert_persistence('success_retention')
        actor=self.end['actor_wait'];self.assertEqual(actor['returncode'],0)
        marker=self.inspect('PREPARED.json');self.inspect('SELECTED.json')
        self.assertEqual(marker['exit_snapshot'],dict(pid=actor['pid'],start_ticks=actor['start_ticks'],
            boot_id=actor['boot_id'],exit_code=0,signal_observed=False))
        self.assertEqual(report['selected_success']['selected_sha256'],self.selected_files['SELECTED.json']['sha256'])
        self.assertEqual(self.end['coding_observations'],[])
        self.assertEqual(report['stream']['selected_input']['status'],'within_observed_limits')
        self.assertFalse(marker['exit_attested']);self.assertFalse(marker['task_success_claimed'])

    def test_actor_nonzero_is_actual_known_wait_with_closed_failure_evidence(self):
        self.start('actor_nonzero');self.end_capture('actor_nonzero');self.finish()
        self.assert_persistence('failure_evidence')
        self.assertEqual(self.end['actor_wait']['raw_status'],17<<8)
        self.assertEqual(self.end['actor_wait']['returncode'],17)
        self.assertNotIn('actor_wait_unknown',self.end['observations'])
        self.assertFalse((self.output/self.run/'PREPARED.json').exists())

    def test_future_origin_keeps_live_bytes_unknown_without_success(self):
        self.start('future_start');self.end_capture('future_start');self.finish()
        self.assert_persistence('failure_evidence')
        self.assertEqual(self.end['actor_wait']['returncode'],0)
        self.assertIn('capture_timing_unknown',self.end['coding_observations'])
        self.assertFalse((self.output/self.run/'PREPARED.json').exists())

    def test_lost_actor_metadata_never_substitutes_capture_zero_wait(self):
        self.start('lost_wait');self.end_capture('lost_wait');self.finish()
        self.assert_persistence('failure_evidence')
        self.assertIsNone(self.end['actor_wait']);self.assertIsNone(self.end['input_bytes_written'])
        self.assertIn('actor_wait_unknown',self.end['observations'])
        self.assertEqual(self.end['capture_wait']['returncode'],0)
        self.assertFalse((self.output/self.run/'PREPARED.json').exists())

    def test_unknown_constructed_H_withholds_success_despite_actual_actor_zero(self):
        self.start();self.end_capture(unknown_hooks=True);self.finish()
        self.assert_persistence('failure_evidence')
        self.assertIn('hook_measurements_unknown',self.end['coding_observations'])
        self.assertFalse((self.output/self.run/'PREPARED.json').exists())

    def test_actual_sink_nonzero_refuses_declaration_after_persistence(self):
        self.start('actor_nonzero','nonzero');self.end_capture('actor_nonzero');self.finish()
        self.assertIsNone(self.closed)
        self.assertEqual(self.domain.closed['composition']['wait']['raw_status'],29<<8)
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],29)
        self.assertEqual(self.inspect('ATTEMPT-EVIDENCE.json')['end_observation'],self.end)

    def test_actual_sink_loss_has_no_accepted_closure_or_persistence(self):
        self.start(sink='hang');self.end_capture()
        self.domain.stop_role('composition',self.deadline)
        result=self.domain.close_role('composition',self.deadline)
        self.assertEqual(result['wait']['returncode'],-9);self.assertTrue(result['released'])
        self.closed=self.transport.closed_sink();self.assertIsNone(self.closed)
        self.assertTrue((self.output/self.run/'INTENT.json').exists())
        self.assertFalse((self.output/self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertFalse((self.output/self.run/'PREPARED.json').exists())
        # Preserve pending transport until ordinary cleanup; never clear it to
        # manufacture EOF or accepted persistence from a killed sink.


if __name__=='__main__':unittest.main()
