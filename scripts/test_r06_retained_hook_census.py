"""Literal execution frames only; does not start a native client or model."""
from pathlib import Path
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
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
        destination = os.environ.get('CAIRN_RETAINED_CENSUS_EVIDENCE')
        if destination:
            evidence = Path(destination)
            evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
            images = evidence / 'images'
            images.mkdir(mode=0o700)
            pins = {}
            for role, image in (('census', cls.census), ('control', cls.binary), ('stager', cls.stager)):
                retained = images / role
                shutil.copyfile(image, retained)
                retained.chmod(0o500)
                pins[role] = dict(actual=material_pin(image), retained=material_pin(retained))
                build = subprocess.run(['go', 'version', '-m', str(image)], capture_output=True, check=True)
                (images / (role + '.buildinfo')).write_bytes(build.stdout)
            sources = [*sorted(ROOT.rglob('*.go')), *sorted((ROOT / 'integrations/lifecycle').glob('*.py')),
                       ROOT / 'go.mod', ROOT / 'go.sum', Path(__file__).resolve(),
                       ROOT / 'scripts/check_r06_native_hook_census.py',
                       ROOT / 'scripts/stage_lifecycle_hooks.py', ROOT / 'scripts/stage_lifecycle_compatibility.py',
                       ROOT / 'scripts/test_r04_retained2_hook_settings.py', ROOT / 'scripts/test_lifecycle_generation_install.py']
            observer = shutil.which('strace')
            if observer is None:
                raise RuntimeError('strace is required by the retained census material fixture')
            collector.write(evidence / 'images-before-first-use.json', dict(images=pins,
                python=material_pin(Path(sys.executable)), shell=material_pin(Path('/bin/sh')),
                observer=material_pin(Path(observer)),
                sources={str(path.relative_to(ROOT)): material_pin(path)['SHA256'] for path in sources},
                scope='synthetic registration/readback unit test; no native client or authenticated D2'))

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

    def test_installed_packages_reach_public_census(self):
        """Declared fixture settings plus actual owned Python executions, no native client.

        The existing session producer and process inspector supply the same
        finite unit boundary as the generation-local test above. Listing and
        effective settings are fixture inputs, never native qualification.
        """
        import stage_lifecycle_compatibility as packages
        import stage_lifecycle_hooks as hooks
        from test_retained_lifecycle_package import fixture, private_directory, write, pin as package_pin
        from check_r04_native_opencode import successful_execs

        with tempfile.TemporaryDirectory(prefix='r06-installed-reader-', dir='/dev/shm') as scratch, \
                tempfile.TemporaryDirectory(prefix='r06-installed-observation-') as observation_root:
            root = Path(scratch)
            # The captured installed facade contract is exactly 284 bytes.
            # Select a disposable delegate pathname of the same length, without
            # copying any installed host path or changing the facade producer.
            padding = 106 - len(str(root / 'one' / 'installed' / 'hotfix' / 'coordination.py')) - 1
            self.assertGreater(padding, 0)
            root = root / ('p' * padding)
            root.mkdir(mode=0o700)
            image = root / 'images' / 'cairn'
            private_directory(image.parent)
            shutil.copyfile(self.binary, image)
            image.chmod(0o500)
            catalogs, proposals, selected_inputs = [], [], []
            for number in ('one', 'two'):
                plan = fixture(root / number, host='talos', harness='claude', count=1, client=image)
                # Different complete role closures, original profiles outside
                # the binding directory, duplicate-last memory JSON, 0755 parent.
                package = plan['package']
                Path(package['profiles']['memory.json']['path']).chmod(0o400)
                member = package['members']['claude-one']
                self.assertEqual(Path(member['runtime']['coordination']['entry']['path']).stat().st_size, 284)
                self.assertEqual(Path(member['runtime']['coordination']['entry']['path']).read_bytes(), packages.package_facade(member['runtime']['coordination']['delegate']['path']))
                generations = root / number / 'generations'
                generations.mkdir(mode=0o700)
                lifecycle = packages.stage_retained_package(str(self.stager), generations, plan)
                config = Path(member['config']['path'])
                coord = json.loads(config.read_bytes())
                memory = json.loads(Path(package['profiles']['memory.json']['path']).read_bytes())
                account = Path(coord['config_home'])
                work = root / number / 'workspace'
                work.mkdir(mode=0o700)
                original = write(account / 'settings.json', dict(opaque='preserved'))
                hook_root = root / number / 'hooks'
                hook_root.mkdir(mode=0o700)
                staged = hooks.stage(str(self.stager), hook_root, dict(id=str(uuid.uuid4()), selection=lifecycle,
                    harness='claude', account_home=str(account), settings=original, coordination_index=0,
                    legacy=True, replace_commands=dict(memory=[], coordination=[])))
                producer = json.loads((Path(staged['selection']['path']).parent / 'hook-selection.json').read_bytes())
                evidence = root / number / 'observations'
                evidence.mkdir(mode=0o700)
                native = str(uuid.uuid4())
                for directory in (Path(coord['state_dir']), Path(memory['state_dir'])):
                    directory.mkdir(mode=0o700, exist_ok=True)
                session_plan = collector.write(evidence / 'session-plan.json', dict(Config=str(config), Native=native,
                    Workspace=str(work), Result=str(evidence / 'session.json')))
                session = subprocess.run([str(self.census), '-test.run=^TestR06RetainedCensusSession$', '-test.v'],
                    env=dict(os.environ, CAIRN_R06_RETAINED_SESSION_PLAN=session_plan['Path']), capture_output=True, timeout=30)
                self.assertEqual(session.returncode, 0, session.stderr + session.stdout)
                executions = []
                for row in producer['rows']:
                    if row['event'] != 'UserPromptSubmit':
                        continue
                    trace = evidence / (row['role'] + '.exec')
                    actual = subprocess.run(['strace', '-f', '-q', '-s', '8192', '-e', 'trace=execve', '-o', str(trace), *row['argv']],
                        input=json.dumps(dict(hook_event_name='UserPromptSubmit', session_id=native, cwd=str(work), prompt='owned process fixture')).encode(),
                        capture_output=True, timeout=30, cwd=work,
                        env=dict(os.environ, CAIRN_LIFECYCLE_DISABLED='1', CAIRN_COORDINATION_DISABLED='1'))
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    raw = trace.read_text()
                    reached = [e for e in successful_execs(raw) if e['path'] == row['argv'][0] and e['argv'] == row['argv']]
                    self.assertEqual(len(reached), 1, raw)
                    execution = reached[0]
                    self.assertEqual(re.findall(r'^' + execution['pid'] + r'\s+\+\+\+ exited with (\d+) \+\+\+$', raw, re.M), ['0'])
                    executions.append(dict(Role=row['role'], Event=row['event'], Program=material_pin(Path(execution['path'])),
                        Launcher=material_pin(Path(execution['path'])), Argv=execution['argv'], PID=int(execution['pid']), Exit=actual.returncode))
                settings = json.loads(Path(staged['settings']['path']).read_bytes())
                readback = dict(Schema='cairn.watch-claude-readback/1', Harness='claude', Layer='flagSettings',
                    NativeSession=native, Settings=dict(Path=staged['settings']['path'], SHA256=staged['settings']['sha256']),
                    Shell=material_pin(Path('/bin/sh')), Effective=settings,
                    Listing=[dict(Event=row['event'], Command=row['hook']['command'], Source='flagSettings') for row in producer['rows']],
                    Executions=executions)
                f = SimpleNamespace(root=root / number, account=account, coord=coord, memory=memory, coord_path=config, binding=Path(package['binding']['path']))
                catalog, proposal, selected = collector.catalog_for(f, (staged, None, producer), native, work, readback, evidence)
                catalogs.append(catalog)
                proposals.append(proposal)
                selected_inputs.append((package, selected))
            config_root = root / 'selected'
            config_root.mkdir(mode=0o700)
            for number, catalog, proposal, (_, selected) in zip(('one', 'two'), catalogs, proposals, selected_inputs):
                old = Path(catalog['Selections'][0]['Directory'])
                new = config_root / number
                old.rename(new)
                selected['Config']['Path'] = str(new / 'one.json')
                catalog['HookSelections'] = [collector.write(root / number / 'census-hooks.json', selected)]
                catalog['Selections'][0]['Directory'] = str(new)
                proposal['Bindings'] = catalog['Selections']
            catalog = dict(catalogs[0], Root=str(root), ConfigRoot=str(config_root), CallbackRoot=str(root / 'callbacks'))
            (root / 'callbacks').mkdir(mode=0o700)
            for field in ('Selections', 'Sessions', 'Bindings', 'CredentialPaths', 'HookSelections'):
                catalog[field] = [item for part in catalogs for item in part[field]]
            proposal = dict(Bindings=catalog['Selections'], Sessions=[p['Path'] for p in catalog['Sessions']], Callbacks=[])
            f = SimpleNamespace(root=root, account=Path(selected_inputs[0][1]['Account']), coord=coord, memory=memory)
            evidence = Path(observation_root)
            result = collector.EmittedCensus(self.census, f, catalog, proposal, evidence, 'public-reader').inspect(True, '')
            self.assertEqual(len(result['Census']['Members']), 2)
            self.assertEqual(len(result['Census']['HookObservations']), 2)
            for package, _ in selected_inputs:
                for profile in package['profiles'].values():
                    self.assertEqual(result['Census']['Facts']['file:' + profile['path']], profile['sha256'])
            # Refusals run the same public entry with otherwise complete
            # material, not a helper call or missing-fixture error.
            first = selected_inputs[0][1]
            hook_selection_path = Path(catalog['HookSelections'][0]['Path'])
            hook_manifest_path = Path(first['Generation']['Path'])
            metadata_path = hook_manifest_path.parent / 'hook-selection.json'
            lifecycle_manifest_path = Path(json.loads(metadata_path.read_bytes())['lifecycle_selection']['path'])
            package_path = lifecycle_manifest_path.parent / 'retained-package.json'
            readback_path = Path(first['Readback']['Path'])
            source_path = root / 'one' / 'installed' / 'other' / 'native_session.py'
            closure_path = root / 'one' / 'installed' / 'hotfix' / 'coordination.py'
            originals = {path: (path.read_bytes(), path.stat().st_mode & 0o777)
                         for path in (hook_selection_path, hook_manifest_path, metadata_path, readback_path, source_path, closure_path,
                                      lifecycle_manifest_path, package_path)}

            def repin_metadata(selected, metadata):
                metadata_path.chmod(0o600)
                metadata_path.write_bytes(packages.encoded(metadata))
                metadata_path.chmod(0o400)
                manifest = json.loads(hook_manifest_path.read_bytes())
                for file in manifest['files']:
                    if file['name'] == metadata_path.name:
                        file.update(sha256=packages.sha(metadata_path.read_bytes()), bytes=metadata_path.stat().st_size)
                hook_manifest_path.chmod(0o600)
                hook_manifest_path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode())
                hook_manifest_path.chmod(0o400)
                selected['Generation'] = material_pin(hook_manifest_path)

            for mutation, reason in (
                    ('wrong-package', 'association/evidence'), ('wrong-member', 'association/evidence'),
                    ('observed-script', 'observed program/literal argv'), ('closure', 'pin changed'),
                    ('digest', 'association/evidence'), ('binding', 'association/evidence'),
                    ('source', 'pin changed'), ('unselected-package', 'private directory'),
                    ('runtime-closure', 'association/evidence'), ('runtime-script', 'association/evidence'),
                    ('external-coverage', 'only for a disposable fixture')):
                with self.subTest(mutation=mutation):
                    try:
                        changed = copy.deepcopy(catalog)
                        selected = copy.deepcopy(first)
                        metadata = json.loads(metadata_path.read_bytes())
                        if mutation == 'wrong-package':
                            metadata['retained_python']['package'] = dict(path=selected_inputs[1][1]['Generation']['Path'], sha256=selected_inputs[1][1]['Generation']['SHA256'])
                            repin_metadata(selected, metadata)
                        elif mutation == 'wrong-member':
                            metadata['retained_python']['member'] = 'foreign-member'
                            repin_metadata(selected, metadata)
                        elif mutation == 'observed-script':
                            observed = json.loads(readback_path.read_bytes())
                            observed['Executions'][0]['Argv'][3] = '/unselected/program.py'
                            selected['Readback'] = collector.write(readback_path, observed)
                        elif mutation in ('closure', 'source'):
                            path = closure_path if mutation == 'closure' else source_path
                            path.write_bytes(path.read_bytes() + b'\n# changed after selected pins\n')
                        elif mutation == 'digest':
                            selected['Config']['SHA256'] = '0' * 64
                        elif mutation == 'binding':
                            changed['Bindings'][0]['SHA256'] = '0' * 64
                        elif mutation in ('runtime-closure', 'runtime-script'):
                            package = json.loads(package_path.read_bytes())
                            runtime = package['members']['claude-one']['runtime']['coordination']
                            if mutation == 'runtime-closure':
                                runtime['closure'] = str(root / 'one' / 'installed' / 'base')
                            else:
                                runtime['entry'] = package_pin(root / 'one' / 'installed' / 'base' / 'coordination.py')
                            package_path.chmod(0o600)
                            package_path.write_bytes(packages.encoded(package))
                            package_path.chmod(0o400)
                            manifest = json.loads(lifecycle_manifest_path.read_bytes())
                            for file in manifest['files']:
                                if file['name'] == package_path.name:
                                    file.update(sha256=packages.sha(package_path.read_bytes()), bytes=package_path.stat().st_size)
                            lifecycle_manifest_path.chmod(0o600)
                            lifecycle_manifest_path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode())
                            lifecycle_manifest_path.chmod(0o400)
                            pin = package_pin(lifecycle_manifest_path)
                            metadata['lifecycle_selection'] = pin
                            metadata['retained_python']['package'] = pin
                            repin_metadata(selected, metadata)
                        elif mutation == 'unselected-package':
                            # No explicit authenticated association: retain the
                            # original strict profile parent/duplicate policy.
                            changed['HookSelections'] = []
                        elif mutation == 'external-coverage':
                            changed['Scope'] = 'external-unknown'
                        if changed['HookSelections']:
                            changed['HookSelections'][0] = collector.write(hook_selection_path, selected)
                        refusal = collector.EmittedCensus(self.census, f, changed, proposal, evidence, mutation).inspect(False, reason)
                        self.assertFalse(refusal['Census']['FixtureReady'])
                        self.assertTrue(refusal['Error'])
                        for credential in catalog['CredentialPaths']:
                            self.assertNotIn('file:' + credential, refusal['Census']['Facts'])
                    finally:
                        for path, (raw, mode) in originals.items():
                            path.chmod(0o600)
                            path.write_bytes(raw)
                            path.chmod(mode)
            destination = os.environ.get('CAIRN_NATIVE_READER_EVIDENCE')
            if destination:
                target = Path(destination)
                target.mkdir(mode=0o700)
                shutil.copytree(root, target / 'fixture')
                shutil.copytree(evidence, target / 'observations')
                collector.write(target / 'boundary.json', dict(original_root=str(root), native_client=False,
                    listing='declared fixture input, not a native observation',
                    executions='actual strace execve/exit under existing lifecycle/coordination disable guards',
                    authenticated_registration=False, production=False, watcher_runs=0))
