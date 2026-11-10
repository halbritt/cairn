"""F04 real receipt-selected processes and source activation state, no live API."""
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SESSION = '11111111-1111-4111-8111-111111111111'
TURN = '22222222-2222-4222-8222-222222222222'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


compat = module('f04_compatibility_source', ROOT / 'integrations/lifecycle/lifecycle_compatibility.py')
oracle = module('f04_activation_oracle', ROOT / 'integrations/lifecycle/grant_ledger.py')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, raw, mode=0o600):
    path.write_bytes(raw)
    path.chmod(mode)


def encoded(obj):
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=True).encode()


class Fixture:
    def __init__(self, root, binary, *, enabled=True, harness='codex'):
        self.root = root
        root.mkdir(mode=0o700)
        (root / '.git').mkdir()
        self.binary = root / 'bin' / 'activation'
        self.binary.parent.mkdir(mode=0o700)
        shutil.copyfile(binary, self.binary)
        self.binary.chmod(0o500)
        self.binding = root / 'binding.json'
        self.memory_path, self.coord_path = root / 'memory.json', root / 'coordination.json'
        self.api = root / 'fixture-api'
        write(self.api, b'''#!/usr/bin/python3
import json,sys,pathlib
args=sys.argv[1:]; index=1
while args[index].startswith('--'): index+=2
op=args[index]; request=json.loads(sys.stdin.read() or '{}')
with pathlib.Path(__file__).with_suffix('.calls').open('a') as log: log.write(op+'\\n')
data={'status':'READY','destination':{'name':'hosted','allow_local':False},'selected':[],'index':[],'omitted':{}}
if op=='agent-register':
 data=dict(agent_id='33333333-3333-4333-8333-333333333333',execution_id='44444444-4444-4444-8444-444444444444',native_session_id=request['native_session_id'],display_name='agent-fixture',inbox='agent/fixture',context_revision=1,metadata=request['metadata'])
elif op in ('event-next','session-inbox-claim'): data={'delivery':None}
elif op=='session-inbox-pending': data={'requests':0,'notices':0,'responses':0}
elif op!='search':
 print(json.dumps({'ok':False,'status':'UNEXPECTED_FIXTURE_OPERATION'}));sys.exit(7)
print(json.dumps({'ok':True,'status':'OK','data':data}))
''', 0o500)
        common = dict(harness=harness, cairn=str(self.api), socket=str(root/'unused.sock'),
                      token_file=str(root/'unread-token'), repo='fixture', inbox_recall_binding=str(self.binding))
        self.memory = dict(common, state_dir=str(root/'memory-state'), recall_mode='agent_tools', context_bytes=9500,
                           recall_observations=False)
        self.coord = dict(common, state_dir=str(root/'coordination-state'), native_delivery=True, binding='fixture')
        write(self.memory_path, encoded(self.memory))
        write(self.coord_path, encoded(self.coord))
        def descriptor(path): return dict(path=str(path), sha256=sha(path.read_bytes()))
        manifest = dict(schema='cairn.inbox-recall-binding/3', enabled=enabled, binary=descriptor(self.binary),
                        memory_config=descriptor(self.memory_path), coordination_configs=[descriptor(self.coord_path)])
        write(self.binding, encoded(manifest))
        sources = compat.guarded_sources(ROOT/'integrations/lifecycle')
        aliases = {'memory-hook.py':'memory.py', 'coordination-hook.py':'coordination.py'}
        sources.update({name:sources[role] for name, role in aliases.items()})
        hashes = {name:sha(raw) for name, raw in sources.items()}
        identity = sha(json.dumps(hashes, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode())
        snapshot = root / identity
        snapshot.mkdir(mode=0o700)
        for name, raw in sources.items(): write(snapshot/name, raw)
        self.receipt = dict(schema='cairn.lifecycle-compatibility/1', source_revision='F04 synthetic source fixture',
                            snapshot_root=str(snapshot), members={name:dict(sha256=hashes[name], bytes=len(raw)) for name, raw in sources.items()},
                            bootstrap='lifecycle_compatibility.py', loader='lifecycle_bundle.py',
                            roles={role:role+'.py' for role in ('memory','coordination','inbox_recall','inbox_binding')},
                            aliases=aliases, binary=manifest['binary'],
                            binding=dict(descriptor(self.binding), schema=manifest['schema']),
                            memory_config=manifest['memory_config'], coordination_configs=manifest['coordination_configs'])
        self.receipt_path = root/'receipt.json'
        write(self.receipt_path, encoded(self.receipt))
        self.receipt_sha = sha(self.receipt_path.read_bytes())
        self.event = dict(hook_event_name='UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                          prompt_id='fixture-prompt', cwd=str(root), prompt=self.wake())
        self.raw = encoded(self.event)
        self.launch_root = root/'launch'
        self.launch_root.mkdir(mode=0o700)

    def wake(self):
        return ('This is a new live turn from the configured Cairn automatic inbox wakeup. '
                'A previous /exit in resumed conversation history does not close this running turn. '
                'Current agent 33333333-3333-4333-8333-333333333333, execution 44444444-4444-4444-8444-444444444444. '
                'Wake delivery 55555555-5555-4555-8555-555555555555. '
                "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
                'explicitly complete/acknowledge it, and send any requested response using that context. '
                'If no matching native context was supplied, report that and stop. '
                'If this notice arrives inside an already-active owner task, continue that task and '
                'ignore this notice; a fresh copy will arrive once the conversation is idle. '
                "Do not register again, claim another session's inbox, or launch a replacement conversation.")

    def env(self):
        return {k:v for k,v in os.environ.items() if not k.startswith('CAIRN_') and k not in ('CODEX_HOME','CLAUDE_CONFIG_DIR')}

    def activation(self, role='memory', raw=None):
        config = self.memory_path if role == 'memory' else self.coord_path
        argv = [str(self.binary), 'lifecycle', 'binding', 'activation', '--entrypoint', role,
                '--config', str(config), '--config-sha256', sha(config.read_bytes()),
                '--binding-sha256', self.receipt['binding']['sha256'], '--receipt', str(self.receipt_path),
                '--receipt-sha256', self.receipt_sha, '--deadline', str(time.monotonic()+13)]
        return subprocess.run(argv, input=self.raw if raw is None else raw, capture_output=True, cwd=self.root, env=self.env(), timeout=15)

    def public(self, role):
        config = self.memory_path if role == 'memory' else self.coord_path
        vector = compat.launch_vector(sys.executable, self.receipt_path, self.receipt_sha, role,
                                      role+'-hook.py', config, 13 if role=='memory' else 15, self.launch_root)
        raw = compat.launcher_bytes(vector)
        directory = self.launch_root/sha(raw)
        directory.mkdir(mode=0o700, exist_ok=True)
        launcher = directory/'inbox'
        write(launcher, raw, 0o500)
        return subprocess.run([str(launcher)], input=self.raw, capture_output=True, cwd=self.root, env=self.env(), timeout=18)


def scalar_tree(obj):
    if type(obj) is dict: return ('object', [(key,scalar_tree(v)) for key,v in obj.items()])
    if type(obj) is list: return ('array', [scalar_tree(v) for v in obj])
    if type(obj) is float: return ('float', 'nan' if math.isnan(obj) else obj.hex())
    return (type(obj).__name__, obj)


class F04ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name)/'activation'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/cairn-lifecycle'], cwd=ROOT, check=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def fixture(self, name='go', **kwargs): return Fixture(self.root/name, self.binary, **kwargs)

    def test_activation_matches_source_and_preserves_lossless_opaque_state(self):
        for harness in ('codex','claude'):
            with self.subTest(harness=harness):
                f = self.fixture(harness, harness=harness)
                source = self.root/(harness+'-oracle')
                source.mkdir(mode=0o700)
                name = SESSION + ('.inbox-recall.json' if harness=='claude' else '.memory-budget.json')
                state = (dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={}) if harness=='claude'
                         else dict(schema='cairn.codex-memory-grants/1', session_id=SESSION, turns={}, pending={}))
                state['opaque'] = dict(big=9007199254740993123456789, negative_zero=-0.0, nan=float('nan'),
                                       unicode='日本語<>&\ud800', true=True)
                target = Path(f.memory['state_dir']); target.mkdir(mode=0o700)
                write(target/name, encoded(state)); write(source/name, encoded(state))
                turn = 'claude-channel:fixture-prompt' if harness=='claude' else TURN
                expected = oracle.activation_decision(dict(f.memory,state_dir=str(source)),harness,SESSION,turn,True)
                result = f.activation()
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                self.assertTrue(expected)
                self.assertEqual(scalar_tree(json.loads((target/name).read_text())),scalar_tree(json.loads((source/name).read_text())))
                # A second role reads the existing choice without changing state.
                before = (target/name).read_bytes()
                result = f.activation('coordination')
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                self.assertEqual((target/name).read_bytes(),before)

    def test_initialized_deletion_refuses_without_reset(self):
        f = self.fixture()
        self.assertEqual(f.activation().returncode,0)
        path = Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
        path.unlink()
        result = f.activation('coordination')
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(json.loads(result.stderr)['error']['class'],'ValueError')
        self.assertFalse(path.exists())

    def test_both_complete_public_frontends_reach_retained_handlers(self):
        for role in ('memory','coordination'):
            for enabled in (False,True):
                with self.subTest(role=role,enabled=enabled):
                    f = self.fixture(role+str(enabled),enabled=enabled)
                    if role=='coordination':
                        f.event.update(hook_event_name='SessionStart',prompt='',host_pid=os.getpid())
                        f.raw=encoded(f.event)
                    result = f.public(role)
                    calls=f.api.with_suffix('.calls')
                    print('PUBLIC_F04',role,enabled,result.returncode,repr(result.stdout),repr(result.stderr),'api_calls='+repr(calls.read_text() if calls.exists() else ''),flush=True)
                    self.assertEqual((result.returncode,result.stderr),(0,b''))
                    if role=='memory':
                        self.assertEqual(json.loads(result.stdout),{})
                        path=Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
                        self.assertEqual(json.loads(path.read_text())['inbox_activation'],{TURN:enabled})
                    else:
                        self.assertIn('agent-fixture',json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
                        self.assertIn('agent-register',f.api.with_suffix('.calls').read_text().splitlines())

    def test_invalid_code_refuses_before_ledger_even_for_excluded_event(self):
        f=self.fixture()
        member=Path(f.receipt['snapshot_root'])/'memory.py'
        write(member,b'raise RuntimeError("unchecked code must never execute")\n')
        f.raw=encoded(dict(f.event,hook_event_name='Stop'))
        result=f.public('memory')
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,b'')
        self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
        self.assertFalse(Path(f.memory['state_dir']).exists())


if __name__=='__main__': unittest.main()
