"""The lifecycle memory module reports its own source artifact as the Cairn CLI caller origin (CAIRN-114).

A recording CLI child shows the real subprocess environment that Memory.call and report_recall pass,
so these tests need no private helper names. The identity is a reported source snapshot taken once when
the module is initialized: never loaded-code attestation, authority, a client census or a release ordering.
"""
import contextlib
import hashlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

import test_inbox_recall_bridge as fixtures
from test_recall_observation import ALLOWED_KEYS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'integrations/lifecycle/memory.py'
VARIABLE = 'CAIRN_CALLER_DIAGNOSTICS'
SNAPSHOT_LIMIT = 1024 * 1024
REQUEST = '11111111-1111-4111-8111-111111111111'
HOSTILE = [
    json.dumps(dict(component='lifecycle-memory', implementation_id='f' * 64, basis='reported')),
    'TOKEN-canary /home/user/path',
]
RECORDER = '''#!PYTHON
import json, os, pathlib, sys
root = pathlib.Path(__file__).parent
body = sys.stdin.read()
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(dict(argv=sys.argv[1:], body=body, env=dict(os.environ))) + '\\n')
if (root / 'fail').exists():
    print(json.dumps(dict(ok=False, status='API_UNAVAILABLE')))
    sys.exit(7)
print(json.dumps(dict(ok=True, status='OK', data=dict(observation_id='fixture', credits_remaining=3))))
'''
names = itertools.count()


class Blocked(BaseException):
    """Raised by the watchdog; not an Exception, so a best-effort handler cannot hide it."""


@contextlib.contextmanager
def bounded(seconds=10):
    def blocked(signum, frame):
        raise Blocked('initialization waited on a nonregular source')
    previous = signal.signal(signal.SIGALRM, blocked)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def sha256(value):
    return hashlib.sha256(value if isinstance(value, bytes) else Path(value).read_bytes()).hexdigest()


