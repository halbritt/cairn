"""Producer/strict-reader packages in disposable trees; no installed host action."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid

import stage_lifecycle_compatibility as product

ROOT = Path(__file__).resolve().parents[1]
MALFORMED_EVIDENCE = ('literal-null', 'literal-int', 'literal-list', 'literal-dict', 'provenance-list',
                      'variant-list', 'declared-profile-list', 'actual-binding-int', 'role-list', 'variants-list', 'native-roles-list')


def pin(path):
    return dict(path=str(path), sha256=product.sha(path.read_bytes()))


def write(path, value):
    private_directory(path.parent)
    path.write_bytes(value if isinstance(value, bytes) else product.encoded(value))
    path.chmod(0o600)
    return pin(path)


def private_directory(path):
    if path.exists():
        return
    private_directory(path.parent)
    path.mkdir(mode=0o700)


def fixture(root, host='proximal', harness='codex', count=3, source_sets=None, sidecar_sets=None, client=None):
    root.mkdir(mode=0o700)
    source_root = root.parent / (root.name + '-external-code') if harness == 'hermes' else root / 'installed'
    profile_root = root.parent / (root.name + '-external-profiles') if harness == 'hermes' else root / 'profiles'
    closures = {}
    for label in ('base', 'hotfix', 'other'):
        directory = source_root / label
        files = {}
        for name in product.RETAINED_MEMBERS:
            raw = source_sets[label][name] if source_sets else (ROOT / 'integrations/lifecycle' / name).read_bytes()
            if not source_sets and label != 'base' and name in ('coordination.py', 'native_session.py'):
                raw += ('\n# fixture complete closure: ' + label + '\n').encode()
            files[name] = write(directory / name, raw)['sha256']
        bundle = dict(schema='cairn.lifecycle-bundle/1', files=files, sha256=product.sha(product.encoded(files)))
        # Captured sidecars retain their exact bytes. Other runs explicitly use
        # generated fixture sidecars, without claiming an installed-host capture.
        body = sidecar_sets[label] if sidecar_sets else bundle
        closures[str(directory)] = bundle | dict(manifest=write(directory / 'lifecycle-bundle.json', body))
    base, hotfix, other = (str(source_root / name) for name in ('base', 'hotfix', 'other'))
    names = ['codex-one', 'codex-two', 'codex-three'] if count == 3 else [harness + '-one']
    token = root / 'synthetic.token'
    write(token, b'synthetic fixture credential; must not be read\n')
    binding_path = root / 'original' / 'binding.json'
    common = dict(harness=harness, cairn=str(client or root / 'unused-client'), socket=str(root / 'absent.sock'),
                  token_file=str(token), repo='fixture:retained-package', inbox_recall_binding=str(binding_path))
    common.update(recall_mode='agent_tools', native_delivery=True, context_bytes=6000)
    coordination_only = harness == 'agy' or host == 'archon'
    if harness not in ('codex', 'claude') or coordination_only:
        del common['inbox_recall_binding']
    profiles, material = {}, {}
    memory = dict(absent=True)
    if not coordination_only:
        memory_body = product.encoded(common | dict(state_dir=str(root / 'memory-state'), opaque={'number': 9007199254740993123456789}))
        memory = write(profile_root / 'memory.json', b'{"opaque":0,' + memory_body[1:])
        profiles['memory.json'] = memory
        material['memory.json'] = memory
    coords = []
    for name in names:
        home = root / 'accounts' / name
        private_directory(home)
        coord = write(profile_root / (name + '.json'), common | dict(binding='fixture-' + name, config_home=str(home), state_dir=str(root / ('state-' + name))))
        profiles['coordination-' + name + '.json'] = coord
        material['coordination-' + name + '.json'] = coord
        coords.append(coord)
    base_bundle = {key: closures[base][key] for key in ('schema', 'sha256', 'files')}
    binding = dict(schema='cairn.inbox-recall-binding/2', enabled=False, memory_config=memory,
                   coordination_configs=coords, engine=pin(Path(base) / 'memory.py') | dict(bundle=base_bundle),
                   bridge=pin(Path(base) / 'inbox_recall.py') | dict(bundle=base_bundle))
    original_binding = write(binding_path, binding)
    material['binding.json'] = original_binding
    material['lifecycle-bundle.json'] = write(root / 'material' / 'lifecycle-bundle.json', sidecar_sets['base'] if sidecar_sets else base_bundle)
    for name in product.RETAINED_MEMBERS:
        material[name] = pin(Path(base) / name)
    alias = write(Path(other) / 'lifecycle.py', (Path(other) / 'memory.py').read_bytes())
    delegate = pin(Path(hotfix) / 'coordination.py')
    facade = write(root / 'installed' / 'coordination' / 'coordination.py', product.package_facade(delegate['path']))
    if host == 'talos':
        # Original controlled profiles, closures, sidecars and runtime entries admit owned 0755 parents.
        for parent_dir in (profile_root, Path(base), Path(hotfix), Path(other), Path(facade['path']).parent):
            parent_dir.chmod(0o755)
    members = {}
    for name, coord in zip(names, coords):
        argv = dict(coordination=[sys.executable, facade['path'], 'hook', '--config', coord['path']])
        runtime = dict(coordination=dict(entry=facade, delegate=delegate, closure=hotfix))
        if not coordination_only:
            argv['memory'] = [sys.executable, '-I', '-B', alias['path'], '--config', memory['path']]
            runtime['memory'] = dict(entry=alias, delegate=None, closure=other)
        members[name] = dict(config=coord, argv=argv, runtime=runtime)
        material['argv-' + name + '.json'] = write(root / 'material' / ('argv-' + name + '.json'), argv)
    native = dict(category='LITERAL', id=host + '/cohort', members=[host + '/' + name for name in names],
                  retained_reverse_staged=False, roles=dict(
                      memory=dict(category='ABSENT') if coordination_only else dict(one_shared_literal_command=shlex.join(members[names[0]]['argv']['memory'])),
                      coordination=dict(member_variants={name: dict(declared_profile=member['config'], literal_commands=[
                          dict(literal_command_text=shlex.join(member['argv']['coordination']))]) for name, member in members.items()})))
    if harness == 'hermes':
        native['roles']['memory'] = dict(provider='inprocess', actual_internal_engine_derived_argv=members[names[0]]['argv']['memory'])
    provenance = dict(schema='fixture-retained-provenance/1', members=names, native_representation=native,
                      actual_native_command_text_rewritten=False, original_profile_bytes_rewritten=False,
                      source17_bytes_rewritten=False, source17_canonicalization_into_native_argv=False,
                      profile_correspondence=[dict(generation_name=name, original_path=p['path'], sha256=p['sha256']) for name, p in profiles.items()])
    material['native-representation.json'] = write(root / 'material' / 'native-representation.json', native)
    material['provenance.json'] = write(root / 'material' / 'provenance.json', provenance)
    material['retained-argv.json'] = write(root / 'material' / 'retained-argv.json', members[names[0]]['argv'])
    evidence = {name: dict(sha256=p['sha256'], members=sorted(names) if name in ('native-representation.json', 'provenance.json') else [name[5:-5]])
                for name, p in material.items() if name.startswith('argv-') or name in ('native-representation.json', 'provenance.json')}
    package = dict(schema=product.RETAINED_PACKAGE_SCHEMA, host=host, harness=harness, multiplicity=count,
                   primary=names[0], binding=original_binding, profiles=profiles, closures=closures,
                   members=members, evidence=evidence, files={name: p['sha256'] for name, p in material.items()})
    return dict(id=str(uuid.uuid4()), package=package, material=material)


def mutate(plan, kind, root):
    p = plan['package']
    first = p['primary']
    if kind == 'none':
        return
    if kind in MALFORMED_EVIDENCE:
        native = json.loads(Path(plan['material']['native-representation.json']['path']).read_bytes())
        provenance = json.loads(Path(plan['material']['provenance.json']['path']).read_bytes())
        variants = native['roles']['coordination']['member_variants']
        if kind.startswith('literal-'):
            variants[first]['literal_commands'][0]['literal_command_text'] = {'literal-null': None, 'literal-int': 7, 'literal-list': ['python'], 'literal-dict': {'command': 'python'}}[kind]
        elif kind == 'variant-list':
            variants[first] = []
        elif kind == 'declared-profile-list':
            variants[first]['declared_profile'] = []
        elif kind == 'actual-binding-int':
            variants[first]['actual_binding'] = 7
        elif kind == 'role-list':
            native['roles']['coordination'] = []
        elif kind == 'variants-list':
            native['roles']['coordination']['member_variants'] = []
        elif kind == 'native-roles-list':
            native['roles'] = []
        provenance['native_representation'] = native
        if kind == 'provenance-list':
            provenance = []
        for name, body in (('native-representation.json', native), ('provenance.json', provenance)):
            plan['material'][name] = write(Path(plan['material'][name]['path']), body)
            p['files'][name] = p['evidence'][name]['sha256'] = plan['material'][name]['sha256']
        return
    if kind in ('extra', 'declared-extra'):
        extra = write(root / 'material' / 'extra.json', dict(unselected=True))
        plan['material']['extra.json'] = extra
        if kind == 'declared-extra':
            p['files']['extra.json'] = extra['sha256']
    elif kind == 'executable-data':
        plan['material']['retained-argv.json']['executable'] = True
    elif kind == 'evidence-digest':
        p['evidence']['native-representation.json']['sha256'] = '0' * 64
    elif kind == 'wrong-closure':
        p['members'][first]['runtime']['coordination']['closure'] = str(root / 'installed' / 'base')
    elif kind == 'wrong-alias-root':
        # Equal entry bytes cannot select another adjacent loader/sidecar.
        p['members'][first]['runtime']['memory']['closure'] = str(root / 'installed' / 'base')
    elif kind == 'missing-closure-member':
        closure = p['closures'][str(root / 'installed' / 'hotfix')]
        del closure['files']['native_session.py']
        closure['sha256'] = product.sha(product.encoded(closure['files']))
    elif kind == 'wrong-delegate':
        p['members'][first]['runtime']['coordination']['delegate'] = pin(root / 'installed' / 'base' / 'coordination.py')
    elif kind == 'wrong-argv':
        p['members'][first]['argv']['coordination'][1] = str(root / 'installed' / 'base' / 'coordination.py')
    elif kind == 'unsupported-multiplicity':
        p['multiplicity'] = 2
    elif kind == 'wrong-binding':
        p['binding'] = write(root / 'original' / 'other-binding.json', Path(p['binding']['path']).read_bytes())
    elif kind == 'source-changed':
        source = root / 'installed' / 'other' / 'native_session.py'
        source.write_bytes(source.read_bytes() + b'\n# changed after pins\n')
    elif kind == 'source-parent-writable':
        (root / 'installed' / 'other').chmod(0o775)
    elif kind == 'generated-parent-not-private':
        (root / 'original').chmod(0o755)
    elif kind == 'profile-parent-writable':
        (root / 'profiles').chmod(0o775)
    elif kind == 'source-hardlink':
        source = root / 'installed' / 'other' / 'native_session.py'
        os.link(source, source.with_name('unselected-alias.py'))
    elif kind == 'source-special-mode':
        (root / 'installed' / 'other' / 'native_session.py').chmod(0o4600)
    elif kind == 'member-mismatch':
        second = next(name for name in p['members'] if name != first)
        p['members'][second]['config'] = p['members'][first]['config']
    elif kind == 'evidence-disagreement':
        name = 'argv-' + first + '.json'
        changed = copy.deepcopy(p['members'][first]['argv'])
        changed['coordination'][-1] = str(root / 'foreign-profile.json')
        plan['material'][name] = write(Path(plan['material'][name]['path']), changed)
        p['files'][name] = p['evidence'][name]['sha256'] = plan['material'][name]['sha256']
    elif kind == 'native-member-disagreement':
        path = Path(plan['material']['native-representation.json']['path'])
        native = json.loads(path.read_text())
        native['roles']['coordination']['member_variants'][first]['literal_commands'][0]['literal_command_text'] = 'python /foreign'
        plan['material']['native-representation.json'] = write(path, native)
        provenance_path = Path(plan['material']['provenance.json']['path'])
        provenance = json.loads(provenance_path.read_text())
        provenance['native_representation'] = native
        plan['material']['provenance.json'] = write(provenance_path, provenance)
        for name in ('native-representation.json', 'provenance.json'):
            p['files'][name] = p['evidence'][name]['sha256'] = plan['material'][name]['sha256']
    elif kind == 'native-profile-disagreement':
        path = Path(plan['material']['native-representation.json']['path'])
        native = json.loads(path.read_bytes())
        other = next(member for name, member in p['members'].items() if name != first)
        native['roles']['coordination']['member_variants'][first]['declared_profile'] = other['config']
        provenance_path = Path(plan['material']['provenance.json']['path'])
        provenance = json.loads(provenance_path.read_bytes())
        provenance['native_representation'] = native
        for name, body in (('native-representation.json', native), ('provenance.json', provenance)):
            plan['material'][name] = write(Path(plan['material'][name]['path']), body)
            p['files'][name] = p['evidence'][name]['sha256'] = plan['material'][name]['sha256']
    elif kind == 'missing-runtime-field':
        del p['members'][first]['runtime']['memory']['delegate']
    elif kind == 'missing-sidecar':
        del p['closures'][str(root / 'installed' / 'hotfix')]['manifest']
    elif kind == 'sidecar-changed':
        path = root / 'installed' / 'hotfix' / 'lifecycle-bundle.json'
        path.write_bytes(path.read_bytes() + b' ')
    elif kind == 'sidecar-map-disagreement':
        closure = p['closures'][str(root / 'installed' / 'hotfix')]
        body = json.loads(Path(closure['manifest']['path']).read_bytes())
        body['files']['memory.py'] = '0' * 64
        closure['manifest'] = write(Path(closure['manifest']['path']), body)
    else:
        raise AssertionError(kind)


def unchecked_generation(stager, root, plan):
    # Authenticated wrapper around deliberately invalid test data, never a producer substitute.
    package_path = root / 'material' / 'retained-package.json'
    write(package_path, plan['package'])
    files = []
    for name, p in plan['material'].items():
        copied = write(root / 'adversarial-material' / name, Path(p['path']).read_bytes())
        files.append(dict(name=name, **copied, executable=p.get('executable', False)))
    files.append(dict(name='retained-package.json', **pin(package_path)))
    (root / 'adversarial-generations').mkdir(mode=0o700)
    return product.backend(str(stager), ['generation', '--root', str(root / 'adversarial-generations')], dict(id=plan['id'], files=files))


CASES = ['none', 'extra', 'declared-extra', 'evidence-digest', 'wrong-closure', 'missing-closure-member',
         'wrong-delegate', 'wrong-argv', 'unsupported-multiplicity', 'wrong-binding', 'source-changed',
         'member-mismatch', 'evidence-disagreement', 'native-member-disagreement', 'missing-runtime-field',
         'missing-sidecar', 'sidecar-changed', 'sidecar-map-disagreement', 'native-profile-disagreement', 'wrong-alias-root', 'executable-data',
         'source-parent-writable', 'generated-parent-not-private', 'profile-parent-writable', 'source-hardlink', 'source-special-mode']
CASES += list(MALFORMED_EVIDENCE)


def parity_cases(stager, root, source_sets=None, sidecar_sets=None):
    results = []
    specifications = [('proximal', 'codex', 3, kind) for kind in CASES]
    specifications += [('archon', 'codex', 1, 'none'), ('talos', 'opencode', 1, 'none')]
    specifications += [('proximal', harness, 1, 'none') for harness in ('claude', 'agy', 'hermes', 'opencode')]
    if sidecar_sets:
        specifications.append(('proximal', 'codex', 3, 'canonical-sidecar-other-closure'))
    for index, (host, harness, count, kind) in enumerate(specifications):
        base = root / (str(index) + '-' + host + '-' + kind)
        plan = fixture(base, host, harness, count, source_sets, sidecar_sets)
        if kind == 'canonical-sidecar-other-closure':
            # Actual canonical sidecar bytes cannot attest to a hotfixed closure.
            closure = plan['package']['closures'][str(base / 'installed' / 'base')]
            closure['manifest'] = write(Path(closure['manifest']['path']), sidecar_sets['canonical'])
        else:
            mutate(plan, kind, base)
        generations = base / 'generations'
        generations.mkdir(mode=0o700)
        producer = True
        try:
            selected = product.stage_retained_package(str(stager), generations, plan)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            producer = False
            if list(generations.iterdir()):
                raise AssertionError('refusing producer published material')
            selected = unchecked_generation(stager, base, plan)
        accepted, inspected = True, []
        try:
            for number in range(count):
                inspected.append(product.inspect_retained(str(stager), selected, number))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            accepted = False
        expected = kind == 'none'
        if producer != expected or accepted != expected:
            raise AssertionError((host, kind, producer, accepted, expected))
        members = []
        for name, member in plan['package']['members'].items():
            parent = Path(selected['path']).parent
            members.append(dict(name=name, config=member['config'], binding=plan['package']['binding'],
                                argv=pin(parent / ('argv-' + name + '.json')),
                                proof=next((row['retained_python'] for row in inspected if row['launchers'] == member['argv']), {})))
        results.append(dict(name=host + '/' + harness + '/' + kind, root=str(base), expected=expected, producer=producer,
                            python=accepted, selection=selected, members=members))
    return results


class RetainedPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='cairn-retained-package-tools-')
        cls.stager = Path(cls.build.name) / 'stager'
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(cls.stager), './cmd/cairn-stage-lifecycle'], cwd=ROOT, check=True)
        cls.stager.chmod(0o500)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def test_real_shape_producer_and_python_parity(self):
        with tempfile.TemporaryDirectory(prefix='cairn-retained-package-', dir='/dev/shm') as temporary:
            results = parity_cases(self.stager, Path(temporary))
            self.assertEqual(len(results), len(CASES) + 6)
            self.assertEqual(sum(case['expected'] for case in results), 7)

    def test_shared_member_hook_selection_keeps_each_original_vector(self):
        import stage_lifecycle_hooks as hooks
        with tempfile.TemporaryDirectory(prefix='cairn-retained-hooks-', dir='/dev/shm') as temporary:
            root = Path(temporary)
            plan = fixture(root / 'installed')
            generations = root / 'generations'
            generations.mkdir(mode=0o700)
            selected = product.stage_retained_package(str(self.stager), generations, plan)
            before = {p: Path(p).read_bytes() for p in [plan['package']['binding']['path'], *[p['path'] for p in plan['package']['profiles'].values()]]}
            for index, (name, member) in enumerate(plan['package']['members'].items()):
                account = json.loads(Path(member['config']['path']).read_bytes())['config_home']
                settings = write(Path(account) / 'hooks.json', dict(opaque='original'))
                hook_root = root / ('hooks-' + name)
                hook_root.mkdir(mode=0o700)
                hook_plan = dict(id=str(uuid.uuid4()), selection=selected, harness='codex', account_home=account,
                                 settings=settings, coordination_index=index, legacy=True,
                                 replace_commands=dict(memory=[], coordination=[]))
                result = hooks.stage(str(self.stager), hook_root, hook_plan)
                metadata = json.loads((Path(result['selection']['path']).parent / 'hook-selection.json').read_bytes())
                self.assertEqual(metadata['retained_python']['member'], name)
                for row in metadata['rows']:
                    self.assertEqual(row['argv'], member['argv'][row['role']])
                    self.assertEqual(row['implementation'], 'python')
                self.assertEqual(Path(settings['path']).read_bytes(), product.encoded(dict(opaque='original')))
            self.assertEqual({p: Path(p).read_bytes() for p in before}, before)
            for index in (-1, 3, True):
                with self.assertRaises(ValueError):
                    product.inspect_retained(str(self.stager), selected, index)

    def test_public_stage_and_legacy_inspect_cli_and_refusal(self):
        with tempfile.TemporaryDirectory(prefix='cairn-retained-cli-', dir='/dev/shm') as temporary:
            root = Path(temporary)
            plan = fixture(root / 'original', harness='claude', count=1)
            generations = root / 'generations'
            generations.mkdir(mode=0o700)
            cli = [sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/stage_lifecycle_compatibility.py'), '--stager', str(self.stager)]
            staged = subprocess.run([*cli, 'stage-retained', '--root', str(generations)], input=product.encoded(plan), capture_output=True, timeout=40)
            self.assertEqual(staged.returncode, 0, staged.stderr)
            selected = json.loads(staged.stdout)['selection']
            inspected = subprocess.run([*cli, 'inspect', '--legacy', '--path', selected['path'], '--sha256', selected['sha256']], capture_output=True, timeout=40)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            self.assertEqual(json.loads(staged.stdout), json.loads(inspected.stdout))
            sidecar = root / 'original' / 'installed' / 'hotfix' / 'lifecycle-bundle.json'
            sidecar.write_bytes(sidecar.read_bytes() + b' ')
            changed = subprocess.run([*cli, 'inspect', '--legacy', '--path', selected['path'], '--sha256', selected['sha256']], capture_output=True, timeout=40)
            self.assertEqual((changed.returncode, changed.stdout), (1, b''))
            self.assertNotIn(b'Traceback', changed.stderr)
            refused_root = root / 'refused'
            refused_root.mkdir(mode=0o700)
            plan['package']['evidence']['native-representation.json']['sha256'] = '0' * 64
            refused = subprocess.run([*cli, 'stage-retained', '--root', str(refused_root)], input=product.encoded(plan), capture_output=True, timeout=40)
            self.assertEqual((refused.returncode, refused.stdout), (1, b''))
            self.assertNotIn(b'Traceback', refused.stderr)
            self.assertEqual(list(refused_root.iterdir()), [])

    def test_malformed_evidence_public_stage_and_inspect_refuse_without_effects(self):
        cli = [sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/stage_lifecycle_compatibility.py'), '--stager', str(self.stager)]
        with tempfile.TemporaryDirectory(prefix='retained-malformed-', dir='/dev/shm') as temporary:
            for kind in MALFORMED_EVIDENCE:
                with self.subTest(kind=kind):
                    root = Path(temporary) / kind
                    plan = fixture(root)
                    mutate(plan, kind, root)
                    generations = root / 'generations'
                    generations.mkdir(mode=0o700)
                    with self.assertRaises(ValueError):
                        product.stage_retained_package(str(self.stager), generations, plan)
                    self.assertEqual(list(generations.iterdir()), [])
                    result = subprocess.run([*cli, 'stage-retained', '--root', str(generations)], input=product.encoded(plan), capture_output=True, timeout=40)
                    self.assertEqual((result.returncode, result.stdout), (1, b''), result.stderr)
                    self.assertNotIn(b'Traceback', result.stderr)
                    self.assertEqual(list(generations.iterdir()), [])
                    selected = unchecked_generation(self.stager, root, plan)
                    selected_root = Path(selected['path']).parent
                    before = {str(path): (path.read_bytes(), path.stat().st_mode, path.stat().st_ino)
                              for path in selected_root.rglob('*') if path.is_file()}
                    result = subprocess.run([*cli, 'inspect', '--legacy', '--path', selected['path'], '--sha256', selected['sha256']], capture_output=True, timeout=40)
                    self.assertEqual((result.returncode, result.stdout), (1, b''), result.stderr)
                    self.assertNotIn(b'Traceback', result.stderr)
                    self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mode, path.stat().st_ino)
                                              for path in selected_root.rglob('*') if path.is_file()})


if __name__ == '__main__':
    if '--fixtures' in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument('--fixtures', required=True)
        parser.add_argument('--stager', required=True)
        parser.add_argument('--source-sets')
        parser.add_argument('--source-sidecars')
        args = parser.parse_args()
        source_sets = None
        sidecar_sets = None
        if args.source_sets:
            selected = product.decoded(Path(args.source_sets).read_bytes())
            product.package_fields(selected, ('base', 'hotfix', 'other'))
            source_sets = {}
            for label, members in selected.items():
                if set(members) != product.RETAINED_MEMBERS:
                    raise ValueError('fixture source set must be a complete explicit closure')
                source_sets[label] = {name: product.read_selected(descriptor, 1024 * 1024) for name, descriptor in members.items()}
        if args.source_sidecars:
            selected = product.decoded(Path(args.source_sidecars).read_bytes())
            product.package_fields(selected, ('base', 'hotfix', 'other', 'canonical'))
            sidecar_sets = {name: product.read_selected(descriptor, 1024 * 1024) for name, descriptor in selected.items()}
        sys.stdout.write(json.dumps(parity_cases(Path(args.stager), Path(args.fixtures), source_sets, sidecar_sets)))
    else:
        unittest.main()
