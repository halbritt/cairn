"""Constructed pure packet checks. None of these declarations attest runtime."""
import copy
import hashlib
import unittest
from trial_worker_protocol import ProtocolError,encoded
from trial_coding_protocol import (CodingDecoder,coding_packet,validate_start,validate_hooks,
    validate_coding_end,decode_timing,decode_hooks,wire_ceiling)

RUN='00000000-0000-4000-8000-000000000001';SOURCE='a'*64
BINDING=dict(run_id=RUN,source_manifest_sha256=SOURCE)


def timing(offset=0,data=b'x',received=2):
    return dict(schema='cairn.worker-capture-timing/1',**BINDING,start_monotonic_ns=1,
                received_monotonic_ns=received,offset=offset,bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),observations=[])


def hooks(rows=None):
    return dict(schema='cairn.worker-hook-measurements/1',**BINDING,rows=rows,
                observations=['hook_measurements_unknown'] if rows is None else [])


def declared_end(decoder):
    wait=dict(pid=1,start_ticks=1,boot_id=RUN,raw_status=0,returncode=0)
    return dict(schema='cairn.worker-coding-end-observation/1',**BINDING,
        stream_sha256=decoder.stream.hexdigest(),stream_bytes=decoder.stream_bytes,
        observed_stream_sha256=decoder.stream.hexdigest(),observed_stream_bytes=decoder.stream_bytes,
        actor_wait=wait.copy(),capture_wait=wait.copy(),capture_released=True,input_bytes=1,input_bytes_written=1,
        observations=[],capture_cleanup_observations=[],wire_sha256=decoder.wire.hexdigest(),
        wire_bytes=decoder.wire_bytes,timing_packets=decoder.timings,hook_packet_sha256=decoder.hook_sha,
        coding_observations=sorted(decoder.observations))


