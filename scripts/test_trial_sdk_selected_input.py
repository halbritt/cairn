"""Inert fixed-run stream, hook and final-custody qualification boundary."""
import dataclasses
import hashlib
import json
import unittest
from unittest.mock import patch
import test_trial_sdk_native_run as native
from trial_sdk_selected_input import RAW, WORKING

NATIVE = r'''#!/usr/bin/python3
import json,sys,os
MODE = 'complete'
def send(value): print(json.dumps(value,ensure_ascii=True,separators=(', ', ' : ')),flush=True)
initialize=json.loads(sys.stdin.readline())
send({'type':'control_response','response':{'subtype':'success','request_id':initialize['request_id'],'response':{}}})
user=json.loads(sys.stdin.readline())
send({'type':'system','subtype':'init','model':sys.argv[sys.argv.index('--model')+1]})
send(user)
if MODE=='overflow':
    for i in range(300):
        send({'type':'assistant','message':{'content':[{'type':'tool_use','id':'overflow-'+str(i),'name':'mcp__cairn__cairn_search','input':{}}]}})
if MODE=='unknown-control':
    send({'type':'control_request','request_id':'bad','request':{'subtype':'unsupported'}})
    sys.stdin.read();sys.exit(0)
if MODE=='malformed-control':
    send({'type':'control_response','response':False})
    sys.stdin.read();sys.exit(0)
def call(ordinal,message):
    ident='memory-'+str(ordinal)
    send({'type':'control_request','request_id':ident,'request':{'subtype':'mcp_message','server_name':'cairn','message':message}})
    response=json.loads(sys.stdin.readline())
    assert response['response']['request_id']==ident
    return response['response']['response']['mcp_response']
call(1,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'fixture','version':'1'}}})
call(2,{'jsonrpc':'2.0','method':'notifications/initialized'})
send({'type':'assistant','message':{'content':[{'type':'tool_use','id':'tool-error','name':'mcp__cairn__cairn_search','input':{'query':'error'}}]}})
result=call(3,{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'cairn_search','arguments':{'query':'error'}}})
part={'type':'tool_result','tool_use_id':'tool-error','is_error':result['result']['isError'],'content':result['result']['content']}
if MODE=='repeated': part['content']='é'*2300
send({'type':'user','message':{'content':[part]}})
if MODE=='repeated': send({'type':'user','message':{'content':[part]}})
for i,event in enumerate(('SessionStart','UserPromptSubmit')):
    ident='hook-'+str(i)
    send({'type':'system','subtype':'hook_started','hook_id':ident,'hook_event':event})
    callback=initialize['request']['hooks'][event][0]['hookCallbackIds'][0]
    send({'type':'control_request','request_id':ident,'request':{'subtype':'hook_callback','callback_id':callback,'input':{'session_id':sys.argv[sys.argv.index('--session-id')+1],'hook_event_name':event,'cwd':os.getcwd(),'prompt':user['message']['content']}}})
    response=json.loads(sys.stdin.readline())
    output=response['response']['response']['hookSpecificOutput']
    # Authored native fixture renders the actual returned context in the
    # fixed producer's canonical envelope; the parent independently matches
    # its observed pipe digest, so a wrong rendering remains unqualified.
    stdout=json.dumps({'hookSpecificOutput':output},ensure_ascii=False,sort_keys=True,separators=(',',':')) if output['additionalContext'] else '{}'
    send({'type':'system','subtype':'hook_response','hook_id':ident,'hook_event':event,'stdout':stdout,'outcome':'success','exit_code':0})
send({'type':'result','session_id':sys.argv[sys.argv.index('--session-id')+1],'subtype':'success','is_error':False,'result':'fixture','permission_denials':[]})
'''

