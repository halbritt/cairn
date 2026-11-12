"""Disposable two-binding watcher cutover; no production or model accounts.

The real R02 producer supplies /3 receipts and launchers. The retained /2
bundle and compiled Go processes use one bindings directory and shared state.
These checks exercise receipt invalidation with modeled API facts. They do not
qualify the actual server's authentication or the complete release unit census.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from test_f04_activation import ROOT, SESSION, TURN, encoded, module, sha, write
from test_r02_compatibility_install import InstalledFixture


BOUNDARIES = (
    'watch-lock-sync', 'write:one.json', 'file-sync:one.json',
    'write:two.json', 'file-sync:two.json', 'prepared-sync', 'active-sync',
    'parent-preparation-sync', 'exchange', 'parent-publication-sync',
)


class TwoBindingFixture(InstalledFixture):
    def configuration_plan(self, **kwargs):
        self.coord['config_home'] = str(self.root / 'profiles' / 'one')
        plan = super().configuration_plan(**kwargs)
        first = Path(plan['coordination_configs'][0]['path'])
        second = first.with_name('coordination-1.json')
        write(second, encoded(dict(self.coord, binding='fixture-two', config_home=str(self.root / 'profiles' / 'two'))))
        plan['coordination_configs'].append(dict(path=str(second), sha256=sha(second.read_bytes())))
        return plan

    def launcher(self, role, timeout=None):
        budget = timeout if timeout is not None else (13 if role == 'memory' else 15)
        index = '0' if role == 'memory' else '1'
        return Path(self.installed['launchers'][role + ':' + index + ':' + str(budget)][0])


class R06SharedUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = os.environ.get('CAIRN_R06_EVIDENCE_DIR')
        if evidence: Path(evidence).mkdir(mode=0o700, parents=True, exist_ok=True)
        cls.build = None if evidence else tempfile.TemporaryDirectory(prefix='cairn-r06-build-')
        tools = Path(evidence) / 'tools' if evidence else Path(cls.build.name)
        tools.mkdir(mode=0o700, parents=True, exist_ok=True)
        cls.native_tools = {}
        for name, path in (('go', Path(shutil.which('go')).resolve()), ('python', Path(sys.executable).resolve()),
                           ('api_python', Path('/usr/bin/python3').resolve()), ('shell', Path('/bin/sh').resolve())):
            cls.native_tools[name] = dict(path=str(path), sha256=sha(path.read_bytes()))
        go = cls.native_tools['go']['path']
        toolchain = json.loads(subprocess.run([go, 'env', '-json', 'GOROOT', 'GOTOOLDIR', 'GOVERSION', 'GOTOOLCHAIN'],
                               capture_output=True, check=True, text=True).stdout)
        compiler_pins = {str(path): sha(path.read_bytes()) for path in sorted(Path(toolchain['GOTOOLDIR']).iterdir()) if path.is_file()}
        versions = dict(python=sys.version, go=toolchain, native=cls.native_tools, compiler_tools=compiler_pins,
                        go_mod=sha((ROOT / 'go.mod').read_bytes()), go_sum=sha((ROOT / 'go.sum').read_bytes()))
        write(tools / 'versions.json', encoded(versions))
        cls.images = {}
        for name, target in (('stager', './cmd/cairn-stage-lifecycle'),
                             ('lifecycle', './cmd/cairn-lifecycle')):
            output = tools / name
            subprocess.run([go, 'build', '-buildvcs=false', '-o', str(output), target], cwd=ROOT, check=True)
            output.chmod(0o500)
            cls.images[name] = output
        output = tools / 'watch-exchange-tests'
        subprocess.run([go, 'test', '-c', '-o', str(output), './internal/lifecycle/coordination'], cwd=ROOT, check=True)
        output.chmod(0o500)
        cls.images['exchange'] = output
        cls.image_pins = {name: sha(path.read_bytes()) for name, path in cls.images.items()}
        versions['images'] = cls.image_pins
        write(tools / 'versions.json', encoded(versions))

    @classmethod
    def tearDownClass(cls):
        if cls.build is not None: cls.build.cleanup()

    def setUp(self):
        evidence = os.environ.get('CAIRN_R06_EVIDENCE_DIR')
        if evidence:
            Path(evidence).mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=evidence))
        self.evidence = bool(evidence)
        self.children = []
        self.callbacks = []
        self.sequence = 0
        self.addCleanup(self.cleanup)
        self.pins = dict(self.image_pins)
        self.pins.update(self.native_tools)
        write(self.root / 'executables.json', encoded(self.pins))

    def cleanup(self):
        for child in self.children:
            if child.poll() is None:
                self.stop(child)
        if self.evidence or any((test is self or getattr(test, 'test_case', None) is self) and error
                               for test, error in self._outcome.result.failures + self._outcome.result.errors):
            print('R06 retained evidence:', self.root, flush=True)
        else:
            shutil.rmtree(self.root)

    def run_process(self, argv, *, body=None, env=None, timeout=40):
        self.sequence += 1
        prefix = self.root / ('command-' + str(self.sequence))
        # Record only selected fixture inputs. Never dump the host environment.
        write(prefix.with_suffix('.argv.json'), encoded(argv))
        result = subprocess.run(argv, input=body, capture_output=True, cwd=self.root,
                                env=os.environ.copy() if env is None else env, timeout=timeout)
        write(prefix.with_suffix('.stdout'), result.stdout)
        write(prefix.with_suffix('.stderr'), result.stderr)
        write(prefix.with_suffix('.exit'), str(result.returncode).encode())
        return result

    def prepare(self):
        f = TwoBindingFixture(self.root / 'installed', self.images['lifecycle'], stager=self.images['stager'], enabled=False)
        self.f = f
        api = b'''#!/usr/bin/python3
import json,pathlib,sys,uuid
args=sys.argv[1:]
if args[0]=='agent':
 index=1
 while args[index].startswith('--'): index+=2
 operation=args[index]
else: operation=args[0]
body=sys.stdin.read(); request=json.loads(body or '{}') if args[0]=='agent' else {}
root=pathlib.Path(__file__).parent
views_path=root/'fixture-views.json'
views=json.loads(views_path.read_text()) if views_path.exists() else {}
with (root/'fixture-api.jsonl').open('a') as stream: stream.write(json.dumps({'operation':operation,'argv':args,'input':body})+'\\n')
if views.get('unavailable')==operation:
 print(json.dumps({'ok':False,'status':'API_UNAVAILABLE'}));sys.exit(7)
if operation=='agent-heartbeat': data=json.loads((root/'fixture-agents.json').read_text())[request['agent_id']]
elif operation=='complete': data={'state':'handled','result':{'record_id':str(uuid.uuid5(uuid.NAMESPACE_URL,args[-1])),'version':1}}
elif operation=='ack': data={'state':'handled','delivery_id':args[-1]}
elif operation=='publish': data={'event_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'event:'+args[-1])),'ref':{'version':int(args[-2]),'record_id':args[-1]}}
elif operation=='session-delivery-observe': data={}
elif operation=='wake-attempts': data={'attempts':views.get('wakes',[]),'more':False}
elif operation=='event-watch': data={'deliveries':views.get('deliveries',[]),'cursor':'r06-fixture-cursor','more':False}
elif operation=='session-inbox-control': data={'attempt':views.get('attempt')}
elif operation=='session-inbox-attempt-status':
 agents=json.loads((root/'fixture-agents.json').read_text())
 current=agents.get(request['session']['agent_id'])
 if current is None or current['execution_id']!=request['session']['execution_id']:
  print(json.dumps({'ok':False,'status':'STALE_SESSION'}));sys.exit(7)
 data=views.get('history',{}).get(request['attempt_id'])
 if data is None:
  print(json.dumps({'ok':False,'status':'NOT_FOUND'}));sys.exit(7)
elif operation in ('session-inbox-claim','event-next','wake-claim'):
 delivery={'delivery_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'r06-injected-delivery')),'lease_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'r06-injected-lease')),'state':'leased','event':{'event_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'r06-injected-event')),'kind':'notice'}}
 views['deliveries']=[delivery]
 if operation=='session-inbox-claim':
  data={'attempt':{'attempt_id':request['request_id'],'session':request['session'],'delivery':delivery}}
  views['attempt']=data['attempt']
 elif operation=='wake-claim':
  data={'attempt':{'attempt_id':request['request_id'],'state':'claimed','delivery':delivery}}
  views['wakes']=[data['attempt']]
 else: data={'delivery':delivery}
 views_path.write_text(json.dumps(views))
else:
 print(json.dumps({'ok':False,'status':'UNEXPECTED_FIXTURE_OPERATION'}));sys.exit(7)
print(json.dumps({'schema':'cairn.response/1','ok':True,'status':'OK','data':data}))
'''
        f.api.chmod(0o600)
        write(f.api, api, 0o500)
        self.legacy = self.root / 'legacy'
        bundle = module('r06_legacy_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
        bundle.stage_runtime(ROOT / 'integrations/lifecycle', self.legacy)
        binding = self.legacy / 'binding.json'
        memory = dict(f.memory, inbox_recall_binding=str(binding))
        write(self.legacy / 'memory.json', encoded(memory))
        self.old_paths, self.new_paths = [], []
        descriptors = []
        for index, name in enumerate(('one.json', 'two.json')):
            coord = dict(f.coord, binding='fixture' if index == 0 else 'fixture-two', inbox_recall_binding=str(binding),
                         config_home=str(f.root / 'profiles' / ('one' if index == 0 else 'two')))
            path = self.legacy / ('coordination-' + str(index) + '.json')
            write(path, encoded(coord))
            self.old_paths.append(path)
            descriptors.append(dict(path=str(path), sha256=sha(path.read_bytes())))
            self.new_paths.append(Path(f.receipt['coordination_configs'][index]['path']))
        descriptor = lambda path: dict(path=str(path), sha256=sha(path.read_bytes()))
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False,
                        memory_config=descriptor(self.legacy / 'memory.json'), coordination_configs=descriptors)
        for role, name in (('engine', 'memory.py'), ('bridge', 'inbox_recall.py')):
            manifest[role] = dict(descriptor(self.legacy / name), bundle=bundle.bundle_descriptor(self.legacy))
        write(binding, encoded(manifest))
        self.legacy_module = module('r06_retained_coordination', self.legacy / 'coordination.py')
        self.active = self.root / 'bindings'
        self.active.mkdir(mode=0o700)
        self.old_pins = {}
        for name, path in zip(('one.json', 'two.json'), self.old_paths):
            write(self.active / name, path.read_bytes())
            self.old_pins[name] = sha(path.read_bytes())
        self.new_pins = {name: sha(path.read_bytes()) for name, path in zip(('one.json', 'two.json'), self.new_paths)}
        self.shared_state = Path(f.coord['state_dir'])
        self.shared_state.mkdir(mode=0o700)
        (self.shared_state / 'intents').mkdir(mode=0o700)
        self.sessions, agents = [], {}
        for index, binding_name in enumerate(('fixture', 'fixture-two')):
            native = str(uuid.uuid5(uuid.NAMESPACE_URL, 'r06-native-' + str(index)))
            agent = dict(agent_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'r06-agent-' + str(index))),
                         execution_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'r06-exec-' + str(index))),
                         native_session_id=native, display_name='r06-synthetic-' + str(index),
                         metadata=dict(state='idle', delivery_mode='existing-session', workspace=str(self.root)))
            path = self.shared_state / (sha((binding_name + '\0' + native).encode()) + '.json')
            state = dict(schema='cairn.native-session/1', native_session_id=native,
                         process=self.legacy_module.process_reference(os.getpid()), agent=agent,
                         registration=dict(binding=binding_name, native_session_id=native), inbox_journals=[])
            write(path, encoded(state))
            self.sessions.append(path)
            agents[agent['agent_id']] = agent
        write(f.root / 'fixture-agents.json', encoded(agents))
        ledger = self.root / 'shared-ledger'
        ledger.mkdir(mode=0o700)
        self.ledger = ledger / 'retained.memory-budget.json'
        write(self.ledger, b'{"opaque":[9007199254740993123456789,"retained"],"inbox_activation":{"old-turn":true}}\n')
        self.ledger_bytes = self.ledger.read_bytes()
        self.legacy_pins = {str(path): sha(path.read_bytes()) for path in self.legacy.iterdir() if path.is_file()}
        write(self.root / 'selected-pins.json', encoded(dict(legacy=self.legacy_pins, receipt=f.receipt,
              receipt_sha256=f.receipt_sha, old_configs=self.old_pins, new_configs=self.new_pins,
              api=dict(path=str(f.api), sha256=sha(f.api.read_bytes())))))
        return f

    def legacy_watch(self, directory=None, once=True):
        argv = [sys.executable, '-I', '-B', str(self.legacy / 'coordination.py'), 'watch', '--config-dir', str(directory or self.active)]
        return argv + (['--once'] if once else [])

    def go_command(self, tail):
        f = self.f
        selection = json.loads((Path(f.selected['path']).parent / 'selection.json').read_bytes())
        vector = selection['anchors']['coordination:1:15']['vector']
        return [str(f.binary), 'coordination', *vector[5:], '--', *tail]

    def go_watch(self, directory=None, once=True):
        return self.go_command(['watch', '--config-dir', str(directory or self.active)] + (['--once'] if once else []))

    def exchange(self, current, paths, retained, *, crash='', check=True, guard=True, inject=None, inject_input=''):
        next_files = [dict(name=name, path=str(path), sha256=sha(path.read_bytes()))
                      for name, path in zip(('one.json', 'two.json'), paths)]
        plan = self.root / ('exchange-' + str(self.sequence + 1) + '.json')
        output = plan.with_suffix('.result')
        write(plan, encoded(dict(Current=dict(Directory=str(self.active), Pins=current), Retained=str(retained),
                                Next=next_files, Crash=crash, Result=str(output), Guard=guard,
                                Callbacks=self.callbacks, Inject=inject or [], InjectInput=inject_input)))
        result = self.run_process([str(self.images['exchange']), '-test.run=^TestSharedWatchUnitProcess$'],
                                  env=dict(os.environ, CAIRN_R06_WATCH_EXCHANGE_PLAN=str(plan)))
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(output.read_bytes())['Published'])
        return result

    def add_request(self, index, label):
        path = self.sessions[index]
        state = json.loads(path.read_bytes())
        attempt = str(uuid.uuid5(uuid.NAMESPACE_URL, 'attempt:' + label))
        delivery = str(uuid.uuid5(uuid.NAMESPACE_URL, 'delivery:' + label))
        complete_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'complete:' + label))
        reply_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'reply:' + label))
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'event:' + label))
        journal = dict(schema='cairn.inbox-intent/1', attempt_id=attempt, delivery_id=delivery, kind='request',
                       session={key: state['agent'][key] for key in ('agent_id', 'execution_id')},
                       completion=dict(kind='complete', request_id=complete_id,
                           argv=[str(self.f.api), 'complete', '--request-id', complete_id, delivery],
                           input='selected synthetic result ' + label, status='pending', recorded_at=time.time()),
                       finish_reply=dict(argv=[str(self.f.api), 'publish', '--request-id', reply_id,
                            '--to', 'agent/r06-synthetic-requester', '--kind', 'response',
                            '--causation-id', event_id, '--correlation-id', event_id], request_id=reply_id))
        target = self.shared_state / 'intents' / (attempt + '.json')
        write(target, encoded(journal))
        state['inbox_journals'].append(attempt)
        write(path, encoded(state))
        return target

    def assert_request(self, path):
        journal = json.loads(path.read_bytes())
        self.assertEqual(journal['completion']['status'], 'committed')
        self.assertEqual(journal['response']['status'], 'committed')
        ref = journal['completion']['result']
        self.assertEqual(journal['response']['expected_ref'], ref)
        self.assertEqual(journal['response']['argv'][-2:], ['1', ref['record_id']])
        return journal

    def calls(self):
        path = self.f.root / 'fixture-api.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def start(self, argv):
        stdout, stderr = self.root / ('watch-' + str(len(self.children)) + '.stdout'), self.root / ('watch-' + str(len(self.children)) + '.stderr')
        with stdout.open('wb') as out, stderr.open('wb') as err:
            child = subprocess.Popen(argv, cwd=self.root, stdout=out, stderr=err, env=os.environ.copy())
        child.r06_stderr = stderr
        self.children.append(child)
        self.callbacks.append(self.legacy_module.process_reference(child.pid))
        return child

    def stop(self, child):
        if child.pid <= 1:
            raise AssertionError('invalid owned watcher PID')
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
        try:
            return child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
            raise

    def wait_for(self, predicate, child, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if child.poll() is not None:
                self.fail('watcher exited: ' + child.r06_stderr.read_text())
            time.sleep(0.02)
        self.fail('watcher did not produce the selected effect: ' + child.r06_stderr.read_text())

    def test_two_binding_cutover_reverse_and_exact_journal_references(self):
        self.prepare()
        first = [self.add_request(index, 'python-' + str(index)) for index in range(2)]
        legacy = self.start(self.legacy_watch(once=False))
        self.wait_for(lambda: all(json.loads(path.read_bytes()).get('response', {}).get('status') == 'committed' for path in first), legacy)
        blocked = self.exchange(self.old_pins, self.new_paths, self.root / 'blocked', check=False, guard=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(b'SESSION_BUSY', blocked.stdout + blocked.stderr)
        self.assertFalse((self.root / 'blocked').exists())
        self.assertEqual(self.stop(legacy), 0)
        self.assertEqual(legacy.r06_stderr.read_bytes(), b'')
        before = {str(path): path.read_bytes() for path in first}
        old_lock = (self.active / '.watch.lock').stat()
        self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old')
        self.assertEqual((old_lock.st_dev, old_lock.st_ino), ((self.active / '.watch.lock').stat().st_dev, (self.active / '.watch.lock').stat().st_ino))
        second = [self.add_request(index, 'go-' + str(index)) for index in range(2)]
        go = self.start(self.go_watch(once=False))
        self.wait_for(lambda: all(json.loads(path.read_bytes()).get('response', {}).get('status') == 'committed' for path in second), go)
        contender = self.run_process(self.legacy_watch(self.root / 'retained-old'))
        self.assertEqual(contender.returncode, 1)
        self.assertIn(b'SESSION_BUSY', contender.stderr)
        self.assertEqual(self.stop(go), 0)
        self.assertEqual(go.r06_stderr.read_bytes(), b'')
        for path in first + second:
            self.assert_request(path)
        for path in first:
            self.assertEqual(path.read_bytes(), before[str(path)])
        self.exchange(self.new_pins, self.old_paths, self.root / 'retained-new')
        third = [self.add_request(index, 'reverse-' + str(index)) for index in range(2)]
        result = self.run_process(self.legacy_watch())
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        for path in first + second + third:
            self.assert_request(path)
        mutations = [call for call in self.calls() if call['operation'] in ('complete', 'publish')]
        self.assertEqual(len(mutations), 12)
        request_ids = [call['argv'][call['argv'].index('--request-id') + 1] for call in mutations]
        self.assertEqual(len(request_ids), len(set(request_ids)))
        self.assertEqual(self.ledger.read_bytes(), self.ledger_bytes)
        self.assertEqual({str(path): sha(path.read_bytes()) for path in self.legacy.iterdir() if path.is_file()}, self.legacy_pins)

    def test_real_process_crashes_preserve_complete_active_selection(self):
        self.prepare()
        for boundary in BOUNDARIES:
            for side in ('before:', 'after:'):
                with self.subTest(boundary=boundary, side=side):
                    case = self.root / (side.replace(':', '-') + boundary.replace(':', '-'))
                    case.mkdir(mode=0o700)
                    previous_active = getattr(self, 'active', None)
                    self.active = case / 'bindings'
                    self.active.mkdir(mode=0o700)
                    old, new, paths = {}, {}, []
                    for name, old_path, new_path in zip(('one.json', 'two.json'), self.old_paths, self.new_paths):
                        write(self.active / name, old_path.read_bytes())
                        old[name], new[name] = sha(old_path.read_bytes()), sha(new_path.read_bytes())
                        paths.append(new_path)
                    journals = [self.add_request(index, side + boundary + str(index)) for index in range(2)]
                    result = self.exchange(old, paths, case / 'retained', crash=side + boundary, check=False, guard=False)
                    self.assertEqual(result.returncode, 86, result.stdout + result.stderr)
                    selected = {path.name: sha(path.read_bytes()) for path in self.active.glob('*.json')}
                    expected = new if boundary == 'parent-publication-sync' or boundary == 'exchange' and side == 'after:' else old
                    self.assertEqual(selected, expected)
                    with (self.active / '.watch.lock').open('r+b') as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    executor = self.go_watch() if expected == new else self.legacy_watch()
                    recovered = self.run_process(executor)
                    self.assertEqual((recovered.returncode, recovered.stderr), (0, b''))
                    for journal in journals:
                        self.assert_request(journal)
                    self.active = previous_active

    def test_initial_preflight_and_python_verifier_watch_refusal(self):
        f = self.prepare()
        # The receipt cannot authorize an old /2 copies directory.
        result = self.run_process(self.go_watch())
        expected = b'Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n'
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', expected))
        self.assertFalse((self.active / '.watch.lock').exists())
        self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old')
        # Python V deliberately keeps watch excluded; use its retained path.
        result = self.run_process([str(f.launcher('coordination')), 'watch', '--config-dir', str(self.active), '--once'])
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'compatibility_preflight', result.stderr)
        (self.active / 'extra.json').write_bytes(self.new_paths[0].read_bytes())
        before = self.calls()
        result = self.run_process(self.go_watch())
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, b'', expected))
        self.assertEqual(self.calls(), before)

    def test_real_watcher_rechecks_membership_config_and_image_every_cycle(self):
        for kind in ('membership', 'config', 'image'):
            with self.subTest(kind=kind):
                case = self.root / kind
                case.mkdir(mode=0o700)
                original_root = self.root
                self.root = case
                child = None
                try:
                    self.prepare()
                    self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old')
                    child = self.start(self.go_watch(once=False))
                    self.wait_for(lambda: sum(call['operation'] == 'agent-heartbeat' for call in self.calls()) >= 4, child)
                    before = len(self.calls())
                    if kind == 'membership':
                        (self.active / 'two.json').unlink()
                    elif kind == 'config':
                        path = self.new_paths[1]
                        path.chmod(0o600)
                        path.write_bytes(path.read_bytes() + b'\n')
                        path.chmod(0o400)
                    else:
                        path = self.f.binary
                        replacement = path.with_name('replacement')
                        write(replacement, path.read_bytes() + b'\n', 0o500)
                        os.replace(replacement, path)
                    self.assertEqual(child.wait(timeout=40), 1)
                    self.assertEqual(child.r06_stderr.read_bytes(), b'Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
                    self.assertEqual(len(self.calls()), before)
                finally:
                    if child is not None and child.poll() is None:
                        self.stop(child)
                    self.root = original_root

    def test_disabled_selection_keeps_effective_cached_grant_visible(self):
        f = self.prepare()
        disabled = self.run_process(f.activation_argv(), body=encoded(dict(f.event,
                 session_id=str(uuid.uuid4()), turn_id=str(uuid.uuid4()))))
        self.assertEqual((disabled.returncode, disabled.stdout, disabled.stderr), (0, b'{"enabled":false}\n', b''))
        # Establish the source's real per-turn grant, then execute disabled /3.
        # This is a D1 blocker, never evidence that disabling the file drains it.
        self.assertTrue(self.legacy_module._grant_ledger.activation_decision(f.memory, 'codex', SESSION, TURN, True))
        ledger = Path(f.memory['state_dir']) / (SESSION + '.memory-budget.json')
        before = ledger.read_bytes()
        result = self.run_process(f.activation_argv(), body=f.raw)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{"enabled":true}\n', b''))
        blocked = self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old', check=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn(b'D1', blocked.stdout + blocked.stderr)
        self.assertEqual(ledger.read_bytes(), before)

    def test_retained_issued_command_is_not_fenced_by_watcher_lock(self):
        f = self.prepare()
        self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old')
        attempt, delivery, request = (str(uuid.uuid4()) for _ in range(3))
        context = self.shared_state / 'inbox' / (attempt + '.json')
        context.parent.mkdir(mode=0o700)
        agent = json.loads(self.sessions[0].read_bytes())['agent']
        write(context, encoded(dict(schema='cairn.session-inbox/1', state_dir=str(self.shared_state),
              attempt_id=attempt, delivery_id=delivery, kind='notice', agent_id=agent['agent_id'],
              execution_id=agent['execution_id'], commands=dict(ack=[str(f.api), 'ack', '--request-id', request, delivery]),
              request_ids=dict(completion=request))))
        # The retained issued entry is required to remain executable. Its real
        # journal effect demonstrates why .watch.lock alone cannot satisfy D6.
        with (self.active / '.watch.lock').open('r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_process([sys.executable, '-I', '-B', str(self.legacy / 'coordination.py'),
                                       'inbox', '--context', str(context), 'ack'])
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        journal = json.loads((self.shared_state / 'intents' / (attempt + '.json')).read_bytes())
        self.assertEqual(journal['completion']['status'], 'committed')
        self.assertEqual(journal['completion']['request_id'], request)
        self.assertEqual([call['operation'] for call in self.calls()
                          if call['operation'] not in ('wake-attempts', 'event-watch', 'session-inbox-control')], ['ack'])

    def test_drain_receipt_invalidates_on_admission_claim_and_wake(self):
        for kind in ('admission', 'claim', 'wake', 'cached-grant', 'issued-ack'):
            with self.subTest(kind=kind):
                original_root = self.root
                self.root = original_root / kind
                self.root.mkdir(mode=0o700)
                try:
                    f = self.prepare()
                    if kind == 'admission':
                        code = '''import importlib.util,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);s=importlib.util.spec_from_file_location('r06_admission',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
c=m.load_config(pathlib.Path(sys.argv[2]));p=pathlib.Path(sys.argv[3])
with m.session_lock(p):
 state=json.loads(p.read_bytes());m.native_session(c,state,p).admit_inbox()
'''
                        inject = [sys.executable, '-I', '-B', '-c', code, str(self.legacy / 'coordination.py'),
                                  str(self.old_paths[0]), str(self.sessions[0])]
                        body = ''
                    elif kind == 'cached-grant':
                        code = '''import importlib.util,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);s=importlib.util.spec_from_file_location('r06_cached_grant',p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
c=json.loads(pathlib.Path(sys.argv[2]).read_bytes())
assert m._grant_ledger.activation_decision(c,'codex',sys.argv[3],sys.argv[4],True)
'''
                        inject = [sys.executable, '-I', '-B', '-c', code, str(self.legacy / 'coordination.py'),
                                  str(self.legacy / 'memory.json'), SESSION, TURN]
                        body = ''
                    elif kind == 'issued-ack':
                        attempt, delivery, request = (str(uuid.uuid4()) for _ in range(3))
                        context = self.shared_state / 'inbox' / (attempt + '.json')
                        context.parent.mkdir(mode=0o700)
                        agent = json.loads(self.sessions[0].read_bytes())['agent']
                        write(context, encoded(dict(schema='cairn.session-inbox/1', state_dir=str(self.shared_state),
                              attempt_id=attempt, delivery_id=delivery, kind='notice', agent_id=agent['agent_id'],
                              execution_id=agent['execution_id'], commands=dict(ack=[str(f.api), 'ack', '--request-id', request, delivery]),
                              request_ids=dict(completion=request))))
                        inject = [sys.executable, '-I', '-B', str(self.legacy / 'coordination.py'),
                                  'inbox', '--context', str(context), 'ack']
                        body = ''
                    else:
                        inject = [str(f.api), 'agent', 'event-next' if kind == 'claim' else 'wake-claim']
                        body = json.dumps(dict(repo='fixture') if kind == 'claim' else dict(request_id=str(uuid.uuid4()), repo='fixture'))
                    result = self.exchange(self.old_pins, self.new_paths, self.root / 'refused',
                                           check=False, inject=inject, inject_input=body)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'D6 drain receipt invalidated', result.stdout + result.stderr)
                    self.assertEqual({p.name: sha(p.read_bytes()) for p in self.active.glob('*.json')}, self.old_pins)
                finally:
                    self.root = original_root

    def historical_record(self):
        target = self.add_request(0, 'retained-history')
        result = self.run_process(self.legacy_watch())
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        journal = self.assert_request(target)
        state = json.loads(self.sessions[0].read_bytes())
        old_session = journal['session']
        state['agent']['execution_id'] = str(uuid.uuid4())
        current = {key: state['agent'][key] for key in ('agent_id', 'execution_id')}
        agents_path = self.f.root / 'fixture-agents.json'
        agents = json.loads(agents_path.read_bytes())
        agents[current['agent_id']] = state['agent']
        write(agents_path, encoded(agents))
        delivery = dict(delivery_id=journal['delivery_id'], state='handled', result=journal['completion']['result'])
        historical = dict(attempt_id=journal['attempt_id'], session=old_session,
                          finished_at='2026-11-12T19:00:00Z', reason='delivery_completed', delivery=delivery)
        state['inbox_attempt'] = dict(historical, delivery=dict(delivery, state='leased', lease_id=str(uuid.uuid4())))
        state['inbox_intent'] = dict(request_id=journal['attempt_id'], session=old_session)
        state['inbox_close'] = dict(request_id=str(uuid.uuid4()), session=old_session,
                                    attempt_id=journal['attempt_id'], reason='delivery_completed')
        state['inbox_completion'] = journal['completion']['request_id']
        state['inbox_response'] = journal['finish_reply']['request_id']
        state['inbox_result_policy'] = dict(claim_request_id=journal['attempt_id'], version=1)
        return state, historical, current

    def test_historical_attempt_current_auth_and_exact_previous_execution(self):
        for kind in ('valid', 'valid-cleanup', 'wrong-session', 'unfinished', 'wrong-result', 'wrong-close',
                     'unknown', 'cancel-unconfirmed', 'wrong-completion-retry', 'wrong-response-retry',
                     'wrong-policy', 'wrong-native-turn', 'capture-gap', 'live-tool', 'missing-capture', 'unsettled-stop-report'):
            with self.subTest(kind=kind):
                original_root = self.root
                self.root = original_root / kind
                self.root.mkdir(mode=0o700)
                try:
                    self.prepare()
                    state, historical, current = self.historical_record()
                    if kind == 'wrong-session': historical['session'] = dict(historical['session'], execution_id=str(uuid.uuid4()))
                    elif kind == 'unfinished': historical.pop('finished_at')
                    elif kind == 'wrong-result': historical['delivery']['result'] = dict(record_id=str(uuid.uuid4()), version=1)
                    elif kind == 'wrong-close': state['inbox_close']['reason'] = 'process_exited'
                    elif kind == 'unknown': historical = None
                    elif kind == 'cancel-unconfirmed': historical['cancel'] = dict(requested_at='2026-11-12T18:00:00Z')
                    elif kind == 'wrong-completion-retry': state['inbox_completion'] = str(uuid.uuid4())
                    elif kind == 'wrong-response-retry': state['inbox_response'] = str(uuid.uuid4())
                    elif kind == 'wrong-policy': state['inbox_result_policy']['claim_request_id'] = str(uuid.uuid4())
                    elif kind == 'wrong-native-turn': state['inbox_attempt']['native_turn_id'] = str(uuid.uuid4())
                    elif kind == 'capture-gap': historical['capture_gap'] = True
                    elif kind == 'live-tool': historical['tools'] = [dict(item_id='tool-one', stop_state='running')]
                    elif kind in ('valid-cleanup', 'missing-capture', 'unsettled-stop-report'):
                        item = dict(item_id='tool-one', process_id='opencode-call:tool-one', command='bash')
                        historical.update(capture_state='complete', terminal_scan='clear', turn_stop_state='ended',
                                          tools=[dict(item, stop_state='terminated')])
                        state['tool_calls'] = {'tool-one': dict(attempt_id=historical['attempt_id'], ended=True,
                              capture=dict(request_id=str(uuid.uuid4()), session=historical['session'],
                                           attempt_id=historical['attempt_id'], items=[item]))}
                        state['cancel_final_report'] = dict(request_id=str(uuid.uuid4()), session=historical['session'],
                              attempt_id=historical['attempt_id'], terminal_scan='clear',
                              tools=[dict(item_id='tool-one', stop_state='terminated')])
                        if kind == 'missing-capture': historical['tools'] = []
                        elif kind == 'unsettled-stop-report': state['cancel_final_report']['terminal_scan'] = 'unknown_remaining'
                    write(self.sessions[0], encoded(state))
                    before = self.sessions[0].read_bytes()
                    write(self.f.root / 'fixture-views.json', encoded(dict(history={state['inbox_attempt']['attempt_id']: historical})))
                    valid = kind in ('valid', 'valid-cleanup')
                    result = self.exchange(self.old_pins, self.new_paths, self.root / 'retained-old', check=valid)
                    if not valid:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual({p.name: sha(p.read_bytes()) for p in self.active.glob('*.json')}, self.old_pins)
                    else:
                        self.exchange(self.new_pins, self.old_paths, self.root / 'retained-new')
                        self.assertEqual({p.name: sha(p.read_bytes()) for p in self.active.glob('*.json')}, self.old_pins)
                    self.assertEqual(self.sessions[0].read_bytes(), before)
                    reads = [json.loads(call['input']) for call in self.calls() if call['operation'] == 'session-inbox-attempt-status']
                    self.assertTrue(reads)
                    self.assertTrue(all(read == dict(session=current, attempt_id=state['inbox_attempt']['attempt_id']) for read in reads))
                finally:
                    self.root = original_root

    def test_unknown_or_held_drain_facts_refuse_without_publish(self):
        for kind in ('D1', 'D2', 'D2-schema', 'D3', 'D4', 'D5', 'intent-lock'):
            with self.subTest(kind=kind):
                original_root = self.root
                self.root = original_root / kind
                self.root.mkdir(mode=0o700)
                held = None
                try:
                    self.prepare()
                    if kind == 'D1':
                        path = self.legacy / 'binding.json'
                        write(path, encoded(dict(json.loads(path.read_bytes()), enabled=True)))
                    elif kind == 'D2':
                        state = json.loads(self.sessions[0].read_bytes())
                        state['inbox_intent'] = dict(request_id=str(uuid.uuid4()), session={})
                        write(self.sessions[0], encoded(state))
                    elif kind == 'D2-schema':
                        state = json.loads(self.sessions[0].read_bytes())
                        state['schema'] = 'cairn.native-session/unknown'
                        write(self.sessions[0], encoded(state))
                    elif kind == 'D3':
                        path = self.add_request(0, 'unresolved-reply')
                        journal = json.loads(path.read_bytes())
                        journal.pop('finish_reply')
                        journal['completion'].update(status='committed', result=dict(record_id=str(uuid.uuid4()), version=1))
                        write(path, encoded(journal))
                    elif kind == 'D4':
                        write(self.f.root / 'fixture-views.json', encoded(dict(unavailable='wake-attempts')))
                    elif kind == 'D5': self.callbacks = [self.legacy_module.process_reference(os.getpid())]
                    else:
                        held = (self.shared_state / 'intents' / 'existing.lock').open('w+b')
                        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        inode = os.fstat(held.fileno()).st_ino
                        rows = [line for line in Path('/proc/locks').read_text().splitlines() if ':' + str(inode) + ' ' in line]
                        write(self.root / 'held-lock-observation.json', encoded(dict(pid=os.getpid(), inode=inode, rows=rows)))
                        self.assertTrue(rows, 'kernel did not expose the held fixture lock')
                    result = self.exchange(self.old_pins, self.new_paths, self.root / 'refused', check=False)
                    self.assertNotEqual(result.returncode, 0)
                    expected = 'D3' if kind == 'intent-lock' else kind.split('-')[0]
                    self.assertIn(expected.encode(), result.stdout + result.stderr)
                    self.assertFalse((self.root / 'refused').exists())
                    self.assertEqual({p.name: sha(p.read_bytes()) for p in self.active.glob('*.json')}, self.old_pins)
                finally:
                    if held is not None: held.close()
                    self.callbacks = []
                    self.root = original_root


if __name__ == '__main__':
    unittest.main()
