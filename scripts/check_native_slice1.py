#!/usr/bin/env python3
"""Pinned Claude control-only publication through the public Go owner.

The API, database, settings, bindings and clients belong to this fixture. No
user/model prompt is sent and no installed account settings are replaced.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import threading
import time
import uuid

import disposable_host
import stage_lifecycle_hooks as hooks
import test_retained_lifecycle_package as retained
from test_r02_compatibility_install import InstalledFixture, InstalledTools
from test_r04_hook_settings import HookFixture, pin


CLAUDE = Path('/home/halbritt/.local/share/claude/versions/2.1.326')
CLAUDE_SHA = 'b6b3647d981130dc44fc670846ae97b6cde531ffb667cf9c4d17cfcafb38cb37'
ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode()


def private(path):
    path.mkdir(mode=0o700)
    return path


def write(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)


def metadata(path):
    info = path.stat()
    return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                gid=info.st_gid, mode=info.st_mode, mtime_ns=info.st_mtime_ns,
                raw=path.read_bytes(),
                xattrs={key: os.getxattr(path, key) for key in os.listxattr(path)})


def old_package(root, forward, host):
    """Construct Source /2 inputs, then seal them once through its real producer."""
    plan = retained.fixture(root, harness='claude', count=1, client=host.binary)
    package, material = plan['package'], plan['material']
    common = dict(cairn=str(host.binary), socket=str(host.socket),
                  token_file=str(host.token('native')), repo=host.repo,
                  config_home=str(forward.account))
    selector = root / 'local-selector'
    write(selector, b'#!/usr/bin/python3\nimport json,sys\nsys.stdin.read()\nprint(json.dumps({"type":"result","is_error":False,"structured_output":{"checkpoint":None,"workstream":None,"memories":[]}}))\n')
    selector.chmod(0o500)
    old_configs = {}
    for name, template in [('memory.json', forward.memory),
                           ('coordination-claude-one.json', forward.coord)]:
        config = copy.deepcopy(template)
        config.update(common, inbox_recall_binding=package['binding']['path'])
        config['selector'] = None
        config['semantic_fallback'] = False
        if name == 'memory.json':
            config['claude'] = str(selector)
        if name.startswith('coordination'):
            config['process_exe_dirs'] = [str(CLAUDE.parent)]
        selected = package['profiles'][name]
        write(Path(selected['path']), encoded(config))
        selected.update(pin(Path(selected['path'])))
        old_configs[name] = config
    binding_path = Path(package['binding']['path'])
    binding = json.loads(binding_path.read_bytes())
    binding.update(enabled=True, memory_config=package['profiles']['memory.json'],
                   coordination_configs=[package['profiles']['coordination-claude-one.json']])
    write(binding_path, encoded(binding))
    package['binding'].update(pin(binding_path))
    native_path = Path(material['native-representation.json']['path'])
    native = json.loads(native_path.read_bytes())
    native['roles']['coordination']['member_variants']['claude-one']['declared_profile'] = package['profiles']['coordination-claude-one.json']
    provenance_path = Path(material['provenance.json']['path'])
    provenance = json.loads(provenance_path.read_bytes())
    provenance.update(native_representation=native, profile_correspondence=[
        dict(generation_name=name, original_path=selected['path'], sha256=selected['sha256'])
        for name, selected in package['profiles'].items()])
    for name, body in [('native-representation.json', native), ('provenance.json', provenance)]:
        path = Path(material[name]['path'])
        write(path, encoded(body))
        material[name].update(pin(path))
        package['evidence'][name]['sha256'] = material[name]['sha256']
    package['files'] = {name: selected['sha256'] for name, selected in material.items()}
    return plan, old_configs


class Controller:
    def __init__(self, stager, root, source, account, work):
        self.diagnostic = (root / 'controller.stderr').open('wb')
        self.trace = root / 'exec.trace'
        self.process = subprocess.Popen([
            'strace', '-f', '-s', '8192', '-e', 'trace=execve,clone,clone3', '-o', str(self.trace),
            str(stager), 'native-claude-owner', '--client', str(CLAUDE),
            '--client-sha256', CLAUDE_SHA, '--client-version', '2.1.326',
            '--source-settings', str(source), '--source-sha256', pin(source)['sha256'],
            '--parent', str(private(root / 'owners')), '--account-home', str(account),
            '--work', str(work)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.diagnostic, env=os.environ.copy())
        self.observation = self.read()['data']

    def read(self):
        # One line is written atomically by the single public controller.
        require(select.select([self.process.stdout], [], [], 180)[0],
                'native owner response timed out')
        raw = self.process.stdout.readline()
        require(raw, 'native owner exited; see controller.stderr')
        result = json.loads(raw)
        require(result['schema'] == 'cairn.native-owner-control/1', 'wrong control schema')
        return result

    def call(self, operation, *, ok=True, **fields):
        request_id = fields.pop('request_id', str(uuid.uuid4()))
        request = dict(id=request_id, operation=operation, **fields)
        if operation != 'inspect':
            request['expected_generation'] = fields.get('expected_generation', self.observation['generation'])
        self.process.stdin.write(encoded(request) + b'\n')
        self.process.stdin.flush()
        response = self.read()
        require(response['id'] == request['id'] and response['ok'] is ok,
                f'{operation}: {response.get("error", response)}')
        if ok and isinstance(response.get('data'), dict):
            data = response['data']
            if data.get('schema') == 'cairn.native-owner-observation/1':
                self.observation = data
            elif 'owner' in data:
                self.observation = data['owner']
        return response

    def inspect(self):
        self.observation = self.call('inspect')['data']
        return self.observation

    def admission_race(self):
        requests = [dict(id=str(uuid.uuid4()), operation=operation,
                         expected_generation=self.observation['generation'])
                    for operation in ('initialize', 'publish')]
        self.process.stdin.write(b''.join(encoded(request) + b'\n' for request in requests))
        self.process.stdin.flush()
        admitted, refused = self.read(), self.read()
        require(admitted['id'] == requests[0]['id'] and admitted['ok'], 'native admission failed')
        require(refused['id'] == requests[1]['id'] and not refused['ok'] and
                'busy' in refused.get('error', ''), 'queued publication crossed native admission')
        return admitted['data']

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                code = self.process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                raise
            require(code == 0, 'native controller cleanup failed; see controller.stderr')
        self.process.stdout.close()
        self.diagnostic.close()


# This is a synthetic callback owner solely for bidirectional durable-state
# compatibility. It is never used as evidence for the real native owner guard.
CALLBACK_OWNER = r'''
import json,os,subprocess,sys
for line in sys.stdin:
    request=json.loads(line)
    event=request.get('event')
    if event is not None:
        event['host_pid']=os.getpid()
    result=subprocess.run(request['argv'],input=json.dumps(event) if event is not None else request.get('body',''),
        text=True,capture_output=True,timeout=40)
    print(json.dumps(dict(code=result.returncode,stdout=result.stdout,stderr=result.stderr)),flush=True)
'''


class CallbackOwner:
    def __init__(self, forward):
        self.process = subprocess.Popen([sys.executable, '-u', '-c', CALLBACK_OWNER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, CLAUDE_CONFIG_DIR=str(forward.account), CAIRN_INBOX_ROOT=str(forward.root)))

    def invoke(self, argv, *, event=None, body='', code=0):
        self.process.stdin.write(json.dumps(dict(argv=[str(x) for x in argv], event=event, body=body)) + '\n')
        self.process.stdin.flush()
        require(select.select([self.process.stdout], [], [], 60)[0], 'callback owner timed out')
        result = json.loads(self.process.stdout.readline())
        require(result['code'] == code, f'callback exit {result["code"]}: {result["stderr"]} {result["stdout"][:500]}')
        return result

    def close(self):
        self.process.stdin.close()
        try:
            require(self.process.wait(timeout=10) == 0, 'callback owner cleanup failed')
        finally:
            self.process.stdout.close()


def mixed_state(root, host, forward, old_plan, work, required_body):
    """Both real public frontends share grants/custody with retained producers."""
    old_argv = old_plan['package']['members']['claude-one']['argv']
    commands = {'python2': old_argv,
                'go3': {role: [str(forward.launcher(role))] +
                        (['hook', '--config', str(forward.coord_path)] if role == 'coordination' else [])
                        for role in ('memory', 'coordination')}}
    coord_dir, memory_dir = Path(forward.coord['state_dir']), Path(forward.memory['state_dir'])
    reports = []
    for first, second in [('python2', 'go3'), ('go3', 'python2')]:
        owner = CallbackOwner(forward)
        native_id = str(uuid.uuid4())
        prompt_id = str(uuid.uuid4())
        base = dict(session_id=native_id, cwd=str(work), prompt_id=prompt_id)

        def event(name, prompt='Inspect the fixture durable accounting.'):
            return dict(base, hook_event_name=name, prompt=prompt)

        def hook(runtime, role, name, prompt='Inspect the fixture durable accounting.'):
            return owner.invoke(commands[runtime][role], event=event(name, prompt))

        def ledger():
            return json.loads((memory_dir / (native_id + '.inbox-recall.json')).read_bytes())

        try:
            hook(first, 'coordination', 'SessionStart', '')
            hook(first, 'coordination', 'UserPromptSubmit')
            initial = hook(first, 'memory', 'UserPromptSubmit')
            require(required_body in initial['stdout'], 'real mandatory context was lost before mixed read')
            before = ledger()
            require(before.get('active') and before['grants'][before['active']]['output_bytes'] > 0,
                    'first implementation did not write a positive memory grant')
            grant = copy.deepcopy(before['grants'][before['active']])
            hook(second, 'memory', 'UserPromptSubmit')
            after = ledger()
            require(len(after['grants']) == len(before['grants']) and
                    after['grants'][before['active']]['output_bytes'] == grant['output_bytes'] and
                    after['grants'][before['active']]['native_allowance_bytes'] == grant['native_allowance_bytes'],
                    'mixed reader reset or duplicated the same native prompt grant')
            agents = host.agent('native', 'agent-directory', dict(repo=host.repo, harness='claude'))['agents']
            agent = next(a for a in agents if a['native_session_id'] == native_id)
            scope = dict(repo=host.repo, task_id='*', run_id='*')
            source = host.agent('sender', 'create', dict(request_id=str(uuid.uuid4()),
                draft=dict(kind='note', body='Complete this disposable mixed-state request exactly once.',
                           scope=scope, claim_type='self', sensitivity='shareable')))
            published = host.agent('sender', 'event-publish', dict(request_id=str(uuid.uuid4()),
                kind='request', ref=dict(record_id=source['record_id'], version=source['version']),
                destination=dict(type='agent', name=agent['inbox'])))
            stopped = hook(first, 'coordination', 'Stop')
            require(json.loads(stopped['stdout']).get('decision') == 'block', 'public callback did not retain a native request hold')
            contexts = [json.loads(path.read_bytes()) for path in (coord_dir / 'inbox').glob('*.json')]
            context = next(c for c in contexts if c.get('event_id') == published['event_id'])
            leased = host.agent('sender', 'event-inspect', dict(event_id=published['event_id']))['deliveries'][0]
            require(leased['state'] == 'leased' and leased['attempts'] == 1, 'mixed callback did not retain one real lease')
            # The opposite implementation reads the existing held context. No
            # second delivery/attempt or prompt grant may be manufactured.
            base['prompt_id'] = 'mixed-next-' + prompt_id
            hook(second, 'coordination', 'UserPromptSubmit',
                 'Continue inspecting the already held disposable request.')
            still_leased = host.agent('sender', 'event-inspect', dict(event_id=published['event_id']))['deliveries'][0]
            require(still_leased['delivery_id'] == leased['delivery_id'] and
                    still_leased['attempts'] == 1 and still_leased['state'] == 'leased',
                    'opposite callback replaced or settled existing custody')
            journal_path = coord_dir / 'intents' / (context['attempt_id'] + '.json')
            body = 'Selected disposable mixed-state result.\n'
            # Execute the actual retained issued Python producer even when a
            # Go callback wrote the context; it must consume the same journal.
            python_issued = [sys.executable, old_argv['coordination'][1], 'inbox',
                             '--context', str(coord_dir / 'inbox' / (context['attempt_id'] + '.json')), 'finish-reply']
            settled = owner.invoke(python_issued, body=body)
            again = owner.invoke(python_issued, body=body)
            require(json.loads(settled['stdout']) == json.loads(again['stdout']), 'retained issued retry changed selected result')
            committed = json.loads(settled['stdout'])['data']
            require(committed['completion_committed'] and committed['reply_committed'], 'retained issued producer did not commit both halves')
            journal = json.loads(journal_path.read_bytes())
            require(journal['completion']['status'] == 'committed' and journal['response']['status'] == 'committed', 'journal lost committed correspondence')
            # Public retained watcher reaps completed Go/Python state after its
            # synthetic owner really exits, retaining all accounting and IDs.
            final_grants = ledger()
            owner.close()
            owner = None
            watch_dir = private(root / ('watch-' + first))
            (watch_dir / 'fixture.json').write_bytes(Path(old_plan['package']['profiles']['coordination-claude-one.json']['path']).read_bytes())
            result = subprocess.run([sys.executable, old_argv['coordination'][1], 'watch', '--config-dir', str(watch_dir), '--once'],
                                    capture_output=True, text=True, timeout=40, env=os.environ.copy())
            require(result.returncode == 0 and result.stderr == '', 'retained watcher recovery failed: ' + result.stderr)
            key = hashlib.sha256((forward.coord['binding'] + '\0' + native_id).encode()).hexdigest()
            state = json.loads((coord_dir / (key + '.json')).read_bytes())
            require(state.get('retired'), 'retained watcher did not retire the joined synthetic owner')
            require(ledger() == final_grants, 'retained recovery changed durable grant accounting')
            delivery = host.agent('sender', 'event-inspect', dict(event_id=published['event_id']))['deliveries'][0]
            require(delivery['state'] == 'handled' and delivery['attempts'] == 1 and delivery['result'] == committed['result'], 'mixed recovery changed settled delivery identity')
            reports.append(dict(writer=first, reader=second, positive_grants=len(final_grants['grants']),
                                retained_issued=True, retained_watcher=True, attempts=1))
        finally:
            if owner:
                owner.close()
    return reports


def check(binary, report_directory=None):
    require(CLAUDE.exists() and pin(CLAUDE)['sha256'] == CLAUDE_SHA,
            'pinned Claude 2.1.326 is required; no alternate client is permitted')
    provided = disposable_host.ProvidedCluster.from_environment()
    root = private(provided.root / ('native-slice1-' + uuid.uuid4().hex))
    host = disposable_host.DisposableHost.start(
        provided, binary=binary, repo='fixture:native-slice1:' + uuid.uuid4().hex,
        database='cairn_native_slice1', home=root / 'api', roles=('native', 'sender'))
    InstalledTools.setUpClass()
    controller = None
    try:
        forward = HookFixture(root / 'forward', InstalledTools.binary,
                              stager=InstalledTools.stager, harness='claude', enabled=True)
        authority = host.operator('bootstrap', dict(request_id=str(uuid.uuid4()), reason='Disposable Slice1 required context'))
        required_body = 'Preserve durable fixture accounting and exact source identity.'
        host.operator('issue', dict(request_id=str(uuid.uuid4()), grant_id=authority['grant_id'],
            mandatory=True, policy_key='slice1-required', category='workflow', reason='Native and mixed fixture coverage',
            draft=dict(kind='instruction', body=required_body, claim_type='self', sensitivity='shareable',
                       scope=dict(repo=host.repo, task_id='*', run_id='*'))))
        plan, old_configs = old_package(root / 'retained', forward, host)
        old = retained.product.stage_retained_package(
            str(InstalledTools.stager), private(root / 'retained-generations'), plan)
        initial = root / 'initial-settings.json'
        write(initial, b'{ "opaque":9007199254740993123456789, "hooks":{"SessionStart":[{"matcher":"","hooks":[{"type":"command","command":"/usr/bin/true","timeout":5}]}]} }\n')
        old_hooks = hooks.stage(str(InstalledTools.stager), private(root / 'old-hook-generations'),
            dict(id=str(uuid.uuid4()), selection=old, legacy=True,
                 harness='claude', account_home=str(forward.account), settings=pin(initial),
                 coordination_index=0, replace_commands=dict(memory=[], coordination=[])))
        original = root / 'original-settings.json'
        write(original, b' \n' + Path(old_hooks['settings']['path']).read_bytes() + b' \n')
        original.chmod(0o640)
        os.utime(original, ns=(1_700_000_000_123456789, 1_700_000_000_987654321))
        os.setxattr(original, 'user.cairn-slice1', b'original metadata')
        # The new /3 profiles retain both role identities and state domains.
        forward.memory = copy.deepcopy(old_configs['memory.json'])
        forward.coord = copy.deepcopy(old_configs['coordination-claude-one.json'])
        InstalledFixture.publish_configuration(forward, enabled=True)
        work = private(root / 'work')
        private(work / '.git')
        controller = Controller(InstalledTools.stager, root, original, forward.account, work)
        settings = Path(controller.observation['settings'])
        original_owned = metadata(settings)
        require(controller.observation['verdict'] == 'UNKNOWN', 'empty owner certified quiescence')
        baseline_id = str(uuid.uuid4())
        baseline_generation = controller.observation['generation']
        baseline = controller.call('initialize', request_id=baseline_id)['data']
        require(baseline['session_id'], 'native Source /2 session was not initialized')
        controller.inspect()
        duplicate = controller.call('initialize', request_id=baseline_id,
                                    expected_generation=baseline_generation)['data']
        require(duplicate == baseline, 'native retry changed its selected result')
        controller.call('close-native', request_id=baseline_id, ok=False)
        require(controller.observation['verdict'] == 'BUSY', 'live native owner was not BUSY')
        controller.call('publish', ok=False)
        controller.call('close-native')
        controller.inspect()
        old_record = json.loads((Path(old_hooks['selection']['path']).parent / 'hook-selection.json').read_bytes())
        replacements = {role: sorted({row['hook']['command'] for row in old_record['rows'] if row['role'] == role})
                        for role in ('memory', 'coordination')}
        new_hooks = hooks.stage(str(InstalledTools.stager), private(root / 'new-hook-generations'),
            dict(id=str(uuid.uuid4()), selection=forward.selected, harness='claude',
                 account_home=str(forward.account), settings=pin(settings),
                 coordination_index=0, replace_commands=replacements))
        publication = dict(selection=new_hooks['selection'], retained_selection=old,
            retained_memory=plan['package']['profiles']['memory.json'],
            retained_coordination=plan['package']['profiles']['coordination-claude-one.json'],
            replace_commands=replacements)
        controller.call('select', publication=publication)
        observation = controller.inspect()
        require(observation['verdict'] == 'QUIESCENT', 'baseline G2: ' + observation['reason'])
        controller.call('publish', ok=False, expected_generation=observation['generation'] - 1)
        receipt_raw, receipt_mode = forward.receipt_path.read_bytes(), forward.receipt_path.stat().st_mode & 0o777
        forward.receipt_path.chmod(0o600)
        forward.receipt_path.write_bytes(receipt_raw + b'\n')
        forward.receipt_path.chmod(receipt_mode)
        controller.call('publish', ok=False)
        require(metadata(settings) == original_owned, 'receipt refusal mutated native settings')
        forward.receipt_path.chmod(0o600)
        forward.receipt_path.write_bytes(receipt_raw)
        forward.receipt_path.chmod(receipt_mode)
        controller.call('select', publication=publication)
        controller.inspect()
        interrupted = threading.Event()
        def interrupt_api_after_prepare():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if list(settings.parent.glob('.cairn-native-*.json')):
                    host.server.process.kill()
                    interrupted.set()
                    return
                time.sleep(0.001)
        interrupt = threading.Thread(target=interrupt_api_after_prepare, daemon=True)
        interrupt.start()
        failed_publication = controller.call('publish', ok=False)
        interrupt.join(timeout=35)
        require(interrupted.is_set() and failed_publication['data']['pending_recovery'],
                'disposable API interruption did not retain prepared recovery')
        require(metadata(settings) == original_owned, 'interrupted guard changed native settings')
        require(controller.inspect()['verdict'] == 'UNKNOWN', 'pending recovery was certified quiescent')
        controller.call('initialize', ok=False)
        host.stop_server()
        host.start_server()
        controller.inspect()
        published = controller.call('recover')['data']
        require(published['exchange']['state'] == 'published', 'settings were not published')
        published_raw = settings.read_bytes()
        require(json.loads(published_raw)['opaque'] == 9007199254740993123456789, 'opaque integer changed')
        go_trace_offset = controller.trace.stat().st_size
        actual_go = controller.admission_race()
        require(actual_go['session_id'] != baseline['session_id'], 'native ID was resumed')
        require(actual_go['hooks'], 'native Go hooks listing is absent')
        controller.inspect()
        controller.call('close-native')
        controller.inspect()
        go_trace = controller.trace.read_text()[go_trace_offset:]
        require(str(forward.binary) in go_trace and '--receipt' in go_trace,
                'actual Go image/receipt did not execute during published native generation')
        new_record = json.loads((Path(new_hooks['selection']['path']).parent / 'hook-selection.json').read_bytes())
        effective = json.dumps(actual_go['hooks'], sort_keys=True)
        require(all(row['hook']['command'] in effective for row in new_record['rows']),
                'native effective listing differs from selected Go hooks')
        inverse = controller.call('inverse')['data']
        require(inverse['exchange']['state'] == 'reversed', 'exact inverse was not completed')
        require(metadata(settings) == original_owned, 'inverse did not preserve original inode/bytes/metadata')
        inverse_trace_offset = controller.trace.stat().st_size
        python_again = controller.call('initialize')['data']
        require(python_again['session_id'] not in (baseline['session_id'], actual_go['session_id']), 'inverse reused a native session')
        controller.inspect()
        controller.call('close-native')
        final = controller.inspect()
        require(final['verdict'] == 'QUIESCENT', 'inverse G2: ' + final['reason'])
        trace = controller.trace.read_text()
        require(str(forward.binary) in trace and '--receipt' in trace, 'actual Go image/receipt execution was not observed')
        require(next(word for word in plan['package']['members']['claude-one']['argv']['memory'] if word.endswith('.py')) in trace,
                'actual retained Python /2 memory callback was not observed')
        require(host.count('agent_session') == 3, 'native callbacks did not create three real API sessions')
        inverse_trace = controller.trace.read_text()[inverse_trace_offset:]
        python_memory = next(word for word in plan['package']['members']['claude-one']['argv']['memory'] if word.endswith('.py'))
        require(python_memory in inverse_trace and str(forward.binary) not in inverse_trace,
                'inverse did not execute fresh retained Python callbacks')
        for native_id in final['native_ids']:
            accounting = json.loads((Path(forward.memory['state_dir']) / (native_id + '.inbox-recall.json')).read_bytes())
            require(accounting['required']['count'] == 1 and accounting['pending_bytes'] > 0,
                    'actual native callback did not retain required memory accounting')
        controller.call('select', publication=publication)
        controller.inspect()
        next_publication = controller.call('publish')['data']
        require(next_publication['exchange']['recovery'] != inverse['exchange']['recovery'],
                'a fresh publication reused the previous transaction record')
        controller.call('inverse')
        require(metadata(settings) == original_owned, 'second guarded cycle lost exact original metadata')
        mixed = mixed_state(root, host, forward, plan, work, required_body)
        ledger_path = Path(forward.memory['state_dir']) / (final['native_ids'][0] + '.inbox-recall.json')
        marker_path = ledger_path.with_suffix('.initialized')
        parked = []
        try:
            for path in (ledger_path,marker_path):
                held = path.with_name(path.name + '.held')
                path.rename(held)
                parked.append((path,held))
            require(controller.inspect()['verdict'] == 'UNKNOWN', 'missing accounting became an empty positive')
            controller.call('publish', ok=False)
            controller.call('initialize', ok=False)
        finally:
            for path,held in parked:
                held.rename(path)
        require(controller.inspect()['verdict'] == 'QUIESCENT', 'restored exact accounting did not correspond')
        settings.write_bytes(settings.read_bytes() + b' ')
        drifted = metadata(settings)
        require(controller.inspect()['verdict'] == 'UNKNOWN', 'changed settings certified quiescence')
        controller.call('publish', ok=False)
        controller.call('initialize', ok=False)
        require(metadata(settings) == drifted, 'drift refusal replaced changed settings')
        report = dict(schema='cairn.native-slice1-fixture/1', native_version='2.1.326',
                      native_sha256=CLAUDE_SHA, native_ids=final['native_ids'],
                      verdict=final['verdict'], source2_memory_bound=True, source3_memory_bound=True,
                      exact_inverse=True, mixed_state=mixed,
                      refusals=['empty-owner', 'busy-owner', 'stale-generation', 'receipt-bytes', 'admission-race', 'settings-drift', 'missing-accounting'],
                      prepared_recovery='disposable-api-interruption-then-recovered',
                      model_prompts=0, live_adoption=False)
        report.update(lifecycle_image_sha256=pin(forward.binary)['sha256'],
                      stager_image_sha256=pin(InstalledTools.stager)['sha256'],
                      lifecycle_receipt_sha256=forward.receipt_sha,
                      retained_selection=old, hook_selection=new_hooks['selection'],
                      source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                      source_clean=not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
        write(root / 'result.json', encoded(report))
        if report_directory:
            report_directory = Path(report_directory)
            require(report_directory.is_absolute(), 'absolute fresh report directory required')
            private(report_directory)
            write(report_directory / 'result.json', encoded(report))
        print(json.dumps(report, sort_keys=True))
    finally:
        try:
            if controller:
                controller.close()
        finally:
            InstalledTools.tearDownClass()
            host.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary',type=Path)
    parser.add_argument('--report-directory',type=Path)
    args = parser.parse_args()
    check(args.binary.resolve(),args.report_directory)
