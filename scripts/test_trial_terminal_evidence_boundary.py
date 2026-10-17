"""Characterize missing live provenance seams; no implementation acceptance."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import trial_task_evidence as evidence
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_fixed_run import FixedRunOwner
from trial_sdk_incoming import LineAssembler, validate_line
from trial_sdk_shared_coordinator import Turn
from trial_sdk_stream_driver import StreamEvent
from trial_work_product import attempt_closure
from trial_paired_request import Refusal


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


class TerminalEvidenceBoundaryTests(unittest.TestCase):
    def test_equal_terminal_results_do_not_determine_call_status_or_deliveries(self):
        corpus = [dict(id='fixture', body='retained synthetic guidance')]
        payload = dict(selection=dict(record=dict(scope=dict(repo='trial:task-eval'),
            id='record', version=1, body=corpus[0]['body'])))
        use = dict(type='assistant', message=dict(content=[dict(type='tool_use',
            id='call-17', name='mcp__cairn__cairn_pull', input={})]))
        result = dict(type='user', message=dict(content=[dict(type='tool_result',
            tool_use_id='call-17', content=[dict(type='text', text=json.dumps(payload))])]))
        terminal = dict(type='result', subtype='success', is_error=False, result='same ungraded answer')
        streams = []
        for failed in (False, True, None):
            rows = [use]
            if failed is not None:
                result['message']['content'][0]['is_error'] = failed
                rows.append(json.loads(json.dumps(result)))
            rows.append(terminal)
            streams.append('\n'.join(json.dumps(row) for row in rows)+'\n')
        selected = [evidence.analyze_stream(s, 'claude', corpus) for s in streams]
        self.assertEqual([s['calls'][0]['status'] for s in selected],
                         ['completed', 'failed', 'incomplete'])
        self.assertEqual([s['body_notes'] for s in selected], [['fixture'], [], []])
        self.assertTrue(all(json.loads(s.splitlines()[-1]) == terminal for s in streams))
        self.assertEqual(len({hashlib.sha256(s.encode()).hexdigest() for s in streams}), 3)

    def test_partial_span_provenance_is_not_terminal_text(self):
        corpus = [dict(id='fixture', body='abcdefgh')]
        def stream(offset):
            body = corpus[0]['body'][offset:offset+2]
            payload = dict(selection=dict(record=dict(scope=dict(repo='trial:task-eval'), body='')),
                span=dict(body=body, offset=offset, end=offset+2, total_bytes=8,
                    source_sha256=evidence.sha256(corpus[0]['body']), sha256=evidence.sha256(body)))
            return '\n'.join(map(json.dumps, [dict(type='item.completed', item=dict(id='call-1',
                type='mcp_tool_call', server='cairn', tool='cairn_pull', status='completed',
                result=dict(content=[dict(type='text', text=json.dumps(payload))]))),
                dict(type='result', result='same ungraded answer')]))
        a,b=stream(0),stream(4)
        self.assertNotEqual(a,b)
        for text in (a,b):
            observed=evidence.analyze_stream(text,'codex',corpus)
            self.assertEqual(observed['body_bytes'],2)
            self.assertEqual(observed['calls'][0]['deliveries'][0]['extent'],'partial_span')
        # Even the analyzer projection omits offsets/call IDs. Retaining only its
        # counts cannot replace the original stream for subsequent verification.
        self.assertEqual(evidence.analyze_stream(a,'codex',corpus),
                         evidence.analyze_stream(b,'codex',corpus))

    def test_missing_stream_is_not_empty_and_overlay_keeps_both_digests(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            frozen=encoded(dict(notes=[dict(id='fixture',body='original')]))
            overlay=encoded(dict(notes=[dict(id='fixture',body='overlay')]))
            (root/'corpus.json').write_bytes(frozen);(root/'observed.json').write_bytes(overlay)
            report=dict(frozen=dict(corpus_sha256=hashlib.sha256(frozen).hexdigest()),
                observed_corpus=dict(path='observed.json',sha256=hashlib.sha256(overlay).hexdigest()),
                records=[dict(run_id='synthetic',case='case',arm='CURRENT',seed=1,outcome='ungraded')])
            (root/'agent.json').write_bytes(encoded(report))
            missing=evidence.analyze_report(root/'agent.json',root/'corpus.json')
            self.assertTrue(missing['records'][0]['missing_stream'])
            self.assertNotIn('body_bytes',missing['records'][0])
            self.assertEqual(missing['corpus_sha256'],hashlib.sha256(frozen).hexdigest())
            self.assertEqual(missing['observed_corpus_sha256'],hashlib.sha256(overlay).hexdigest())
            (root/'runs/synthetic').mkdir(parents=True)
            (root/'runs/synthetic/stream.jsonl').write_bytes(b'')
            empty=evidence.analyze_report(root/'agent.json',root/'corpus.json')
            self.assertNotIn('missing_stream',empty['records'][0])
            self.assertEqual(empty['records'][0]['body_bytes'],0)
            (root/'observed.json').write_bytes(frozen)
            with self.assertRaisesRegex(ValueError,'observed corpus hash'):
                evidence.analyze_report(root/'agent.json',root/'corpus.json')

    def test_actual_run_turn_refuses_ordinary_document_without_evidence_handoff(self):
        bank=AttemptResources()
        ticket=bank.allocate(bank.local,'standing',65536,2**20)
        run=FixedRunOwner(bank,clock=lambda:1);run._state='session';run._deadline=2
        raw=encoded(dict(type='assistant',message=dict(content=[dict(type='text',text='synthetic')])) )+b'\n'
        assembly=LineAssembler(131072)
        document=validate_line(next(assembly.feed(raw,1)))
        run._coordinator=SimpleNamespace(step=lambda:Turn('session',StreamEvent('document',document=document)),
                                         _fail=lambda primary:None)
        with self.assertRaisesRegex(ValueError,'run_document_retention_missing'):
            run.step()
        self.assertTrue(run._failed)
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertIn(ticket,bank._memory)
        self.assertIsNone(run._terminal_graph)
        self.assertFalse(document.taken)
        # Inert event boundary only: this is not full standing/native admission.

    def test_export_closure_cannot_replace_unknown_persistence_with_wait(self):
        prepared=dict(binding=dict(request_id='synthetic'),workspace_sha256='a'*64)
        terminal=dict(schema='cairn.work-product-attempt-terminal/1',binding=prepared['binding'],
            workspace_sha256=prepared['workspace_sha256'],actual_wait_status=0,actual_returncode=0,
            containment_status='released',failure_evidence_status='unknown',stop_cause='completed',
            observations=[],proof_sha256='b'*64)
        with self.assertRaisesRegex(Refusal,'attempt_closure_unknown'):
            attempt_closure(prepared,lambda binding:terminal)
        terminal['failure_evidence_status']='closed'
        # Acceptance of this declared dictionary demonstrates the trust seam,
        # NOT an observed child wait, released containment or durable sink.
        self.assertIs(attempt_closure(prepared,lambda binding:terminal),terminal)

if __name__=='__main__':unittest.main()
