"""Actual callback RPC/service path with inert edges; unchanged quotes."""
import json
import unittest
from unittest.mock import patch

from test_trial_sdk_callback_phases import PhaseHarness, MIB
from test_trial_sdk_engine_memory_rpc_parent import ARGS
from trial_sdk_read_custody import ReadCustody, ReadCustodyError
from trial_sdk_shared_coordinator import SharedCoordinator, CoordinatorError
import test_trial_sdk_shared_coordinator as coordinator_fixture

RECEIPT = '00000000-0000-4000-8000-000000000001'
HANDLE = '00000000-0000-4000-8000-000000000002'
REQUEST = '00000000-0000-4000-8000-000000000003'
RECORD = '00000000-0000-4000-8000-000000000004'


def search_result(**updates):
    data = dict(status='READY', destination={'name':'hosted'}, receipt_id=RECEIPT,
                index=[dict(record_id=RECORD, version=1, **{'class':'A'},
                    pull_arguments=dict(request_id=REQUEST,receipt_id=RECEIPT,handle=HANDLE),
                    summary_span=dict(offset=0,length=10))])
    data.update(updates)
    return json.dumps(dict(ok=True,data=data)).encode()


class CustodyHarness(PhaseHarness):
    def __init__(self, test):
        super().__init__(test)
        self.stdout, self.stderr, self.code = search_result(), b'', 0
        self.rpc._read_custody = ReadCustody(self.rpc, self.service._policy)

    def handoff(self):
        self.rpc._read_custody.authorize_pending()
        super().handoff()


