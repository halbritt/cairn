"""Real service exit bytes and interrupted receipts under original ownership."""
import unittest
from unittest.mock import patch

import test_trial_sdk_native_run as native
import test_trial_sdk_selected_input as selected
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_producer_resources import ProducerResources, ProducerResourceError


class ServiceShutdownTests(unittest.TestCase):
    setUp=native.NativeRunTests.setUp
    config=native.NativeRunTests.config
    change=native.NativeRunTests.change
    prepare=native.NativeRunTests.prepare
    until=native.NativeRunTests.until
    cleanup=native.NativeRunTests.cleanup
    native_source=selected.NATIVE

    def completed_with_exit_output(self):
        owner=self.prepare()
        original=NativeProcessOwner.start
        # Only the inert service process edge is authored differently: execute
        # the original held service unchanged, then emit an oversized diagnostic
        # at exit. All record I/O, original commands, EOF and wait remain real.
        wrapper=("import atexit,os,runpy,sys; "
                 "atexit.register(os.write,1,b'x'*600); "
                 "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')")
        def start(child,argv,cwd,env):
            if child is owner._link.owner:
                argv=(*argv[:4],'-c',wrapper,*argv[4:])
            return original(child,argv,cwd,env)
        with patch.object(NativeProcessOwner,'start',start):owner.start_service()
        self.until(owner.step,lambda:owner._state=='session')
        owner.start_native()
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        self.assertEqual(owner._selected_input.evidence()['measurement']['status'],
                         'within_observed_limits')
        return owner

    def assert_quarantined_prefix(self,owner):
        output=owner._service_output
        receipt=owner._link.owner.io_receipt()
        self.assertEqual(receipt[1],'stdout')
        count=receipt[4]
        self.assertGreater(count,0)
        self.assertLessEqual(count,512)
        self.assertEqual(output._slab[:count],b'x'*count)
        self.assertEqual(output.stream.snapshot()['known'],count)
        self.assertTrue(output.stream.snapshot()['unknown'])
        self.assertTrue(owner.resources.snapshot()['unknown'])
        self.assertIn(output.allocation,owner.resources._memory)
        self.assertIn(output.stream.ticket,owner.resources._streams)
        self.assertFalse(owner._link.owner.snapshot()['stdout_eof'])
        self.assertLessEqual(owner.resources.snapshot()['committed_peak'],4*2**20)
        self.cleanup(owner)
        self.assertEqual(owner._state,'closed_unknown')
        release=owner._link.owner.snapshot()['release']
        self.assertTrue(release['handles_detached'])
        self.assertFalse(release['released'])
        self.assertIn('stdout_eof_unobserved',release['cleanup'])
        with self.assertRaisesRegex(ValueError,'selected_input_evidence_binding'):
            owner._selected_input.evidence()

    def test_service_output_overflow_retains_charged_prefix_and_refuses_known_cleanup(self):
        owner=self.completed_with_exit_output()
        with self.assertRaisesRegex(ValueError,'service_unexpected_output'):
            self.until(owner.cleanup_step,lambda:owner._state in ('closed','closed_unknown'))
        self.assert_quarantined_prefix(owner)

    def test_interruption_after_actual_service_read_cannot_erase_prefix_or_invent_eof(self):
        owner=self.completed_with_exit_output()
        original=NativeProcessOwner.read_into
        def interrupted(child,name,view):
            count=original(child,name,view)
            if child is owner._link.owner and count>0:
                raise KeyboardInterrupt('after actual service read')
            return count
        with patch.object(NativeProcessOwner,'read_into',interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.until(owner.cleanup_step,lambda:owner._state in ('closed','closed_unknown'))
        self.assert_quarantined_prefix(owner)

    def test_original_producer_binding_still_refuses_service_standing_and_local_data(self):
        owner=self.prepare()
        owner.start_service()
        output=owner._service_output
        with self.assertRaisesRegex(ProducerResourceError,'producer_resource_binding'):
            ProducerResources(owner.resources,output.owner,output.domain,
                              output.allocation,output.stream)


if __name__=='__main__':unittest.main()
