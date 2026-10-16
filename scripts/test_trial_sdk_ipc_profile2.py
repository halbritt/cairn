import unittest
from test_trial_sdk_ipc import VECTORS, LITERAL as LEGACY
from trial_sdk_ipc import Codec, CodecError, CreditDenied
from trial_sdk_input_profile import InputClient

LITERAL = bytes.fromhex('4349504302090000000000000000000100000000000000070000000300000000616263')
RECEIPT = bytes.fromhex('43495043028c00000000000000000001000000000000000700000002000000000003')
END = bytes.fromhex('43495043028500000000000000000002000000000000000c00000005000000000100000003')


class Profile2Tests(unittest.TestCase):
    def test_explicit_profile2_custody_wire(self):
        held = []
        c = Codec('N2P_DATA', lambda n: held.append(n) or True, held.remove, profile=2)
        f = c.encode_into(memoryview(bytearray(35)), 9, 7, memoryview(b'abc'))
        self.assertEqual(bytes(f.record), LITERAL)
        self.assertEqual(held, [35])
        f.release()
        self.assertEqual(held, [])
        r = Codec('N2P_DATA', lambda n: True, lambda n: None, profile=2)
        f = r.decode(memoryview(LITERAL))
        self.assertEqual((f.kind, f.operation, bytes(f.body)), (9, 7, b'abc'))
        f.release()

    def test_framed_custody_and_end_state(self):
        client = InputClient(2, 100)
        client.begin_custody(7, 3)
        sender = Codec('P2N_DATA', lambda n: True, lambda n: None, profile=2)
        receiver = Codec('P2N_DATA', lambda n: True, lambda n: None, profile=2)
        for kind, op, body, expected in [(140, 7, b'\x00\x03', RECEIPT),
                                         (133, 12, b'\x01\x00\x00\x00\x03', END)]:
            if kind == 133:
                self.assertEqual(client.snapshot()['custodied_total'], 3)
                self.assertIsNone(client.snapshot()['known_native_total'])
                client.begin_end(12)
            frame = sender.encode_into(memoryview(bytearray(len(expected))), kind, op, memoryview(body))
            self.assertEqual(bytes(frame.record), expected)
            frame.release()
            frame = receiver.decode(memoryview(expected))
            view = frame.body
            try:
                client.result(frame.operation, frame.kind, view)
            finally:
                view.release()
                frame.release()
        self.assertEqual(client.snapshot()['state'], 'closed')
        self.assertEqual(client.snapshot()['known_native_total'], 3)

    def test_all_unchanged_kinds_preserve_wire_except_profile_byte(self):
        for vector in VECTORS:
            if vector['kind'] in (4, 131, 133):
                continue
            with self.subTest(kind=vector['kind']):
                expected = bytearray.fromhex(vector['wire'])
                expected[4] = 2
                c = Codec(vector['direction'], lambda n: True, lambda n: None,
                          int(vector['sequence']), profile=2)
                f = c.encode_into(memoryview(bytearray(len(expected))), vector['kind'],
                                  vector['operation'], memoryview(bytes.fromhex(vector['body'])))
                self.assertEqual(bytes(f.record), expected)
                f.release()

    def test_cross_profile_and_removed_kinds_refuse_before_credit(self):
        for profile, wire in [(1, LITERAL), (2, LEGACY)]:
            calls = []
            c = Codec('N2P_DATA', lambda n: calls.append(n) or True, lambda n: None, profile=profile)
            with self.assertRaisesRegex(CodecError, '^frame_version$'):
                c.decode(memoryview(wire))
            self.assertEqual(calls, [])
            self.assertTrue(c.failed)
        for direction, kind, body, expected in [
            ('N2P_DATA', 4, b'abc', 'frame_kind'),
            ('P2N_DATA', 131, b'\x00\x03', 'frame_kind'),
            ('P2N_DATA', 133, b'\x01', 'frame_length'),
            ('N2P_DATA', 9, b'', 'frame_length'),
            ('N2P_DATA', 9, bytes(513), 'frame_length'),
            ('P2N_DATA', 140, b'\x00', 'frame_length'),
            ('P2N_DATA', 140, bytes(3), 'frame_length')]:
            calls = []
            c = Codec(direction, lambda n: calls.append(n) or True, lambda n: None, profile=2)
            with self.assertRaisesRegex(CodecError, '^'+expected+'$'):
                c.encode_into(memoryview(bytearray(32+len(body))), kind, 7, memoryview(body))
            self.assertEqual(calls, [])
            self.assertTrue(c.failed)

    def test_profile_selection_and_known_credit_denial(self):
        for profile in [True, False, 0, 3, '2', 2.0, None]:
            with self.assertRaisesRegex(CodecError, '^frame_configuration$'):
                Codec('N2P_DATA', lambda n: True, lambda n: None, profile=profile)
        grant = False
        c = Codec('N2P_DATA', lambda n: grant, lambda n: None, profile=2)
        destination = memoryview(bytearray(b'x'*35))
        with self.assertRaises(CreditDenied):
            c.encode_into(destination, 9, 7, memoryview(b'abc'))
        self.assertEqual(bytes(destination), b'x'*35)
        self.assertFalse(c.failed)
        grant = True
        f = c.encode_into(destination, 9, 7, memoryview(b'abc'))
        self.assertEqual(bytes(f.record), LITERAL)
        f.release()


if __name__ == '__main__':
    unittest.main()
