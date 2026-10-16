import unittest
from trial_sdk_ipc import Codec, CodecError, CreditDenied, MAX_SEQUENCE
import json
from pathlib import Path
VECTORS = json.loads(Path(__file__).with_name("trial_sdk_ipc_vectors.json").read_text())
LITERAL=bytes.fromhex('4349504301040000000000000000000100000000000000070000000300000000616263')
class Ledger:
 def __init__(self):self.live=0;self.peak=0;self.calls=[];self.returns=[]
 def reserve(self,n):self.calls.append(n);self.live+=n;self.peak=max(self.peak,self.live);return True
 def release(self,n):self.returns.append(n);self.live-=n
class Tests(unittest.TestCase):
 def test_literal_vector(self):
  l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release);f=c.decode(memoryview(LITERAL))
  self.assertEqual(bytes(f.body),b'abc');self.assertEqual(f.operation,7);self.assertEqual(l.live,35);f.release();self.assertEqual(l.live,0)
 def test_all_kinds_independent_wire_vectors(self):
  for v in VECTORS:
   with self.subTest(kind=v['kind']):
    l=Ledger();c=Codec(v['direction'],l.reserve,l.release,int(v['sequence']))
    dst=memoryview(bytearray(len(bytes.fromhex(v['wire']))));body=memoryview(bytes.fromhex(v['body']))
    f=c.encode_into(dst,v['kind'],v['operation'],body);self.assertEqual(bytes(f.record).hex(),v['wire']);f.release()
    r=Codec(v['direction'],l.reserve,l.release,int(v['sequence']));f=r.decode(dst)
    self.assertEqual(bytes(f.body).hex(),v['body']);self.assertEqual(f.sequence,int(v['sequence']));self.assertEqual(f.operation,v['operation']);f.release();self.assertEqual(l.live,0)
 def test_offset_views(self):
  backing=bytearray(b'x'+LITERAL+b'y');l=Ledger();f=Codec('N2P_DATA',l.reserve,l.release).decode(memoryview(backing)[1:-1]);self.assertEqual(bytes(f.body),b'abc');f.release()
 def test_bad_headers_refuse_before_reservation_and_poison(self):
  changes=[(0,0),(4,2),(5,255),(6,1),(7,1),(28,1),(31,1),(15,0),(15,2),(16,255),(23,0),(24,1),(27,4)]
  for offset,value in changes:
   raw=bytearray(LITERAL);raw[offset]=value;l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release)
   with self.assertRaises(CodecError):c.decode(memoryview(raw))
   self.assertEqual(l.calls,[]);self.assertTrue(c.failed)
   with self.assertRaisesRegex(CodecError,'frame_failed'):c.decode(memoryview(LITERAL))
 def test_wrong_direction_and_truncation(self):
  for raw in [LITERAL[:31],LITERAL[:-1],LITERAL+b'x',bytes(545)]:
   l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release)
   with self.assertRaises(CodecError):c.decode(memoryview(raw))
   self.assertEqual(l.calls,[])
  l=Ledger()
  with self.assertRaisesRegex(CodecError,'frame_direction'):Codec('P2N_DATA',l.reserve,l.release).decode(memoryview(LITERAL))
  self.assertEqual(l.calls,[])
 def test_one_frame_and_replay(self):
  l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release);f=c.decode(memoryview(LITERAL))
  with self.assertRaisesRegex(CodecError,'frame_busy'):c.decode(memoryview(LITERAL))
  self.assertEqual(l.calls,[35]);f.release()
  with self.assertRaisesRegex(CodecError,'frame_released'):f.release()
  with self.assertRaisesRegex(CodecError,'frame_released'):f.body
  with self.assertRaisesRegex(CodecError,'frame_sequence'):c.decode(memoryview(LITERAL))
  self.assertEqual(l.returns,[35])
 def test_credit_denied_preserves_destination_and_sequence(self):
  granted=False;returns=[]
  c=Codec('N2P_DATA',lambda n:granted,returns.append);dst=memoryview(bytearray(b'x'*35));body=memoryview(b'abc')
  with self.assertRaises(CreditDenied):c.encode_into(dst,4,7,body)
  self.assertEqual(bytes(dst),b'x'*35);self.assertFalse(c.failed);granted=True
  f=c.encode_into(dst,4,7,body);self.assertEqual(bytes(f.record),LITERAL);f.release();self.assertEqual(returns,[35])
 def test_unknown_credit_is_terminal_without_release_guess(self):
  for answer in [None,1,'yes']:
   returns=[];c=Codec('N2P_DATA',lambda n:answer,returns.append)
   with self.assertRaisesRegex(CodecError,'frame_credit_unknown'):c.decode(memoryview(LITERAL))
   self.assertTrue(c.failed);self.assertEqual(returns,[])
  def reserve(n):raise CreditDenied('private callback detail')
  c=Codec('N2P_DATA',reserve,lambda n:None)
  with self.assertRaisesRegex(CodecError,'^frame_credit_unknown$'):c.decode(memoryview(LITERAL))
  self.assertTrue(c.failed)
 def test_release_error_is_terminal_and_not_retried(self):
  calls=[]
  def release(n):calls.append(n);raise RuntimeError('private detail')
  c=Codec('N2P_DATA',lambda n:True,release);f=c.decode(memoryview(LITERAL))
  with self.assertRaisesRegex(CodecError,'frame_release_unknown'):f.release()
  self.assertTrue(c.failed);self.assertTrue(c.cleanup_unknown)
  with self.assertRaises(CodecError):f.release()
  self.assertEqual(calls,[35])
 def test_callbacks_cannot_reenter(self):
  def reserve(n):
   with self.assertRaisesRegex(CodecError,'frame_busy'):c.decode(memoryview(LITERAL))
   return True
  def release(n):
   with self.assertRaisesRegex(CodecError,'frame_busy'):c.decode(memoryview(LITERAL))
  c=Codec('N2P_DATA',reserve,release);f=c.decode(memoryview(LITERAL));f.release();self.assertFalse(c.failed)
 def test_max_sequence_used_once_then_exhausted(self):
  c=Codec('N2P_DATA',lambda n:True,lambda n:None,MAX_SEQUENCE);f=c.encode_into(memoryview(bytearray(35)),4,7,memoryview(b'abc'));self.assertEqual(f.sequence,MAX_SEQUENCE);f.release()
  with self.assertRaisesRegex(CodecError,'frame_sequence_exhausted'):c.encode_into(memoryview(bytearray(35)),4,7,memoryview(b'abc'))
 def test_body_bounds_operation_bounds_and_view_contract(self):
  for kind,op,body in [(4,7,b''),(4,7,bytes(513)),(4,0,b'a'),(4,2**53,b'a'),(4,True,b'a'),(4,-1,b'a'),(6,1,b'aa'),(6,0,b'a')]:
   l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release)
   with self.assertRaises(CodecError):c.encode_into(memoryview(bytearray(32+len(body))),kind,op,memoryview(body))
   self.assertEqual(l.calls,[])
  for raw in [LITERAL,memoryview(LITERAL)[::2]]:
   with self.assertRaisesRegex(CodecError,'frame_view'):Codec('N2P_DATA',lambda n:True,lambda n:None).decode(raw)
  base=bytearray(35)
  with self.assertRaisesRegex(CodecError,'frame_alias'):Codec('N2P_DATA',lambda n:True,lambda n:None).encode_into(memoryview(base),4,7,memoryview(base)[32:])
  with self.assertRaisesRegex(CodecError,'frame_view'):Codec('N2P_DATA',lambda n:True,lambda n:None).encode_into(memoryview(bytes(35)),4,7,memoryview(b'abc'))
 def test_each_kind_wrong_lengths(self):
  for v in VECTORS:
   length=len(bytes.fromhex(v['body']));bad=[0,513] if v['kind'] in (2,4,134,136) else [length+1]+([length-1] if length else [])
   for size in bad:
    l=Ledger();c=Codec(v['direction'],l.reserve,l.release,int(v['sequence']))
    raw=bytearray.fromhex(v['wire'][:64]);raw[24:28]=size.to_bytes(4,'big');raw.extend(bytes(size))
    with self.assertRaises(CodecError):c.decode(memoryview(raw))
    self.assertEqual(l.calls,[])
 def test_receive_credit_denied_then_same_frame(self):
  grant=False;c=Codec('N2P_DATA',lambda n:grant,lambda n:None)
  with self.assertRaises(CreditDenied):c.decode(memoryview(LITERAL))
  grant=True;f=c.decode(memoryview(LITERAL));self.assertEqual(f.sequence,1);f.release()
 def test_shared_ledger_enforces_overlapping_frame_loans(self):
  live=0
  def reserve(n):
   nonlocal live
   if live+n>544:return False
   live+=n;return True
  def release(n):
   nonlocal live
   live-=n
  a=Codec('N2P_DATA',reserve,release);b=Codec('N2P_DATA',reserve,release)
  f=a.encode_into(memoryview(bytearray(544)),4,1,memoryview(bytes(512)))
  with self.assertRaises(CreditDenied):b.decode(memoryview(LITERAL))
  self.assertEqual(live,544);f.release();g=b.decode(memoryview(LITERAL));g.release();self.assertEqual(live,0)
 def test_releasing_a_returned_view_does_not_destroy_frame_loan(self):
  l=Ledger();c=Codec('N2P_DATA',l.reserve,l.release);f=c.decode(memoryview(LITERAL))
  f.record.release();self.assertEqual(bytes(f.body),b'abc');f.release();self.assertEqual(l.live,0)
if __name__=='__main__':unittest.main()