class SelectedInputTests(unittest.TestCase):
    setUp = native.NativeRunTests.setUp
    config = native.NativeRunTests.config
    change = native.NativeRunTests.change
    prepare = native.NativeRunTests.prepare
    cleanup = native.NativeRunTests.cleanup
    until = native.NativeRunTests.until
    native_source = NATIVE

    def start(self, mode='complete'):
        owner=self.prepare(mode)
        owner.start_service()
        self.until(owner.step,lambda:owner._state=='session')
        owner.start_native()
        return owner

    def test_complete_actual_stream_and_hook_custody_qualifies_only_observed_limits(self):
        owner=self.start()
        with self.assertRaisesRegex(ValueError,'selected_input_unqualified'):
            owner._selected_input.evidence()
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        report=owner._selected_input.evidence()
        measured=report['measurement']
        self.assertEqual(measured['unknown'],[])
        self.assertEqual(measured['status'],'within_observed_limits')
        self.assertEqual(measured['hook_wire_bytes'],sum(r['stdout_bytes']+r['stderr_bytes'] for r in owner._selected_input._hooks))
        self.assertGreater(measured['hook_wire_bytes'],4)
        self.assertEqual(measured['native_searches'],1)
        self.assertGreater(measured['native_memory_result_bytes'],0)
        self.assertEqual(measured['public_actions']['status'],'observed')
        retained=owner._evidence.snapshot()
        self.assertEqual(report['stdout_bytes'],retained['written']['stdout'])
        self.assertEqual(report['stdout_sha256'],retained['sha256']['stdout'])
        self.assertEqual(report['validated_controls'],6)
        self.assertLessEqual(owner.resources.snapshot()['raw_peak'],131072)
        self.assertLessEqual(owner.resources.snapshot()['known_allocation_peak'],512*2**20)
        self.cleanup(owner)
        self.assertEqual(owner._selected_input.evidence(),report)

    def test_final_evidence_rechecks_original_binding_and_returns_no_mutable_authority(self):
        owner=self.start()
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        measured=owner._selected_input
        report=measured.evidence()
        report['measurement']['status']='invented'
        self.assertEqual(measured.evidence()['measurement']['status'],'within_observed_limits')
        changes=((owner._session._pump,'_selected_input',None),
                 (owner._session._pump,'_retention',None),
                 (owner._evidence,'state','unknown'),
                 (owner._session.native_owner,'_wait',None),
                 (owner._session.native_owner,'_release',None),
                 (owner._session._pump,'_line_offset',0))
        for target,attribute,value in changes:
            with self.subTest(attribute=attribute):
                with patch.object(target,attribute,value):
                    with self.assertRaises(ValueError):measured.evidence()
        before=owner._evidence.synced['stdout']
        owner._evidence.synced['stdout']=0
        try:
            with self.assertRaisesRegex(ValueError,'selected_input_stream_mismatch'):measured.evidence()
        finally:owner._evidence.synced['stdout']=before

    def test_repeated_native_error_envelopes_exceed_total_even_when_control_fits(self):
        owner=self.start('repeated')
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        measured=owner._selected_input.evidence()['measurement']
        self.assertIn('selected_input_exceeded_9500_bytes',measured['failures'])
        self.assertGreater(measured['native_memory_result_bytes'],9200)
        self.assertEqual(measured['status'],'failed')
        self.assertLess(owner._session._custody.control.ledger()['reserved_input_bytes'],9500)

    def test_unknown_control_poisoned_and_kept_in_original_retention(self):
        owner=self.start('unknown-control')
        with self.assertRaises(ValueError): self.until(owner.step,lambda:False)
        self.assertEqual(owner._selected_input.state,'unknown')
        self.assertGreater(owner._evidence.observed['stdout'],owner._selected_input._bytes)
        with self.assertRaisesRegex(ValueError,'selected_input_unqualified'):owner._selected_input.evidence()

    def test_malformed_control_cannot_qualify(self):
        owner=self.start('malformed-control')
        with self.assertRaises(ValueError): self.until(owner.step,lambda:False)
        self.assertEqual(owner._selected_input.state,'unknown')
        with self.assertRaisesRegex(ValueError,'selected_input_unqualified'):owner._selected_input.evidence()

    def test_actual_stream_correlation_overflow_cannot_qualify(self):
        owner=self.start('overflow')
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        measured=owner._selected_input.evidence()['measurement']
        self.assertEqual(measured['status'],'unknown')
        self.assertEqual(measured['memory_calls'],256)
        self.assertIn('memory_correlation_limit',measured['unknown'])
        self.assertIn('rendered_memory_dispatch_uncorrelated',measured['unknown'])

    def test_full_two_search_four_pull_dispatch_keeps_whole_bank_caps(self):
        self.native_source=native.NATIVE.replace("error=call(7,", """for extra in (20,21):
    arguments=dict(args,request_id='20000000-0000-4000-8000-'+str(extra).zfill(12))
    call(extra,{'jsonrpc':'2.0','id':extra,'method':'tools/call','params':{'name':'cairn_pull','arguments':arguments}})
error=call(7,""")
        owner=self.start()
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        gate=owner._coordinator.gate.snapshot()
        self.assertEqual(gate['native_searches_debited'],2)
        self.assertEqual(gate['expansions_debited'],4)
        bank=owner.resources.snapshot()
        self.assertLessEqual(bank['raw_peak'],131072)
        self.assertLessEqual(bank['known_allocation_peak'],512*2**20)
        self.assertLessEqual(bank['committed_peak'],4*2**20)
        self.assertFalse(bank['unknown'])
        self.assertEqual(owner._selected_input.evidence()['measurement']['status'],'unknown')

    def test_actual_receipt_mismatch_fails_before_qualification(self):
        from trial_sdk_stream_driver import StreamLineReceipt
        owner=self.start()
        original=StreamLineReceipt
        def changed(*args):
            return dataclasses.replace(original(*args),raw_sha256='0'*64)
        with patch('trial_sdk_stream_driver.StreamLineReceipt',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'selected_input_receipt_mismatch'):
                self.until(owner.step,lambda:False)
        self.assertEqual(owner._selected_input.state,'unknown')
        with self.assertRaisesRegex(ValueError,'selected_input_unqualified'):owner._selected_input.evidence()

    def test_caller_completion_declaration_is_not_an_export_owner(self):
        from types import SimpleNamespace
        from trial_sdk_selected_input import qualification
        owner=self.prepare()
        declared=SimpleNamespace(state='finished',run=owner,
            evidence=lambda:dict(status='within_observed_limits'))
        with patch.object(owner,'_selected_input',declared):
            with self.assertRaisesRegex(ValueError,'selected_input_export_owner'):qualification(owner)
            with self.assertRaisesRegex(ValueError,'run_selected_input_unbound'):owner.require_complete()

    def test_cancel_retains_measurement_allocation_and_refuses_qualification(self):
        owner=self.start()
        allocation=owner._selected_input._allocation
        owner.cancel();self.cleanup(owner)
        row=owner.resources._lookup(owner.resources._memory,allocation)
        self.assertEqual(row[2:],(RAW,WORKING))
        with self.assertRaisesRegex(ValueError,'selected_input_unqualified'):owner._selected_input.evidence()

