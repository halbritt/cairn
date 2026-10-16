"""Public controlled process port: exact requested signal and wire operation."""
import unittest
from test_trial_sdk_parent_reactor import Rig


def test_term_and_kill_keep_their_wire_operations_and_never_invent_wait():
    r = Rig()
    calls = []
    def signal(*args):
        calls.append(args)
        return 'accepted'
    r.ports['process'].signal = signal
    r.launch()
    r.ctrl(2, op=1)
    r.pump()
    r.ctrl(3, op=2)
    r.pump()
    assert calls == [(1, 'SIGTERM'), (2, 'SIGKILL')], calls
    assert (202, 1, b'\1') in r.ports['control'].sent
    assert (202, 2, b'\1') in r.ports['control'].sent
    assert r.e.wait_code is None and r.ports['process'].waits == 0
    assert r.e.primary is None


def test_prelaunch_signals_reject_without_process_effect():
    r = Rig()
    calls = []
    r.ports['process'].signal = lambda *args: calls.append(args)
    for operation, command in ((1, 2), (2, 3)):
        r.ctrl(command, op=operation)
        r.pump()
        assert (202, operation, b'\0') in r.ports['control'].sent
    assert not calls and r.ports['launch'].commits == 0
    assert r.e.primary is None and r.e.state == 'COLLECTING'


def test_stale_operation_and_unknown_command_never_forward_requested_signal():
    for command, operation in ((3, 7), (4, 8)):
        r = Rig()
        calls = []
        def signal(*args):
            calls.append(args)
            return 'accepted'
        r.ports['process'].signal = signal
        r.launch()
        r.ctrl(2, op=7)
        r.pump()
        r.ctrl(command, op=operation)
        r.pump()
        assert calls == [(7, 'SIGTERM')], calls
        assert r.e.primary == 'control_semantics_invalid'
        assert r.e.wait_code is None


def test_protocol_one_refuses_before_launch_or_signal():
    r = Rig()
    r.data(1, 1, b'\0\0\0\2\0\0\0\1')
    r.pump()
    assert r.e.primary == 'launch_header_invalid'
    assert r.ports['launch'].commits == 0 and r.ports['process'].signals == 0


def test_rejected_signal_acknowledges_zero_and_unknown_never_acknowledges():
    for outcome in ('rejected', True):
        r = Rig()
        r.launch()
        r.ports['process'].signal = lambda operation, name: outcome
        r.ctrl(3, op=1)
        r.pump()
        assert r.e.wait_code is None
        if outcome == 'rejected':
            assert (202, 1, b'\0') in r.ports['control'].sent
            assert r.e.primary is None
        else:
            assert r.e.unknown and r.e.primary == 'signal_unknown'
            assert not r.ports['control'].sent


def load_tests(loader, tests, pattern):
    cases = (
        test_term_and_kill_keep_their_wire_operations_and_never_invent_wait,
        test_prelaunch_signals_reject_without_process_effect,
        test_stale_operation_and_unknown_command_never_forward_requested_signal,
        test_protocol_one_refuses_before_launch_or_signal,
        test_rejected_signal_acknowledges_zero_and_unknown_never_acknowledges,
    )
    return unittest.TestSuite(unittest.FunctionTestCase(case) for case in cases)


if __name__ == '__main__':
    unittest.main()
