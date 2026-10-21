"""Authored full retrieval allowance through actual inert fixed-run custody.

These are synthetic source identities and child programs, not original cases,
selected vendor compatibility, fresh grading or weekly usefulness evidence.
"""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_trial_sdk_native_run as native
import test_trial_sdk_selected_input as selected


# Keep the same real initialization, prompt and correlated hook observations as
# the single-search fixture. Replace only its authored native retrieval actions.
RETRIEVAL = r'''
def retrieve(ordinal,name,arguments):
    ident='tool-'+str(ordinal)
    send({'type':'assistant','message':{'content':[{'type':'tool_use','id':ident,'name':'mcp__cairn__'+name,'input':arguments}]}})
    result=call(ordinal,{'jsonrpc':'2.0','id':ordinal,'method':'tools/call','params':{'name':name,'arguments':arguments}})['result']
    assert result.get('isError',False) is False
    # Render precisely the returned content, including the native envelope.
    # The parent independently charges this original complete stream line.
    send({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':ident,'is_error':False,'content':result['content']}]}})
    return json.loads(result['content'][0]['text'])
for ordinal in (3,6):
    found=retrieve(ordinal,'cairn_search',{'query':'inert fixture','available_tokens':32000})
    args=found['index'][0]['pull_arguments']
    whole=dict(args,request_id='20000000-0000-4000-8000-'+str(ordinal+1).zfill(12))
    retrieve(ordinal+1,'cairn_pull',whole)
    span=dict(args,request_id='20000000-0000-4000-8000-'+str(ordinal+2).zfill(12),span={'offset':6,'length':4},opening_bytes=3)
    retrieve(ordinal+2,'cairn_pull',span)
'''
FULL_NATIVE = (selected.NATIVE[:selected.NATIVE.index("\nsend({'type':'assistant'")]
               + RETRIEVAL + selected.NATIVE[selected.NATIVE.index('\nfor i,event'):])
# Complete source rows that the older dispatch-only fixture intentionally omits.
# A span/opening response has no competing whole bodies; those arrive on whole
# pulls and carry their own independent record/version identity.
FULL_COMMAND = (native.COMMAND
    .replace("'version':1,\n", "'version':1,'summary':'inert preview',\n")
    .replace("{'body':'inert competing position'}",
             "{'record_id':'30000000-0000-4000-8000-000000000002','version':1,'body':'inert competing position'}")
    .replace("if 'span' in args:\n", "if 'span' in args:\n        data['competing']=[]\n"))


