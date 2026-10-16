import sys, unittest, gc, weakref
sys.dont_write_bytecode = True
from trial_sdk_input_profile import InputClient as C, ProfileError, parse_custody, parse_end

def guard(event, args):
    if event.startswith(('socket.', 'subprocess.', 'os.system', 'os.exec', 'os.spawn', 'ctypes.')):
        raise RuntimeError('effect_refused')
def receipt(n): return n.to_bytes(2, 'big')
def end(n): return bytes([1]) + n.to_bytes(4, 'big')
class Tests(unittest.TestCase):
    def reject(self, c, action):
        with self.assertRaises(ProfileError): action()
        self.assertEqual(c.snapshot()['state'], 'failed')
        with self.assertRaises(ProfileError): c.begin_end(999)
    def test_fragmentation(self):
        c=C(2,1000)
        for op, offered, n in [(1,512,200),(5,312,0),(8,312,312),(20,488,488)]:
            c.begin_custody(op,offered); c.result(op,140,receipt(n))
            self.assertIsNone(c.snapshot()['known_native_total'])
            if n==0: c.writable()
        c.begin_end(25); c.result(25,133,end(1000))
        self.assertEqual(c.snapshot()['known_native_total'],1000)
        self.assertEqual(c.snapshot()['state'],'closed')
        self.reject(c,lambda:c.result(25,133,end(1000)))
    def test_operations(self):
        for op in [True,False,0,-1,2**53,1.0,'1',None]:
            c=C(2,10); self.reject(c,lambda:c.begin_custody(op,1))
        for op in [1,2]:
            c=C(2,10); c.begin_custody(2,1); c.result(2,140,receipt(1))
            self.reject(c,lambda:c.begin_end(op))
        c=C(2,10); c.begin_end(2**53-1); c.result(2**53-1,133,end(0))
    def test_correlation(self):
        for op,kind in [(2,140),(1,131),(True,140),(1,True),(1,9)]:
            c=C(2,10); c.begin_custody(1,1); self.reject(c,lambda:c.result(op,kind,receipt(1)))
        c=C(2,10); self.reject(c,lambda:c.result(1,140,receipt(1)))
        c=C(2,10); c.begin_custody(1,1); self.reject(c,lambda:c.begin_end(2))
    def test_bounds(self):
        for offered in [0,513,True,-1,1.0]:
            c=C(2,1000); self.reject(c,lambda:c.begin_custody(1,offered))
        c=C(2,1); self.reject(c,lambda:c.begin_custody(1,2))
        c=C(2,10); c.begin_custody(1,1); self.reject(c,lambda:c.result(1,140,receipt(2)))
        for profile,maximum in [(1,1),(True,1),(2,True),(2,0),(2,3145729)]:
            with self.assertRaises(ProfileError): C(profile,maximum)
    def test_pause_unknown(self):
        for phase in ['ready','pending','paused']:
            c=C(2,10)
            if phase!='ready': c.begin_custody(1,1)
            if phase=='paused': c.result(1,140,receipt(0))
            self.reject(c,c.transport_unknown); self.assertTrue(c.snapshot()['unknown'])
        c=C(2,10); self.reject(c,c.writable)
        c=C(2,10); c.begin_custody(1,1); c.result(1,140,receipt(0)); self.reject(c,lambda:c.begin_end(2))
    def test_parsers_and_close(self):
        self.assertEqual(parse_custody(bytes([255,255])),65535)
        self.assertEqual(parse_end(bytes([1,255,255,255,255])),2**32-1)
        for body in [b'',b'a',b'abc',bytearray(2),[0,1],memoryview(bytearray(2))]:
            with self.assertRaises(ProfileError): parse_custody(body)
        for body in [bytes(5),end(1),b'',bytes([2,0,0,0,0])]:
            c=C(2,10); c.begin_end(1); self.reject(c,lambda:c.result(1,133,body))
    def test_no_view_retention(self):
        v=memoryview(b'\x00\x01'); ref=weakref.ref(v)
        c=C(2,10); c.begin_custody(1,1); c.result(1,140,v)
        del v; gc.collect(); self.assertIsNone(ref())
        v=memoryview(b'x'); ref=weakref.ref(v); error=None
        try: parse_custody(v)
        except ProfileError as e: error=e
        del v; gc.collect(); self.assertIsNone(ref()); self.assertIsNotNone(error)
        snapshot=c.snapshot(); snapshot['state']='closed'; self.assertEqual(c.snapshot()['state'],'ready')
if __name__ == '__main__':
    sys.addaudithook(guard)
    unittest.main()
