"""Whole-bank limitation through actual callback/command state machines."""
import unittest

from test_trial_sdk_callback_phases import PhaseHarness, MIB
from test_trial_sdk_engine_memory_rpc_parent import ARGS
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_source_profile import PREPARATION_RAW, PREPARATION_WORKING
from trial_sdk_command_operation import OperationError


class SourceProfileTests(unittest.TestCase):
    def test_fourth_retained_callback_command_still_exceeds_working_cap(self):
        h=PhaseHarness(self)
        preparation=h.resources.allocate(h.resources.local,'standing',PREPARATION_RAW,PREPARATION_WORKING)
        session=h.resources.bind(NativeProcessOwner())
        standing=h.resources.allocate(session,'operation',2566,MIB)
        self.assertIsNotNone(preparation);self.assertIsNotNone(standing)
        with h.inert_owner(),h.readiness():
            h.finish_input()
            for _ in range(3):h.client.call('search',ARGS)
            self.assertEqual(h.rpc._phases.snapshot()['retained_command_working'],192*MIB)
            with self.assertRaisesRegex(OperationError,'operation_child_denied'):
                h.client.call('search',ARGS)
        # Original standing66 + callback129 + three retained64 + parent64
        # are held. Adding child64 would be515MiB, even before raw storage.
        self.assertEqual(h.resources.snapshot()['working_reserved'],451*MIB)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertIn(preparation,h.resources._memory)
        self.assertIn(standing,h.resources._memory)
        self.assertLessEqual(h.resources.snapshot()['raw_peak'],131072)


if __name__=='__main__':unittest.main()
