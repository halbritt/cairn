"""Original-ticket verification and real held-file checks; no runtime launch."""
import dataclasses
import unittest
from unittest.mock import patch
import test_trial_sdk_bootstrap_review as bf
from test_trial_sdk_pinned_sources import stage_pinned_sources
import test_trial_sdk_session_resources as sf
import test_trial_sdk_fixed_run as rf
from trial_sdk_session_driver import SDKSessionDriver
from trial_native_bootstrap import PreparationRefusal

class VerifierTests(unittest.TestCase):
    setUp=bf.SDKReviewTests.setUp
    setup_binding=sf.SessionResourcesTests.setup_binding

    def session(self):
        review,binding=self.setup_binding()
        driver=SDKSessionDriver.from_resources(review,binding)
        self.addCleanup(driver.close_sources)
        return driver,binding

    def test_void_check_avoids_export_and_preserves_first_hook_user_backing(self):
        driver,binding=self.session();custody=driver._custody;allocation=custody._allocation
        held=dict(binding.resources._memory);user=custody.control._user
        self.assertFalse(custody.control._user_written)
        # Actual selected first-hook state transition, synthetic write report.
        control=custody.control
        control.written(len(control.pending_write()))
        control.receive(dict(type='control_request',request_id='first-hook',request=dict(
            subtype='hook_callback',callback_id=control.bindings['callback_ids']['SessionStart'],
            input=dict(session_id=control.bindings['session_id'],hook_event_name='SessionStart'))))
        self.assertFalse(control._user_written)
        with patch('trial_native_bootstrap.copy.deepcopy',side_effect=AssertionError('metadata export')), \
             patch.object(custody._snapshot,'bindings',side_effect=AssertionError('full metadata')):
            self.assertIsNone(driver.verify_sources())
        self.assertIs(custody.control._user,user)
        self.assertEqual(binding.resources._memory,held)
        self.assertFalse(allocation._verification_active)

    def test_explicit_export_keeps_original_charge_and_independent_containers(self):
        driver,binding=self.session();held=dict(binding.resources._memory)
        result=driver.source_bindings()
        result['initial_control']['bindings']['session_id']='mutated export'
        self.assertIsNone(driver.verify_sources())
        self.assertNotEqual(result['initial_control'],driver._custody._initial['control'])
        self.assertEqual(binding.resources._memory,held)

    def test_void_and_export_both_refuse_source_and_launch_mutations(self):
        for mode in ('verify_sources','source_bindings'):
            for mutation in ('source','argv','control','metadata'):
                with self.subTest(mode=mode,mutation=mutation):
                    self.output=str(self.root/(mode+mutation))
                    driver,binding=self.session();custody=driver._custody
                    if mutation=='source':
                        leaf=custody._snapshot.files['instruction.txt'][0]
                        import os
                        os.fchmod(leaf.fd,0o600)
                    elif mutation=='argv':
                        custody._launch=dataclasses.replace(custody.launch,argv=custody.launch.argv+('changed',))
                    elif mutation=='control':custody.control._session='changed'
                    else:custody._initial['snapshots']['instruction.txt']['sha256']='0'*64
                    with self.assertRaises(PreparationRefusal):getattr(driver,mode)()
                    self.assertTrue(binding.resources.snapshot()['unknown'])
                    self.assertIn(custody._allocation.ticket,binding.resources._memory)

    def test_reentrant_scope_freezes_original_ticket(self):
        driver,binding=self.session();a=driver._custody._allocation
        with self.assertRaisesRegex(PreparationRefusal,'session_custody_unknown'):
            with a.verification():driver.verify_sources()
        self.assertTrue(a._unknown)
        self.assertIn(a.ticket,binding.resources._memory)

    def test_scope_exit_interrupt_keeps_original_charge_unknown(self):
        driver,binding=self.session();a=driver._custody._allocation
        held=dict(binding.resources._memory);original=a.check;calls=[]
        def check():
            calls.append(1)
            if len(calls)==2:raise KeyboardInterrupt('before scope close')
            original()
        with patch.object(a,'check',check):
            with self.assertRaises(KeyboardInterrupt):
                with a.verification():pass
        self.assertTrue(a._unknown)
        self.assertEqual(binding.resources._memory,held)

    def test_interrupt_retains_charge_and_cleanup_owner(self):
        driver,binding=self.session();a=driver._custody._allocation;held=dict(binding.resources._memory)
        with patch.object(driver._custody._snapshot,'_readback',side_effect=KeyboardInterrupt('read')):
            with self.assertRaises(KeyboardInterrupt):driver.verify_sources()
        self.assertTrue(a._unknown)
        self.assertEqual(binding.resources._memory,held)
        with self.assertRaises(Exception):driver.verify_sources()
        driver.close_sources()
        self.assertTrue(driver._custody._snapshot.closed)

class ServiceVerifierTests(unittest.TestCase):
    def setUp(self):
        bf.SDKReviewTests.setUp(self)
        stage_pinned_sources(self)

    setup_binding=sf.SessionResourcesTests.setup_binding
    config=rf.RunTests.config
    prepared=rf.RunTests.prepared

    def test_real_service_check_is_sequential_under_original_allocation(self):
        owner,review,binding=self.prepared();allocation=owner._service._allocation
        calls=[];original=allocation.verification
        from contextlib import contextmanager
        @contextmanager
        def observed():
            calls.append('enter')
            with original():
                self.assertTrue(allocation._verification_active)
                yield
            calls.append('exit')
        with patch.object(allocation,'verification',observed), \
             patch('trial_native_bootstrap.copy.deepcopy',side_effect=AssertionError('unused export')):
            owner._service._check()
        self.assertEqual(calls,['enter','exit','enter','exit'])
        self.assertFalse(allocation._verification_active)

    def test_service_config_mutation_still_refuses_and_keeps_charge(self):
        owner,review,binding=self.prepared();held=dict(binding.resources._memory)
        owner._service._config['state_dir']='/changed'
        with self.assertRaisesRegex(Exception,'fixed_config_changed'):owner._service._check()
        self.assertEqual(binding.resources._memory,held)
        self.assertTrue(binding.resources.snapshot()['unknown'])

if __name__=='__main__':unittest.main()
