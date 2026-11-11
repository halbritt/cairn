"""Negative controls for process observation decoding; no process control effects."""
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_semantic_reference as reference
import semantic_refusal_processes as processes


class ProcessObservationTest(unittest.TestCase):
    def fixture(self, root):
        (root/'driver.stdout').write_text(json.dumps(dict(case='stream-wrong-id',calls=[
            dict(error='semantic worker response ID mismatch',result={})])))
        start=dict(event='start',pid=10,start_ticks='100',pgrp=10,parent=4,
                   stdin_pipe='pipe:[50]',stdout_pipe='pipe:[60]')
        request=dict(event='request',pid=10,frame=1,cache=False,request={'id':'wire'})
        (root/'events.jsonl').write_text(json.dumps(start)+'\n'+json.dumps(request)+'\n')
        raw=b'{"id":"x","result":{"model_sha256":"fixture","algorithm":"fixture/1","scores":[]}}\n'
        quoted=lambda data: ''.join('\\x%02x'%byte for byte in data)
        (root/'trace.10').write_text('1000.010 write(1<pipe:[60]>, "'+quoted(raw)+'", '+str(len(raw))+') = '+str(len(raw))+'\n1000.040 +++ killed by SIGKILL +++\n')
        # The later read's thread file is created first: filesystem order is not wire order.
        (root/'trace.7').write_text('1000.030 read(8<pipe:[60]>, "'+quoted(raw[9:])+'", 4096) = '+str(len(raw)-9)+'\n')
        (root/'trace.8').write_text('1000.020 read(8<pipe:[60]>, "'+quoted(raw[:9])+'", 4096) = 9\n')

    def test_stdout_prefix_uses_first_read_across_go_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            result=processes.process_observation(root,'stream-wrong-id')
            self.assertEqual(result['scope'],'Go-supervisor/process-double')
            self.assertEqual(result['disposition']['stdout_prefix']['prefixes'],[dict(pid=10,hex='7b226964223a227822')])
            self.assertEqual(result['disposition']['restarts']['replacement_count'],0)

    def test_stdout_prefix_decodes_actual_xx_descriptor_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            for path in root.glob('trace.*'):
                path.write_text(re.sub(r'<(pipe:\[\d+\])>',
                    lambda match:'<'+''.join('\\x%02x'%b for b in match[1].encode())+'>',path.read_text()))
            actual=processes.process_observation(root,'stream-wrong-id')
            self.assertEqual(actual['disposition']['stdout_prefix']['prefixes'],[dict(pid=10,hex='7b226964223a227822')])

    def test_normalized_retained_trace_excerpt_records_nonempty_stdout(self):
        # Retained stream-wrong-id process-double excerpt; timestamps/pipe IDs normalized.
        worker = '1000.010 write(1<\\x70\\x69\\x70\\x65\\x3a\\x5b\\x36\\x30\\x5d>, "\\x7b\\x22\\x69\\x64\\x22\\x3a\\x22\\x77\\x72\\x6f\\x6e\\x67\\x22\\x2c\\x22\\x72\\x65\\x73\\x75\\x6c\\x74\\x22\\x3a\\x7b\\x22\\x6d\\x6f\\x64\\x65\\x6c\\x5f\\x73\\x68\\x61\\x32\\x35\\x36\\x22\\x3a\\x22\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x22\\x2c\\x22\\x61\\x6c\\x67\\x6f\\x72\\x69\\x74\\x68\\x6d\\x22\\x3a\\x22\\x66\\x69\\x78\\x74\\x75\\x72\\x65\\x2f\\x31"..., 144) = 144'
        received = '1000.020 read(7<\\x70\\x69\\x70\\x65\\x3a\\x5b\\x36\\x30\\x5d>, "\\x7b\\x22\\x69\\x64\\x22\\x3a\\x22\\x77\\x72\\x6f\\x6e\\x67\\x22\\x2c\\x22\\x72\\x65\\x73\\x75\\x6c\\x74\\x22\\x3a\\x7b\\x22\\x6d\\x6f\\x64\\x65\\x6c\\x5f\\x73\\x68\\x61\\x32\\x35\\x36\\x22\\x3a\\x22\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x61\\x22\\x2c\\x22\\x61\\x6c\\x67\\x6f\\x72\\x69\\x74\\x68\\x6d\\x22\\x3a\\x22\\x66\\x69\\x78\\x74\\x75\\x72\\x65\\x2f\\x31"..., 679937) = 144'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            (root/'trace.10').write_text(worker+'\n1000.040 +++ killed by SIGKILL +++\n')
            (root/'trace.7').write_text('')
            (root/'trace.8').write_text(received+'\n')
            actual=processes.process_observation(root,'stream-wrong-id')
            prefix=actual['disposition']['stdout_prefix']['prefixes'][0]['hex']
            self.assertTrue(prefix.startswith('7b226964223a2277726f6e6722'))
            self.assertEqual(len(prefix),128)

    def test_unfinished_write_cannot_hide_bytes_read_from_known_stdout_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            (root/'trace.10').write_text('1000.010 write(1<pipe:[60]>, "\\x7b\\x22", 65537 <unfinished ...>\n1000.040 +++ killed by SIGKILL +++\n')
            actual=processes.process_observation(root,'stream-wrong-id')
            self.assertEqual(actual['disposition']['stdout_prefix']['prefixes'],[dict(pid=10,hex='7b226964223a227822')])

    def test_stdout_write_without_a_retained_read_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            for name in ('trace.7','trace.8'):(root/name).write_text('')
            actual=processes.process_observation(root,'stream-wrong-id')
            self.assertEqual(actual['disposition']['stdout_prefix']['state'],'partial')
            self.assertIsNone(actual['disposition']['stdout_prefix']['prefixes'][0]['hex'])

    def test_trace_requalification_cannot_change_the_captured_actor_or_scope(self):
        value=reference.inventory();ledger=reference.failure_inventory(value)
        report=dict(schema='cairn.semantic-process-refusals/1',ledger_sha256='old',
            sources={name:ledger['source_sha256'][name] for name in processes.ACTOR_SOURCES},tool_sha256={},
            driver_sha256='',fixture_sha256=reference.digest(reference.PROJECT/'scripts/semantic_refusal_fixture.py'),
            go_source_sha256=reference.digest(reference.PROJECT/'scripts/testdata/semantic-refusal-driver/main.go'),
            project_path='',cases=[dict(id=name) for name in processes.PROBES],failed=[],
            model_execution=False,receipts_sha256={})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'old-report.json';reference.save(path,report)
            self.assertEqual(len(processes.prior_report(value,path,reference.digest(path))['cases']),44)
            for change in [dict(model_execution=True),dict(go_source_sha256='different'),dict(accepted=True),
                           dict(cases=report['cases'][:-1])]:
                reference.save(path,dict(report,**change))
                with self.assertRaises(ValueError):processes.prior_report(value,path,reference.digest(path))

    def test_unjoined_process_and_missing_go_result_field_refuse(self):
        for mutation,message in [('exit','exit/kill'),('result','result/error')]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.fixture(root)
                if mutation=='exit':(root/'trace.10').write_text('1000.010 execve("fixture", [], []) = 0\n')
                else:
                    value=reference.load(root/'driver.stdout');del value['calls'][0]['result']
                    reference.save(root/'driver.stdout',value)
                with self.assertRaisesRegex(ValueError,message):processes.process_observation(root,'stream-wrong-id')

    def test_invalid_pid_or_group_cannot_trigger_process_effects(self):
        for mutation in [dict(pid=1),dict(pid=0),dict(pid=-1),dict(pgrp=1)]:
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);self.fixture(root)
                lines=(root/'events.jsonl').read_text().splitlines()
                value=json.loads(lines[0]);value.update(mutation)
                (root/'events.jsonl').write_text(json.dumps(value)+'\n'+lines[1]+'\n')
                with patch('os.kill',side_effect=AssertionError('decoder signalled a process')), \
                     patch('os.killpg',side_effect=AssertionError('decoder signalled a group')), \
                     patch('subprocess.Popen',side_effect=AssertionError('decoder launched a process')):
                    with self.assertRaises(ValueError):processes.process_observation(root,'stream-wrong-id')

    def test_model_or_acceptance_claim_cannot_be_added_to_process_report(self):
        value=reference.inventory()
        report=dict(schema='cairn.semantic-process-refusals/1',ledger_sha256=value['failure_ledger']['sha256'],
            sources={},tool_sha256={},driver_sha256='',fixture_sha256='',go_source_sha256='',
            project_path='',cases=[],failed=[],model_execution=False,receipts_sha256={})
        for change in [dict(model_execution=True),dict(accepted=True),dict(scope='actual-model')]:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json';reference.save(path,dict(report,**change))
                with self.assertRaisesRegex(ValueError,'unsupported process'):
                    processes.verify(value,path,reference.digest(path))


if __name__=='__main__':
    unittest.main()
