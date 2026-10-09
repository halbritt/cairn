"""Actual owned composition waits; actor/T/H declarations remain constructed."""
import ast
import dataclasses
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from selected_native.retention import Intent, encode
from trial_external_supervision import fixture_service_delegation,sandbox
from trial_owned_workers import AttemptDomain,OwnedWorker
from trial_worker_transport import WorkerTransport
from trial_coding_protocol import coding_packet
from trial_worker_protocol import encoded
import test_trial_coding_sink as sink_fixture
from test_trial_coding_stream import composer
from test_trial_attempt_actions import SESSION


SCRIPTS=Path(__file__).resolve().parent


def sources():
    """Complete local Python import closure, exclusively readonly in fixture."""
    done=set()
    def visit(path):
        if path in done or not path.is_file():return
        done.add(path)
        for node in ast.walk(ast.parse(path.read_bytes())):
            names=[]
            if isinstance(node,ast.Import):names=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom) and not node.level and node.module:names=[node.module]
            elif isinstance(node,ast.ImportFrom) and node.level and node.module:
                visit(path.parent.joinpath(*node.module.split('.')).with_suffix('.py'))
            for name in names:
                candidate=SCRIPTS.joinpath(*name.split('.'))
                visit(candidate.with_suffix('.py'))
                visit(candidate/'__init__.py')
                if len(name.split('.'))>1:visit(SCRIPTS/name.split('.')[0]/'__init__.py')
    worker=SCRIPTS/'supervision_fixture/coding_sink_worker.py'
    visit(worker)
    return [(path,'worker.py' if path==worker else str(path.relative_to(SCRIPTS))) for path in sorted(done)]


class OwnedSinkTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        self.output=root/'output';self.output.mkdir(mode=0o700)
        source_root=root/'readonly-sources';source_root.mkdir(mode=0o700)
        self.sources=[];manifest={}
        for path,target in sources():
            data=path.read_bytes();copy=source_root/target
            copy.parent.mkdir(parents=True,exist_ok=True)
            copy.write_bytes(data);copy.chmod(0o444)
            self.sources.append((copy,target));manifest[target]=hashlib.sha256(data).hexdigest()
        self.domain=AttemptDomain(fixture_service_delegation())
        self.addCleanup(self.cleanup)
        self.source=hashlib.sha256(encoded(manifest)).hexdigest()
        self.source_manifest_sha256=self.source
        intent=Intent(SESSION,'a'*64,'b'*64,'c'*64,self.source)
        body=dict(schema='cairn.selected-retention-intent/1',**dataclasses.asdict(intent),
                  admission_basis='caller_pinned_not_verified',launch_performed_by_sink=False)
        self.intent_sha256=hashlib.sha256(encode(body)).hexdigest()
        self.transport=WorkerTransport(self.domain,SESSION,self.source,self.intent_sha256)
        self.deadline=time.monotonic()+4
        # Only constructs the frozen test native/T/H bytes, not a parent writer.
        self.sink=type('FixtureInput',(),dict(composer=composer()))()

    def cleanup(self):
        result=self.domain.close(time.monotonic()+1)
        self.assertTrue(result['released'],result)
        self.assertFalse(result['errors'])
        self.transport.forget_transient()

    def pump(self,predicate):
        while not predicate() and time.monotonic()<self.deadline:self.transport.step()
        self.assertTrue(predicate(),'owned coding sink condition missing')

    def start(self,mode='normal'):
        child=OwnedWorker(self.domain.leaf('composition'),sandbox(self.sources,[(self.output,'/output')],
            ['composition',mode,SESSION,self.source,self.intent_sha256]))
        self.domain.bind('composition',child)
        self.pump(lambda:self.transport.ready)
        self.assertTrue((self.output/SESSION/'INTENT.json').is_file())
        return child

    def finish(self,child,successful=False):
        data=sink_fixture.SinkTests.packets(self,successful=successful,code=0 if successful else 17)
        framed=b''.join(coding_packet(tag,payload) for tag,payload in data)
        self.assertLess(len(framed),128*1024)
        # The disposable controller sends constructed packets through the owned
        # queue. This does not exercise the real capture/timing transport.
        self.transport.queue.extend(framed);self.transport.ended=True
        self.pump(lambda:child.wait is not None and {'stdout','stderr'}<=child.eof)
        closed=self.domain.close_role('composition',self.deadline)
        self.assertTrue(closed['released'])
        self.assertFalse(closed['errors'])
        return self.transport.closed_sink()

    def test_failure_persistence_binds_actual_owned_sink_zero_wait_and_release(self):
        closed=self.finish(self.start())
        self.assertEqual(closed['wait']['raw_status'],0)
        self.assertEqual(closed['wait']['returncode'],0)
        self.assertTrue(closed['wait']['start_ticks']>0)
        self.assertTrue(closed['released'])
        self.assertIsNotNone(closed['namespace_identity'])
        saved=(self.output/SESSION/'ATTEMPT-EVIDENCE.json').read_bytes()
        self.assertEqual(closed['receipt']['manifest_sha256'],hashlib.sha256(saved).hexdigest())
        self.assertEqual(closed['receipt']['selected_bytes'],len(saved))
        self.assertEqual(json.loads(saved)['end_observation']['actor_wait']['returncode'],17)
        self.assertFalse((self.output/SESSION/'PREPARED.json').exists())

    def test_success_selected_and_observations_have_actual_closed_sink(self):
        closed=self.finish(self.start(),successful=True)
        self.assertEqual(closed['receipt']['outcome'],'success_retention')
        saved=(self.output/SESSION/'OBSERVATIONS.json').read_bytes()
        self.assertEqual(closed['receipt']['manifest_sha256'],hashlib.sha256(saved).hexdigest())
        selected=json.loads(saved)['selected_success']
        self.assertEqual(selected['selected_sha256'],
                         hashlib.sha256((self.output/SESSION/'SELECTED.json').read_bytes()).hexdigest())

    def test_closed_declaration_with_actual_nonzero_sink_exit_is_refused(self):
        child=self.start('nonzero')
        self.assertIsNone(self.finish(child))
        self.assertEqual(self.domain.closed['composition']['wait']['raw_status'],29<<8)
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],29)
        self.assertTrue((self.output/SESSION/'ATTEMPT-EVIDENCE.json').exists())

    def test_killed_sink_after_ready_has_consumed_intent_without_closed(self):
        child=self.start('hang')
        self.domain.stop_role('composition',self.deadline)
        self.pump(lambda:child.wait is not None and {'stdout','stderr'}<=child.eof)
        self.domain.close_role('composition',self.deadline)
        self.assertIsNone(self.transport.closed_sink())
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],-9)
        self.assertTrue((self.output/SESSION/'INTENT.json').exists())
        self.assertFalse((self.output/SESSION/'ATTEMPT-EVIDENCE.json').exists())
