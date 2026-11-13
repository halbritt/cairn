"""Literal execution frames only; does not start a native client or model."""
from pathlib import Path
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import uuid

import check_r06_native_hook_census as collector
from check_r06_native_hook_census import hook_process_frame
from test_r02_compatibility_install import InstalledTools
from test_f04_activation import ROOT, encoded


def material_pin(path):
    return dict(Path=str(path), SHA256=hashlib.sha256(path.read_bytes()).hexdigest())


class RetainedHookFrames(unittest.TestCase):
    def test_retained_python_has_no_shell_or_added_tail(self):
        for role in ('memory', 'coordination'):
            with self.subTest(role=role):
                argv = ['/usr/bin/python3', '-I', '-B', '/owned/real retained/' + role + '.py']
                if role == 'coordination':
                    argv.append('hook')
                argv += ['--config', '/owned/real retained/' + role + '.json']
                before = list(argv)
                program, actual = hook_process_frame(dict(implementation='python', argv=argv))
                self.assertEqual(program, '/usr/bin/python3')
                self.assertEqual(actual, before)
                self.assertEqual(argv, before)

    def test_checked_go_keeps_emitted_shell_tail(self):
        with tempfile.TemporaryDirectory() as scratch:
            launcher = Path(scratch) / 'inbox'
            vector = ['/owned/pinned image/cairn', 'coordination', '--receipt', '/owned/receipt.json']
            launcher.write_text('#!/bin/sh\nexec ' + shlex.join(vector) + ' -- "$@"\n')
            row = dict(implementation='go', argv=[str(launcher), 'hook', '--config', '/owned/café config.json'])
            self.assertEqual(hook_process_frame(row),
                             (vector[0], [*vector, '--', 'hook', '--config', '/owned/café config.json']))

    def test_changed_shell_frame_refuses(self):
        with tempfile.TemporaryDirectory() as scratch:
            launcher = Path(scratch) / 'inbox'
            launcher.write_text('#!/bin/sh\nexec /owned/cairn coordination "$@"\n')
            with self.assertRaisesRegex(AssertionError, 'unexpected emitted launcher frame'):
                hook_process_frame(dict(implementation='go', argv=[str(launcher)]))


class RetainedHookMaterial(InstalledTools, unittest.TestCase):
    """Actual producers and Go inspection; synthetic readback, no native proof."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.census = Path(cls.build.name) / 'census-tests'
        subprocess.run(['go', 'test', '-c', '-buildvcs=false', '-o', str(cls.census),
                        './internal/lifecycle/coordination'], cwd=ROOT, check=True, timeout=120)
        cls.census.chmod(0o500)

    def test_real_retained_material_reaches_consumed_reader(self):
        from test_r04_retained2_hook_settings import Retained2HookSettingsTests

        producer = Retained2HookSettingsTests(methodName='runTest')
        producer.stager, producer.binary = self.stager, self.binary
        producer.setUp()
        self.addCleanup(producer.doCleanups)
        for mutation in ('none', 'binding-proof', 'unknown-proof', 'observed-argv'):
            with self.subTest(mutation=mutation):
                f = producer.fixture('claude')
                staged, hook_root, record = producer.selected(f)
                evidence = Path(tempfile.mkdtemp(prefix='r06-retained-consumer-evidence-'))
                self.addCleanup(shutil.rmtree, evidence)
                self.addCleanup(self.preserve_material, f, evidence, mutation)
                collector.write(evidence / 'before-use.json', dict(census=material_pin(self.census),
                    stager=material_pin(self.stager), producer=material_pin(ROOT / 'scripts/stage_lifecycle_hooks.py'),
                    go_mod=material_pin(ROOT / 'go.mod'), go_sum=material_pin(ROOT / 'go.sum'),
                    readback='synthetic unit input; no native client, API or authenticated D2'))
                if mutation in ('binding-proof', 'unknown-proof'):
                    if mutation == 'binding-proof':
                        record['retained_python']['binding']['sha256'] = '0' * 64
                    else:
                        record['retained_python']['unknown'] = True
                    path = hook_root / 'hook-selection.json'
                    path.chmod(0o600)
                    path.write_bytes(encoded(record))
                    path.chmod(0o400)
                    manifest_path = Path(staged['selection']['path'])
                    manifest = json.loads(manifest_path.read_bytes())
                    for member in manifest['files']:
                        if member['name'] == path.name:
                            member.update(sha256=material_pin(path)['SHA256'], bytes=path.stat().st_size)
                    manifest_path.chmod(0o600)
                    manifest_path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode())
                    manifest_path.chmod(0o400)
                    staged['selection']['sha256'] = material_pin(manifest_path)['SHA256']
                memory = json.loads((f.old_dir / 'memory.json').read_bytes())
                coord = json.loads((f.old_dir / 'coordination.json').read_bytes())
                native = str(uuid.uuid4())
                plan = dict(Config=str(f.old_dir / 'coordination.json'), Native=native,
                            Workspace=str(f.work), Result=str(evidence / 'session.json'))
                plan_path = evidence / 'session-plan.json'
                collector.write(plan_path, plan)
                result = subprocess.run([str(self.census), '-test.run=^TestR06RetainedCensusSession$', '-test.v'],
                    env=dict(os.environ, CAIRN_R06_RETAINED_SESSION_PLAN=str(plan_path)), capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                settings = json.loads(Path(staged['settings']['path']).read_bytes())
                rows = [row for row in record['rows'] if row['event'] == 'UserPromptSubmit']
                observed = dict(Schema='cairn.watch-claude-readback/1', Harness='claude', Layer='flagSettings',
                    NativeSession=native, Settings=dict(Path=staged['settings']['path'], SHA256=staged['settings']['sha256']),
                    Shell=material_pin(Path('/bin/sh')), Effective=settings,
                    Listing=[dict(Event=row['event'], Command=row['hook']['command'], Source='flagSettings') for row in record['rows']],
                    Executions=[dict(Role=row['role'], Event=row['event'], Program=material_pin(Path(row['argv'][0])),
                        Launcher=material_pin(Path(row['argv'][0])), Argv=list(row['argv']), PID=42 + index, Exit=0)
                        for index, row in enumerate(rows)])
                if mutation == 'observed-argv':
                    observed['Executions'][0]['Argv'].append('--')
                selected = SimpleNamespace(root=f.base, account=f.account, coord=coord, memory=memory,
                    coord_path=f.old_dir / 'coordination.json', binding=f.old_dir / 'binding.json')
                catalog, proposal, _ = collector.catalog_for(selected, (staged, None, record), native, f.work, observed, evidence)
                reason = {'binding-proof': 'producer pin differs', 'unknown-proof': 'producer selection',
                          'observed-argv': 'observed program/literal argv'}
                collector.inspect(self.census, selected, catalog, proposal, evidence, 'inspection',
                                  ready=mutation == 'none', reason=reason.get(mutation, ''))

    def preserve_material(self, fixture, evidence, mutation):
        destination = os.environ.get('CAIRN_RETAINED_CENSUS_EVIDENCE')
        if destination:
            target = Path(destination) / mutation
            target.mkdir(mode=0o700, parents=True)
            shutil.copytree(fixture.base, target / 'fixture')
            shutil.copytree(evidence, target / 'observations')
            (target / 'original-disposable-root.txt').write_text(str(fixture.base) + '\n')
