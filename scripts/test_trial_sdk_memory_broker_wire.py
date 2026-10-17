"""Broker framing and shared backing loans; no endpoints/processes or API calls."""
import unittest

from trial_sdk_ipc import Codec, CodecError
from trial_sdk_parent_storage import Root, Channel


class MemoryBrokerWireTests(unittest.TestCase):
    def codec(self, direction='C2B', profile=3):
        return Codec(direction, lambda n: True, lambda n: None, profile=profile)

    def encode(self, direction, kind, body, operation=1, profile=3):
        codec=self.codec(direction,profile)
        with memoryview(bytearray(32+len(body))) as destination, memoryview(body) as source:
            frame=codec.encode_into(destination,kind,operation,source)
            try:
                with frame.record as record:return bytes(record)
            finally:frame.release()

    def test_broker_profile_refuses_sdk_kinds_and_directions(self):
        for direction in ('N2P_DATA','P2N_DATA','N2P_CONTROL','P2N_CONTROL'):
            with self.subTest(direction=direction):
                with self.assertRaisesRegex(CodecError,'^frame_configuration$'):
                    self.codec(direction)
        for kind in (1,2,4,9,129,133,140,201,202):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(CodecError,'^frame_kind$'):
                    self.encode('C2B',kind,b'x')
        for direction,kind,body in (('C2B',65,b'x'),('B2C',33,b'x')):
            with self.subTest(direction=direction):
                with self.assertRaisesRegex(CodecError,'^frame_direction$'):
                    self.encode(direction,kind,body)

    def test_broker_limits_and_correlated_operations_are_closed(self):
        for direction,kind,low,high in (('C2B',33,1,512),('C2B',34,0,0),
                ('B2C',65,1,512),('B2C',66,1,512),('B2C',67,4,4),('B2C',68,1,1)):
            for length in set((low,high)):
                with self.subTest(kind=kind,length=length):
                    wire=self.encode(direction,kind,b'x'*length)
                    with memoryview(wire) as view:
                        decoded=self.codec(direction).decode(view)
                        self.assertEqual((decoded.kind,decoded.operation),(kind,1))
                        decoded.release()
            for length in set((low-1,high+1))-{ -1 }:
                with self.subTest(kind=kind,invalid_length=length):
                    with self.assertRaisesRegex(CodecError,'^frame_length$'):
                        self.encode(direction,kind,b'x'*length)
            for operation in (0,True,2**53):
                with self.subTest(kind=kind,operation=operation):
                    with self.assertRaisesRegex(CodecError,'^frame_operation$'):
                        self.encode(direction,kind,b'x'*low,operation)

    def test_profile_header_is_bound_and_sequence_cannot_replay(self):
        wire=self.encode('C2B',33,b'{}')
        decoder=self.codec()
        with memoryview(wire) as view:
            frame=decoder.decode(view);frame.release()
            with self.assertRaisesRegex(CodecError,'^frame_sequence$'):decoder.decode(view)
        for version in (1,2,4):
            altered=bytearray(wire);altered[4]=version
            with memoryview(altered) as view:
                with self.assertRaisesRegex(CodecError,'^frame_version$'):
                    self.codec().decode(view)

    def test_legacy_profiles_keep_independent_literal_headers(self):
        for profile in (1,2):
            wire=self.encode('N2P_DATA',2,b'x',profile=profile)
            expected=bytes.fromhex('43495043'+f'{profile:02x}'+'02000000000000000000010000000000000001000000010000000078')
            self.assertEqual(wire,expected)
            with memoryview(wire) as view:
                with self.assertRaisesRegex(CodecError,'^frame_version$'):
                    self.codec().decode(view)

    def test_broker_profile_has_own_directions_and_preserves_loans(self):
        root=Root(2048)
        output=root.allocate('request-record',544)
        payload=root.allocate('request-body',512)
        channel=Channel(Codec,root,output,'C2B',profile=3)
        body=payload.hold(2)
        body.view[:]=b'{}'
        token=channel.encode(33,1,body)
        wire=[]
        token.visit(lambda frame:wire.append(bytes(frame.record)))
        self.assertEqual(wire[0].hex(),'43495043032100000000000000000001000000000000000100000002000000007b7d')
        self.assertEqual(root.leased,34)
        token.complete(token.generation,'known')
        self.assertEqual(root.leased,0)
        body.close();payload.retire();output.retire()
        self.assertEqual(root.used,0)


if __name__=='__main__': unittest.main()
