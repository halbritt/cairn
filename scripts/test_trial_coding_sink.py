"""Disposable persistence and packet effects; constructed actor declarations."""
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from selected_native.retention import Intent, encode, RetentionError
from trial_coding_protocol import coding_packet
from trial_coding_sink import CodingSink, SinkError, serve
from trial_worker_protocol import encoded, ProtocolError
from test_trial_coding_stream import composer
from test_trial_attempt_actions import SESSION, PRIVATE, init, result, event, wire


class SinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.addCleanup(os.close, fd)
        self.intent = Intent(SESSION, 'a'*64, 'b'*64, 'c'*64, 'd'*64)
        body = dict(schema='cairn.selected-retention-intent/1', **dataclasses.asdict(self.intent),
                    admission_basis='caller_pinned_not_verified', launch_performed_by_sink=False)
        self.sink = CodingSink(fd, self.intent, composer(), hashlib.sha256(encode(body)).hexdigest())
        self.addCleanup(self.sink.close)
        self.run = self.root/SESSION

    def packets(self, successful=False, code=17, unknown=False):
        source = self.sink.composer
        source_digest = getattr(self, 'source_manifest_sha256', 'd'*64)
        final = dict.fromkeys(source.profile.final_required_keys, None)
        terminal = (event('result', subtype='success', is_error=False, result=json.dumps(final),
            permission_denials=[], num_turns=1, total_cost_usd=.1,
            modelUsage={'fixture-model':dict(provider='firstParty', inputTokens=1, outputTokens=2,
                cacheReadInputTokens=0, cacheCreationInputTokens=0)}) if successful else result())
        raw = wire(init())+wire(terminal)
        timing = dict(schema='cairn.worker-capture-timing/1', run_id=SESSION,
            source_manifest_sha256=source_digest, start_monotonic_ns=None if unknown else 10,
            received_monotonic_ns=None if unknown else 1000000010, offset=0, bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(), observations=['capture_timing_unknown'] if unknown else [])
        hooks = dict(schema='cairn.worker-hook-measurements/1', run_id=SESSION,
                     source_manifest_sha256=source_digest, rows=[], observations=[])
        packets = [(b'T',encoded(timing)), (b'D',raw), (b'H',encoded(hooks))]
        framed = b''.join(coding_packet(tag,payload) for tag,payload in packets)
        actor = dict(pid=1,start_ticks=1,boot_id=SESSION,raw_status=code<<8,returncode=code)
        input_bytes = len((json.dumps(dict(type='user',message=dict(role='user',content=source.prompt)),
                                     ensure_ascii=False)+'\n').encode())
        end = dict(schema='cairn.worker-coding-end-observation/1', run_id=SESSION,
            source_manifest_sha256=source_digest, stream_sha256=hashlib.sha256(raw).hexdigest(),
            stream_bytes=len(raw), observed_stream_sha256=hashlib.sha256(raw).hexdigest(),
            observed_stream_bytes=len(raw), actor_wait=actor,
            capture_wait=dict(actor,raw_status=0,returncode=0),capture_released=True,
            input_bytes=input_bytes,input_bytes_written=input_bytes,observations=[],capture_cleanup_observations=[],
            wire_sha256=hashlib.sha256(framed).hexdigest(),wire_bytes=len(framed),timing_packets=1,
            hook_packet_sha256=hashlib.sha256(coding_packet(*packets[-1])).hexdigest(),
            coding_observations=['capture_timing_unknown'] if unknown else [])
        return packets+[(b'E',encoded(end))]

    def pipe(self, packets):
        return io.BytesIO(b''.join(coding_packet(tag,payload) for tag,payload in packets))

    def test_ready_follows_consumed_intent_and_both_writer_acquisition(self):
        self.assertTrue((self.run/'INTENT.json').exists())
        self.assertIsNotNone(self.sink.retention.fd)
        self.assertIsNotNone(self.sink.writer.fd)
        self.assertEqual(self.sink.ready()['intent_sha256'],
                         hashlib.sha256((self.run/'INTENT.json').read_bytes()).hexdigest())

    def test_nonzero_actor_retains_all_wire_bindings_and_selected_failure(self):
        output = io.BytesIO()
        declaration = serve(self.sink,self.pipe(self.packets()),output)
        self.assertEqual(declaration['outcome'],'failure_evidence')
        saved = (self.run/'ATTEMPT-EVIDENCE.json').read_bytes()
        report = json.loads(saved)
        self.assertEqual(report['end_observation'],self.sink.decoder.end)
        self.assertEqual(report['end_observation']['actor_wait']['returncode'],17)
        self.assertNotIn(PRIVATE.encode(),saved)
        self.assertFalse(report['validity_decided'])
        self.assertIsNone(self.sink.retention.fd)
        self.assertIsNone(self.sink.writer.fd)
        self.assertEqual(self.sink.composer.raw,b'')
        self.assertEqual([json.loads(line)['kind'] for line in output.getvalue().splitlines()],['READY','CLOSED'])

    def test_success_uses_original_exit_snapshot_before_selected_observations(self):
        output = io.BytesIO()
        declaration = serve(self.sink,self.pipe(self.packets(successful=True,code=0)),output)
        self.assertEqual(declaration['outcome'],'success_retention')
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        saved = json.loads((self.run/'OBSERVATIONS.json').read_bytes())
        marker = json.loads((self.run/'PREPARED.json').read_bytes())
        self.assertEqual(marker['exit_snapshot']['pid'],1)
        self.assertEqual(marker['exit_snapshot']['exit_code'],0)
        self.assertEqual(saved['selected_success']['selected_sha256'],
                         hashlib.sha256((self.run/'SELECTED.json').read_bytes()).hexdigest())
        self.assertFalse(saved['task_acceptance'])
        self.assertFalse(marker['exit_attested'])

    def test_missing_end_preserves_prior_selected_channels_without_closed(self):
        output = io.BytesIO()
        with self.assertRaisesRegex(SinkError,'coding_sink_end_missing'):
            serve(self.sink,self.pipe(self.packets()[:-1]),output)
        saved = json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertIsNone(saved['end_observation'])
        self.assertIn('coding_transport_unknown',saved['stream']['composition_unknown'])
        self.assertEqual(len(output.getvalue().splitlines()),1)
        self.assertIsNone(self.sink.writer.fd)

    def test_end_wire_drift_is_control_fault_not_closed_failure_claim(self):
        packets = self.packets(); end = json.loads(packets[-1][1]);end['wire_sha256']='0'*64
        packets[-1] = b'E',encoded(end)
        output = io.BytesIO()
        with self.assertRaisesRegex(ProtocolError,'coding_end_binding'):
            serve(self.sink,self.pipe(packets),output)
        self.assertEqual(len(output.getvalue().splitlines()),1)
        self.assertIsNone(self.sink.retention.fd)
        self.assertTrue(self.sink.composer.closed)

    def test_extra_packet_after_end_withholds_closed(self):
        output = io.BytesIO()
        with self.assertRaisesRegex(ProtocolError,'coding_packet_order'):
            serve(self.sink,self.pipe(self.packets()+[(b'H',self.packets()[2][1])]),output)
        self.assertEqual(len(output.getvalue().splitlines()),1)

    def test_unknown_timing_never_becomes_success_even_with_zero_actor_wait(self):
        declaration = serve(self.sink,self.pipe(self.packets(successful=True,code=0,unknown=True)),io.BytesIO())
        self.assertEqual(declaration['outcome'],'failure_evidence')
        self.assertFalse((self.run/'PREPARED.json').exists())
        report = json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertIn('capture_timing_unknown',report['end_observation']['coding_observations'])

    def test_success_observation_io_failure_does_not_retry_or_emit_closed(self):
        original = self.sink.writer._write
        def fail(data,name='ATTEMPT-EVIDENCE.json'):
            if name == 'OBSERVATIONS.json':
                raise OSError(PRIVATE)
            return original(data,name)
        output = io.BytesIO()
        with patch.object(self.sink.writer,'_write',side_effect=fail):
            with self.assertRaisesRegex(RetentionError,'^attempt_retention_io$'):
                serve(self.sink,self.pipe(self.packets(successful=True,code=0)),output)
        self.assertTrue((self.run/'SELECTED.json').exists())
        self.assertTrue((self.run/'PREPARED.json').exists())
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertEqual(self.sink.writer.state,'failed')
        self.assertEqual(len(output.getvalue().splitlines()),1)

    def test_close_fault_withholds_closed_after_selected_write(self):
        original = self.sink.writer.close
        def fail():
            original()
            raise RetentionError('attempt_close_io')
        output = io.BytesIO()
        with patch.object(self.sink.writer,'close',side_effect=fail):
            with self.assertRaisesRegex(SinkError,'coding_sink_close_unknown'):
                serve(self.sink,self.pipe(self.packets()),output)
        self.assertTrue((self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertIsNone(self.sink.retention.fd)
        self.assertIsNone(self.sink.writer.fd)
        self.assertEqual(len(output.getvalue().splitlines()),1)

    def test_interrupted_diagnostic_preserves_original_pipe_fault_and_closes_both(self):
        output = io.BytesIO()
        with patch.object(self.sink.writer,'prepare',side_effect=KeyboardInterrupt):
            with self.assertRaisesRegex(SinkError,'coding_sink_packet_truncated'):
                serve(self.sink,io.BytesIO(b'T\x00'),output)
        self.assertIsNone(self.sink.retention.fd)
        self.assertIsNone(self.sink.writer.fd)
        self.assertTrue(self.sink.composer.closed)

    def test_missing_typed_decoder_consumes_coding_writer_without_legacy_fallback(self):
        with self.assertRaisesRegex(RetentionError,'attempt_coding_decoder_unavailable'):
            self.sink.writer.prepare_coding(self.sink.composer,None)
        self.assertEqual(self.sink.writer.state,'failed')
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())

    def test_changed_decoder_wire_state_refuses_persistence_after_complete_end(self):
        for tag,payload in self.packets():self.sink.feed(tag,payload)
        self.sink.decoder.wire=hashlib.sha256(b'changed selected wire')
        with self.assertRaisesRegex(RetentionError,'attempt_coding_wire_binding'):
            self.sink.finish()
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        self.assertEqual(self.sink.writer.state,'failed')

    def test_changed_consumed_hooks_refuse_before_success_or_observation_persistence(self):
        for tag,payload in self.packets(successful=True,code=0):self.sink.feed(tag,payload)
        self.sink.decoder.hooks['rows'] = [dict(event='PostToolUse', stdout_sha256='a'*64,
            stdout_bytes=77, stderr_bytes=0, process_seconds=0, exit_code=0,
            memory_call_attempts=dict(search=0,pull=0,**{'pull-evidence':0},history=0,other=0))]
        with self.assertRaisesRegex(RetentionError,'attempt_coding_hook_binding'):
            self.sink.finish()
        self.assertFalse((self.run/'SELECTED.json').exists())
        self.assertFalse((self.run/'PREPARED.json').exists())
        self.assertFalse((self.run/'OBSERVATIONS.json').exists())
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())

    def test_hook_source_drift_consumes_attempt_writer_without_persistence(self):
        for tag,payload in self.packets():self.sink.feed(tag,payload)
        self.sink.decoder.hooks['source_manifest_sha256']='e'*64
        with self.assertRaisesRegex(RetentionError,'attempt_coding_hook_binding'):
            self.sink.writer.prepare_coding(self.sink.composer,self.sink.decoder,
                hook_snapshot=self.sink.hook_snapshot)
        self.assertEqual(self.sink.writer.state,'failed')
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())
        with self.assertRaisesRegex(RetentionError,'attempt_writer_unavailable'):
            self.sink.writer.prepare(self.sink.composer,None)

    def test_hook_observation_drift_between_h_and_e_withholds_closed(self):
        packets = self.packets()
        for tag,payload in packets[:-1]:self.sink.feed(tag,payload)
        self.sink.decoder.hooks['observations']=['hook_measurements_unknown']
        self.sink.feed(*packets[-1])
        with self.assertRaisesRegex(RetentionError,'attempt_coding_hook_binding'):
            self.sink.finish()
        self.assertFalse((self.run/'ATTEMPT-EVIDENCE.json').exists())

    def test_noncanonical_hook_wire_keeps_original_digest_and_selected_projection(self):
        packets=self.packets()
        packets[2]=b'H',json.dumps(json.loads(packets[2][1]),indent=2).encode()
        framed=b''.join(coding_packet(tag,payload) for tag,payload in packets[:-1])
        end=json.loads(packets[-1][1])
        end.update(wire_sha256=hashlib.sha256(framed).hexdigest(),wire_bytes=len(framed),
            hook_packet_sha256=hashlib.sha256(coding_packet(*packets[2])).hexdigest())
        packets[-1]=b'E',encoded(end)
        declaration=serve(self.sink,self.pipe(packets),io.BytesIO())
        self.assertEqual(declaration['outcome'],'failure_evidence')
        selected=json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertEqual(selected['end_observation']['hook_packet_sha256'],end['hook_packet_sha256'])
        self.assertNotEqual(self.sink.hook_snapshot.packet_sha256,
            hashlib.sha256(coding_packet(b'H',self.sink.hook_snapshot.selected)).hexdigest())
        self.assertEqual(selected['stream']['selected_input']['hook_wire_bytes'],0)

    def test_declared_complete_input_for_different_message_cannot_enable_success(self):
        packets = self.packets(successful=True,code=0)
        end = json.loads(packets[-1][1]);end.update(input_bytes=7,input_bytes_written=7)
        packets[-1] = b'E',encoded(end)
        declaration = serve(self.sink,self.pipe(packets),io.BytesIO())
        self.assertEqual(declaration['outcome'],'failure_evidence')
        saved = json.loads((self.run/'ATTEMPT-EVIDENCE.json').read_bytes())
        self.assertIn('coding_input_binding_unknown',saved['stream']['composition_unknown'])
        self.assertFalse(saved['validity_decided'])


if __name__=='__main__':
    unittest.main()