def load(path):
    """An ordinary import of one owned source copy."""
    spec = importlib.util.spec_from_file_location('origin_copy_%d' % next(names), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(raw, location, validated=None):
    """Initialize a module the way the bound bridge does: execute pinned bytes with `location` as its __file__."""
    spec = importlib.util.spec_from_file_location('origin_bound_%d' % next(names), location)
    module = importlib.util.module_from_spec(spec)
    if validated is not None:
        module.__dict__['_CAIRN_VALIDATED_SOURCE'] = validated
    exec(compile(raw, str(location), 'exec'), module.__dict__)
    return module


def padded(raw, size):
    return raw + b'\n#' + b'x' * (size - len(raw) - 2)


class CallerOriginTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.raw = SOURCE.read_bytes()
        self.cli = self.root / 'cairn'
        self.cli.write_text(RECORDER.replace('PYTHON', os.fspath(sys.executable), 1))
        self.cli.chmod(0o700)
        self.config = dict(cairn=str(self.cli), socket=str(self.root / 'api.sock'),
                           token_file=str(self.root / 'token'), repo='fixture', harness='claude')

    def copy(self, name, data=None):
        path = self.root / name / 'memory.py'
        path.parent.mkdir()
        path.write_bytes(self.raw if data is None else data)
        return path

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def origin(self, call):
        value = call['env'].get(VARIABLE)
        return None if value is None else json.loads(value)

    def declared(self, digest=None):
        origin = dict(component='lifecycle-memory', basis='reported')
        if digest:
            origin['implementation_id'] = digest
        return origin

    def contact(self, module, session='session-1'):
        memory = module.Memory(self.config, session)
        return memory, memory.call('history', payload=dict(record_id='record-1'))

    def report(self, module, memory):
        with patch.object(module, 'RECALL_REPORT_SECONDS', 5.0):  # a loaded host must not turn this into a skip
            module.report_recall(memory, dict(hook_event_name='UserPromptSubmit'), time.monotonic(), 0.1, 'completed', 0)

    def assert_component_only(self, module):
        before = len(self.calls())
        _, result = self.contact(module)
        self.assertEqual(result, dict(observation_id='fixture', credits_remaining=3))
        calls = self.calls()
        self.assertEqual(len(calls), before + 1)
        self.assertEqual(self.origin(calls[before]), self.declared())

    def test_both_outbound_paths_report_the_modules_own_source_digest(self):
        path = self.copy('a')
        digest = sha256(path)
        module = load(path)
        memory, _ = self.contact(module)
        self.report(module, memory)
        calls = self.calls()
        self.assertRegex(digest, '^[0-9a-f]{64}$')
        self.assertEqual([call['argv'][5] for call in calls], ['history', 'recall-observation'])
        for call in calls:
            value = call['env'][VARIABLE]
            self.assertLessEqual(len(value.encode()), 512)
            # Exactly the allowlisted member names: no path, body, query, token, session or exception text.
            self.assertEqual(json.loads(value), self.declared(digest))
        argv = [argument for call in calls for argument in call['argv']]
        self.assertEqual(argv[:5], ['agent', '--socket', self.config['socket'], '--token-file', self.config['token_file']])

    def test_distinct_artifacts_differ_and_identical_bytes_at_another_path_share_one_id(self):
        first, second = self.copy('a'), self.copy('b')
        third = self.copy('c', self.raw + b'\n# a different artifact\n')
        for path in (first, second, third):
            self.contact(load(path))
        declared = [self.origin(call)['implementation_id'] for call in self.calls()]
        self.assertEqual(declared, [sha256(first), sha256(second), sha256(third)])
        self.assertEqual(declared[0], declared[1])
        self.assertNotEqual(declared[0], declared[2])
        self.assertNotIn(str(first), json.dumps(self.calls()[0]['env'][VARIABLE]))

    def test_each_initialization_route_observes_its_source(self):
        path = self.copy('a')
        digest = sha256(path)
        modules = [load(path), execute(self.raw, path), types.SimpleNamespace(**runpy.run_path(str(path)))]
        for module in modules:
            self.contact(module)
        self.assertEqual([self.origin(call) for call in self.calls()], [self.declared(digest)] * 3)

    def test_a_symlinked_regular_source_is_observed(self):
        target = self.copy('target')
        link = self.root / 'link' / 'memory.py'
        link.parent.mkdir()
        os.symlink(target, link)
        self.contact(execute(self.raw, link))
        self.assertEqual(self.origin(self.calls()[0]), self.declared(sha256(target)))

    def test_snapshot_is_fixed_for_one_initialization_and_a_fresh_one_observes_anew(self):
        path = self.copy('a')
        original = sha256(path)
        module = load(path)
        first = module.Memory(self.config, 'first')
        # Replace by rename, then rewrite in place: neither changes a completed snapshot.
        other = path.with_name('replacement')
        other.write_bytes(self.raw + b'\n# replaced by rename\n')
        os.replace(other, path)
        second = module.Memory(self.config, 'second')
        for memory in (first, second):
            memory.call('history', payload=dict(record_id='record-1'))
            self.report(module, memory)
        path.write_bytes(self.raw + b'\n# rewritten in place\n')
        first.call('history', payload=dict(record_id='record-1'))
        self.report(module, first)
        calls = self.calls()
        self.assertEqual(len(calls), 6)
        self.assertEqual([self.origin(call) for call in calls], [self.declared(original)] * 6)
        # A newly initialized module may observe a new artifact.
        fresh = load(path)
        self.contact(fresh)
        self.assertEqual(self.origin(self.calls()[-1]), self.declared(sha256(path)))
        self.assertNotEqual(sha256(path), original)

    def test_unknown_sources_report_only_the_component_and_never_fail_the_call(self):
        missing = self.root / 'gone' / 'memory.py'
        directory = self.root / 'directory' / 'memory.py'
        directory.mkdir(parents=True)
        fifo = self.root / 'fifo' / 'memory.py'
        fifo.parent.mkdir()
        os.mkfifo(fifo)
        link = self.root / 'fifo-link' / 'memory.py'
        link.parent.mkdir()
        os.symlink(fifo, link)
        bytecode = self.root / 'bytecode' / 'memory.pyc'
        bytecode.parent.mkdir()
        bytecode.write_bytes(self.raw)
        oversize = self.copy('oversize', padded(self.raw, SNAPSHOT_LIMIT + 1))
        for name, location in dict(missing=missing, directory=directory, fifo=fifo, fifo_link=link,
                                   bytecode_only=bytecode, oversize=oversize).items():
            with self.subTest(source=name), bounded():
                self.assert_component_only(execute(self.raw, location))
        with self.subTest(source='no __file__'):
            namespace = {'__name__': 'bare'}
            exec(compile(self.raw, 'memory.py', 'exec'), namespace)
            self.assert_component_only(types.SimpleNamespace(**namespace))
        with self.subTest(source='unreadable'):
            locked = self.copy('locked')
            locked.chmod(0)
            if os.access(locked, os.R_OK):
                self.skipTest('this account can read a mode 000 file')
            self.assert_component_only(execute(self.raw, locked))

    def test_the_snapshot_limit_is_inclusive(self):
        exact = self.copy('exact', padded(self.raw, SNAPSHOT_LIMIT))
        self.assertEqual(exact.stat().st_size, SNAPSHOT_LIMIT)
        self.contact(execute(self.raw, exact))
        self.assertEqual(self.origin(self.calls()[0]), self.declared(sha256(exact)))
        over = self.copy('over', padded(self.raw, SNAPSHOT_LIMIT + 1))
        self.assert_component_only(execute(self.raw, over))

    def test_a_change_detected_while_observing_leaves_the_identity_unknown(self):
        def append(path):
            with path.open('ab') as out:
                out.write(b'# written during observation\n')

        def replace(path):  # identical bytes under a new identity are still a replacement
            other = path.with_name('replacement')
            other.write_bytes(path.read_bytes())
            os.replace(other, path)

        def touch(path):
            stamp = time.time_ns() - 10 ** 13
            os.utime(path, ns=(stamp, stamp))

        for name, disturb in dict(append=append, replace=replace, touch=touch).items():
            with self.subTest(change=name):
                location = self.copy(name)
                real = os.read
                seen = []

                def read(descriptor, size, real=real, location=location, disturb=disturb, seen=seen):
                    data = real(descriptor, size)
                    if not seen:
                        seen.append(True)
                        disturb(location)
                    return data

                with patch('os.read', read):
                    module = execute(self.raw, location)
                self.assertTrue(seen, 'the observation never read the source')
                self.assert_component_only(module)

    def test_loader_validated_bytes_are_the_snapshot_and_are_not_retained(self):
        path = self.copy('a', self.raw + b'\n# a different artifact on disk\n')
        module = execute(self.raw, path, validated=self.raw)
        self.assertFalse(hasattr(module, '_CAIRN_VALIDATED_SOURCE'))
        self.contact(module)
        self.assertEqual(self.origin(self.calls()[0]), self.declared(sha256(self.raw)))
        self.assertNotEqual(sha256(self.raw), sha256(path))
        # Without validated bytes the one file observation describes what is on disk.
        self.contact(execute(self.raw, path))
        self.assertEqual(self.origin(self.calls()[1]), self.declared(sha256(path)))
        # Anything but bytes within the limit is unknown; it never falls back to another read.
        exact = bytes(SNAPSHOT_LIMIT)
        self.contact(execute(self.raw, path, validated=exact))
        self.assertEqual(self.origin(self.calls()[2]), self.declared(sha256(exact)))
        for invalid in (self.raw.decode(), bytearray(self.raw), bytes(SNAPSHOT_LIMIT + 1)):
            with self.subTest(validated=type(invalid).__name__, size=len(invalid)):
                self.assert_component_only(execute(self.raw, path, validated=invalid))

    def test_inherited_declarations_cannot_impersonate_and_the_parent_environment_is_unchanged(self):
        path = self.copy('a')
        digest = sha256(path)
        for hostile in HOSTILE:
            with self.subTest(inherited=hostile[:24]), patch.dict(os.environ, {VARIABLE: hostile, 'CAIRN_TEST_MARKER': 'kept'}):
                before = dict(os.environ)
                start = len(self.calls())
                healthy = load(path)
                unknown = execute(self.raw, self.root / 'gone' / 'memory.py')
                with patch('json.dumps', side_effect=RuntimeError('diagnostic construction failed')):
                    failed = execute(self.raw, path)
                for module in (healthy, unknown, failed):
                    memory, _ = self.contact(module)
                    self.report(module, memory)
                self.assertEqual(dict(os.environ), before)
                self.assertEqual(os.environ[VARIABLE], hostile)
                calls = self.calls()[start:]
                self.assertEqual(len(calls), 6)
                for call in calls[0:2]:
                    self.assertEqual(self.origin(call), self.declared(digest))
                for call in calls[2:4]:
                    self.assertEqual(self.origin(call), self.declared())
                for call in calls[4:6]:  # no declaration could be built: nothing stale is forwarded either
                    self.assertNotIn(VARIABLE, call['env'])
                for call in calls:
                    self.assertNotEqual(call['env'].get(VARIABLE), hostile)
                    self.assertNotIn('TOKEN-canary', call['env'].get(VARIABLE, ''))
                    # Only the dedicated variable differs from the parent (the interpreter running the
                    # recording CLI may add or coerce LC_CTYPE).
                    ignored = {VARIABLE, 'LC_CTYPE'}
                    child = {k: v for k, v in call['env'].items() if k not in ignored}
                    self.assertEqual(child, {k: v for k, v in before.items() if k not in ignored})

    def test_failed_diagnostics_change_no_business_call_or_outcome(self):
        healthy = load(self.copy('a'))
        with patch('json.dumps', side_effect=RuntimeError('diagnostic construction failed')):
            failed = execute(self.raw, self.copy('b'))

        def business(module, session):
            memory = module.Memory(self.config, session)
            pulled = memory.call('pull', payload=dict(receipt_id='receipt-1', handle='handle', request_id=REQUEST))
            created = memory.call('create', payload=dict(request_id=REQUEST, draft=dict(kind='note', body='Selected body')),
                                  timeout=7)
            return [pulled, created, memory.receipt_credits, memory.pull_calls]

        outcomes = [business(healthy, 'same-session'), business(failed, 'same-session')]
        self.assertEqual(outcomes[0], outcomes[1])
        calls = self.calls()
        self.assertEqual(len(calls), 4)
        for left, right in ((calls[0], calls[2]), (calls[1], calls[3])):
            self.assertEqual((left['argv'], left['body']), (right['argv'], right['body']))
        self.assertEqual(json.loads(calls[1]['body'])['request_id'], REQUEST)
        self.assertIsNotNone(self.origin(calls[0]))
        self.assertIsNone(self.origin(calls[2]))
        # A failing CLI reaches the caller exactly as before, after exactly one invocation (no retry).
        (self.root / 'fail').write_text('')
        failures = []
        for module in (healthy, failed):
            before = len(self.calls())
            with self.assertRaises(Exception) as caught:
                module.Memory(self.config, 'same-session').call('history', payload=dict(record_id='record-1'))
            failures.append((type(caught.exception).__name__, str(caught.exception), caught.exception.code))
            self.assertEqual(len(self.calls()), before + 1)
        self.assertEqual(failures[0], failures[1])
        self.assertEqual(failures[0][2], 'API_UNAVAILABLE')

    def test_recall_report_body_and_best_effort_semantics_are_unchanged(self):
        module = load(self.copy('a'))
        memory = module.Memory(self.config, 'session')
        self.report(module, memory)
        (self.root / 'fail').write_text('')
        self.report(module, memory)  # best effort: a failing CLI never escapes the hook
        reports = self.calls()
        self.assertEqual([call['argv'][5] for call in reports], ['recall-observation'] * 2)  # one call each, no retry
        for call in reports:
            body = json.loads(call['body'])
            self.assertLessEqual(set(body), ALLOWED_KEYS)  # the declaration is transport only, never a body field
            self.assertEqual(body['method'], 'cairn-lifecycle/recall-meter/1')
            self.assertNotIn('implementation_id', call['body'])

    def test_selector_subprocess_does_not_receive_the_declaration(self):
        module = load(self.copy('a'))
        with patch.dict(os.environ):
            os.environ.pop(VARIABLE, None)
            with patch.object(module, 'run_json', return_value={}) as run:
                module.select_json(dict(claude='claude'), {}, 'prompt', {})
        environment = run.call_args.kwargs['env']
        self.assertNotIn(VARIABLE, environment)
        self.assertEqual(environment['CAIRN_LIFECYCLE_CHILD'], '1')

    def test_only_the_memory_producer_names_the_caller_channel(self):
        named = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'integrations').rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts and VARIABLE.encode() in path.read_bytes())
        self.assertEqual(named, ['integrations/lifecycle/memory.py'])


class BoundEngineTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.mc['recall_observations'] = True
        case.fixture['search']['receipt_id'] = fixtures.EXEC
        case.freeze()
        source = case.cli.read_text().replace(
            "body=sys.stdin.read()\n",
            "body=sys.stdin.read()\nimport os\nwith (root/'origins.jsonl').open('a') as f:"
            "f.write(json.dumps(dict(op=op,origin=os.environ.get('CAIRN_CALLER_DIAGNOSTICS')))+'\\n')\n", 1)
        case.cli.write_text(source.replace(
            "if op=='search':", "if op=='recall-observation':\n result=dict(observation_id='fixture')\nelif op=='search':", 1))
        return case

    def pin_modified_engine(self, case):
        engine = case.root / 'memory.py'
        engine.write_bytes(engine.read_bytes() + b'\n# the pinned engine is not the hook module\n')
        manifest = json.loads(case.binding.read_text())
        manifest['engine']['sha256'] = sha256(engine)
        case.binding.write_text(json.dumps(manifest))
        return engine

    def origins(self, case):
        return [json.loads(line) for line in (case.root / 'origins.jsonl').read_text().splitlines()]

    def test_bound_inbox_recall_reports_the_pinned_engine_digest_on_every_cairn_call(self):
        case = self.fixture()
        engine = self.pin_modified_engine(case)
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        rows = self.origins(case)
        self.assertIn('recall-observation', [row['op'] for row in rows])
        self.assertGreaterEqual(len(rows), 3)
        expected = dict(component='lifecycle-memory', implementation_id=sha256(engine), basis='reported')
        for row in rows:
            self.assertEqual(json.loads(row['origin']), expected)
        self.assertNotEqual(sha256(engine), sha256(SOURCE))

    def test_bound_engine_snapshot_is_the_validated_bytes_not_a_second_read_of_the_path(self):
        case = self.fixture()
        bridge = fixtures.memory.load_inbox_recall(case.mc, validate=False)
        path = case.root / 'memory.py'
        pinned = path.read_bytes()
        real = bridge.checked_file

        def racing(spec):
            checked, raw = real(spec)
            if checked == path:  # replaced after its pin was validated, before the engine runs
                checked.write_bytes(raw + b'\n# replaced after the pin was validated\n')
            return checked, raw

        with patch.object(bridge, 'checked_file', racing):
            engine, config = bridge.binding(case.mc)
        self.assertNotEqual(path.read_bytes(), pinned)
        engine.Memory(config, 'session').call('history', payload=dict(record_id=fixtures.RECORD))
        self.assertEqual(json.loads(self.origins(case)[-1]['origin']),
                         dict(component='lifecycle-memory', implementation_id=sha256(pinned), basis='reported'))
        self.assertFalse(hasattr(engine, '_CAIRN_VALIDATED_SOURCE'))


if __name__ == '__main__':
    unittest.main()