class CodingProtocolTests(unittest.TestCase):
    def decoder(self):
        return CodingDecoder(RUN,SOURCE)

    def complete_prefix(self):
        decoder=self.decoder()
        decoder.feed(b'T',encoded(timing()));decoder.feed(b'D',b'x')
        decoder.feed(b'H',encoded(hooks([])))
        return decoder

    def test_full_framed_wire_H_and_D_digest_binding(self):
        decoder=self.complete_prefix();value=declared_end(decoder)
        self.assertEqual(decoder.feed(b'E',encoded(value)),('end',value))
        self.assertIsNone(decoder.pending)
        self.assertNotEqual(value['stream_sha256'],value['wire_sha256'])
        with self.assertRaises(ProtocolError):decoder.feed(b'D',b'x')

    def test_each_end_digest_count_and_identity_tamper_refuses(self):
        for field,changed in [('wire_sha256','c'*64),('wire_bytes',1),('timing_packets',0),
                              ('hook_packet_sha256','c'*64),('stream_sha256','c'*64),
                              ('run_id','00000000-0000-4000-8000-000000000002')]:
            decoder=self.complete_prefix();end=declared_end(decoder);end[field]=changed
            with self.subTest(field=field),self.assertRaises(ProtocolError):decoder.feed(b'E',encoded(end))
            self.assertTrue(decoder.poisoned)

    def test_one_byte_fragments_no_lower_chunk_allowance_or_history(self):
        decoder=self.decoder()
        for offset in range(100):
            decoder.feed(b'T',encoded(timing(offset,received=offset+2)))
            self.assertEqual(decoder.feed(b'D',b'x')[1],b'x')
            self.assertIsNone(decoder.pending)
        self.assertEqual(decoder.timings,100)
        self.assertLessEqual(decoder.wire_bytes,wire_ceiling(100,100))
        self.assertEqual(wire_ceiling(4*1024*1024,4*1024*1024),4341170181)
        self.assertEqual(wire_ceiling(64*1024*1024,64*1024*1024),69457739781)
        decoder.feed(b'H',encoded(hooks([])));decoder.feed(b'E',encoded(declared_end(decoder)))

    def test_missing_duplicate_or_intervening_timing_poison(self):
        for sequence in [[(b'D',b'x')],[(b'T',encoded(timing())),(b'T',encoded(timing()))],
                         [(b'T',encoded(timing())),(b'H',encoded(hooks([])))],
                         [(b'T',encoded(timing())),(b'D',b'y')]]:
            decoder=self.decoder()
            with self.assertRaises(ProtocolError):
                for tag,payload in sequence:decoder.feed(tag,payload)
            with self.assertRaisesRegex(ProtocolError,'poisoned'):decoder.feed(b'H',encoded(hooks([])))

    def test_decreasing_clock_and_changed_origin_are_unknown_control(self):
        for changes in ({'received_monotonic_ns':1},{'start_monotonic_ns':0},{'offset':3}):
            decoder=self.decoder();decoder.feed(b'T',encoded(timing()));decoder.feed(b'D',b'x')
            value=timing(1,received=3);value.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ProtocolError):decoder.feed(b'T',encoded(value))

    def test_null_or_explicit_unknown_timing_never_invents_elapsed(self):
        for changes in ({'start_monotonic_ns':None},{'received_monotonic_ns':None},{}):
            decoder=self.decoder();value=timing();value.update(changes)
            value['observations']=['capture_timing_unknown']
            decoder.feed(b'T',encoded(value));self.assertIsNone(decoder.feed(b'D',b'x')[2])
            decoder.feed(b'H',encoded(hooks()));end=declared_end(decoder)
            self.assertEqual(end['coding_observations'],['capture_timing_unknown','hook_measurements_unknown'])
            decoder.feed(b'E',encoded(end))

    def test_H_exact_projection_privacy_bounds_and_copy(self):
        row=dict(event='SessionStart',stdout_sha256='b'*64,stdout_bytes=1,stderr_bytes=0,
            process_seconds=1,exit_code=0,memory_call_attempts={k:0 for k in ('search','pull','pull-evidence','history','other')})
        value=hooks([row]);projected=validate_hooks(value,**BINDING)
        projected['rows'][0]['memory_call_attempts']['search']=1
        self.assertEqual(row['memory_call_attempts']['search'],0)
        for changes in ({'private':'arbitrary private payload'},{'process_seconds':float('nan')},
                        {'process_seconds':10**1000},{'stdout_bytes':True},{'exit_code':999},{'memory_call_attempts':{}},
                        {'event':'Other'}):
            with self.subTest(keys=list(changes)),self.assertRaises(ProtocolError):validate_hooks(hooks([{**row,**changes}]),**BINDING)
        with self.assertRaises(ProtocolError):validate_hooks(hooks([row]*129),**BINDING)
        self.assertLessEqual(len(encoded(validate_hooks(hooks([row]*128),**BINDING))),65536)

    def test_strict_json_start_and_packet_encoding_bounds(self):
        for payload in (b'['*2000+b'0'+b']'*2000,b'{"schema":0,"schema":1}',b'{"schema":NaN}'):
            with self.assertRaises(ProtocolError):decode_hooks(payload,**BINDING)
        with self.assertRaises(ProtocolError):coding_packet(b'T',b'x'*1025)
        with self.assertRaises(ProtocolError):decode_timing(b'x'*1025,**BINDING)
        start=dict(kind='CAPTURE_START',schema='cairn.worker-capture-start/1',**BINDING,start_monotonic_ns=1)
        self.assertEqual(validate_start(start,**BINDING),start)
        with self.assertRaises(ProtocolError):validate_start({**start,'start_monotonic_ns':True},**BINDING)

    def test_coding_E_does_not_weaken_common_closure_and_missing_H(self):
        decoder=self.complete_prefix();end=declared_end(decoder)
        for changes in ({'actor_wait':None},{'capture_released':None},{'input_bytes_written':None},
                        {'capture_wait':{**end['capture_wait'],'returncode':17}}):
            with self.subTest(changes=changes),self.assertRaises(ProtocolError):validate_coding_end({**end,**changes},**BINDING)
        decoder=self.decoder()
        with self.assertRaises(ProtocolError):decoder.feed(b'E',encoded(end))