class ReadCustodyTests(unittest.TestCase):
    def test_actual_search_offer_permits_pull_and_retains_original_charges(self):
        h = CustodyHarness(self)
        with h.inert_owner(), h.readiness():
            h.finish_input()
            h.client.call('search', ARGS)
            self.assertIn((RECEIPT,HANDLE),h.rpc._read_custody._offers)
            self.assertEqual(h.resources._memory[h.allocation][3],193*MIB)
            h.stdout=json.dumps(dict(ok=True,data={'selection':{'record':{
                'record_id':RECORD,'version':1,'body':'synthetic'}}})).encode()
            answer=h.client.call('pull',(),json.dumps(dict(
                request_id=REQUEST,receipt_id=RECEIPT,handle=HANDLE)))
            self.assertIn('synthetic',answer.stdout)
            self.assertEqual(h.resources._memory[h.allocation][3],257*MIB)
            self.assertEqual(h.gate.snapshot()['expansions_debited'],1)
            h.client.close()

    def test_producer_unoffered_inventory_refuses_before_command_or_gate(self):
        h=CustodyHarness(self)
        with h.inert_owner(),h.readiness():
            h.finish_input()
            with self.assertRaisesRegex(ReadCustodyError,'read_custody_unoffered'):
                h.client.call('pull',(),json.dumps(dict(
                    request_id=REQUEST,receipt_id=RECEIPT,handle=HANDLE)))
        self.assertEqual(h.gate.snapshot()['dispatches'],0)
        self.assertEqual(h.service_endpoint.sent,[])
        self.assertIsNone(h.rpc._operation)

    def test_unoffered_span_refuses_without_second_forward(self):
        h=CustodyHarness(self)
        with h.inert_owner(),h.readiness():
            h.finish_input();h.client.call('search',ARGS)
            with self.assertRaisesRegex(ReadCustodyError,'read_custody_span'):
                h.client.call('pull',(),json.dumps(dict(request_id=REQUEST,
                    receipt_id=RECEIPT,handle=HANDLE,span=dict(offset=1,length=10))))
        self.assertEqual(h.gate.snapshot()['dispatches'],1)
        self.assertEqual(h.resources._memory[h.allocation][3],193*MIB)

    def test_wrong_destination_never_publishes_offers_or_success_status(self):
        h=CustodyHarness(self);h.stdout=search_result(destination={'name':'local'})
        with h.inert_owner(),h.readiness():
            h.finish_input()
            with self.assertRaisesRegex(ReadCustodyError,'read_custody_destination'):
                h.client.call('search',ARGS)
        self.assertEqual(h.rpc._read_custody._offers,{})
        self.assertTrue(h.rpc._unknown)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertIsNotNone(h.rpc._operation._parent)

    def test_result_observation_rejects_arbitrary_no_operation_call(self):
        h=CustodyHarness(self)
        with self.assertRaisesRegex(ReadCustodyError,'read_custody_result'):
            h.rpc._read_custody.observe_completed()
        self.assertEqual(h.rpc._read_custody._offers,{})

    def test_interrupt_at_parse_retains_parent_and_freezes(self):
        h=CustodyHarness(self);primary=KeyboardInterrupt('synthetic parse interrupt')
        with h.inert_owner(),h.readiness(),patch('trial_sdk_read_custody.parse_document',side_effect=primary):
            h.finish_input()
            with self.assertRaises(KeyboardInterrupt) as caught:h.client.call('search',ARGS)
        self.assertIs(caught.exception,primary)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertIsNotNone(h.rpc._operation._parent)
        self.assertEqual(h.rpc._read_custody._offers,{})

    def test_other_callback_and_forged_handle_have_no_offer(self):
        for receipt, handle in ((RECEIPT,HANDLE),(RECEIPT,RECORD)):
            h=CustodyHarness(self)
            with h.inert_owner(),h.readiness():
                h.finish_input()
                with self.assertRaisesRegex(ReadCustodyError,'read_custody_unoffered'):
                    h.client.call('pull',(),json.dumps(dict(request_id=REQUEST,
                        receipt_id=receipt,handle=handle)))
            self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_record_mismatch_does_not_publish_result_and_keeps_charge(self):
        h=CustodyHarness(self)
        with h.inert_owner(),h.readiness():
            h.finish_input();h.client.call('search',ARGS)
            h.stdout=json.dumps(dict(ok=True,data={'selection':{'record':{
                'record_id':HANDLE,'version':1}}})).encode()
            with self.assertRaisesRegex(ReadCustodyError,'read_custody_selection'):
                h.client.call('pull',(),json.dumps(dict(request_id=REQUEST,
                    receipt_id=RECEIPT,handle=HANDLE)))
        self.assertIsNotNone(h.rpc._operation._parent)
        self.assertTrue(h.resources.snapshot()['unknown'])

    def test_invalid_json_preserves_live_parent_charge(self):
        h=CustodyHarness(self);h.stdout=b'not JSON'
        with h.inert_owner(),h.readiness():
            h.finish_input()
            with self.assertRaises(ValueError):h.client.call('search',ARGS)
        self.assertEqual(h.rpc._read_custody._offers,{})
        self.assertIsNotNone(h.rpc._operation._parent)
        self.assertTrue(h.resources.snapshot()['unknown'])

    def test_actual_engine_summary_span_then_source_opening(self):
        import hashlib
        import time
        from test_trial_sdk_engine_adapter import engine_module
        from trial_sdk_engine_adapter import EngineMemoryAdapter
        h=CustodyHarness(self)
        source='x'*2000
        digest=hashlib.sha256(source.encode()).hexdigest()
        entry=dict(record_id=RECORD,version=1,body_sha256=digest,
                   summary_span=dict(offset=900,length=10),
                   pull_arguments=dict(request_id=REQUEST,receipt_id=RECEIPT,handle=HANDLE),
                   **{'class':'A'})
        h.stdout=search_result(index=[entry])
        engine=engine_module()
        config=dict(cairn='/fixed/cairn',socket='/fixed/socket',token_file='/fixed/token',
                    repo='repo',task_id='task',run_id='run',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        def response(offset,end):
            text=source[offset:end]
            return json.dumps(dict(ok=True,data=dict(selection=dict(mandatory=False,
                record=dict(record_id=RECORD,version=1,body='',**{'class':'A'})),
                span=dict(offset=offset,end=end,total_bytes=len(source),body=text,
                    sha256=hashlib.sha256(text.encode()).hexdigest(),source_sha256=digest)))).encode()
        with h.inert_owner(),h.readiness(),EngineMemoryAdapter(engine,config,h.client):
            h.finish_input();memory=engine.Memory(config,'session')
            memory.search('query')
            deadline=time.monotonic()+5
            h.stdout=response(900,2000)
            span=engine.current_span_pull(memory,entry,deadline)
            h.stdout=response(0,768)
            candidate=dict(pull_arguments=entry['pull_arguments'],response=span)
            inspection=dict(pull_calls=1,remaining_pull_calls=3)
            paired=engine.attach_source_opening(memory,entry,None,candidate,inspection,
                                                deadline,lambda item:'')
            self.assertEqual(paired['source_opening_excerpt']['status'],'provided')
            self.assertEqual(inspection['pull_calls'],2)
            self.assertEqual(h.resources._memory[h.allocation][3],321*MIB)
            h.client.close()


class CoordinatorReadBoundaryTests(unittest.TestCase):
    setUp=coordinator_fixture.CoordinatorTests.setUp
    setup_binding=coordinator_fixture.CoordinatorTests.setup_binding
    setup_coordinator=coordinator_fixture.CoordinatorTests.setup_coordinator

    def test_no_custody_no_automatic_handoff(self):
        h,session,coordinator=self.setup_coordinator()
        with self.assertRaisesRegex(CoordinatorError,'coordinator_read_custody'):
            coordinator.prepare_next_command(None)
        self.assertEqual(h.gate.snapshot()['dispatches'],0)
        self.assertIsNone(coordinator.operation)
        self.assertTrue(h.resources.snapshot()['unknown'])


if __name__=='__main__':unittest.main()
