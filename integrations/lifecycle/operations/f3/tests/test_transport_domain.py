import os
import sys
import unittest
sys.path[:0]=['/work','/work/tests']
from effects import Action, ExchangeUnknown, SECOND
from transport import ChildTransport


def bad_values():
    return [float('-inf'),float('nan'),float('inf'),.5,True,False,-1,'50',None,[],OSError('fixture clock unavailable')]


class Clock:
    def __init__(self,samples):self.samples=list(samples);self.calls=0
    def __call__(self):
        self.calls+=1
        if self.calls>4:raise OSError('bounded fixture clock exhausted')
        if self.samples:value=self.samples.pop(0)
        else:value=100*SECOND  # Bounded escape for a missing-validation mutant.
        if isinstance(value,Exception):raise value
        return value


class PipeChild:
    """No process: fixture-owned pipes stay open until explicit fixture cleanup."""
    def __init__(self):
        ri,wi=os.pipe();ro,wo=os.pipe();re,we=os.pipe()
        self.keep=(ri,wo,we);self.pid=4242;self.polls=0
        self.stdin=os.fdopen(wi,'wb',buffering=0)
        self.stdout=os.fdopen(ro,'rb',buffering=0)
        self.stderr=os.fdopen(re,'rb',buffering=0)
    def poll(self):self.polls+=1;return None
    def close_fixture(self):
        for stream in (self.stdin,self.stdout,self.stderr):
            if not stream.closed:stream.close()
        for fd in self.keep:os.close(fd)


class Launcher:
    def __init__(self):self.calls=0;self.child=None
    def launch(self,request):self.calls+=1;self.child=PipeChild();return self.child
    def close_fixture(self):
        if self.child:self.child.close_fixture()


REQUEST={'run_id':'fixture','effect_id':'one','action':Action.START_SELECTED.value,
         'binding_sha256':'a'*64,'request_sha256':'b'*64}


class TransportDomainTests(unittest.TestCase):
    def test_bad_start_clock_never_launches_or_reopens_session(self):
        for bad in bad_values():
            with self.subTest(value=repr(bad)):
                clock=Clock([bad]);launcher=Launcher();transport=ChildTransport(clock,launcher)
                try:
                    with self.assertRaises(ExchangeUnknown) as caught:transport.exchange(dict(REQUEST),50_000_000)
                    self.assertIsNone(caught.exception.child_identity)
                    self.assertEqual(launcher.calls,0);self.assertEqual(transport.children,{})
                    with self.assertRaises(ExchangeUnknown):transport.exchange(dict(REQUEST),50_000_000)
                    self.assertEqual(launcher.calls,0)
                finally:launcher.close_fixture()

    def test_bad_loop_clock_stops_before_poll_with_child_custody(self):
        for bad in bad_values():
            with self.subTest(value=repr(bad)):
                clock=Clock([10*SECOND,bad]);launcher=Launcher();transport=ChildTransport(clock,launcher)
                try:
                    with self.assertRaises(ExchangeUnknown) as caught:transport.exchange(dict(REQUEST),50_000_000)
                    self.assertEqual(caught.exception.child_identity,'local-transport-child:4242')
                    self.assertEqual(clock.calls,2);self.assertEqual(launcher.calls,1)
                    self.assertEqual(launcher.child.polls,0)
                    self.assertIs(transport.children[('fixture','one')],launcher.child)
                    with self.assertRaises(ExchangeUnknown):transport.exchange(dict(REQUEST),50_000_000)
                    self.assertEqual(launcher.calls,1)
                finally:launcher.close_fixture()

    def test_bad_or_zero_budget_refuses_before_clock_and_launch(self):
        for bad in bad_values()+[0]:
            with self.subTest(value=repr(bad)):
                clock=Clock([10*SECOND]);launcher=Launcher();transport=ChildTransport(clock,launcher)
                try:
                    with self.assertRaises(ExchangeUnknown):transport.exchange(dict(REQUEST),bad)
                    self.assertEqual(clock.calls,0);self.assertEqual(launcher.calls,0)
                finally:launcher.close_fixture()

    def test_valid_integer_wait_expiry_retains_custody(self):
        clock=Clock([10*SECOND,10*SECOND+50_000_000]);launcher=Launcher();transport=ChildTransport(clock,launcher)
        try:
            with self.assertRaises(ExchangeUnknown) as caught:transport.exchange(dict(REQUEST),50_000_000)
            self.assertEqual(str(caught.exception),'outcome_wait_expired')
            self.assertEqual(clock.calls,2);self.assertEqual(launcher.calls,1)
            self.assertIs(transport.children[('fixture','one')],launcher.child)
        finally:launcher.close_fixture()


if __name__=='__main__':unittest.main(verbosity=2)