class CompletionTimingTests(unittest.TestCase):
    def test_split_late_result_receipt_charges_every_original_error_envelope(self):
        from test_trial_sdk_line_receipt import LineReceiptTests
        from trial_native_observation import LineMeasurement
        observer=LineMeasurement(input_bytes=500,expects_hooks=False,model='fixture',prompt='task')
        def event(graph,seconds=1):
            raw=json.dumps(graph)+'\n'
            observer.consume(raw,dict(sha256=hashlib.sha256(raw.encode()).hexdigest(),received_seconds=seconds))
        event(dict(type='system',subtype='init',model='fixture'))
        event(dict(type='user',message=dict(content='task')))
        event(dict(type='assistant',message=dict(content=[dict(type='tool_use',id='call',name='mcp__cairn__cairn_search',input={})])))
        result=dict(type='tool_result',tool_use_id='call',is_error=True,content='é')
        raw=(json.dumps(dict(type='user',message=dict(content=[result])),ensure_ascii=False)+'\n').encode()
        split=raw.index(b'\xc3')+1
        pump=LineReceiptTests().pump([raw[:split],raw[split:]])
        with patch('trial_sdk_stream_driver.time.monotonic_ns',side_effect=[29_000_000_000,29_100_000_000,30_900_000_000,31_000_000_000]):
            self.assertIsNone(pump._read_body('stdout'))
            delivered=pump._read_body('stdout')
        receipt=delivered.line_receipt
        self.assertEqual(receipt.first_byte_ns,29_000_000_000)
        for _ in range(2):
            observer.consume_document(delivered.document._graph,len(raw),
                dict(received_seconds=receipt.completed_ns/1e9))
        event(dict(type='result',permission_denials=[]),32)
        report=observer.finish(())
        self.assertEqual(report['native_memory_result_bytes'],2*len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()))
        self.assertEqual(report['last_native_memory_seconds'],31)
        self.assertIn('last_memory_exceeded_30_seconds',report['failures'])
        pump._drop_lines()

if __name__=='__main__':unittest.main()
