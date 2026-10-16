"""Real codec/receiver, controlled OS-port testimony; no process effects."""
import unittest
from test_trial_sdk_parent_reactor import (Rig, ControlledPeer, ControlledLaunch, ControlledProcess,
                          ControlledNative, ControlledStreams, FixtureValidator,
                          WireFixture, Codec, InputReceiver)
from trial_sdk_owned_reactor import OwnedEngine


class ControlledActor:
    def __init__(self):
        self.available = False
        self.code = 0
        self.observations = self.releases = 0

    def wait_ready(self):
        self.observations += 1
        self.available = False
        return self.code

    def release(self):
        self.releases += 1
        assert self.releases == 1


class OwnedRig(Rig):
    def __init__(self):
        self.now = 1
        self.ports = {'data': ControlledPeer('P2N_DATA'), 'control': ControlledPeer('P2N_CONTROL'),
                      'launch': ControlledLaunch(), 'process': ControlledProcess(),
                      'native': ControlledNative(), 'streams': ControlledStreams(),
                      'validator': FixtureValidator(), 'actor': ControlledActor()}
        self.e = OwnedEngine(codec=Codec, receiver=InputReceiver, ports=self.ports,
                             clock=lambda: self.now, work_deadline=100, stop_seconds=2)
        self.wire = WireFixture('N2P_DATA')
        self.control = WireFixture('N2P_CONTROL')

    def tick(self):
        for key, ticket in list(self.e.interests().items()):
            if key == 'actor_wait' and self.ports['actor'].available:
                self.e.notify(key, ticket)
        return super().tick()

    def target_finished(self):
        self.launch()
        self.input()
        self.end()
        self.credit(124)
        self.ports['streams'].queue = {'stdout': [None], 'stderr': [None]}
        self.ports['process'].available = True
        self.pump()


def test_native_completion_waits_for_release_request_before_closing_endpoints():
    r = OwnedRig()
    r.target_finished()
    assert r.e.state == 'WAIT_RELEASE', r.e.snapshot()
    assert not any(kind == 139 for kind, _, _ in r.ports['data'].sent)
    assert r.ports['process'].releases == 0
    assert r.ports['data'].close_calls == r.ports['control'].close_calls == 0


def test_local_release_keeps_real_external_quota_until_actual_actor_port_wait():
    r = OwnedRig()
    # Fixed parent1131 + counterparty22386 + actual custody decoder scratch16.
    assert r.e.root.used == 23533 and r.e.root.counterparty_reserved == 22386
    assert not any(name.startswith('node_') or name == 'receive_aux' for name in r.e.regions)
    r.target_finished()
    r.data(8, 4)
    r.pump()
    snap = r.e.snapshot()
    assert snap['state'] == 'ENGINE_LOCAL_RELEASED', snap
    assert (139, 4, b'\1') in r.ports['data'].sent
    assert snap['target_released'] and snap['local_released'] and not snap['actor_released']
    assert snap['local_reserved'] == 0 and snap['counterparty_reserved'] == 22386
    assert r.ports['actor'].observations == r.ports['actor'].releases == 0
    assert all(region.closed and region.backing is None for region in r.e.regions.values())
    r.ports['actor'].available = True
    r.pump()
    snap = r.e.snapshot()
    assert snap['state'] == 'RELEASED' and snap['actor_wait'] == 0, snap
    assert snap['actor_released'] and snap['backing_reserved'] == 0
    assert r.ports['actor'].observations == r.ports['actor'].releases == 1
    before = r.e.actions
    r.pump()
    assert r.e.actions == before and r.ports['actor'].releases == 1


def test_blocked_release_retains_exact_frame_and_keeps_control_independent():
    r = OwnedRig()
    r.target_finished()
    r.ports['data'].writable = False
    r.data(8, 4)
    r.pump()
    box = r.e.box['data']
    assert box is not None and r.e.target_released and not r.e.local_released
    assert box[0].visit(lambda frame: (frame.kind, frame.operation, bytes(frame.body))) == (139, 4, b'\1')
    r.e.notify('data_tx', r.e.interests()['data_tx'])
    r.pump()
    calls = r.ports['data'].send_calls
    r.ports['process'].signal = lambda operation, name: 'rejected'
    r.ctrl(3, op=1)
    r.pump()
    assert (202, 1, b'\0') in r.ports['control'].sent
    assert r.e.box['data'] is box and r.ports['data'].send_calls == calls
    assert r.ports['process'].releases == r.ports['streams'].closes == 1
    assert r.ports['data'].close_calls == 0 and r.e.root.counterparty_reserved == 22386
    r.ports['data'].writable = True
    r.pump()
    assert r.e.local_released and r.e.release_ack_sent
    assert sum(kind == 139 for kind, _, _ in r.ports['data'].sent) == 1
    r.ports['actor'].available = True
    r.pump()
    assert r.e.state == 'RELEASED' and r.e.root.used == 0


def test_unknown_release_send_never_replays_or_refunds_counterparty():
    r = OwnedRig()
    r.target_finished()
    r.ports['data'].send_outcome = 1
    r.data(8, 4)
    r.pump()
    assert r.e.target_released and r.e.unknown and not r.e.release_ack_sent
    assert r.e.primary == 'send_unknown' and r.e.root.leased > 0
    count = r.ports['data'].send_calls
    r.ports['actor'].available = True
    r.pump()
    assert r.e.actor_wait_code == 0
    assert r.e.root.counterparty_reserved == 22386 and r.ports['actor'].releases == 0
    r.now = 3
    r.pump()
    assert r.e.state == 'UNRESOLVED' and r.ports['data'].send_calls == count


def test_endpoint_close_unknown_retains_failure_and_is_never_retried():
    r = OwnedRig()
    r.target_finished()
    calls = []
    def unknown():
        calls.append('control')
        raise OSError('injected close outcome unknown')
    r.ports['control'].close = unknown
    r.data(8, 4)
    r.pump()
    assert r.e.release_ack_sent and r.e.unknown and not r.e.local_released
    assert r.e.root.counterparty_reserved == 22386
    r.ports['actor'].available = True
    r.pump()
    r.now = 3
    r.pump()
    assert r.e.state == 'UNRESOLVED' and calls == ['control']
    assert r.e.root.counterparty_reserved == 22386


def test_actor_cleanup_unknown_keeps_external_credit_after_known_wait():
    r = OwnedRig()
    r.target_finished()
    r.data(8, 4)
    r.pump()
    calls = []
    def unknown():
        calls.append('actor')
        raise OSError('injected actor owner cleanup unknown')
    r.ports['actor'].release = unknown
    r.ports['actor'].available = True
    r.pump()
    assert r.e.actor_wait_code == 0 and r.e.unknown and not r.e.actor_released
    assert r.e.local_released and r.e.root.used == r.e.root.counterparty_reserved == 22386
    r.now = 3
    r.pump()
    assert r.e.state == 'UNRESOLVED' and calls == ['actor']


def load_tests(loader, tests, pattern):
    cases = (
        test_native_completion_waits_for_release_request_before_closing_endpoints,
        test_local_release_keeps_real_external_quota_until_actual_actor_port_wait,
        test_blocked_release_retains_exact_frame_and_keeps_control_independent,
        test_unknown_release_send_never_replays_or_refunds_counterparty,
        test_endpoint_close_unknown_retains_failure_and_is_never_retried,
        test_actor_cleanup_unknown_keeps_external_credit_after_known_wait,
    )
    return unittest.TestSuite(unittest.FunctionTestCase(case) for case in cases)


if __name__ == '__main__':
    unittest.main()
