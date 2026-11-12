"""Paired R05-062 assertions; the original Source method remains the old oracle.

Default discovery runs paired assertions and checker-sensitivity controls.
Select test_three_matched_samples explicitly for the bounded timing experiment.
The Go test-binary adapter exercises the journal component, not installed CLI
admission; retained process and installed acceptance remain separate.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import test_inbox_characterization as characterization
import test_inbox_recovery as source


ROOT = Path(__file__).resolve().parents[1]
METHOD = 'test_committed_command_replays_through_the_api_and_a_different_body_is_refused'
GO_TEST = 'TestR05CommittedCommandCore062'


class _Core062Case(unittest.TestCase):
    setUp = characterization.InboxCharacterization.setUp
    context = source.InboxRecovery.context
    replies = source.InboxRecovery.replies
    journal = source.InboxRecovery.journal
    log = source.InboxRecovery.log
    run_context = source.InboxRecovery.run_context
    test_selected = getattr(source.InboxRecovery, METHOD)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def actor_pins():
    paths = list((ROOT / 'internal/lifecycle/journal').glob('*.go'))
    paths += list((ROOT / 'integrations/lifecycle').glob('*.py'))
    paths += [ROOT / 'go.mod', ROOT / 'go.sum', Path(__file__).resolve(),
              Path(source.__file__).resolve(), Path(characterization.__file__).resolve()]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def normalized(trace, root):
    if isinstance(trace, dict):
        return {k: normalized(v, root) for k, v in trace.items()}
    if isinstance(trace, list):
        return [normalized(v, root) for v in trace]
    if isinstance(trace, str):
        return trace.replace(str(root), '$FIXTURE')
    return trace


def comparison(trace, root):
    projected = copy.deepcopy(trace)
    projected.pop('core_seconds')
    projected.pop('setup_seconds')
    for outcome in projected['outcomes']:
        outcome.pop('execute_seconds')
        outcome.pop('parent_subprocess_seconds')
        outcome['stdout'] = json.loads(outcome['stdout'])
    for name in ('fence_journal', 'final_journal'):
        projected[name]['completion']['recorded_at'] = '$TIME'
    return normalized(projected, root)


def old_check(trace):
    case = _Core062Case('test_selected')
    outcomes = iter(trace['outcomes'])
    case.context = lambda: copy.deepcopy(trace['native'])
    case.replies = lambda replies: None
    # Keep the original eight expressions; replay only their recorded boundaries.
    def replay(argv, body=''):
        outcome = next(outcomes)
        return subprocess.CompletedProcess(argv, outcome['returncode'], outcome['stdout'], outcome['stderr'])
    case.run_context = replay
    case.log = lambda: copy.deepcopy(trace['replay_calls'])
    case.journal = lambda: copy.deepcopy(trace['fence_journal'])
    try:
        case.test_selected()
    except (AssertionError, KeyError) as exc:
        return {'rejected': True, 'diagnostic': str(exc), 'exception': type(exc).__name__}
    return {'rejected': False}


def fault_trace(trace, fault):
    changed = copy.deepcopy(trace)
    if fault == 'wrong_refusal':
        reply = json.loads(changed['outcomes'][2]['stdout'])
        reply['status'] = 'AUTHORITY_DENIED'
        changed['outcomes'][2]['stdout'] = json.dumps(reply) + '\n'
    elif fault == 'wrong_exit':
        changed['outcomes'][2]['returncode'] = 0
    elif fault == 'reordered_reply_steps':
        changed['outcomes'][2:4] = reversed(changed['outcomes'][2:4])
    elif fault == 'reused_request_id':
        argv = changed['replay_calls'][1]['argv']
        argv[argv.index('--request-id') + 1] = changed['native']['request_ids']['response']
    elif fault == 'missing_persisted_field':
        del changed['fence_journal']['completion']['replay_code']
    elif fault == 'missing_final_store_call':
        changed['final_calls'].pop()
    else:
        raise ValueError(fault)
    return changed


class PairedCore062Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='cairn-r05-062-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.root = Path(cls.scratch.name)
        evidence = os.environ.get('CAIRN_R05_062_EVIDENCE')
        cls.evidence = Path(evidence).resolve() if evidence else cls.root / 'evidence'
        cls.evidence.mkdir(parents=True, exist_ok=True)
        cls.pins = actor_pins()
        reused = os.environ.get('CAIRN_R05_062_BINARY')
        cls.binary = Path(reused).resolve() if reused else cls.evidence / 'journal-core062.test'
        if reused:
            cls.build = json.loads(cls.binary.with_name('driver-build.json').read_text())
            if cls.build['actor_pins'] != cls.pins or cls.build['binary_sha256'] != digest(cls.binary):
                raise AssertionError('prebuilt R05 driver/source pins changed')
        else:
            argv = ['go', 'test', '-c', '-o', str(cls.binary), './internal/lifecycle/journal']
            started = time.perf_counter()
            built = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=90)
            cls.build = dict(command=argv, seconds=time.perf_counter() - started,
                             returncode=built.returncode, stdout=built.stdout, stderr=built.stderr,
                             actor_pins=cls.pins)
            if built.returncode:
                cls.save('failed-driver-build.json', cls.build)
                raise AssertionError(built.stdout + built.stderr)
            cls.build['binary_sha256'] = digest(cls.binary)
            go_version = subprocess.run(['go', 'version'], capture_output=True, text=True, check=True)
            cls.build['go_version'] = go_version.stdout.strip()
            cls.build['go_executable'] = str(Path(shutil.which('go')).resolve())
            cls.build['go_executable_sha256'] = digest(cls.build['go_executable'])
            cls.build['flags'] = ['test', '-c']
            cls.build['race_instrumented'] = False
            cls.save('driver-build.json', cls.build)
        cls.baselines = None

    @classmethod
    def save(cls, name, report):
        (cls.evidence / name).write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')

    @classmethod
    def go_run(cls, selector, path):
        env = dict(os.environ)
        env.pop('CAIRN_R05_062_TRACE', None)
        env.pop('CAIRN_R05_062_COMMAND', None)
        env[selector] = str(path)
        return subprocess.run([str(cls.binary), '-test.run=^' + GO_TEST + '$', '-test.count=1'],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)

    @classmethod
    def fresh_trace(cls, mode):
        case = _Core062Case('test_selected')
        started = time.perf_counter()
        case.setUp()
        setup_seconds = time.perf_counter() - started
        trace = {'outcomes': [], 'setup_seconds': setup_seconds}
        context_method, dispatch, calls, journal = case.context, case.run_context, case.log, case.journal

        def selected_context():
            native = context_method()
            trace['native'] = copy.deepcopy(native)
            return native

        def execute(argv, body=''):
            started = time.perf_counter()
            if mode == 'source':
                outcome = dispatch(argv, body)
                captured = dict(stdout=outcome.stdout, stderr=outcome.stderr,
                                returncode=outcome.returncode, execute_seconds=None)
            else:
                frame = case.root / 'go-command.json'
                output = case.root / 'go-outcome.json'
                frame.write_text(json.dumps(dict(context=argv[argv.index('--context') + 1],
                                                kind=argv[-1], input=body, output=str(output))))
                driver = cls.go_run('CAIRN_R05_062_COMMAND', frame)
                if driver.returncode:
                    raise AssertionError('Go command adapter failed: ' + driver.stdout + driver.stderr)
                captured = json.loads(output.read_text())
                outcome = subprocess.CompletedProcess(argv, captured['returncode'], captured['stdout'], captured['stderr'])
            captured['parent_subprocess_seconds'] = time.perf_counter() - started
            captured['issued_request'] = dict(argv=argv, input=body, kind=argv[-1])
            trace['outcomes'].append(captured)
            return outcome

        def replay_calls():
            observed = calls()
            trace['replay_calls'] = copy.deepcopy(observed)
            return observed

        def fence_journal():
            observed = journal()
            trace['fence_journal'] = copy.deepcopy(observed)
            return observed

        case.context, case.run_context, case.log, case.journal = selected_context, execute, replay_calls, fence_journal
        try:
            initial = copy.deepcopy(case.state)
            started = time.perf_counter()
            case.test_selected()
            trace['core_seconds'] = time.perf_counter() - started
            trace['final_calls'], trace['final_journal'] = calls(), journal()
            trace['initial_state'] = initial
            trace['api_script'] = [source.HANDLED, source.HANDLED, source.LOST, dict(ok=False, status='STALE_SESSION')]
            trace['fake_cli_sha256'] = digest(case.cairn)
            trace['root'] = str(case.root)
            return trace
        finally:
            case.doCleanups()

    @classmethod
    def check_go(cls, trace, name):
        path = cls.evidence / (name + '.json')
        path.write_text(json.dumps(trace) + '\n')
        checked = cls.go_run('CAIRN_R05_062_TRACE', path)
        cls.save(name + '-go-check.json', dict(returncode=checked.returncode, stdout=checked.stdout, stderr=checked.stderr))
        if checked.returncode not in (0, 1) or checked.returncode == 1 and ('FAIL: ' + GO_TEST) not in checked.stdout:
            raise AssertionError('not a checker verdict: ' + checked.stdout + checked.stderr)
        return {'rejected': checked.returncode == 1, 'stdout': checked.stdout, 'stderr': checked.stderr}

    @classmethod
    def paired(cls):
        if cls.baselines is None:
            cls.baselines = {mode: cls.fresh_trace(mode) for mode in ('source', 'go')}
            cls.save('paired-traces.json', cls.baselines)
        return cls.baselines

    def test_paired_assertions_and_effects(self):
        traces = self.paired()
        old, new = [comparison(traces[mode], traces[mode]['root']) for mode in ('source', 'go')]
        old.pop('root'); new.pop('root')
        self.assertEqual(old, new)
        for mode, trace in traces.items():
            self.assertFalse(old_check(trace)['rejected'])
            self.assertFalse(self.check_go(trace, mode + '-baseline')['rejected'])

    def test_fault_sensitivity(self):
        controls = []
        for mode, trace in self.paired().items():
            self.assertFalse(old_check(trace)['rejected'])
            self.assertFalse(self.check_go(trace, mode + '-fault-baseline')['rejected'])
            for fault in ('wrong_refusal', 'wrong_exit', 'reordered_reply_steps', 'reused_request_id',
                          'missing_persisted_field', 'missing_final_store_call'):
                changed = fault_trace(trace, fault)
                old = old_check(changed)
                new = self.check_go(changed, mode + '-' + fault)
                controls.append(dict(executor=mode, fault=fault, old=old, new=new))
                self.assertEqual(old['rejected'], fault not in ('wrong_exit', 'missing_final_store_call'), fault)
                self.assertTrue(new['rejected'], fault)
        self.save('fault-controls.json', dict(controls=controls, scope='Observable checker-sensitivity mutations, not modified production runtime or Source oracle. Reordering swaps the two differing reply steps; API requests themselves are identical complete replays.'))

    def test_three_matched_samples(self):
        started = time.perf_counter()
        samples = []
        for number in range(3):
            order = ('source', 'go') if number != 1 else ('go', 'source')
            measured = {mode: self.fresh_trace(mode) for mode in order}
            old, new = [comparison(measured[mode], measured[mode]['root']) for mode in ('source', 'go')]
            old.pop('root'); new.pop('root')
            self.assertEqual(old, new)
            samples.append(dict(sample=number + 1, order=order, load_average=os.getloadavg(), traces=measured))
        self.assertEqual(self.pins, actor_pins())
        supplied_environment = '\0'.join(sorted(k + '=' + v for k, v in os.environ.items())) + '\0'
        self.save('matched-three-samples.json', dict(samples=samples, build=self.build,
                  measurement_method_seconds=time.perf_counter() - started,
                  supplied_parent_environment_sha256=hashlib.sha256(supplied_environment.encode()).hexdigest(),
                  source_inner_execute_seconds=None, per_executor_peak_footprint_bytes=None,
                  python=dict(executable=sys.executable, sha256=digest(sys.executable), version=sys.version),
                  scripted_cli_python=dict(path=shutil.which('python3'), sha256=digest(shutil.which('python3'))),
                  platform=platform.platform(), cpu_count=os.cpu_count(),
                  environment={k: os.environ.get(k) for k in ('HOME', 'PATH', 'LANG', 'TZ', 'TMPDIR', 'GOFLAGS', 'GOTOOLCHAIN')},
                  limits=['Original Source method with the same fixed fixture IDs/API replies/state; scratch paths differ by an explicit bijection.',
                          'Core time includes shared Python fixture/context/assertion work and coordinator/test-driver startup; not isolated journal CPU time.',
                          'Source in-child execute time and per-executor peak footprint are unobserved.',
                          'Go adapter performs one real journal Execute per process and uses actual scripted CLI subprocesses; it does not qualify installed admission.',
                          'Supplied environment is hashed without exposing credentials. Go consumes/removes its one adapter control variable before journal execution; actual Source child environments are not independently attested.',
                          'Shared host admission is outside the samples; concurrent load is uncontrolled. No same-suite speedup while old tests remain.']))


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PairedCore062Tests(name) for name in
                             ('test_paired_assertions_and_effects', 'test_fault_sensitivity'))


if __name__ == '__main__':
    unittest.main()