class PositiveWorkloadTests(unittest.TestCase):
    setUp = native.NativeRunTests.setUp
    config = native.NativeRunTests.config
    change = native.NativeRunTests.change
    prepare = native.NativeRunTests.prepare
    cleanup = native.NativeRunTests.cleanup
    until = native.NativeRunTests.until
    start = selected.SelectedInputTests.start
    native_source = FULL_NATIVE
    command_source = FULL_COMMAND

    def test_two_search_four_pull_qualifies_actual_stream_source_and_resource_limits(self):
        owner=self.start()
        self.until(owner.step,lambda:owner._state=='terminal_waited')
        report=owner._selected_input.evidence()
        measured=report['measurement']
        self.assertEqual(measured['unknown'],[])
        self.assertEqual(measured['failures'],[])
        self.assertEqual(measured['status'],'within_observed_limits')
        self.assertEqual(measured['public_actions']['status'],'observed')
        self.assertEqual(measured['native_searches'],2)
        self.assertEqual(measured['total_actual_pull_calls'],4)
        self.assertEqual(measured['memory_calls'],6)
        gate=owner._coordinator.gate.snapshot()
        self.assertEqual(gate['native_searches_debited'],2)
        self.assertEqual(gate['expansions_debited'],4)
        self.assertEqual(measured['hook_searches'],gate['hook_searches'])
        self.assertEqual(len(owner._selected_input._hooks),2)
        self.assertTrue(all(row['process_seconds']<=5 for row in owner._selected_input._hooks))
        self.assertLessEqual(measured['last_native_memory_seconds'],30)
        self.assertGreater(measured['native_memory_result_bytes'],0)
        self.assertEqual(measured['total_bytes'],measured['input_bytes']
                         +measured['hook_wire_bytes']+measured['native_memory_result_bytes'])
        self.assertLessEqual(measured['total_bytes'],9500)
        bank=owner.resources.snapshot()
        self.assertLessEqual(bank['raw_peak'],131072)
        self.assertLessEqual(bank['known_allocation_peak'],512*2**20)
        self.assertLessEqual(bank['committed_peak'],4*2**20)
        self.assertFalse(bank['unknown'])
        retained=owner._evidence.snapshot()
        self.assertEqual(report['stdout_bytes'],retained['written']['stdout'])
        self.assertEqual(report['stdout_sha256'],retained['sha256']['stdout'])
        # Independently count the actual retained native envelopes. Control
        # traffic is kept in this same hash but is not selected-input delivery.
        digest=hashlib.sha256()
        charged=rendered=controls=0
        with (Path(owner._evidence.path)/'stdout.bin').open('rb') as stream:
            for line in stream:
                digest.update(line)
                event=json.loads(line)
                if event['type'] in ('control_request','control_response'):
                    controls+=1
                content=event.get('message',{}).get('content')
                if isinstance(content,list):
                    for part in content:
                        if part['type']=='tool_result':
                            rendered+=1
                            charged+=len(json.dumps(part,ensure_ascii=False,separators=(',',':')).encode())
        self.assertEqual(rendered,6)
        self.assertEqual(controls,11)
        self.assertEqual(report['validated_controls'],controls)
        self.assertEqual(measured['native_memory_result_bytes'],charged)
        self.assertEqual(report['stdout_sha256'],digest.hexdigest())
        deliveries=measured['source_deliveries']
        self.assertEqual(len(deliveries),6)
        self.assertTrue(all(row['delivery']['status']=='observed' for row in deliveries))
        items=[item for row in deliveries for item in row['delivery']['items']]
        self.assertEqual(len(items),10)
        self.assertEqual(sum(i['extent']=='preview' for i in items),2)
        self.assertEqual(sum(i['extent']=='full_body' for i in items),4)
        self.assertEqual(sum(i['extent']=='partial_span' for i in items),4)
        self.assertEqual(sum(i.get('source_component')=='source_opening' for i in items),2)
        self.assertEqual({i['record_id'] for i in items},
                         {'30000000-0000-4000-8000-000000000001',
                          '30000000-0000-4000-8000-000000000002'})
        for item in items:
            self.assertEqual(item['version'],1)
            body=(b'inert body' if item['record_id'].endswith('0001')
                  else b'inert competing position')
            self.assertEqual(item['source_sha256'],hashlib.sha256(body).hexdigest())
            delivered=(b'inert preview' if item['extent']=='preview' else
                       b'ine' if item.get('source_component')=='source_opening' else
                       b'body' if item['extent']=='partial_span' else body)
            self.assertEqual(item['delivered_sha256'],hashlib.sha256(delivered).hexdigest())
            self.assertEqual(item['delivered_bytes'],len(delivered))
        native_state=owner._session.native_owner.snapshot()
        self.assertEqual(native_state['wait']['returncode'],0)
        self.assertTrue(native_state['release']['released'])
        self.assertTrue(native_state['release']['handles_detached'])
        self.cleanup(owner)
        self.assertEqual(owner._state,'closed')
        self.assertEqual(owner._faults,set())
        self.assertEqual(owner._link.transcript.snapshot()['phase'],'closed')
        service_state=owner._link.owner.snapshot()
        self.assertEqual(service_state['wait']['returncode'],0)
        self.assertTrue(service_state['stdout_eof'] and service_state['stderr_eof'])
        self.assertTrue(service_state['release']['released'])
        self.assertEqual(owner._service_output.stream.snapshot()['known'],0)
        self.assertTrue(owner._service_output.stream.snapshot()['closed'])
        self.assertEqual(owner._service_shutdown.credit.snapshot()['known'],64)
        self.assertLessEqual(owner.resources.snapshot()['committed_peak'],4*2**20)
        self.assertLessEqual(owner.resources.snapshot()['raw_peak'],131072)
        self.assertLessEqual(owner.resources.snapshot()['known_allocation_peak'],512*2**20)
        self.assertTrue(all(o.snapshot()['release']['handles_detached']
                            for o in owner._original_owners()))
        self.assertEqual(owner._selected_input.evidence(),report)
        # A completed cleanup is idempotent after the original deadline; no
        # additional I/O, deadline extension or renewed shutdown is authorized.
        with patch.object(owner,'_clock',return_value=owner._deadline+1):
            self.assertEqual(owner.cleanup_step(),'closed')


if __name__=='__main__':unittest.main()
