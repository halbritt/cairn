#!/usr/bin/env python3
"""Selected emitted Claude settings/readback consumed by the finite Go census.

Run only inside test_hybrid_runtime_operations.py, with the selected frozen
client and current private Go images. This is a new bounded census cell; it
does not replace or replay the existing eleven-case R04 collector.
"""
import argparse
import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from check_r04_native_claude import ClaudeSession, MessagesFixture, NativeFixture
from check_r04_native_opencode import native_api_fixture, successful_execs, wait_for
from check_r06_unit_census import Fixture as CensusFixture, process_ref, require, sha, write
from test_f04_activation import encoded, overwrite_fixture
from test_r04_host_wiring import pin


def selected_pin(path):
    return dict(Path=str(path), SHA256=sha(path))


def private_directories(root):
    root.chmod(0o700)
    for path in root.rglob('*'):
        if path.is_dir():
            path.chmod(0o700)


def layer_files(account, work):
    return [selected_pin(path) for root in (account, work)
            for path in sorted(root.rglob('*')) if path.is_file()]


def preserve_producer(f, selection, output, label):
    target = output / ('producer-' + label)
    target.mkdir(mode=0o700)
    for name in ('manifest.json', 'hook-selection.json', 'hook-settings.json', 'previous-settings.json'):
        source = Path(selection[0]['selection']['path']).parent / name
        shutil.copyfile(source, target / name)
    lifecycle = Path(selection[2]['lifecycle_selection']['path']).parent
    for name in ('manifest.json', 'selection.json', 'receipt.json', 'binding.json', 'memory.json', 'coordination-0.json'):
        shutil.copyfile(lifecycle / name, target / ('lifecycle-' + name))
    launchers = target / 'launchers'
    launchers.mkdir(mode=0o700)
    for row in selection[2]['rows']:
        shutil.copyfile(Path(row['argv'][0]), launchers / (row['role'] + '-' + row['event']))
    write(target / 'selection-result.json', selection[0])


def native_observation(client, selection, output, label):
    settings = client.control('get_settings')
    listing = client.control('get_hooks_listing')
    expected = json.loads(Path(selection[0]['settings']['path']).read_bytes())
    require(settings['effective']['hooks'] == expected['hooks'], 'native selected settings mismatch')
    start = client.trace.stat().st_size
    response = client.prompt('R06 selected hook census ' + label)
    require(response.get('subtype') == 'success' and not response.get('is_error'), 'native fixture prompt failed')
    require(not [e for e in client.events if e.get('subtype') == 'hook_response' and (e.get('exit_code') != 0 or e.get('outcome') != 'success')], 'native startup/prompt hook failed')
    rows = [r for r in selection[2]['rows'] if r['event'] == 'UserPromptSubmit']

    def observed():
        trace = client.trace.read_text()[start:]
        executions = list(successful_execs(trace))
        reached = []
        for row in rows:
            launcher = row['argv'][0]
            issued = [e for e in executions if e['path'] == launcher and e['argv'] == row['argv']]
            vector = shlex.split(Path(launcher).read_text().split('\n', 1)[1])[1:]
            require(vector[-2:] == ['--', '$@'], 'unexpected emitted launcher frame')
            vector = vector[:-2]
            actual = [e for e in executions if e['pid'] in {x['pid'] for x in issued}
                      and e['path'] == vector[0] and e['argv'] == vector + ['--'] + row['argv'][1:]]
            if not actual:
                return None
            for execution in actual:
                exits = re.findall(r'^' + execution['pid'] + r'\s+\+\+\+ exited with (\d+) \+\+\+$', trace, re.M)
                if not exits:
                    return None
                require(exits == ['0'], 'native selected hook process failed: ' + repr(exits))
                reached.append(dict(Role=row['role'], Event=row['event'], Launcher=selected_pin(Path(launcher)),
                                    Program=selected_pin(Path(vector[0])), Argv=execution['argv'],
                                    PID=int(execution['pid']), Exit=0))
        require({e['Role'] for e in reached} == {'memory', 'coordination'}, 'native prompt role membership changed')
        return reached

    wait_for(observed, 'selected native prompt hooks exited')
    # wait_for is an assertion helper; collect the now-complete value explicitly.
    reached = observed()
    require(reached is not None, 'native execution disappeared')
    (output / (label + '.exec')).write_text(client.trace.read_text()[start:])
    view = dict(Schema='cairn.watch-claude-readback/1', Harness='claude', Layer='flagSettings',
                NativeSession=client.identity, Settings=dict(Path=selection[0]['settings']['path'],
                                                            SHA256=selection[0]['settings']['sha256']),
                Shell=selected_pin(Path('/bin/sh')), Effective=settings['effective'],
                Listing=[dict(Event=h['event'], Command=h['commandText'], Source=h['source'])
                         for h in listing['hooks']], Executions=reached)
    write(output / (label + '-readback.json'), view)
    return view


