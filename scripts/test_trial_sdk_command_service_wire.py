"""Pure real Codec/Channel transcript checks; no transport or subject effects."""
import hashlib
import dis
import json
import struct
import sys
import unittest

from trial_sdk_ipc import Codec, CodecError, TABLE_PROFILE_3
from trial_sdk_parent_storage import Root, Channel
import trial_sdk_command_service_wire as w


class WireTests(unittest.TestCase):
    def setUp(self):
        self.root=Root(128*1024)
        self.body=self.root.allocate('body',512)
        self.channels={direction:Channel(Codec,self.root,self.root.allocate(direction,544),
                                         direction,profile=4) for direction in ('P2S','S2P')}
        self.state=w.ServiceTranscript()

    def emit(self,kind,op=1,*fields,data=None):
        direction=w.TABLE[kind][0]
        size=len(data) if data is not None else w.STRUCTS[kind].size
        hold=self.body.hold(size)
        try:
            if data is None:w.pack_into(kind,hold.view,*fields)
            else:hold.view[:]=data
            token=self.channels[direction].encode(kind,op,hold)
            try:return token.visit(self.state.observe)
            finally:token.complete(token.generation,'known')
        finally:hold.close()

    def output(self,rawout,rawerr,wireout,wireerr,*,out=b'',err=b''):
        sha=lambda value:hashlib.sha256(value).digest()
        self.emit(w.OUTPUT,1,rawout,rawerr,wireout,wireerr,
                  sha(b''),sha(out),sha(b'?'*rawerr),sha(out),sha(err))

    def prepared(self,op=1,request=b'{}'):
        self.emit(w.READY,0)
        self.begin(op,request)
        self.emit(w.PREPARED,op,10,hashlib.sha256(request).digest())

    def begin(self,op,request=b'{}'):
        self.emit(w.PREPARE,op,1,999999999,10,1548,20_000_000,
                  len(request),hashlib.sha256(request).digest())
        self.emit(w.REQUEST,op,data=request)
        self.emit(w.REQUEST_END,op)

    def running(self):
        self.prepared()
        self.emit(w.START,1,10)

    def terminal(self):
        self.running()
        self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,1,31,0)
        self.output(2,1,2,3,out=b'ok',err='�'.encode())
        self.emit(w.DELIVER,1,21,1,2)
        self.emit(w.STDOUT,1,data=b'ok')
        self.emit(w.DELIVER,1,22,2,3)
        self.emit(w.STDERR,1,data='�'.encode())
        self.emit(w.STATUS,1,17<<8,17)

    def test_complete_transcript_preserves_nonzero_cli_report_and_release(self):
        self.terminal()
        state=self.state.snapshot()
        self.assertEqual(state['returncode'],17)
        self.assertEqual(state['raw_counts'],(0,2,1))
        self.assertEqual(state['wire_counts'],(2,3))
        self.emit(w.RELEASE,1,10)
        self.emit(w.RELEASED,1,10,0)
        self.assertEqual(self.state.snapshot()['phase'],'idle')
        self.emit(w.SHUTDOWN,0)
        self.emit(w.BYE,0)
        self.assertEqual(self.state.snapshot()['phase'],'closed')
        self.assertEqual(self.root.leased,0)

    def test_ready_prepare_digest_and_order_are_required(self):
        with self.assertRaises(w.ServiceWireError):
            self.emit(w.PREPARE,1,1,1,10,1,1,2,hashlib.sha256(b'{}').digest())
        self.assertTrue(self.state.snapshot()['failed'])
        self.setUp()
        self.emit(w.READY,0)
        self.emit(w.PREPARE,1,1,1,10,1,1,2,hashlib.sha256(b'{}').digest())
        self.emit(w.REQUEST,1,data=b'[]')
        with self.assertRaises(w.ServiceWireError):self.emit(w.REQUEST_END,1)
        self.setUp();self.prepared()
        with self.assertRaises(w.ServiceWireError):self.emit(w.START,1,11)

    def test_no_default_grant_and_duplicate_start_are_terminal(self):
        self.running()
        with self.assertRaises(w.ServiceWireError):
            self.emit(w.PROGRESS,1,20,1,0,0,0,0,0)
        self.setUp();self.running()
        with self.assertRaises(w.ServiceWireError):self.emit(w.START,1,10)

    def test_final_zero_turn_can_report_actual_zero_eof_once(self):
        self.running()
        self.emit(w.TURN,1,20,1,0,1)
        self.assertEqual(self.state.snapshot()['turn'],(20,1,0,1))
        self.emit(w.PROGRESS,1,20,1,0,0,0,31,0)
        self.assertIsNone(self.state.snapshot()['turn'])
        self.output(0,0,0,0)
        self.emit(w.STATUS,1,0,0)
        self.assertEqual(self.state.snapshot()['phase'],'result')
        self.setUp();self.running()
        with self.assertRaises(w.ServiceWireError):self.emit(w.TURN,1,20,1,0,0)

    def test_grant_correlation_overrun_and_unknown_progress_cannot_be_results(self):
        for report in ((21,1,1,0,0,0,0),(20,1,2,0,0,0,0)):
            self.setUp();self.running();self.emit(w.TURN,1,20,1,1,1)
            with self.assertRaises(w.ServiceWireError):self.emit(w.PROGRESS,1,*report)
        self.setUp();self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,23,0,0,0,1)
        state=self.state.snapshot()
        self.assertTrue(state['attempt_failed'])
        self.assertEqual(state['turn'],(20,1,1536,0))  # Unsettled commitment retained.
        self.assertEqual(state['raw_counts'],(23,0,0))
        with self.assertRaises(w.ServiceWireError):
            self.output(0,0,0,0)

    def test_output_requires_known_lifecycle_and_original_count_agreement(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,15,0)  # Wait+EOF alone omit known release.
        with self.assertRaises(w.ServiceWireError):
            self.output(2,0,2,0,out=b'ok')
        self.setUp();self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        with self.assertRaises(w.ServiceWireError):
            self.output(1,0,1,0,out=b'x')

    def test_text_needs_delivery_grant_and_exact_terminal_counts(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'ok')
        with self.assertRaises(w.ServiceWireError):self.emit(w.STDOUT,1,data=b'ok')
        self.setUp();self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'ok')
        with self.assertRaises(w.ServiceWireError):self.emit(w.STATUS,1,0,0)

    def test_split_utf8_and_malformed_text(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'\xc3\xa9')
        self.emit(w.DELIVER,1,21,1,1);self.emit(w.STDOUT,1,data=b'\xc3')
        self.emit(w.DELIVER,1,22,1,1);self.emit(w.STDOUT,1,data=b'\xa9')
        self.emit(w.STATUS,1,0,0)
        self.setUp();self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,1,0,31,0)
        self.output(1,0,1,0,out=b'\xff')
        self.emit(w.DELIVER,1,21,1,1)
        with self.assertRaises(w.ServiceWireError):self.emit(w.STDOUT,1,data=b'\xff')

    def test_cancel_refusal_release_never_reopens_failed_attempt(self):
        self.running();self.emit(w.CANCEL,1,1)
        self.emit(w.REFUSE,1,6)
        self.emit(w.RELEASE,1,10);self.emit(w.RELEASED,1,10,0)
        self.assertTrue(self.state.snapshot()['attempt_failed'])
        with self.assertRaises(w.ServiceWireError):self.begin(2)
        self.setUp();self.prepared();self.emit(w.REFUSE,1,3)
        self.emit(w.RELEASE,1,10);self.emit(w.RELEASED,1,10,1)
        self.assertEqual(self.state.snapshot()['phase'],'release_unknown')

    def test_operation_replay_and_forged_wait_status_refuse(self):
        self.terminal();self.emit(w.RELEASE,1,10);self.emit(w.RELEASED,1,10,0)
        with self.assertRaises(w.ServiceWireError):self.begin(1)
        self.setUp();self.running();self.emit(w.TURN,1,20,1,0,1)
        self.emit(w.PROGRESS,1,20,1,0,0,0,31,0)
        self.output(0,0,0,0)
        with self.assertRaises(w.ServiceWireError):self.emit(w.STATUS,1,17<<8,0)

    def test_profile_boundaries_and_combined_quote(self):
        self.assertEqual(TABLE_PROFILE_3[67],('B2C',4,4,True))
        for direction in ('P2S','S2P'):
            with self.assertRaises(CodecError):Codec(direction,lambda n:True,lambda n:None,profile=3)
        self.emit(w.READY,0)
        with self.assertRaises(w.ServiceWireError):
            self.emit(w.PREPARE,1,1,1,10,128*1024,512*1024*1024,2,hashlib.sha256(b'{}').digest())

    def test_diagnostics_are_copies_and_sequences_do_not_replay(self):
        self.prepared()
        state=self.state.snapshot();state['phase']='running'
        self.assertEqual(self.state.snapshot()['phase'],'prepared')
        with self.assertRaises(w.ServiceWireError):self.emit(w.START,2,10)

    def test_final_allowance_is_known_remaining_not_replenishable(self):
        self.running()
        self.emit(w.TURN,1,20,1,10,1)
        self.emit(w.PROGRESS,1,20,1,4,0,0,0,0)
        self.assertEqual(self.state.snapshot()['final_remaining'],6)
        self.emit(w.TURN,1,21,2,6,1)
        self.emit(w.PROGRESS,1,21,2,6,0,0,0,0)
        self.emit(w.TURN,1,22,3,0,1)
        self.emit(w.PROGRESS,1,22,3,0,0,0,0,0)
        self.emit(w.TURN,1,23,4,0,1)
        self.emit(w.PROGRESS,1,23,4,0,0,0,31,0)
        self.assertEqual(self.state.snapshot()['final_remaining'],0)
        self.setUp();self.running();self.emit(w.TURN,1,20,1,10,1)
        self.emit(w.PROGRESS,1,20,1,4,0,0,0,0)
        with self.assertRaises(w.ServiceWireError):self.emit(w.TURN,1,21,2,7,1)
        self.setUp();self.running();self.emit(w.TURN,1,20,1,10,1)
        self.emit(w.PROGRESS,1,20,1,0,0,0,0,0)
        with self.assertRaises(w.ServiceWireError):self.emit(w.TURN,1,21,2,1536,0)

    def test_request_envelope_reuses_fixed_policy_without_executable_argv(self):
        from trial_sdk_command_policy import FixedCommandPolicy, CommandPolicyError
        policy=FixedCommandPolicy('/fixed/cairn','/fixed/socket','/fixed/token','repo','task','run')
        args=['--repo','repo','--task','task','--run','run','--tokens','1024','--','q']
        text=json.dumps({'operation':'search','args':args,'body':None})
        request=w.parse_request(text,len(text.encode()),1)
        self.assertEqual(request.args,tuple(args))
        self.assertEqual(request.plan(policy).argv[-2:],('--','q'))
        for bad in (
            text.replace('"operation": "search"','"operation": "pull"'),
            text[:-1]+',"route":"other"}',
            '{"operation":"search","operation":"search","args":[],"body":null}',
            '{"operation":"search","args":[true],"body":null}',
        ):
            with self.assertRaises(w.ServiceWireError):w.parse_request(bad,len(bad.encode()),1)
        bad=json.dumps({'operation':'search','args':['--socket','/attacker'],'body':None})
        request=w.parse_request(bad,len(bad.encode()),1)
        with self.assertRaises(CommandPolicyError):request.plan(policy)

    def test_text_digest_detects_valid_utf8_substitution(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'ok')
        self.emit(w.DELIVER,1,21,1,2);self.emit(w.STDOUT,1,data=b'no')
        with self.assertRaises(w.ServiceWireError):self.emit(w.STATUS,1,0,0)

    def test_cancel_preserves_crossed_receipt_until_explicit_acknowledgement(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.CANCEL,1,1)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'ok')
        self.assertEqual(self.state.snapshot()['phase'],'cancelling')
        self.assertEqual(self.state.snapshot()['raw_counts'],(0,2,0))
        self.emit(w.REFUSE,1,6)
        self.emit(w.RELEASE,1,10);self.emit(w.RELEASED,1,10,0)
        self.assertTrue(self.state.snapshot()['attempt_failed'])
        self.setUp();self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.PROGRESS,1,20,1,0,2,0,31,0)
        self.output(2,0,2,0,out=b'ok')
        self.emit(w.DELIVER,1,21,1,2);self.emit(w.CANCEL,1,1)
        self.emit(w.STDOUT,1,data=b'ok');self.emit(w.STATUS,1,0,0)
        self.assertEqual(self.state.snapshot()['phase'],'cancelling')
        with self.assertRaises(w.ServiceWireError):self.emit(w.RELEASE,1,10)

    def test_observed_frame_replay_and_cleanup_interruption_are_sticky(self):
        hold=self.body.hold(0)
        token=self.channels['S2P'].encode(w.READY,0,hold)
        try:
            token.visit(self.state.observe)
            with self.assertRaises(w.ServiceWireError):token.visit(self.state.observe)
        finally:
            token.complete(token.generation,'known');hold.close()
        self.assertTrue(self.state.snapshot()['failed'])

        self.setUp()
        class Interrupted(BaseException):pass
        hit=[]
        code=w.ServiceTranscript.observe.__code__
        # Required real line-trace interruption at explicit return, after final
        # busy reset. This does not claim all interpreter return events are
        # catchable (the caller still owns interruption after function exit).
        return_line=next(i.positions.lineno for i in dis.get_instructions(code)
                         if i.opname.startswith('RETURN_'))
        def trace(frame,event,arg):
            if (frame.f_code is code and event=='line'
                    and frame.f_lineno==return_line and not hit):
                hit.append(True)
                raise Interrupted()
            return trace
        old=sys.gettrace()
        try:
            sys.settrace(trace)
            with self.assertRaises(Interrupted):self.emit(w.READY,0)
        finally:sys.settrace(old)
        self.assertEqual(hit,[True])
        self.assertTrue(self.state.snapshot()['failed'])
        self.assertEqual(self.root.leased,0)

    def test_fixed_body_encoder_does_not_pad_digest_or_coerce_boolean(self):
        with memoryview(bytearray(w.STRUCTS[w.PREPARED].size)) as view:
            for digest in (b'x',b'x'*33):
                with self.assertRaises(w.ServiceWireError):
                    w.pack_into(w.PREPARED,view,10,digest)
            with self.assertRaises(w.ServiceWireError):
                w.pack_into(w.PREPARED,view,True,b'x'*32)

    def test_profile4_literal_and_decode_direction_length_replay(self):
        wire=bytes.fromhex('4349504304010000000000000000000100000000000000000000000000000000')
        received=Codec('S2P',lambda n:True,lambda n:None,profile=4)
        with memoryview(wire) as view:
            frame=received.decode(view)
            try:self.state.observe(frame)
            finally:frame.release()
            with self.assertRaises(CodecError):received.decode(view)
        for direction,raw in (('P2S',wire),('S2P',wire+b'x')):
            with memoryview(raw) as view:
                codec=Codec(direction,lambda n:True,lambda n:None,profile=4)
                with self.assertRaises(CodecError):codec.decode(view)
        self.assertEqual(self.state.snapshot()['phase'],'idle')


    def cancelled_unknown(self):
        self.running();self.emit(w.TURN,1,20,1,1536,0)
        self.emit(w.CANCEL,1,1)
        self.emit(w.PROGRESS,1,20,1,23,0,0,0,1)
        state=self.state.snapshot()
        self.assertEqual(state['phase'],'failed_operation')
        self.assertEqual(state['raw_counts'],(23,0,0))
        self.assertEqual(state['turn'],(20,1,1536,0))

    def test_cancel_unknown_progress_rejects_wrong_refusal_ack(self):
        self.cancelled_unknown()
        with self.assertRaises(w.ServiceWireError):self.emit(w.REFUSE,1,1)

    def test_cancel_unknown_progress_rejects_release_before_ack(self):
        self.cancelled_unknown()
        with self.assertRaises(w.ServiceWireError):self.emit(w.RELEASE,1,10)

    def test_cancel_unknown_progress_cannot_repeat_unsettled_receipt(self):
        self.cancelled_unknown()
        with self.assertRaises(w.ServiceWireError):
            self.emit(w.PROGRESS,1,20,1,23,0,0,0,1)
        self.assertEqual(self.state.snapshot()['raw_counts'],(23,0,0))

    def test_cancel_unknown_progress_ack_preserves_prefix_and_commitment(self):
        self.cancelled_unknown()
        self.emit(w.REFUSE,1,6);self.emit(w.RELEASE,1,10)
        self.emit(w.RELEASED,1,10,0)
        state=self.state.snapshot()
        self.assertEqual(state['phase'],'idle')
        self.assertTrue(state['attempt_failed'])
        self.assertEqual(state['turn'],(20,1,1536,0))
        self.assertEqual(state['raw_counts'],(23,0,0))


if __name__=='__main__':unittest.main()
