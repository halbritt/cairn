"""Real checked /3 process versus the retained /2 inbox identity guards."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from test_f04_activation import Fixture, ROOT, SESSION, TURN, encoded, module, sha, write


AGENT = '33333333-3333-4333-8333-333333333333'
EXECUTION = '44444444-4444-4444-8444-444444444444'
DELIVERY = '55555555-5555-4555-8555-555555555555'
ATTEMPT = '66666666-6666-4666-8666-666666666666'

SOURCE = '''import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('source_inbox',sys.argv[1])
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
config=json.loads(Path(sys.argv[2]).read_text());frame=json.load(sys.stdin)
def no_renew(attempt): raise AssertionError('identity guard reached lease renewal')
try:
 result=bridge.output(config,frame['event'],frame['observation'],frame['state'],frame['control'],
                      revalidate=no_renew,outer_deadline=time.monotonic()+12,reports=[])
 response=dict(ok=True,message=result)
except (ValueError,KeyError,TypeError,OSError) as exc:
 response=dict(ok=False,error=dict(**{'class':type(exc).__name__},message=str(exc),
                         cairn_inbox_refusal=getattr(exc,'cairn_inbox_refusal',{})))
print(json.dumps(response,ensure_ascii=False,separators=(',',':')))
'''.replace('import importlib.util,json,sys', 'import importlib.util,json,sys,time')


class InboxIdentityProcesses(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / 'lifecycle'
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(cls.binary), './cmd/cairn-lifecycle'],
                       cwd=ROOT, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_actual_attempt_owner_and_session_guards_match_source_without_effects(self):
        bundle = module('inbox_identity_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
        for harness in ('codex', 'claude'):
            for fault in ('owner', 'session'):
                with self.subTest(harness=harness, fault=fault), tempfile.TemporaryDirectory() as directory:
                    f = Fixture(Path(directory) / 'project', self.binary, harness=harness)
                    native = TURN if harness == 'codex' else 'claude-channel:fixture-prompt'
                    if harness == 'claude':
                        f.event['prompt'] = ('<channel source="cairn-events" native_session_id="' + SESSION +
                            '" agent_id="' + AGENT + '" execution_id="' + EXECUTION +
                            '" delivery_id="' + DELIVERY + '">\n' + f.wake() + '\n</channel>')
                    f.raw = encoded(f.event)
                    activated = f.activation('coordination')
                    self.assertEqual((activated.returncode, activated.stdout, activated.stderr),
                                     (0, b'{"enabled":true}\n', b''))
                    session = dict(agent_id=AGENT, execution_id=EXECUTION)
                    attempt = dict(attempt_id=ATTEMPT, session=session, native_turn_id=native,
                                   delivery=dict(delivery_id=DELIVERY, lease_id=ATTEMPT,
                                                 event=dict(event_id=TURN, kind='notice',
                                                            ref=dict(record_id=AGENT, version=1))))
                    if fault == 'owner':
                        attempt['native_turn_id'] = 'different-owner'
                    else:
                        attempt['session'] = dict(session, execution_id=ATTEMPT)
                    state = dict(agent=session, workspace=str(f.root), inbox_attempt=attempt)
                    frame = dict(event=f.event, observation=dict(event='UserPromptSubmit', native_turn_id=native),
                                 state=state, control='Cairn fixture control', started_monotonic=time.monotonic())
                    before = {p.name: p.read_bytes() for p in Path(f.memory['state_dir']).iterdir()}
                    runtime = f.root / 'legacy-runtime'
                    bundle.stage_runtime(ROOT / 'integrations/lifecycle', runtime)
                    manifest_bytes = f.binding.read_bytes()
                    legacy = json.loads(manifest_bytes)
                    legacy['schema'] = 'cairn.inbox-recall-binding/2'
                    for key, name in (('engine', 'memory.py'), ('bridge', 'inbox_recall.py')):
                        path = runtime / name
                        legacy[key] = dict(path=str(path), sha256=sha(path.read_bytes()),
                                           bundle=bundle.bundle_descriptor(runtime))
                    write(f.binding, encoded(legacy))
                    try:
                        source = subprocess.run([sys.executable, '-B', '-c', SOURCE,
                            str(ROOT / 'integrations/lifecycle/inbox_recall.py'), str(f.coord_path)],
                            input=encoded(frame), capture_output=True, cwd=f.root, env=f.env(), timeout=18)
                    finally:
                        write(f.binding, manifest_bytes)
                    argv = f.activation_argv('coordination')
                    argv[1:4] = ['lifecycle', 'inbox', 'recall']
                    candidate = subprocess.run(argv, input=encoded(frame), capture_output=True,
                                               cwd=f.root, env=f.env(), timeout=18)
                    self.assertEqual((candidate.returncode, candidate.stdout, candidate.stderr),
                                     (source.returncode, source.stdout, source.stderr))
                    result = json.loads(candidate.stdout)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['error']['cairn_inbox_refusal'],
                                     dict(refusal_stage='identity', refusal_reason='attempt_' + fault))
                    self.assertEqual({p.name: p.read_bytes() for p in Path(f.memory['state_dir']).iterdir()}, before)
                    self.assertEqual(f.api_calls(), [])


if __name__ == '__main__':
    unittest.main()