def catalog_for(f, selection, identity, work, readback, output):
    root = f.root
    selected = root / 'selected-configs' / 'claude'
    selected.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = selected / 'one.json'
    config.write_bytes(f.coord_path.read_bytes())
    config.chmod(0o400)
    callbacks = root / 'census-callbacks'
    callbacks.mkdir(mode=0o700, exist_ok=True)
    for directory in (Path(f.coord['state_dir']), Path(f.memory['state_dir'])):
        directory.mkdir(mode=0o700, exist_ok=True)
    state_path = Path(f.coord['state_dir']) / (hashlib.sha256((f.coord['binding'] + '\0' + identity).encode()).hexdigest() + '.json')
    require(state_path.is_file(), 'native callback did not produce the selected native-session record')
    state = json.loads(state_path.read_bytes())
    require(state['native_session_id'] == identity, 'actual native record identity differs')
    require(state['agent']['metadata']['workspace'] == str(work), 'actual native record workspace differs')
    sessions = [selected_pin(p) for p in sorted(Path(f.coord['state_dir']).glob('*.json'))]
    private_directories(f.account)
    private_directories(work)
    readback_pin = write(root / 'census-readback.json', readback)
    hook_input = dict(Schema='cairn.watch-claude-hooks/1', Harness='claude', Layer='flagSettings',
                      Account=str(f.account), Workspace=str(work), Config=selected_pin(config),
                      Session=selected_pin(state_path), Generation=dict(Path=selection[0]['selection']['path'],
                                                                     SHA256=selection[0]['selection']['sha256']),
                      Settings=dict(Path=selection[0]['settings']['path'], SHA256=selection[0]['settings']['sha256']),
                      Readback=readback_pin, LayerFiles=layer_files(f.account, work))
    hook_pin = write(root / 'census-hooks.json', hook_input)
    binding = json.loads(f.binding.read_bytes())
    client = Path(f.coord['cairn'])
    catalog = dict(Schema='cairn.watch-unit-catalog/1', Scope='disposable-fixture', Root=str(root),
                   ConfigRoot=str(selected.parent), CallbackRoot=str(callbacks),
                   Selections=[dict(Directory=str(selected), Pins={'one.json': sha(config)})],
                   Sessions=sessions, Callbacks=[], CredentialPaths=[f.coord['token_file']],
                   Bindings=[selected_pin(f.binding)], Clients=[selected_pin(client)], HookSelections=[hook_pin])
    proposal = dict(Bindings=catalog['Selections'], Sessions=[p['Path'] for p in sessions], Callbacks=[])
    write(output / 'emitted-hook-selection.json', selection[2])
    write(output / 'emitted-binding.json', binding)
    custody = output / ('census-inputs-' + readback_pin['SHA256'][:16])
    custody.mkdir(mode=0o700, exist_ok=True)
    write(custody / 'catalog.json', catalog)
    for label, source in [('hooks', root / 'census-hooks.json'), ('readback', root / 'census-readback.json')]:
        if source.is_file():
            shutil.copyfile(source, custody / (label + '.json'))
    for i, item in enumerate(sessions):
        shutil.copyfile(item['Path'], custody / ('native-state-' + str(i) + '.json'))

    return catalog, proposal, hook_input


class EmittedCensus(CensusFixture):
    def __init__(self, image, f, catalog, proposal, output, label):
        self.image, self.universe = image, f.root
        self.evidence, self.name, self.serial = output / label, label, 0
        self.evidence.mkdir(mode=0o700)
        self.catalog_path, self.catalog, self.proposal = f.root / 'census-catalog.json', catalog, proposal


