"""Actual provider-free process boundary; hook rows/codec probes are declarations."""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
import struct
import termios
import time
import unittest
import uuid
from unittest.mock import patch
from trial_external_supervision import fixture_service_delegation,sandbox,SOURCE_PINS
from trial_selected_native_mounts import reviewed_package
from trial_owned_workers import AttemptDomain,OwnedWorker,Unavailable
from trial_worker_protocol import encoded
from trial_coding_transport import CodingTransport
from trial_coding_protocol import wire_ceiling

ROOT=Path(__file__).resolve().parent
PACKAGE=reviewed_package(ROOT/'selected_native',SOURCE_PINS)
SOURCES=[(ROOT/'supervision_fixture/coding_worker.py','worker.py'),
         (ROOT/'supervision_fixture/owned_worker.py','fixture_base.py'),
         (ROOT/'trial_coding_protocol.py','trial_coding_protocol.py'),
         (ROOT/'trial_worker_protocol.py','trial_worker_protocol.py')]+[p for p in PACKAGE.sources if not p[1].endswith('/retention.py')]


class CodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.output=Path(self.tmp.name)
        self.run=str(uuid.uuid4())
        manifest={target:hashlib.sha256(path.read_bytes()).hexdigest() for path,target in SOURCES}
        for name in ('trial_coding_transport.py','trial_worker_transport.py','trial_owned_workers.py',
                     'trial_external_supervision.py','trial_task_eval.py','trial_selected_native_mounts.py'):
            manifest[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        manifest['launcher.py']=hashlib.sha256((ROOT/'supervision_fixture/launcher.py').read_bytes()).hexdigest()
        self.source=hashlib.sha256(encoded(manifest)).hexdigest()
        self.intent=hashlib.sha256(encoded(dict(run_id=self.run,source_manifest_sha256=self.source))).hexdigest()
        self.domain=AttemptDomain(fixture_service_delegation());self.deadline=time.monotonic()+4
        self.expected_cleanup_errors=set()
        self.transport=CodingTransport(self.domain,self.run,self.source,self.intent)

    def tearDown(self):
        closed=self.domain.close(time.monotonic()+1)
        self.assertTrue(closed['released'],closed)
        self.assertFalse(closed['errors'])
        self.assertEqual({error for role in closed['roles'].values() for error in role['errors']},self.expected_cleanup_errors)
        self.transport.forget_transient();self.assertFalse(self.transport.queue or self.transport.input)
        self.tmp.cleanup()

    def worker(self,role,mode):
        group=self.domain.leaf(role)
        child=OwnedWorker(group,sandbox(SOURCES,[(self.output,'/output')],[role,mode,self.run,self.source,self.intent]))
        self.domain.bind(role,child);return child

    def pump(self,predicate):
        while not predicate() and time.monotonic()<self.deadline:
            self.transport.step()
        self.assertTrue(predicate())

    def drained(self,child):
        return child.wait is not None and {'stdout','stderr'}<=child.eof

    def start(self,mode='normal',sink='normal'):
        self.sink=self.worker('composition',sink);self.pump(lambda:self.transport.ready)
        self.capture=self.worker('capture',mode)
        self.pump(lambda:self.transport.gate_capture(b'frozen input') if not self.transport.gated else True)

    def finish(self,unknown_hooks=False):
        self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        metadata=self.transport.capture_report()['final']
        hooks=dict(schema='cairn.worker-hook-measurements/1',run_id=self.run,
            source_manifest_sha256=self.source,rows=None if unknown_hooks else [],
            observations=['hook_measurements_unknown'] if unknown_hooks else [])
        end=self.transport.end_capture(metadata['actor_wait'],input_bytes=metadata['input_bytes'],
            input_bytes_written=metadata['input_bytes_written'],observations=metadata['observations'],hook_measurements=hooks)
        self.pump(lambda:self.drained(self.sink));self.domain.close_role('composition',self.deadline)
        self.assertIsNotNone(self.transport.closed_sink())
        return end,json.loads((self.output/'CODING-FAILURE.json').read_bytes())

    def test_actual_start_read_clock_and_nonzero_failure_closure(self):
        self.start('nonzero');end,record=self.finish()
        self.assertEqual(end['actor_wait']['returncode'],17)
        self.assertEqual(end['coding_observations'],[])
        self.assertGreaterEqual(record['first_elapsed'],0)
        domain=self.domain.closed['capture']['namespace_identity']['clock_domain']
        self.assertEqual(len(set(domain.values())),1)
        self.assertEqual(end['stream_sha256'],hashlib.sha256(b'coding disposable work\n').hexdigest())
        self.assertLess((self.output/'CODING-FAILURE.json').stat().st_size,8192)

    def test_slow_sink_cannot_supply_timing(self):
        self.start('delayed','slow');end,record=self.finish()
        self.assertGreaterEqual(record['first_elapsed'],.04)
        self.assertGreater(record['first_sink_elapsed']-record['first_elapsed'],.05)
        self.assertNotIn('capture_timing_unknown',end['coding_observations'])

    def test_many_real_fragments_keep_D_hash_and_wire_binding(self):
        self.start('fragments');end,record=self.finish()
        self.assertGreater(end['timing_packets'],1)
        self.assertEqual(end['stream_sha256'],hashlib.sha256(b'coding disposable work\n').hexdigest())
        self.assertEqual(record['packets'],end['timing_packets'])
        self.assertLessEqual(end['wire_bytes'],wire_ceiling(end['stream_bytes'],end['timing_packets']))

    def test_malformed_start_preserves_stream_with_timing_unknown(self):
        self.start('malformed');end,record=self.finish()
        self.assertIn('capture_timing_unknown',end['coding_observations'])
        self.assertIsNone(record['first_elapsed'])
        self.assertEqual(end['stream_bytes'],len(b'coding disposable work\n'))

    def test_future_start_never_invents_elapsed(self):
        self.start('future');end,record=self.finish()
        self.assertIn('capture_timing_unknown',end['coding_observations'])
        self.assertIsNone(record['first_elapsed'])

    def test_late_contradictory_clock_keeps_origin_and_preserves_unknown(self):
        self.start('fragments');self.pump(lambda:self.transport.timings>=1)
        origin=self.transport.start_ns
        with patch('trial_coding_transport.time.monotonic_ns',lambda:origin-1):
            end,record=self.finish()
        self.assertEqual(self.transport.start_ns,origin)
        self.assertIsNotNone(record['first_elapsed']);self.assertIsNone(record['last_elapsed'])
        self.assertIn('capture_timing_unknown',end['coding_observations'])

    def test_missing_start_waits_for_control_eof_preserves_failure_unknown(self):
        self.start('missing');self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        hooks=dict(schema='cairn.worker-hook-measurements/1',run_id=self.run,source_manifest_sha256=self.source,rows=None,observations=['hook_measurements_unknown'])
        with self.assertRaisesRegex(Unavailable,'metadata_unknown'):
            self.transport.capture_report()
        end=self.transport.end_capture(None,input_bytes=12,input_bytes_written=None,
            observations=['capture_metadata_missing'],hook_measurements=hooks)
        self.pump(lambda:self.drained(self.sink));self.domain.close_role('composition',self.deadline)
        self.assertIsNotNone(self.transport.closed_sink())
        self.assertIn('capture_timing_unknown',end['coding_observations'])
        self.assertIn('actor_wait_unknown',end['observations'])

    def test_unknown_H_not_silently_empty(self):
        self.start();end,record=self.finish(True)
        self.assertIsNone(record['hooks']['rows'])
        self.assertIn('hook_measurements_unknown',end['coding_observations'])

    def test_real_wrong_namespace_fd_refuses_before_gate(self):
        self.sink=self.worker('composition','normal');self.pump(lambda:self.transport.ready)
        self.capture=self.worker('capture','normal')
        self.pump(lambda:self.domain.verified('capture'))
        original=os.open
        def wrong(path,*args,**kwargs):
            if str(path).endswith('/ns/time_for_children') and not str(path).startswith('/proc/self/'):
                return original('/proc/self/ns/pid',*args,**kwargs)
            return original(path,*args,**kwargs)
        with patch('trial_coding_transport.os.open',wrong):
            with self.assertRaisesRegex(Unavailable,'clock_namespace_unknown'):
                self.transport.gate_capture(b'frozen input')
        self.assertFalse(self.transport.gated);self.assertFalse(self.transport.input)

    def unknown_end(self,observations):
        hooks=dict(schema='cairn.worker-hook-measurements/1',run_id=self.run,
            source_manifest_sha256=self.source,rows=[],observations=[])
        return self.transport.end_capture(None,input_bytes=12,input_bytes_written=None,
            observations=observations,hook_measurements=hooks)

    def test_known_output_cap_keeps_prefix_timing_wire_and_closes_failure(self):
        # Select a smaller internal test bound, not a new public route/allowance.
        self.transport.stream_limit=1024
        self.start('large')
        with self.assertRaisesRegex(Unavailable,'worker_stream_budget_exhausted'):
            self.pump(lambda:self.drained(self.capture))
        self.domain.stop_role('capture',self.deadline)
        self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        end=self.unknown_end(['capture_not_completed','capture_metadata_missing'])
        self.pump(lambda:self.drained(self.sink));self.domain.close_role('composition',self.deadline)
        self.assertIsNotNone(self.transport.closed_sink())
        record=json.loads((self.output/'CODING-FAILURE.json').read_bytes())
        self.assertEqual(record['end'],end)
        self.assertEqual(end['stream_bytes'],1024)
        self.assertEqual(end['stream_sha256'],hashlib.sha256(b'x'*1024).hexdigest())
        self.assertGreater(end['observed_stream_bytes'],end['stream_bytes'])
        self.assertIn('stream_budget_exhausted',end['observations'])
        self.assertNotIn('stream_transport_unknown',end['observations'])

    def test_startup_raw_backpressure_requires_owned_stop_and_unknown(self):
        self.start('startup_hang')
        # Observe real buffered pipe bytes without consuming the held stdout.
        fd=self.capture.process.stdout.fileno()
        self.pump(lambda:struct.unpack('I',fcntl.ioctl(fd,termios.FIONREAD,struct.pack('I',0)))[0]>0)
        self.assertFalse(self.transport.start_ready)
        self.assertEqual(self.transport.stream_bytes,0)
        self.assertEqual(self.transport.observed_bytes,0)
        self.domain.stop_role('capture',self.deadline)
        self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        end=self.unknown_end(['capture_not_completed','capture_metadata_missing'])
        self.pump(lambda:self.drained(self.sink));self.domain.close_role('composition',self.deadline)
        self.assertIsNotNone(self.transport.closed_sink())
        record=json.loads((self.output/'CODING-FAILURE.json').read_bytes())
        self.assertIsNone(record['first_elapsed'])
        self.assertGreater(end['stream_bytes'],0)
        self.assertIn('capture_timing_unknown',end['coding_observations'])
        self.assertEqual(end['capture_wait']['returncode'],-9)

    def test_lost_final_metadata_never_becomes_known_actor_wait(self):
        self.start('lost_final');self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        with self.assertRaisesRegex(Unavailable,'metadata_unknown'):
            self.transport.capture_report()
        end=self.unknown_end(['capture_metadata_missing'])
        self.pump(lambda:self.drained(self.sink));self.domain.close_role('composition',self.deadline)
        self.assertIsNotNone(self.transport.closed_sink())
        self.assertIsNone(end['actor_wait']);self.assertEqual(end['capture_wait']['returncode'],0)
        self.assertIn('actor_wait_unknown',end['observations'])

    def test_hanging_sink_actual_stop_release_never_claims_persistence(self):
        self.start(sink='hang');self.pump(lambda:self.drained(self.capture))
        self.domain.close_role('capture',self.deadline)
        report=self.transport.capture_report()['final']
        hooks=dict(schema='cairn.worker-hook-measurements/1',run_id=self.run,
            source_manifest_sha256=self.source,rows=[],observations=[])
        self.transport.end_capture(report['actor_wait'],input_bytes=12,input_bytes_written=12,hook_measurements=hooks)
        until=time.monotonic()+.05
        while time.monotonic()<until:
            self.transport.step()
        self.domain.stop_role('composition',self.deadline)
        # Stop closes the owned pipes; pending queue writes can refuse while
        # independent process wait/release remain available.
        self.transport.queue.clear()
        self.pump(lambda:self.drained(self.sink))
        close=self.domain.close_role('composition',self.deadline)
        self.assertEqual(close['wait']['returncode'],-9);self.assertTrue(close['released'])
        self.assertIsNone(self.transport.closed_sink())
        self.assertFalse((self.output/'CODING-FAILURE.json').exists())

    def test_clock_refusal_attempts_every_held_close_and_retains_uncertainty(self):
        self.sink=self.worker('composition','normal');self.pump(lambda:self.transport.ready)
        self.capture=self.worker('capture','normal');self.pump(lambda:self.domain.verified('capture'))
        original_open,original_close=os.open,os.close
        acquired=[];closed=[]
        def wrong(path,*args,**kwargs):
            target='/proc/self/ns/pid' if str(path).endswith('/ns/time_for_children') and not str(path).startswith('/proc/self/') else path
            fd=original_open(target,*args,**kwargs)
            if '/ns/' in str(path):acquired.append(fd)
            return fd
        def uncertain_close(fd):
            original_close(fd);closed.append(fd)
            if len(closed)==1:raise OSError('fixture close after actual close')
        self.expected_cleanup_errors={'worker_descriptor_close_unknown'}
        with patch('trial_coding_transport.os.open',wrong),patch('trial_coding_transport.os.close',uncertain_close):
            with self.assertRaisesRegex(Unavailable,'coding_clock_descriptor_unknown'):
                self.transport.gate_capture(b'frozen input')
        self.assertEqual(closed,acquired);self.assertEqual(len(closed),4)
        for fd in acquired:
            with self.assertRaises(OSError):os.fstat(fd)
        self.assertFalse(self.transport.gated)
        self.assertIn('worker_descriptor_close_unknown',self.capture.acquisition_observations)