def inspect(image, f, catalog, proposal, output, label, *, ready, reason=''):
    # Reuse the accepted real Go entry, syscall guards and complete protected
    # snapshot checks; this adapter only supplies the emitted/native catalog.
    fixture = EmittedCensus(image, f, catalog, proposal, output, label)
    result = fixture.inspect(ready, reason)
    require(bool(result['Error']) is (not ready), 'census error/disposition disagrees')
    if ready:
        require(len(result['Census']['HookObservations']) == 1, 'selected hook observation absent')
    return dict(case=label, passed=True, ready=ready, error=result['Error'])


def run(args, output):
    support = NativeFixture()
    support.binary, support.stager = args.lifecycle, args.stager
    support.root = output / 'owned'
    support.root.mkdir(mode=0o700)
    clients, observations = [], []
    provider = MessagesFixture()
    support.addCleanup(provider.close)
    try:
        f = support.fixture('installation', harness='claude', bound=True)
        native_api_fixture(f.api)
        support.addCleanup(lambda: write(output / 'cairn-api-calls.json', f.api_calls()))
        selector = f.root / 'null-selector'
        selector.write_text('#!/usr/bin/python3\nimport json,sys\njson.load(sys.stdin)\n'
                            'print(json.dumps({"structured_output":{"checkpoint":None,"workstream":None,"memories":[]}}))\n')
        selector.chmod(0o500)
        f.memory['claude'] = str(selector)
        token = f.root / 'fixture.token'
        f.coord['token_file'] = f.memory['token_file'] = str(token)
        token.write_text('synthetic-fixture-token')
        token.chmod(0o000)
        f.coord.update(process_names=[args.claude.name], process_exe_dirs=[])
        f.publish_configuration(enabled=False)
        original = encoded(dict(alwaysThinkingEnabled=False, permissions=dict(defaultMode='dontAsk')))
        overwrite_fixture(f.settings, original)
        f.hook_plan.update(selection=f.selected, settings=pin(f.settings))
        a = support.selected(f)
        preserve_producer(f, a, output, 'a')
        work = f.root / 'workspace'
        work.mkdir(mode=0o700)

        def start(name, selection, resume=None):
            client = ClaudeSession(args.claude, support.root / name, f.account, Path(selection[0]['settings']['path']),
                                   provider, output, work, resume=resume)
            clients.append(client)
            support.addCleanup(client.close)
            return client

        target = start('native-a', a)
        native_owner_a = process_ref(target.process.pid)
        native_observation(target, a, output, 'selected-a')
        f.publish_configuration(enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected, settings=pin(f.settings))
        b = support.selected(f)
        preserve_producer(f, b, output, 'b')
        cached = native_observation(target, a, output, 'cached-a-after-selected-b')
        target.close()
        require(target.process.returncode == 0, 'native A was not joined')
        catalog, proposal, hook_input = catalog_for(f, b, target.identity, work, cached, output)
        observations.append(inspect(args.census_image, f, catalog, proposal, output, 'cached-a-refused', ready=False, reason='readback'))
        refreshed = start('native-b-resume', b, target.identity)
        native_owner_b = process_ref(refreshed.process.pid)
        require(refreshed.identity == target.identity and refreshed.process.pid != target.process.pid, 'refresh changed conversation or reused process')
        b_view = native_observation(refreshed, b, output, 'selected-b-after-resume')
        refreshed.close()
        require(refreshed.process.returncode == 0, 'native B was not joined')
        catalog, proposal, hook_input = catalog_for(f, b, refreshed.identity, work, b_view, output)
        observations.append(inspect(args.census_image, f, catalog, proposal, output, 'emitted-native-b', ready=True))
        require(f.settings.read_bytes() == original, 'source unrelated settings changed')
        mutations = ('account-hook', 'project-hook', 'receipt', 'config', 'launcher', 'program', 'shell', 'argv', 'missing-readback', 'mixed-unit', 'unsupported-layer')
        for mutation in mutations:
            selected = copy.deepcopy(hook_input)
            observed = copy.deepcopy(b_view)
            changed = None
            saved = None
            reason = ''
            try:
                if mutation in ('account-hook', 'project-hook'):
                    changed = f.account / 'settings.local.json' if mutation == 'account-hook' else work / '.claude' / 'settings.json'
                    write(changed, dict(hooks=dict(UserPromptSubmit=[dict(hooks=[dict(type='command', command='cairn extra')])])) )
                    selected['LayerFiles'] = layer_files(f.account, work)
                    reason = 'hook layer'
                elif mutation in ('receipt', 'config', 'launcher', 'program'):
                    changed = {'receipt': f.receipt_path, 'config': f.coord_path,
                               'launcher': Path(b_view['Executions'][0]['Launcher']['Path']),
                               'program': Path(b_view['Executions'][0]['Program']['Path'])}[mutation]
                    saved = changed.read_bytes()
                    overwrite_fixture(changed, saved + b'\n')
                    reason = {'receipt': 'pin changed', 'config': 'descriptor changed', 'launcher': 'launcher', 'program': 'pin changed'}[mutation]
                elif mutation == 'shell':
                    observed['Shell']['SHA256'] = '0' * 64
                    reason = 'shell pin changed'
                elif mutation == 'argv':
                    observed['Executions'][0]['Argv'].append('--unexpected')
                    reason = 'literal argv'
                elif mutation == 'missing-readback':
                    selected['Readback']['Path'] += '.missing'
                    reason = 'no such file'
                elif mutation == 'mixed-unit':
                    selected['Config'] = selected['Session']
                    reason = 'mixed unit'
                elif mutation == 'unsupported-layer':
                    selected['Layer'] = 'accountSettings'
                    reason = 'unsupported native harness/layer'
                if mutation in ('shell', 'argv'):
                    selected['Readback'] = write(f.root / 'mutated-readback.json', observed)
                hook_pin = write(f.root / 'census-hooks.json', selected)
                mutated = dict(catalog, HookSelections=[hook_pin])
                observations.append(inspect(args.census_image, f, mutated, proposal, output, mutation, ready=False, reason=reason))
            finally:
                if changed is not None:
                    if saved is not None:
                        overwrite_fixture(changed, saved)
                    else:
                        changed.unlink()
                        if mutation == 'project-hook':
                            changed.parent.rmdir()
        require(f.settings.read_bytes() == original, 'unrelated settings changed after refusals')
        require(not provider.failures, 'local provider failed')
        return dict(schema='cairn.r06-native-hook-census/1', passed=True, cases=observations,
                    native=dict(sha256=sha(args.claude), old_process=native_owner_a, new_process=native_owner_b,
                                same_conversation=target.identity, both_joined_exit0=True),
                    images={str(path): sha(path) for path in (args.census_image, args.lifecycle, args.stager)},
                    limits=['Finite selected flagSettings and prompt-hook observations only; native connection refresh unobserved.',
                            'Synthetic provider/API; no model account, authenticated native owner or production acceptance.'])
    finally:
        # unittest cleanups continue through every owned resource after a failure.
        cleanup_ok = support.doCleanups()
        write(output / 'cleanup.json', dict(passed=cleanup_ok, native=[dict(pid=c.process.pid, exit=c.process.returncode, joined=c.process.poll() is not None) for c in clients], provider_stopped=not provider.thread.is_alive()))
        require(cleanup_ok, 'fixture cleanup failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('claude', 'census-image', 'lifecycle', 'stager'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--archive-stdout', action='store_true')
    args = parser.parse_args()
    require(os.environ.get('HOME') == '/tmp/home' and Path('/tmp/work-tmp').is_dir(), 'existing hybrid wrapper required')
    require(sha(args.claude) == args.sha256, 'selected native binary mismatch')
    with tempfile.TemporaryDirectory(prefix='r06hc-', dir='/tmp') as temporary:
        output = Path(temporary)
        try:
            report = run(args, output)
            require(sha(args.claude) == args.sha256, 'native binary changed')
            write(output / 'report.json', report)
            print(json.dumps(report, indent=2))
        finally:
            if args.archive_stdout:
                archive = io.BytesIO()
                with tarfile.open(fileobj=archive, mode='w:gz') as tar:
                    for path in sorted(output.rglob('*')):
                        if path.is_file() and 'owned' not in path.relative_to(output).parts:
                            tar.add(path, arcname=str(path.relative_to(output)))
                print(json.dumps(dict(archive_base64=base64.b64encode(archive.getvalue()).decode())))


if __name__ == '__main__':
    main()
