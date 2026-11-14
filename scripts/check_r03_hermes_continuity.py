#!/usr/bin/env python3
"""Prepare the held Hermes continuity experiment without launching a client.

The session-api phase uses the real held SessionDB, not a conversation stand-in.
It proves only the database prerequisite. Native CLI/gateway execution remains
an explicit NOTRUN boundary; neither a fixture flag nor this report admits it.
"""
import argparse
import ast
import atexit
import base64
import hashlib
import http.server
import json
import os
import re
import select
import shlex
import shutil
import signal
import socket
import tempfile
from urllib.parse import urlsplit
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from datetime import datetime, timezone

BASE = '522a13a63b64fc4f960ad0664e9ebe6ef4293fe9'
HERMES_REVISION = '9904fd411587729dcdf7440cfb830e96e9830597'
MANIFEST_SHA256 = 'b2910e13d782eef359ce920659518385c6c022e2901d71b765d86617eb9c39c0'
SCHEMA = 'cairn.r03-hermes-continuity/1'
NOTRUN = 75
ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = 'agent/a4139631-599c-4e0b-a9ba-1bd723ba0a2c'
AGENT_ID = 'a6f6a17d-5a15-4f84-806f-00b27d50acc4'
EXECUTION_ID = '498eaa6e-0e24-4e5b-98ab-b6c49f1cab60'
MODEL = 'r03-scripted-fixture'
PROVIDER_URL = 'http://127.0.0.1:43867/v1'
IMAGE_SOURCE = '7d22b663d6abb0295dda0263adc9033353e5cefa'
IMAGE_TREE = 'b9589ad0ac27a1557fd25e198143233239b322a5'


class PrerequisiteError(RuntimeError):
    pass


class AdmissionRefusal(PrerequisiteError):
    pass


def require(condition, message):
    if not condition:
        raise PrerequisiteError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def private_output(evidence_root, output):
    """Validate the owned private subtree before creating any application state."""
    evidence_root, output = Path(evidence_root), Path(output)
    require(evidence_root.is_absolute() and output.is_absolute(), 'R03_PATH: absolute paths required')
    require(evidence_root == evidence_root.resolve(), 'R03_PATH: evidence root contains a symlink')
    require(evidence_root.is_relative_to(Path.home() / '.cache'), 'R03_PATH: evidence root must be under the host cache')
    require(output.is_relative_to(evidence_root) and output != evidence_root, 'R03_PATH: output escapes evidence root')
    require(output == output.resolve(), 'R03_PATH: output contains a symlink')
    require(not output.exists(), 'R03_PATH: fresh output required; preserve prior attempts')
    for parent in (output.parent, *output.parent.parents):
        require(parent.is_dir(), 'R03_PATH: create private evidence parents first')
        info = parent.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'R03_PATH: every evidence ancestor must be owned mode 0700: ' + str(parent))
        if parent == evidence_root:
            return output
    raise PrerequisiteError('R03_PATH: missing evidence root')


def verify_inputs(native_root, manifest):
    """Rehash the entire held source/interpreter/module inventory before import."""
    native_root, manifest = Path(native_root), Path(manifest)
    require(digest(manifest) == MANIFEST_SHA256, 'R03_PINS: held inventory changed')
    pins = json.loads(manifest.read_bytes())
    require(pins['hermes_revision'] == HERMES_REVISION and pins['hermes_source_clean'] is True,
            'R03_PINS: wrong Hermes revision or dirty held source')
    actual = set()
    for parent, directories, files in os.walk(native_root, followlinks=False):
        for name in directories + files:
            require(not (Path(parent) / name).is_symlink(), 'R03_PINS: symlink in held inventory')
        actual.update(str((Path(parent) / name).relative_to(native_root)) for name in files)
    require(actual == set(pins['files']), 'R03_PINS: added or missing held source/module files')
    total = 0
    for relative, expected in pins['files'].items():
        path = native_root / relative
        require(not Path(relative).is_absolute() and '..' not in Path(relative).parts,
                'R03_PINS: inventory path escapes held root')
        require(path.resolve().is_relative_to(native_root.resolve()), 'R03_PINS: held input escapes root')
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o222,
                'R03_PINS: held input must be a frozen regular file: ' + relative)
        require(info.st_size == expected['bytes'] and digest(path) == expected['sha256'],
                'R03_PINS: held bytes changed: ' + relative)
        total += info.st_size
    package = tomllib.loads((native_root / 'hermes/pyproject.toml').read_text())['project']
    require(package['version'] == '0.20.5', 'R03_PINS: wrong held Hermes package version')
    return dict(manifest_sha256=MANIFEST_SHA256, hermes_revision=HERMES_REVISION,
                package_version=package['version'],
                file_count=len(pins['files']), bytes=total, all_inventory_files_verified=True)


def source_pins():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT, check=True)
    return dict(head=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'), base=BASE,
                status=git('status', '--porcelain=v1'),
                files={name: digest(ROOT / name) for name in (
                    'scripts/check_r03_hermes_continuity.py', 'scripts/test_r03_hermes_continuity.py',
                    'docs/verification/r03-hermes-continuity.md', 'go.mod', 'go.sum', 'mise.toml',
                    'scripts/check_r03_hermes_installed.py', 'scripts/check_r03_hermes_objects.py',
                    'scripts/check_r03_hermes_store.py')})


def session_api(home, hermes_root, report):
    """Use the supported persistence API only; never import cli or run_agent."""
    require(Path(home).is_dir() and Path(home).stat().st_uid == os.getuid()
            and stat.S_IMODE(Path(home).stat().st_mode) == 0o700,
            'R03_SESSION_API: private home required')
    require(os.environ.get('HERMES_HOME') == str(home), 'R03_SESSION_API: explicit disposable HERMES_HOME required')
    require(sys.version_info[:3] == (3, 11, 15), 'R03_SESSION_API: held Python 3.11.15 required')
    require(os.getpid() > 1, 'R03_SESSION_API: invalid owned process identity')
    marker = json.loads((Path(home).parent / 'prep-marker.json').read_bytes())
    require(marker['schema'] == SCHEMA and marker['home'] == str(home)
            and marker['hermes_root'] == str(hermes_root), 'R03_SESSION_API: owned preparation marker required')
    require(Path(report) == Path(home).parent / 'session-api.json', 'R03_SESSION_API: report escapes preparation')
    private_output(Path(marker['evidence_root']), Path(report))
    require(Path(home) == Path(home).resolve() and not list(Path(home).iterdir()),
            'R03_SESSION_API: fresh application state required')
    sys.path.insert(0, str(hermes_root))
    from hermes_state import SessionDB

    session = 'r03-existing-api-' + str(uuid.uuid4())
    database = Path(home) / 'state.db'
    selectors = Path(home) / 'credential-selectors.json'
    # Synthetic selectors describe the future local transport; no installed
    # auth store or real credential is read, copied, refreshed or replaced.
    write_json(selectors, dict(provider='custom', api_key='r03-fixture-only',
                               base_url='http://127.0.0.1:9/v1', fallback=[]))
    selector_sha = digest(selectors)
    messages = [('user', 'Synthetic continuity baseline.'),
                ('assistant', 'Synthetic baseline retained.')]
    db = SessionDB(db_path=database)
    try:
        db.create_session(session, source='cli', model='r03-scripted-fixture',
                          model_config=dict(provider='custom'), cwd=str(home), profile_name='r03-fixture')
        for role, content in messages:
            db.append_message(session, role, content)
        before = db.get_resume_conversations(session)
        require(db.resolve_resume_session_id(session) == session, 'R03_SESSION_API: unexpected lineage replacement')
        require(db.try_acquire_session_turn_lease(session, 'r03-existing-owner', ttl_seconds=60),
                'R03_SESSION_API: cannot establish active-turn negative control')
        require(not db.try_acquire_session_turn_lease(session, 'r03-foreign-owner', ttl_seconds=60),
                'R03_SESSION_API: foreign owner acquired active conversation')
        db.release_session_turn_lease(session, 'r03-foreign-owner')
        require(not db.try_acquire_session_turn_lease(session, 'r03-foreign-owner', ttl_seconds=60),
                'R03_SESSION_API: foreign release removed owner lease')
        db.release_session_turn_lease(session, 'r03-existing-owner')
        db.end_session(session, 'r03-fixture-idle')
    finally:
        db.close()
    db = SessionDB(db_path=database)
    try:
        require(db.get_session(session) is not None, 'R03_SESSION_API: original session disappeared')
        require(db.get_resume_conversations(session) == before, 'R03_SESSION_API: history changed across close/reopen')
        db.reopen_session(session)
        require(db.resolve_resume_session_id(session) == session, 'R03_SESSION_API: resume changed identity')
        require(db.get_resume_conversations(session) == before, 'R03_SESSION_API: resume duplicated or lost history')
        require(db.get_resume_message_count(session) == len(messages), 'R03_SESSION_API: duplicate effects')
        require(db.try_acquire_session_turn_lease(session, 'r03-idle-reader', ttl_seconds=60),
                'R03_SESSION_API: own idle lease did not release')
        db.release_session_turn_lease(session, 'r03-idle-reader')
        require(digest(selectors) == selector_sha, 'R03_SESSION_API: credential selectors changed')
        require('cli' not in sys.modules and 'run_agent' not in sys.modules,
                'R03_SESSION_API: native client unexpectedly imported')
        write_json(report, dict(schema=SCHEMA, session=session, database=str(database),
            model_history=before[0], display_history=before[1], message_count=len(messages),
            credential_selectors_sha256=selector_sha, active_foreign_acquire=False,
            foreign_release_effect=False, idle_acquire=True, client_imported=False,
            api='hermes_state.SessionDB', scope='persistence prerequisite only; no native conversation proof',
            owned_process=dict(pid=os.getpid(), parent_pid=os.getppid(),
                               executable=os.readlink('/proc/self/exe')),
            python=sys.version, hermes_module_sha256=digest(Path(hermes_root) / 'hermes_state.py')))
    finally:
        db.close()


def native_boundary():
    return dict(schema=SCHEMA, status='NOTRUN', exit=NOTRUN, ac4_closed=False,
        reason='R03_NATIVE_ADMISSION_REQUIRED: existing-conversation proof needs the held native Hermes client',
        supported_boundary='Finish the owned idle turn, cleanly finalize CLI, resume the SAME session ID; never replace its history or credentials.',
        native_api=['cli.HermesCLI(resume=existing_session_id)',
                    'CLIAgentSetupMixin._preload_resumed_session()',
                    'HermesCLI.chat(synthetic_prompt)', 'cli._finalize_single_query(cli)'],
        pending=['actual CLI-emitted callbacks and local scripted conversation transport',
                 'A cached while B is staged; next supported idle resume selects checked Go B',
                 'explicit retained Python /2 reverse on that same session/history/selectors',
                 'real disposable Cairn Store effects and cleanup across all three phases',
                 'qualified shared watcher/hook/config/state unit joined in R06'],
        provider_recreation_is_continuity=False, admission_flag_is_authority=False)


def validate_admission(status, admission, context, bindings):
    """Bind an authenticated server readback, never a caller's admitted flag."""
    event = status['event']
    require(event['from'] == RELEASE_ROOT and event['kind'] == 'request', 'R03_ADMISSION: not a Release Root request')
    require(event['event_id'] == context['event_id'] and event['ref'] == context['source'],
            'R03_ADMISSION: event/source mismatch')
    require(event['destination'] == dict(type='agent', name='agent/' + AGENT_ID),
            'R03_ADMISSION: wrong recipient')
    require(context['agent_id'] == AGENT_ID and context['execution_id'] == EXECUTION_ID,
            'R03_ADMISSION: foreign execution')
    require(event['resolution']['execution_id'] == EXECUTION_ID, 'R03_ADMISSION: stale resolution')
    require(any(row['delivery_id'] == context['delivery_id'] and row['state'] == 'leased'
                and row['consumer'] == 'agent/' + AGENT_ID for row in status['deliveries']),
            'R03_ADMISSION: delivery is not currently leased to this execution')
    require(admission.get('schema') == 'cairn.r03-hermes-native-admission/1'
            and admission.get('operation') == 'one-direct-host-hermes-continuity'
            and admission.get('attempt_limit') == 1, 'R03_ADMISSION: no exact native operation admitted')
    require(admission.get('bindings') == bindings, 'R03_ADMISSION: exact code/image/runtime/provider bindings differ')
    expires = datetime.fromisoformat(admission['expires_at'].replace('Z', '+00:00'))
    require(expires.tzinfo is not None and datetime.now(timezone.utc) < expires,
            'R03_ADMISSION: expired or unzoned admission')
    return dict(event_id=event['event_id'], delivery_id=context['delivery_id'], source=event['ref'],
                bindings=bindings, expires_at=admission['expires_at'])


def authenticated_admission(context_path, bindings, journal=None):
    context = json.loads(Path(context_path).read_bytes())
    require(context['agent_id'] == AGENT_ID and context['execution_id'] == EXECUTION_ID,
            'R03_ADMISSION: foreign context')
    read_argv = context['read']
    require(read_argv[0] == bindings['admission_cli'], 'R03_ADMISSION: hook CLI differs from pinned normal host CLI')
    require('--socket' in read_argv and '--token-file' in read_argv, 'R03_ADMISSION: explicit hook profile/socket required')
    api_socket = read_argv[read_argv.index('--socket') + 1]
    profile = read_argv[read_argv.index('--token-file') + 1]
    require(api_socket == bindings['admission_socket'] and profile == '/home/halbritt/.local/share/cairn/hosted-agent.token',
            'R03_ADMISSION: hook profile/socket mismatch')
    processes = []
    def read(argv, payload=None):
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        require(process.pid > 1, 'R03_ADMISSION: invalid readback PID')
        try:
            stdout, stderr = process.communicate(payload, timeout=30)
        except subprocess.TimeoutExpired as error:
            process.terminate()
            try: process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.communicate(timeout=5)
            raise PrerequisiteError('R03_ADMISSION: readback deadline; no client admitted') from error
        processes.append(dict(pid=process.pid, argv=argv, exit=process.returncode))
        if journal is not None: write_json(journal, dict(processes=processes, socket=api_socket))
        require(process.returncode == 0, 'R03_ADMISSION: authenticated readback failed')
        envelope = json.loads(stdout)
        require(envelope.get('ok') is True, 'R03_ADMISSION: server refused readback')
        return envelope['data']
    status = read([read_argv[0], 'event-status', '--socket', api_socket, '--token-file', profile,
        '--agent-id', AGENT_ID, '--execution-id', EXECUTION_ID, '--limit', '100', context['event_id']])
    require(context['read_input'] == context['source'], 'R03_ADMISSION: hook source-read mismatch')
    history = read(read_argv, json.dumps(context['read_input']))
    require(history['current_version'] == context['source']['version'] and history['current_lifecycle'] == 'active',
            'R03_ADMISSION: source superseded or inactive')
    version = history['versions'][0]
    require(version['version'] == context['source']['version'], 'R03_ADMISSION: wrong source version')
    receipt = validate_admission(status, json.loads(version['body']), context, bindings)
    receipt.update(source_sha256=version['body_sha256'], readback_processes=processes)
    return receipt


class ScriptedChat:
    """A real local chat-completions transport, including SDK SSE streaming."""
    def __init__(self, output, port=0):
        self.output, self.requests, self.phase = Path(output), [], 'A'
        self.lock, self.violations = threading.Lock(), []
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    owner.violations.append('invalid request size'); self.send_error(400); return
                body = json.loads(self.rfile.read(length))
                row = dict(path=self.path, body=body, phase=owner.phase,
                           authorization_expected=self.headers.get('Authorization') == 'Bearer r03-fixture-only')
                with owner.lock:
                    owner.requests.append(row)
                    write_json(owner.output, owner.requests)
                if (self.path != '/v1/chat/completions' or body.get('model') != MODEL
                        or self.headers.get('Authorization') != 'Bearer r03-fixture-only'):
                    owner.violations.append('unexpected route/model/credential: ' + self.path)
                    self.send_error(400, 'Unexpected provider route, model or credential')
                    return
                content = 'Synthetic continuity response ' + owner.phase + '.'
                base = dict(id='r03-' + str(len(owner.requests)), object='chat.completion',
                            created=1, model=MODEL)
                if body.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    for delta, reason in [(dict(role='assistant', content=content), None), ({}, 'stop')]:
                        event = dict(base, object='chat.completion.chunk',
                                     choices=[dict(index=0, delta=delta, finish_reason=reason)])
                        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    result = dict(base, choices=[dict(index=0, message=dict(role='assistant', content=content),
                                                     finish_reason='stop')],
                                  usage=dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
                    raw = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.url = 'http://127.0.0.1:' + str(self.server.server_address[1]) + '/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        require(not self.thread.is_alive(), 'R03_TRANSPORT: owned server did not join')


def require_idle_boundary(owner_finalized, original_session, next_session):
    require(owner_finalized, 'R03_REFRESH: cached owner remains; supported idle finalization required')
    require(original_session == next_session, 'R03_REFRESH: replacement conversation refused')


def frozen_inventory(root, manifest):
    pins = json.loads(Path(manifest).read_bytes())
    for name, expected in pins['files'].items():
        path = Path(root) / name
        require(path.resolve().is_relative_to(Path(root).resolve()), 'R03_ASSET: inventory escape')
        require(path.is_file() and not path.stat().st_mode & 0o222
                and path.stat().st_size == expected['bytes'] and digest(path) == expected['sha256'],
                'R03_ASSET: frozen asset changed: ' + name)
    return dict(sha256=digest(manifest), files=len(pins['files']),
                bytes=sum(row['bytes'] for row in pins['files'].values()))


def image_source_carry(pins):
    require(pins['source_head'] == IMAGE_SOURCE and pins['source_tree'] == IMAGE_TREE,
            'R03_IMAGES: exact Root 7d source required')
    names = list(pins['source_files'])
    proc = subprocess.run(['git', 'cat-file', '--batch'], cwd=ROOT,
        input=''.join(IMAGE_SOURCE + ':' + name + '\n' for name in names).encode(),
        capture_output=True, check=True)
    position = 0
    for name in names:
        end = proc.stdout.index(b'\n', position)
        header = proc.stdout[position:end].split()
        require(len(header) == 3 and header[1] == b'blob', 'R03_IMAGES: missing source blob')
        size = int(header[2]); body = proc.stdout[end + 1:end + 1 + size]
        require(hashlib.sha256(body).hexdigest() == pins['source_files'][name],
                'R03_IMAGES: compiled source inventory mismatch: ' + name)
        # The current fixture's retained Python/installer/adapter inputs must
        # carry exactly; Go product images are the separately pinned Root build.
        if name.startswith(('integrations/lifecycle/', 'integrations/hermes/')) or name in (
                'scripts/install-hermes-integration.py', 'scripts/install_lifecycle_shared.py',
                'scripts/stage_lifecycle_compatibility.py', 'scripts/stage_lifecycle_hooks.py'):
            require(digest(ROOT / name) == pins['source_files'][name], 'R03_IMAGES: retained input carry changed: ' + name)
        position = end + size + 2
    for name, row in pins['images'].items():
        require(digest(row['path']) == row['sha256'] and not Path(row['path']).stat().st_mode & 0o222,
                'R03_IMAGES: Root image changed: ' + name)
    return len(names)


def make_mechanism_plan(args):
    output = private_output(args.evidence_root, args.output)
    held = verify_inputs(args.native_root, args.manifest)
    image_pins = json.loads(args.root_images.read_bytes())
    source_count = image_source_carry(image_pins)
    pg = frozen_inventory(args.pg_root, args.pg_root.parent / 'native-pg-pins.json')
    source = source_pins()
    require(not source['status'], 'R03_PLAN: commit and freeze before pinning')
    tools = {}
    for name in ('strace', 'git', 'env', 'sh', 'locale'):
        path = shutil.which(name)
        require(path is not None, 'R03_TOOL: missing normal host tool: ' + name)
        tools[str(Path(path).resolve())] = digest(path)
    admission_cli = '/home/halbritt/.local/bin/cairn'
    tools[admission_cli] = digest(admission_cli)
    libraries = {}
    for executable in (args.native_root / 'venv/bin/python', *tools,
                       args.pg_root / 'usr/lib/postgresql/18/bin/postgres',
                       args.pg_root / 'usr/lib/postgresql/18/bin/psql'):
        result = subprocess.run(['ldd', str(executable)], text=True, capture_output=True, check=True)
        for path in re.findall(r'(?:=>\s*)?(/[^\s()]+)', result.stdout): libraries[path] = digest(path)
    # The kernel chooses the port. The admitted run must bind this exact port
    # exclusively before any client starts; no alternate target is selected.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    provider_url = 'http://127.0.0.1:' + str(port) + '/v1'
    bindings = dict(case_id=str(uuid.uuid4()), execution_mode='direct-host',
        source_head=source['head'], source_tree=source['tree'], checker_sha256=digest(__file__), source_files=source['files'],
        held_manifest_sha256=held['manifest_sha256'], hermes_revision=HERMES_REVISION,
        interpreter_sha256=digest(args.native_root / 'venv/bin/python'),
        images_source_head=IMAGE_SOURCE, images_source_tree=IMAGE_TREE,
        images={name: row['sha256'] for name, row in image_pins['images'].items()}, pg_manifest_sha256=pg['sha256'],
        libraries=libraries, native_tools=tools, admission_cli=admission_cli,
        admission_socket='/home/halbritt/.local/share/cairn/api.sock',
        system_configuration={path:digest(path) for path in ('/etc/hosts','/etc/nsswitch.conf')},
        provider=dict(provider='custom', url=provider_url, model=MODEL,
                      api_key_sha256=hashlib.sha256(b'r03-fixture-only').hexdigest(), fallback=[]))
    os.umask(0o077); output.mkdir(mode=0o700)
    plan = dict(schema='cairn.r03-hermes-native-plan/2', bindings=bindings,
        source_root=str(ROOT), native_root=str(args.native_root), manifest=str(args.manifest), pg_root=str(args.pg_root),
        root_images=str(args.root_images), image_directory=str(Path(image_pins['images']['api']['path']).parent),
        evidence_root=str(args.evidence_root), output=str(output), normal_home=os.environ['HOME'], normal_path=os.environ['PATH'],
        host_net_namespace=os.readlink('/proc/self/ns/net'), host_pid_namespace=os.readlink('/proc/self/ns/pid'),
        held=held, pg=pg, verified_compiled_source_files=source_count, admission_required=True, native_executed=False)
    write_json(output / 'plan.json', plan)
    write_json(output / 'admission-template.json', dict(schema='cairn.r03-hermes-native-admission/1',
        operation='one-direct-host-hermes-continuity', attempt_limit=1, bindings=bindings,
        expires_at='ROOT_MUST_SET_A_FRESH_RFC3339_EXPIRY'))
    print(json.dumps(dict(status='DIRECT_HOST_PLAN_PINNED',plan=str(output/'plan.json'),native='NOTRUN',ac4_closed=False)))


def host_private_call(plan, function, argument, native=False):
    code = ('import runpy,sys;sys.path.insert(0,' + repr(str(ROOT/'scripts')) + ');'
            'ns=runpy.run_path(' + repr(str(Path(__file__).resolve())) + ',run_name="r03_internal");'
            'ns[' + repr(function) + '](' + repr(str(argument)) + (',native=True' if native else '') + ')')
    return [str(Path(plan['native_root'])/'venv/bin/python'), '-I', '-B', '-c', code]


def require_host_view(plan):
    require(os.environ['HOME'] == plan['normal_home'] and os.environ['PATH'] == plan['normal_path'],
            'R03_HOST: normal HOME/PATH changed')
    require(os.readlink('/proc/self/ns/net') == plan['host_net_namespace']
            and os.readlink('/proc/self/ns/pid') == plan['host_pid_namespace'], 'R03_HOST: foreign namespace refused')
    require(str(ROOT) == plan['source_root'] and digest(__file__) == plan['bindings']['checker_sha256'],
            'R03_HOST: source/checker changed')
    for path, expected in {**plan['bindings']['libraries'], **plan['bindings']['native_tools'],
                            **plan['bindings']['system_configuration']}.items():
        require(digest(path) == expected, 'R03_HOST: pinned host input changed: ' + path)
    for name, expected in plan['bindings']['images'].items():
        require(digest(Path(plan['image_directory'])/name) == expected, 'R03_IMAGES: changed image: ' + name)


def run_mechanism_host(plan_path, mode, admission_context=None):
    plan = json.loads(Path(plan_path).read_bytes())
    require(plan['schema'] == 'cairn.r03-hermes-native-plan/2' and plan['bindings']['execution_mode'] == 'direct-host',
            'R03_HOST: obsolete execution plan refused')
    current = source_pins()
    require(not current['status'] and current['files'] == plan['bindings']['source_files']
            and current['head'] == plan['bindings']['source_head'] and current['tree'] == plan['bindings']['source_tree'],
            'R03_PLAN: source moved')
    require_host_view(plan)
    if mode == 'native':
        try:
            require(admission_context is not None, 'R03_ADMISSION: no native context')
            authenticated_admission(admission_context, plan['bindings'])
        except (PrerequisiteError, KeyError, ValueError, OSError) as error:
            raise AdmissionRefusal('R03_ADMISSION: ' + str(error)) from error
    verify_inputs(Path(plan['native_root']), Path(plan['manifest']))
    run_dir = Path(plan['output']) / ('native-run' if mode == 'native' else 'setup-run'); run_dir.mkdir(mode=0o700)
    scratch = Path(tempfile.mkdtemp(prefix='cairn-r03h-'))
    runtime = dict(plan, run_dir=str(run_dir), scratch_root=str(scratch),
                   admission_context=str(admission_context) if admission_context else None)
    write_json(run_dir/'runtime-plan.json',runtime)
    # Observe network/process identities without recording credential-bearing
    # send/receive buffers. Synthetic model bodies and hook stdin have their
    # own selected journals; workflow authentication wire bytes are never read.
    trace_calls = 'process,socket,socketpair,connect,bind,listen,accept,accept4,getsockname,getpeername,shutdown'
    descriptor, writer = os.pipe()
    reducer, stop = TraceReducer(scratch=scratch, exec_pins=mechanism_exec_pins(plan)), threading.Event()
    require(all((identity := reducer.identity(path)) is not None and identity['sha256'] == expected
                for path,expected in reducer.exec_pins.items()),
            'R03_PINS: executable changed before launch')
    reader = threading.Thread(target=reducer.drain, args=(descriptor, stop), daemon=True)
    worker_argv = host_private_call(plan, 'host_suite', run_dir/'runtime-plan.json', native=mode=='native')
    command = [shutil.which('strace'), '-f', '-s', '131072', '-xx', '-e', 'trace='+trace_calls,
               '-o', '/dev/fd/'+str(writer), *worker_argv]
    write_json(run_dir/'invocation.json',dict(argv=command,mode=mode,execution='direct normal host',normal_home=os.environ['HOME'],normal_path=os.environ['PATH']))
    reader.start()
    process = None; stdout = stderr = b''; timed_out = False
    try:
        process = subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=(writer,))
        os.close(writer); writer = None
        require(process.pid > 1, 'R03_PROCESS: invalid owned tracer PID')
        try: stdout, stderr = process.communicate(timeout=480)
        except subprocess.TimeoutExpired:
            timed_out = True; process.terminate()
            try: stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); stdout, stderr = process.communicate(timeout=5)
    finally:
        if writer is not None: os.close(writer)
        reader.join(timeout=5)
        if reader.is_alive():
            stop.set(); reader.join(timeout=1); reducer.refuse('trace_incomplete')
        # Unvalidated process diagnostics can echo argv. Retain facts only on
        # success AND failure; communicate's exceptions never escape with argv.
        write_json(run_dir/'process.json',dict(exit=process.returncode if process else None, timed_out=timed_out,
            stdout_bytes=len(stdout),stderr_bytes=len(stderr),
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            raw_streams_retained=False))
        stdout = stderr = b''
    policy_path = run_dir/'trace-policy.json'
    if not policy_path.is_file():
        reducer.rows.clear(); reducer.pending.clear()
        if scratch.exists() and not list(scratch.iterdir()): scratch.rmdir()
    require(policy_path.is_file(), 'R03_TRACE: controller policy unavailable; STOP')
    policy = json.loads(policy_path.read_bytes())
    syscall = audit_syscalls(set(policy['allowed_exec']),set(policy['allowed_unix']),
        {int(pid):label for pid,label in policy['phase_roots'].items()}, reducer=reducer,
        provider_url=policy['provider_url'],python_path=policy['python_path'],scratch_root=scratch,
        normal_path=plan['normal_path'],admission_socket=plan['bindings']['admission_socket'],
        admission_pids=policy['admission_pids'],exec_pins=policy['exec_pins'],
        owned_daemons={int(pid):path for pid,path in policy['owned_daemons'].items()})
    write_json(run_dir/'syscall-audit.json',syscall)
    require(not syscall['violations'], 'R03_AUDIT: unapproved or incomplete process/network observation; STOP')
    require(process.returncode==0 and not timed_out,'R03_MECHANISM: direct-host '+mode+' failed; STOP; metadata retained')
    print(json.dumps(dict(status='NATIVE_FIXTURE_PASSED' if mode=='native' else 'DIRECT_HOST_SETUP_ONLY_PASSED',native=mode=='native',ac4_closed=False,output=str(run_dir))))


class NativeAudit:
    """Passive Python call/engine observation plus fail-closed socket/exec audit.

    No held client, provider method or hook dispatcher is replaced. strace on
    the normal host additionally records every owned child exec and
    socket syscall, including Go/selector/auxiliary/finalizer descendants; HOME and PATH remain unchanged.
    """
    def __init__(self, output, spec):
        self.output, self.spec = Path(output), spec
        self.events, self.calls, self.violations, self.frames = [], [], [], {}

    def reject(self, message):
        self.violations.append(message)
        self.flush()
        raise PrerequisiteError(message)

    def audit(self, event, args):
        if event == 'socket.connect':
            address = args[1]
            target = urlsplit(self.spec['provider_url'])
            allowed = ((isinstance(address, tuple) and address[:2] == ('127.0.0.1', target.port))
                       or (isinstance(address, str) and address == self.spec['api_socket']))
            if not allowed: self.reject('R03_NETWORK: unexpected connection')
            self.events.append(dict(kind=event, address=str(address)))
        elif event == 'socket.getaddrinfo':
            if args[0] not in ('127.0.0.1', 'localhost'):
                self.reject('R03_NETWORK: external DNS refused')
            self.events.append(dict(kind=event, host=str(args[0])))
        elif event in ('subprocess.Popen', 'os.exec'):
            executable, argv = str(args[0]), args[1]
            row = dict(kind=event, executable_sha256=hashlib.sha256(executable.encode()).hexdigest(),
                       argument_count=len(argv) if isinstance(argv,(list,tuple)) else 1)
            self.events.append(row)
            import shutil
            resolved = str(Path(shutil.which(executable) or executable).resolve())
            if resolved not in self.spec['exec_pins']:
                self.reject('R03_EXEC: unexpected executable')
            if digest(resolved) != self.spec['exec_pins'][resolved]:
                self.reject('R03_EXEC: executable changed')
            actual = [str(value) for value in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
            exact = any(actual == candidate for candidate in self.spec['exact_argv'])
            git = self.spec['git_executable']
            if resolved == git:
                tail = actual[1:]
                if tail[:1] == ['-C']: tail = tail[2:]
                read_command = bool(tail) and tail[0] in ('--version','version','rev-parse','status','diff','log','show','ls-files','symbolic-ref')
                read_command = read_command or tail[:1] == ['branch'] and tail[1:] == ['--show-current']
                read_command = read_command or tail[:2] == ['worktree','list']
                read_command = read_command or tail[:1] == ['config'] and any(arg in ('--get','--get-all','--list') for arg in tail)
                exact = exact or read_command
            if not exact:
                self.reject('R03_EXEC: executable arguments outside the owned fixture contract')
            row.update(executable=resolved, argv_sha256=argv_digest(actual), argv_validated=True)

    def profile(self, frame, event, result):
        filename, name = frame.f_code.co_filename, frame.f_code.co_name
        if name == 'run_engine' and filename.endswith('/plugins/cairn/__init__.py'):
            if event == 'call':
                command = list(frame.f_locals['command'])
                if command not in self.spec['exact_argv']:
                    self.reject('R03_EXEC: unselected engine vector')
                row = dict(argv=command, payload=dict(frame.f_locals['payload']),
                           timeout=frame.f_locals.get('timeout'), started=time.monotonic())
                self.calls.append(row); self.frames[id(frame)] = row
            elif event == 'return' and id(frame) in self.frames:
                row = self.frames.pop(id(frame))
                if result is not None:
                    row['exit'] = result.returncode
                    for name, value in (('stdout',result.stdout),('stderr',result.stderr)):
                        raw = value.encode() if isinstance(value,str) else value
                        row[name+'_sha256'] = hashlib.sha256(raw).hexdigest()
                        row[name+'_bytes'] = len(raw)
                row['seconds'] = time.monotonic() - row['started']
        watched = ('/agent/auxiliary_client.py', '/hermes_cli/runtime_provider.py', '/openai/', '/httpx/', '/agent/backends/')
        if event == 'call' and (any(part in filename for part in watched)
                or name in ('chat', '_init_agent', '_finalize_single_query', '_run_cleanup')):
            row = dict(kind='python-call', file=filename, function=name)
            for key in ('provider', 'requested_provider', 'model', 'base_url', 'task'):
                value = frame.f_locals.get(key)
                if isinstance(value, (str, int, bool)): row[key] = value
            self.events.append(row)
            request = frame.f_locals.get('request')
            if '/httpx/' in filename and name == 'send' and request is not None:
                if str(request.url).startswith(self.spec['provider_url'] + '/'):
                    if request.headers.get('authorization') != 'Bearer r03-fixture-only':
                        self.reject('R03_PROVIDER: wrong credential selector before provider request')
            if name.startswith(('_try_payment_fallback', '_try_configured_fallback', '_try_main_fallback')):
                self.reject('R03_PROVIDER: fallback resolution is not admitted')
        if len(self.events) > 200_000:
            self.reject('R03_AUDIT: bounded call journal exceeded')

    def install(self):
        sys.addaudithook(self.audit)
        sys.setprofile(self.profile); threading.setprofile(self.profile)
        atexit.register(self.flush)  # Registered before native imports: runs after their cleanup.

    def flush(self):
        write_json(self.output / 'calls.json', self.calls)
        write_json(self.output / 'audit.json', dict(events=self.events, violations=self.violations))


def native_worker(spec_path):
    spec = json.loads(Path(spec_path).read_bytes())
    plan = json.loads(Path(spec['plan']).read_bytes())
    require_host_view(plan)
    grant = authenticated_admission(plan['admission_context'], plan['bindings'], journal=Path(spec['output'])/'admission-readback.json')
    # Live authenticated readback above MUST precede every held client import.
    ledger = Path.home()/'.cache/cairn301-native-admission-ledger-1467'
    if spec['label'] == 'A':
        ledger.mkdir(mode=0o700, exist_ok=True)
        require(ledger.resolve()==ledger and ledger.stat().st_uid==os.getuid()
                and stat.S_IMODE(ledger.stat().st_mode)==0o700, 'R03_ADMISSION: private ledger required')
        descriptor = os.open(ledger/(grant['event_id']+'.json'), os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        with os.fdopen(descriptor,'w') as stream: json.dump(grant,stream)
    else:
        prior = json.loads((ledger/(grant['event_id']+'.json')).read_bytes())
        require(prior['bindings']==plan['bindings'], 'R03_ADMISSION: continuation differs from the one native attempt')
    output, home = Path(spec['output']), Path(spec['home'])
    os.environ['HERMES_HOME'] = str(home)
    os.environ['TERMINAL_CWD'] = spec['project']
    audit = NativeAudit(output, spec); audit.install()
    sys.path.insert(0, str(Path(plan['native_root'])/'hermes'))
    from cli import HermesCLI, _finalize_single_query
    from agent.memory_provider import MemoryProvider
    from hermes_cli.plugins import PluginRegistration, get_plugin_manager
    from hermes_state import SessionDB

    cli = provider = None
    finalized = False
    result = {}
    try:
        manager = get_plugin_manager(); manager.discover_and_load()
        def registrations():
            return [{key: tuple(values) for key, values in registry.items() if values}
                    for registry in (manager._hooks, manager._middleware)]
        baseline_handles = registrations()
        cli = HermesCLI(model=MODEL, provider='custom', api_key='r03-fixture-only', base_url=spec['provider_url'],
                        toolsets=['mcp-cairn'], max_turns=1, compact=True, resume=spec['session'])
        initial_session = cli.session_id
        require(cli._fallback_model == [], 'R03_PROVIDER: fallback configured')
        require(cli.api_key == 'r03-fixture-only' and str(cli.base_url).rstrip('/') == spec['provider_url'],
                'R03_PROVIDER: constructor selected different credentials/target')
        if spec['session'] is not None:
            require(cli._preload_resumed_session(), 'R03_SESSION: real CLI failed supported resume')
            require(cli.session_id == spec['session'], 'R03_SESSION: conversation replaced')
            expected = [row for row in spec['previous_history'][0] if row.get('role') != 'session_meta']
            require(cli.conversation_history == expected, 'R03_SESSION: native resume changed history')
        require(cli._init_agent(), 'R03_CLIENT: real CLI agent initialization failed')
        agent = cli.agent
        provider = agent._memory_manager.get_provider('cairn')
        require(isinstance(provider, MemoryProvider), 'R03_CLIENT: genuine installed provider missing')
        handles = list(provider.registrations)
        require(len(handles) == 5 and all(isinstance(h, PluginRegistration) and h.active for h in handles),
                'R03_CLIENT: actual registration handles missing')
        require(provider.config == spec['selection'], 'R03_CLIENT: selected configuration changed')
        selected = provider.engine_command('UserPromptSubmit')
        baseline_history = None
        if spec['label'] == 'A':
            baseline_reply = cli.chat('Synthetic existing-history baseline before B is staged.')
            require(isinstance(baseline_reply, str) and 'Synthetic continuity response' in baseline_reply,
                    'R03_SESSION: real A baseline conversation did not complete')
            agent._memory_manager.flush_pending(timeout=10)
            require(cli.agent is agent and cli.session_id == initial_session,
                    'R03_SESSION: baseline replaced its native owner or session')
            baseline_db = SessionDB(db_path=home / 'state.db')
            try: baseline_history = baseline_db.get_resume_conversations(initial_session)
            finally: baseline_db.close()
            require(len([row for row in baseline_history[1] if row.get('role') == 'user']) == 1,
                    'R03_SESSION: no real existing-history baseline before staging B')
            require(digest(home / 'config.yaml') == spec['credential_selector_sha256'],
                    'R03_PROVIDER: baseline changed credential selectors')
            write_json(output / 'baseline-before-B.json', dict(session=initial_session,
                        history=baseline_history, cached_selection=provider.config))
        write_json(output / 'ready.json', dict(pid=os.getpid(), session=cli.session_id,
                    cached_selection=provider.config, cached_command=selected, baseline_history=baseline_history))
        deadline = time.monotonic() + 45
        while not (output / 'continue.json').exists():
            require(time.monotonic() < deadline, 'R03_BOUNDARY: parent did not release owned idle fixture')
            time.sleep(0.05)
        if spec['stage_b']:
            require(json.loads((home / 'cairn-lifecycle.json').read_bytes()) != spec['selection'],
                    'R03_BOUNDARY: B was not installed while A was cached')
            require(provider.config == spec['selection'] and provider.engine_command('UserPromptSubmit') == selected,
                    'R03_BOUNDARY: cached A changed before supported idle finalization')
            try:
                require_idle_boundary(False, cli.session_id, cli.session_id)
            except PrerequisiteError as refusal:
                write_json(output / 'refresh-refusal.json', dict(code=str(refusal), cached_owner_alive=True))
            else:
                raise PrerequisiteError('R03_BOUNDARY: premature transition was accepted')
        reply = cli.chat('Synthetic continuity prompt ' + spec['label'] + ': retain the selected phase.')
        require(isinstance(reply, str) and 'Synthetic continuity response' in reply, 'R03_CLIENT: actual scripted chat did not complete')
        require(cli.agent is agent and agent._memory_manager.get_provider('cairn') is provider,
                'R03_BOUNDARY: native chat silently recreated the cached provider/agent')
        require(cli.session_id == (spec['session'] or initial_session), 'R03_SESSION: chat rotated conversation')
        require(cli.api_key == 'r03-fixture-only' and str(cli.base_url).rstrip('/') == spec['provider_url'],
                'R03_PROVIDER: native credential selectors changed')
        _finalize_single_query(cli); finalized = True
        require(all(not handle.active for handle in handles), 'R03_CLIENT: registration handles leaked at finalization')
        require(registrations() == baseline_handles, 'R03_CLIENT: foreign hook/middleware registry changed')
        database = SessionDB(db_path=home / 'state.db')
        try:
            history = database.get_resume_conversations(cli.session_id)
            require(database.resolve_resume_session_id(cli.session_id) == cli.session_id, 'R03_SESSION: lineage replaced')
        finally:
            database.close()
        for call in audit.calls:
            event = call['payload']['hook_event_name']
            require(call['payload']['session_id'] == cli.session_id, 'R03_CALLBACK: foreign session effect')
            expected_argv = (spec['selection']['commands'][event] if 'commands' in spec['selection'] else
                             [sys.executable, spec['selection']['script'], '--config', spec['selection']['engine_config']])
            require(call['argv'] == expected_argv and call.get('exit') == 0, 'R03_CALLBACK: wrong selection or failed selected callback')
        require(any(row['payload']['hook_event_name'] == 'UserPromptSubmit' for row in audit.calls),
                'R03_CALLBACK: no actual native pre-LLM callback emitted')
        require(any(row['payload']['hook_event_name'] == 'SessionEnd' for row in audit.calls),
                'R03_CALLBACK: no actual native finalize callback emitted')
        require(not audit.violations, 'R03_AUDIT: provider/request/exec/finalizer violation')
        require(digest(home / 'config.yaml') == spec['credential_selector_sha256'], 'R03_PROVIDER: profile selectors changed')
        result = dict(native=True, label=spec['label'], session=cli.session_id, history=history,
                      finalized=True, callbacks=len(audit.calls), registrations_disposed=True,
                      native_turns=2 if spec['label'] == 'A' else 1,
                      credential_selector_sha256=spec['credential_selector_sha256'])
        write_json(output / 'result.json', result)
    finally:
        if cli is not None and not finalized:
            _finalize_single_query(cli)
        audit.flush()


def auxiliary_routes(native_root, provider_url):
    tree = ast.parse((Path(native_root)/'hermes/hermes_cli/config_defaults.py').read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict): continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == 'auxiliary' and isinstance(value, ast.Dict):
                return {key.value: dict(provider='custom', model=MODEL, base_url=provider_url,
                    api_key='r03-fixture-only', fallback_chain=[], timeout=10)
                    for key, item in zip(value.keys, value.values)
                    if isinstance(key, ast.Constant) and isinstance(item, ast.Dict)}
    raise PrerequisiteError('R03_INTERFACE: held auxiliary routing defaults unavailable')


def retain_evidence(root, destination):
    """Default to metadata; select only checker-owned synthetic receipts.

    Application profiles, logs, databases, identity documents, code and unknown
    future files are never content evidence. Configuration continuity is proved
    by its existing hashes, not by copying credential-bearing configuration.
    """
    destination = Path(destination); destination.mkdir(mode=0o700)
    selected = {
        'host-view.json', 'owned-processes.json', 'seed.json',
        'retained2-before-use.json', 'credential-selectors.json',
        'selection-A.json', 'selection-B.json', 'selection.json',
        'transport-requests.json', 'stored-bodies.txt',
        'capture_observation.jsonl', 'recall_observation.jsonl',
        'result.json', 'cleanup.json', 'admission-readback.json',
        'selector-calls.jsonl',
    }
    phase_receipts = {'spec.json', 'ready.json', 'continue.json', 'result.json',
                      'calls.json', 'audit.json', 'admission-readback.json',
                      'baseline-before-B.json', 'refresh-refusal.json'}
    metadata = {}
    for path in sorted(Path(root).rglob('*')):
        relative = str(path.relative_to(root))
        if relative == 'cluster/data' or relative.startswith('cluster/data/'): continue
        info = path.lstat()
        metadata[relative] = dict(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, inode=info.st_ino,
                                  device=info.st_dev, links=info.st_nlink)
        if path.is_symlink():
            metadata[relative]['symlink_sha256'] = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(); continue
        if not path.is_file() or path.is_socket(): continue
        metadata[relative]['sha256'] = digest(path)
        metadata[relative]['bytes'] = info.st_size
        parts = Path(relative).parts
        keep = relative in selected or (len(parts) == 2 and parts[0] in ('A', 'B', 'retained2')
                                         and parts[1] in phase_receipts)
        metadata[relative]['content_selected'] = keep
        if not keep: continue
        require(info.st_uid == os.getuid() and info.st_nlink == 1 and info.st_size <= 16 * 1024 * 1024,
                'R03_PRIVACY: invalid selected receipt metadata')
        raw = path.read_bytes()
        # Selected receipt schemas contain credential hashes/selectors only.
        # Reject credential fields rather than redacting or altering proof bytes.
        if path.suffix in ('.json', '.jsonl') and raw.strip():
            values = ([json.loads(raw)] if path.suffix == '.json' else
                      [json.loads(line) for line in raw.splitlines() if line.strip()])
            def no_credentials(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        require(key.lower() not in ('token', 'access_token', 'refresh_token', 'api_key',
                            'password', 'secret', 'authorization', 'bearer', 'identities'),
                            'R03_PRIVACY: credential field in selected receipt')
                        no_credentials(item)
                elif isinstance(value, list):
                    for item in value: no_credentials(item)
            for value in values: no_credentials(value)
        target = destination / relative; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        target.write_bytes(raw); target.chmod(0o600)
    write_json(destination/'filesystem-before-cleanup.json',metadata)


def argv_digest(argv):
    return hashlib.sha256(json.dumps(list(argv), ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def mechanism_exec_pins(plan):
    """Exact stable images, tools and interpreter pinned before tracing starts."""
    pins = dict(plan['bindings']['native_tools'])
    pins.update({str(Path(plan['image_directory'])/name): value
                 for name,value in plan['bindings']['images'].items()})
    pins[str(Path(plan['native_root'])/'venv/bin/python')] = plan['bindings']['interpreter_sha256']
    manifest = Path(plan['pg_root']).parent/'native-pg-pins.json'
    require(digest(manifest) == plan['bindings']['pg_manifest_sha256'], 'R03_PINS: PG inventory changed')
    for name,row in json.loads(manifest.read_bytes())['files'].items():
        if Path(name).parent == Path('usr/lib/postgresql/18/bin'):
            pins[str(Path(plan['pg_root'])/name)] = row['sha256']
    for alias in ('/bin/sh',shutil.which('sh')):
        resolved = str(Path(alias).resolve())
        require(resolved in pins, 'R03_PINS: undeclared shell anchor')
        pins[alias] = pins[resolved]
    return pins


def probe_sha256(python):
    vector = [python, '-I', '-S', '-B', '-c',
        'import json,sys;print(json.dumps(dict(version=sys.version,path=sys.path,isolated=sys.flags.isolated,no_site=sys.flags.no_site)))']
    raw = '#!/bin/sh\nexec '+shlex.join(vector)+'\n'
    return hashlib.sha256(raw.encode()).hexdigest()


class TraceReducer:
    """Bounded in-memory strace input. No raw line, argv or error is an artifact."""
    max_line, max_bytes, max_rows = 1024*1024, 128*1024*1024, 50000

    def __init__(self, scratch=None, exec_pins=None):
        self.scratch, self.exec_pins = scratch, exec_pins or {}
        self.rows, self.pending, self.errors = [], {}, []
        self.identities, self.terminal = {}, set()
        self.bytes = 0
        for path in self.exec_pins: self.identity(path)

    def identity(self, path):
        """Observe only declared host images or owned disposable files."""
        candidate = Path(path)
        try:
            resolved = candidate.resolve()
            owned = self.scratch is not None and candidate.is_relative_to(Path(self.scratch))
            if str(candidate) not in self.exec_pins and not owned: return None
            if owned and candidate != resolved: return None
            info = resolved.stat()
            if not stat.S_ISREG(info.st_mode): return None
            key = (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
            if key not in self.identities:
                self.identities[key] = dict(sha256=digest(resolved),uid=info.st_uid,
                    mode=stat.S_IMODE(info.st_mode),links=info.st_nlink,
                    private_parent=resolved.parent.stat().st_uid == os.getuid()
                        and stat.S_IMODE(resolved.parent.stat().st_mode) == 0o700)
            return self.identities[key]
        except (OSError, RuntimeError):
            return None

    def refuse(self, code):
        if code not in self.errors: self.errors.append(code)

    @staticmethod
    def string(token):
        value = ast.literal_eval(token)
        if '\\x' in token: value = value.encode('latin1').decode('utf-8')
        return value

    def feed(self, line):
        self.bytes += len(line)
        if self.bytes > self.max_bytes or len(line) > self.max_line or len(self.rows) >= self.max_rows:
            self.refuse('trace_limit'); return
        try:
            line = line.decode('ascii') if isinstance(line, bytes) else line
            match = re.fullmatch(r'(\d+)\s+(.*?)\s*', line)
            if not match: raise ValueError()
            pid, call = int(match[1]), match[2]
            if pid <= 1: raise ValueError()
            if call.startswith('<... ') and ' resumed>' in call:
                call = self.pending.pop(pid) + call.split(' resumed>', 1)[1]
            if '<detached ...>' in call:
                if not call.endswith('<detached ...>'): raise ValueError()
                prefix = call.split('<detached ...>')[0]
                if prefix.strip():
                    if pid in self.pending: raise ValueError()
                    self.pending[pid] = prefix
                self.rows.append(dict(pid=pid,kind='detached',result=None)); return
            if '<unfinished ...>' in call:
                if pid in self.pending or len(self.pending) >= 4096: raise ValueError()
                self.pending[pid] = call.split('<unfinished ...>')[0]; return
            if call.startswith('+++ exited with '):
                status = re.fullmatch(r'\+\+\+ exited with (\d+) \+\+\+', call)
                if not status: raise ValueError()
                self.terminal.add(pid)
                self.rows.append(dict(pid=pid, kind='exit', result=int(status[1]))); return
            if call.startswith(('--- ', '+++ killed by ')):
                signal_name = re.match(r'(?:--- |\+\+\+ killed by )(SIG[A-Z0-9]+)', call)
                if not signal_name: raise ValueError()
                if call.startswith('+++ killed by '): self.terminal.add(pid)
                self.rows.append(dict(pid=pid, kind='signal', signal=signal_name[1])); return
            parsed = re.fullmatch(r'(\w+)\((.*)\)\s+=\s+(-?\d+|\?)(?: ([A-Z][A-Z0-9_]+))?.*', call)
            if not parsed: raise ValueError()
            name, args, result, error = parsed.groups(); args = args.strip()
            row = dict(pid=pid, kind=name, result=int(result) if result != '?' else None)
            if error: row['errno'] = error
            if name == 'execve':
                vector = re.fullmatch(r'("(?:[^"\\]|\\.)*"), (\[.*\]), (?:0x[0-9a-f]+|NULL)(?: /\* \d+ vars \*/)?', args)
                if not vector: raise ValueError()
                path = self.string(vector[1])
                # literal_eval rejects truncation/ellipsis and non-string argv.
                argv = ast.literal_eval(vector[2])
                if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv): raise ValueError()
                if '\\x' in vector[2]: argv = [v.encode('latin1').decode('utf-8') for v in argv]
                row.update(path=path, identity=self.identity(path), argument_count=len(argv),
                    selector_argument=bool(self.scratch and str(Path(self.scratch)/'selector') in argv))
            elif name == 'execveat':
                self.refuse('unapproved_execveat'); return
            elif name in ('clone', 'clone3', 'fork', 'vfork'):
                if row['result'] and row['result'] > 1: row['child'] = row['result']
            elif name in ('kill','tkill','tgkill'):
                signal_call = re.fullmatch(r'(-?\d+), (?:(-?\d+), )?(SIG[A-Z0-9]+|0)',args)
                if not signal_call: raise ValueError()
                targets = [int(value) for value in signal_call.groups()[:2] if value is not None]
                row.update(targets=targets,signal=signal_call[3])
            elif name in ('connect','bind','accept','accept4','getsockname','getpeername'):
                quoted = r'"(?:[^"\\]|\\.)*"'
                unix = re.search(r'sa_family=AF_UNIX, sun_path=('+quoted+')', args)
                inet = re.search(r'sa_family=AF_INET, sin_port=htons\((\d+)\), sin_addr=inet_addr\(('+quoted+r')\)', args)
                if unix: row['sockaddr'] = dict(family='AF_UNIX', path=self.string(unix[1]))
                elif inet: row['sockaddr'] = dict(family='AF_INET', port=int(inet[1]), address=self.string(inet[2]))
                elif name in ('connect', 'bind'): row['sockaddr'] = dict(family='unknown')
            elif name not in ('socket','socketpair','listen','shutdown','wait4','waitid','exit','exit_group',
                              'setsid','setpgid','getpgid','getsid','getpgrp','restart_syscall',
                              'getpid','getppid','gettid','pidfd_open'):
                self.refuse('unparsed_syscall'); return
            self.rows.append(row)
        except (ValueError, TypeError, KeyError, SyntaxError, UnicodeError, RecursionError):
            self.refuse('trace_parse_error')
        # Raw input and argv exist only inside this call, never in self.rows.

    def drain(self, descriptor, stop):
        pending = b''
        try:
            while not stop.is_set():
                if not select.select([descriptor], [], [], .2)[0]: continue
                chunk = os.read(descriptor, 65536)
                if not chunk: break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1); self.feed(line)
                if len(pending) > self.max_line:
                    self.refuse('trace_line_limit'); pending = b''
            if pending: self.feed(pending)
        except OSError:
            self.refuse('trace_pipe_error')
        finally:
            pending = b''
            os.close(descriptor)


def audit_syscalls(allowed_exec, allowed_unix, phase_roots=None, trace_path=None,
                   provider_url=PROVIDER_URL, python_path=None, scratch_root=None, normal_path='',
                   admission_socket=None, admission_pids=(), reducer=None, exec_pins=None, owned_daemons=None):
    if reducer is None:
        reducer = TraceReducer()
        with Path(trace_path).open('rb') as stream:
            for line in stream: reducer.feed(line.rstrip(b'\n'))
    violations = list(reducer.errors)
    parents = {row['child']:row['pid'] for row in reducer.rows if 'child' in row}
    def ancestor(pid, candidates):
        seen = set()
        while pid not in seen:
            seen.add(pid)
            if pid in candidates: return pid
            if pid not in parents: return None
            pid = parents[pid]
        return None
    target = urlsplit(provider_url)
    owned_daemons = owned_daemons or {}
    def daemon_gone(pid):
        return ancestor(pid,owned_daemons) is not None and not Path('/proc',str(pid)).exists()
    shutdown, unresolved = [], []
    for pid, prefix in reducer.pending.items():
        match = re.match(r'(\w+)\(',prefix)
        name = match[1] if match else None
        harmless = name in ('accept','accept4','wait4','waitid','restart_syscall','exit','exit_group',
            'socket','socketpair','listen','shutdown','getsockname','getpeername',
            'getpid','getppid','gettid','getsid','getpgid','getpgrp')
        thread_creation = name in ('clone','clone3') and re.search(r'\bCLONE_THREAD\b',prefix) is not None
        if (harmless or thread_creation) and (pid in reducer.terminal or daemon_gone(pid)):
            shutdown.append(dict(pid=pid,kind=name,result=None,expected_shutdown=True))
        else:
            violations.append('unfinished audited operation')
            unresolved.append(dict(pid=pid,kind=name if name in ('execve','connect','clone','clone3','exit','exit_group',
                'accept','accept4','wait4','waitid','restart_syscall','socket','socketpair','bind','listen','shutdown',
                'getsockname','getpeername','kill','tkill','tgkill') else 'other',terminated=pid in reducer.terminal))
    normal_dirs = {str(Path(path).resolve()) for path in normal_path.split(':') if path}
    tool_names = {Path(path).name for path in allowed_exec} | {'sh'}
    executions, connections, probes, workflow, processes = [], [], [], [], []
    for row in reducer.rows:
        pid, kind = row['pid'], row['kind']
        safe = {key:value for key,value in row.items() if key in ('pid','kind','result','errno','child','signal','targets')}
        if kind == 'execve':
            path = row['path']
            negative = (Path(path).name in tool_names and str(Path(path).parent.resolve()) in normal_dirs
                        and row.get('errno') == 'ENOENT')
            identity = row.get('identity')
            anchor = (scratch_root is not None and python_path is not None and
                re.fullmatch(re.escape(str(Path(scratch_root)/'project/release'))+r'/\.prepare-[A-Za-z0-9_-]{1,64}/probe', path)
                and identity is not None and identity['uid'] == os.getuid() and identity['mode'] == 0o500
                and identity['links'] == 1 and identity['private_parent']
                and identity['sha256'] == probe_sha256(python_path))
            known_path = negative or anchor or path in allowed_exec
            pinned = (negative or anchor or exec_pins is None or identity is not None and
                      identity['sha256'] == exec_pins.get(path))
            if not known_path or not pinned: violations.append('unapproved or changed executable')
            if known_path: safe.update(path=path, argument_count=row['argument_count'], executable_validated=bool(pinned))
            else: safe['path_sha256'] = hashlib.sha256(path.encode()).hexdigest()
            if negative or anchor: probes.append(safe)
            executions.append(safe)
            owner = ancestor(pid, phase_roots or {})
            if owner is not None and phase_roots[owner] in ('A','B') and path == python_path and pid != owner:
                if not row['selector_argument']: violations.append('Source Python fallback in Go phase')
        elif kind == 'detached':
            if pid not in reducer.terminal and not daemon_gone(pid): violations.append('unexpected detached process')
            processes.append(safe)
        elif 'sockaddr' in row:
            address = row['sockaddr']; family = address['family']
            absent_nss = family == 'AF_UNIX' and address['path'] == '/var/run/nscd/socket' and row.get('errno') == 'ENOENT'
            workflow_read = (family == 'AF_UNIX' and address['path'] == admission_socket
                             and ancestor(pid,set(admission_pids)) is not None)
            owned = (family == 'AF_UNIX' and address['path'] in allowed_unix or
                     family == 'AF_INET' and address['address'] == '127.0.0.1' and address['port'] == target.port)
            # Unnamed ephemeral bind (port 0) is allowed only on loopback.
            local_ephemeral = kind == 'bind' and family == 'AF_INET' and address['address'] == '127.0.0.1' and address['port'] == 0
            # Local/accepted socket names are endpoint observations, not new
            # outbound admissions. Every connect/bind is checked independently.
            local_observation = (kind in ('accept','accept4','getsockname','getpeername')
                and family == 'AF_INET' and address['address'] == '127.0.0.1' and 0 < address['port'] <= 65535)
            if not (owned or absent_nss or workflow_read or local_ephemeral or local_observation):
                violations.append('unapproved socket target')
                safe['sockaddr_sha256'] = hashlib.sha256(json.dumps(address,sort_keys=True).encode()).hexdigest()
            else: safe['sockaddr'] = address
            connections.append(safe)
            if absent_nss: probes.append(safe)
            if workflow_read: workflow.append(safe)
        else: processes.append(safe)
    for pid,path in owned_daemons.items():
        if not any(row['pid'] == pid and row.get('path') == path and row['result'] == 0
                   and row.get('executable_validated') for row in executions):
            violations.append('missing owned daemon execution')
    if owned_daemons:
        for path in allowed_unix:
            if not any(row['kind'] == 'connect' and row['result'] == 0
                       and row.get('sockaddr',{}).get('path') == path for row in connections):
                violations.append('missing owned socket connection')
    result = dict(execve=executions, connect=connections, processes=processes,expected_shutdown=shutdown,
        unfinished=unresolved,
        declared_negative_or_anchor_probes=probes, authenticated_workflow_reads=workflow,
        violations=violations, raw_trace_retained=False, raw_argv_retained=False,
        input_bytes=reducer.bytes, rows=len(reducer.rows))
    reducer.rows.clear(); reducer.pending.clear()
    return result


def host_suite(plan_path, native=False):
    plan = json.loads(Path(plan_path).read_bytes()); require_host_view(plan)
    native_root = Path(plan['native_root']); images = Path(plan['image_directory'])
    provider_url = plan['bindings']['provider']['url']
    verify_inputs(native_root,Path(plan['manifest']))
    if not native:
        def no_native_import(event, args):
            if event == 'import' and args[0] in ('cli', 'run_agent'):
                raise PrerequisiteError('R03_SETUP: native import forbidden in setup-only mode')
        sys.addaudithook(no_native_import)
    # All of these are maintained Cairn fixtures; they do not import HermesCLI.
    import disposable_host
    import check_r03_hermes_installed as installed
    from test_r03_hermes_installer import installer

    os.umask(0o077)
    root = Path(plan['scratch_root'])
    require(root.parent == Path(tempfile.gettempdir()) and root.name.startswith('cairn-r03h-')
            and not root.is_symlink() and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700 and not list(root.iterdir()),
            'R03_CLEANUP: fresh owned scratch required')
    run_dir = Path(plan['run_dir'])
    write_json(root/'host-view.json',dict(home=os.environ['HOME'],path=os.environ['PATH'],pid=os.getpid(),
        net_namespace=os.readlink('/proc/self/ns/net'),pid_namespace=os.readlink('/proc/self/ns/pid'),
        usr_uid=Path('/usr').stat().st_uid,usr_mode=stat.S_IMODE(Path('/usr').stat().st_mode),
        scratch=str(root),execution='normal host; no environment/home replacement'))
    pg_bin = Path(plan['pg_root'])/'usr/lib/postgresql/18/bin'
    cluster = host = None
    cleanup = {}; phases = []; session = None; previous_history = None
    allowed_exec = set(mechanism_exec_pins(plan))
    allowed_exec.update(str(path.resolve()) for path in images.iterdir() if path.is_file())
    allowed_exec.update(str(path.resolve()) for path in pg_bin.iterdir() if path.is_file())
    python = str(native_root/'venv/bin/python')
    allowed_exec.add(str(Path(python).resolve()))
    allowed_unix = set()
    trace_exec_pins = mechanism_exec_pins(plan)
    phase_roots = {}
    try:
        grant = authenticated_admission(plan['admission_context'],plan['bindings'],journal=root/'admission-readback.json') if native else None
        cluster = disposable_host.OwnedCluster.start(root / 'cluster', pg_bin=pg_bin)
        host = disposable_host.DisposableHost.start(cluster, binary=images/'api',
            repo='fixture:r03-hermes-native-continuity', database='r03_hermes_continuity',
            home=root / 'api', roles=('worker',), principals={'worker': 'agent:r03-hermes-fixture'},
            label='r03_hermes_continuity', log=root / 'api.log')
        host.require_fresh('memory_record')
        pg_pid = int((root / 'cluster/data/postmaster.pid').read_text().splitlines()[0])
        require(pg_pid > 1 and host.process.pid > 1, 'R03_PROCESS: invalid owned PID')
        allowed_unix.update((str(host.socket), str(Path(cluster.socket) / '.s.PGSQL.5432')))
        write_json(root / 'owned-processes.json', dict(postgres=pg_pid, api=host.process.pid,
            postgres_executable=os.readlink('/proc/' + str(pg_pid) + '/exe'),
            api_executable=os.readlink('/proc/' + str(host.process.pid) + '/exe')))
        seed = host.agent('worker', 'remember', 'Synthetic baseline retention policy.',
            args=('--repo', host.repo, '--kind', 'decision', '--shareable', '--request-id', str(uuid.uuid4()), '--stdin'))
        write_json(root / 'seed.json', seed)
        selector = root / 'selector'
        selector.write_text('#!'+python+'\n' +
            'import json,pathlib,sys\np=pathlib.Path(__file__).parent\n'
            'import hashlib\n'
            'with (p/"selector-calls.jsonl").open("a") as f: f.write(json.dumps(dict(argv_sha256=hashlib.sha256(json.dumps(sys.argv[1:]).encode()).hexdigest(),stdin=sys.stdin.read()))+"\\n")\n'
            'print(json.dumps(json.loads((p/"selection.json").read_text())))\n')
        selector.chmod(0o500); allowed_exec.add(str(selector)); trace_exec_pins[str(selector)] = digest(selector)
        installed.SOURCE_REVISION = IMAGE_SOURCE
        f = installed.HermesInstalledFixture(root / 'project', images/'control',
                                             stager=images/'stager', harness='hermes', enabled=False)
        for config in (f.memory, f.coord):
            config.update(cairn=str(images/'api'), socket=str(host.socket),
                          token_file=str(host.token('worker')), repo=host.repo)
        f.memory.update(claude=str(selector), model=MODEL, recall_mode='agent_tools',
                        semantic_fallback=False, capture_observations=True, recall_observations=True)
        f.publish_configuration(enabled=False)
        home = f.account
        write_json(home / 'config.yaml', dict(model=dict(default=MODEL, provider='custom', base_url=provider_url,
                api_key='r03-fixture-only'), fallback_model=[], fallback_providers=[],
                auxiliary=auxiliary_routes(native_root,provider_url), memory=dict(memory_enabled=True), plugins=dict(enabled=[])))
        skill = root / 'fixture-skill.md'; skill.write_text('Synthetic retained continuity fixture.\n')
        native_config = dict(executable=str(images/'api'), socket=str(host.socket),
                             token_file=str(host.token('worker')), repo=host.repo)
        installer.install(home, native_config, str(selector), skill, model=MODEL, recall_mode='agent_tools')
        legacy = json.loads((home / 'cairn-lifecycle.json').read_bytes())
        engine_path = Path(legacy['engine_config'])
        engine = json.loads(engine_path.read_bytes())
        engine.update(state_dir=f.memory['state_dir'], semantic_fallback=False,
                      capture_observations=True, recall_observations=True)
        require(engine.get('claude') == str(selector), 'R03_SETUP: retained memory config lacks the installed selector')
        installer.shared.write_json(engine_path, engine)
        retained = {str(path): path.read_bytes() for path in
                    (home / 'cairn-lifecycle.json', home / 'cairn/installed.json', engine_path)}
        legacy_members = {str(path): digest(path) for path in (home / 'plugins/cairn').rglob('*') if path.is_file()}
        write_json(root / 'retained2-before-use.json', dict(schema='cairn.r03-retained-python/2',
                    selection=legacy, members=legacy_members, engine_sha256=digest(engine_path)))
        def install_go():
            installer.install(home, native_config, str(selector), skill,
                               lifecycle_selection=f.selected, stager=str(images/'stager'))
            return json.loads((home / 'cairn-lifecycle.json').read_bytes())
        selected_a = install_go()
        credential_sha = digest(home / 'config.yaml')
        write_json(root / 'credential-selectors.json', dict(path=str(home / 'config.yaml'), sha256=credential_sha,
                    provider='custom', model=MODEL, base_url=provider_url, key_sha256=hashlib.sha256(b'r03-fixture-only').hexdigest()))
        selected_b = None
        def stage_b():
            nonlocal selected_b
            f.publish_configuration(enabled=False)
            selected_b = install_go()
            require(selected_b != selected_a, 'R03_SELECTION: B did not create a distinct checked selection')
            write_json(root / 'selection-B.json', selected_b)
        write_json(root / 'selection-A.json', selected_a)

        def run_phase(label, selection, transition=None):
            nonlocal session, previous_history
            phase = root / label; phase.mkdir(mode=0o700)
            write_json(root / 'selection.json', dict(result=dict(structured_output=dict(checkpoint=None, workstream=None,
                memories=[dict(record_id=None, kind='decision', title='Synthetic phase ' + label,
                               body='Selected continuity phase ' + label + ' retention.')]))))
            before = {table: host.count(table) for table in ('memory_record', 'capture_observation', 'recall_observation')}
            if not native:
                # A stub of the process boundary ONLY. No fake CLI, callback,
                # registration, auth record, transcript or native outcome.
                if transition: transition()
                try: require_idle_boundary(False, 'setup-only', 'setup-only')
                except PrerequisiteError: pass
                else: raise PrerequisiteError('R03_SETUP: open-owner negative control failed')
                row = dict(label=label, evidence='SETUP_STUB_ONLY', selection=selection,
                           native=False, client_imported=False, before=before, after=before)
                write_json(phase / 'result.json', row); phases.append(row)
                return
            git_executable = str(Path(shutil.which('git')).resolve())
            mcp = json.loads((home/'config.yaml').read_bytes())['mcp_servers']['cairn']
            exact_argv = [[mcp['command'], *mcp['args']]]
            actor_exec = {str(Path(python).resolve()), str((images/'api').resolve()), git_executable}
            exec_pins = {path: digest(path) for path in actor_exec}
            for config in (selection, selected_a, selected_b or {}):
                for argv in config.get('commands', {}).values():
                    exec_pins[str(Path(argv[0]).resolve())] = digest(argv[0]); allowed_exec.add(str(Path(argv[0]).resolve()))
                    exact_argv.append(argv)
                    exec_pins[str(Path(argv[0]).resolve())] = digest(argv[0])
            if 'commands' not in selection:
                exact_argv.append([python, selection['script'], '--config', selection['engine_config']])
            spec = dict(label=label, selection=selection, home=str(home), project=str(f.root),
                output=str(phase), session=session, previous_history=previous_history,
                stage_b=transition is not None, api_socket=str(host.socket),
                exec_pins=exec_pins, exact_argv=exact_argv, git_executable=git_executable,
                provider_url=provider_url, plan=str(plan_path), credential_selector_sha256=credential_sha)
            trace_exec_pins.update(exec_pins)
            write_json(phase / 'spec.json', spec)
            argv = host_private_call(plan,'native_worker',phase/'spec.json')
            process = subprocess.Popen(argv, cwd=f.root, stdin=subprocess.DEVNULL,
                stdout=(phase / 'stdout').open('wb'), stderr=(phase / 'stderr').open('wb'), start_new_session=True)
            require(process.pid > 1, 'R03_PROCESS: invalid owned CLI PID')
            phase_roots[process.pid] = label
            try:
                deadline = time.monotonic() + 60
                while not (phase / 'ready.json').exists():
                    require(process.poll() is None and time.monotonic() < deadline,
                            'R03_CLIENT: native owner failed before ready; retain raw process output')
                    time.sleep(0.05)
                ready = json.loads((phase / 'ready.json').read_bytes())
                require(ready['pid'] == process.pid, 'R03_PROCESS: wrong owned CLI ready identity')
                if session is None: session = ready['session']
                require(ready['session'] == session, 'R03_SESSION: new conversation between phases')
                if transition: transition()
                write_json(phase / 'continue.json', dict(parent=os.getpid(), session=session))
                require(process.wait(timeout=180) == 0, 'R03_CLIENT: actual native phase failed; STOP')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=5)
            row = json.loads((phase / 'result.json').read_bytes())
            require(row['finalized'] is True and row['session'] == session, 'R03_BOUNDARY: no supported finalized owner')
            require_idle_boundary(True, session, row['session'])
            audit = json.loads((phase / 'audit.json').read_bytes())
            require(not audit['violations'], 'R03_AUDIT: late finalizer/atexit violation')
            sys.path.insert(0, str(Path(plan['native_root'])/'hermes'))
            from hermes_state import SessionDB
            readback = SessionDB(db_path=home / 'state.db')
            try: history = readback.get_resume_conversations(session)
            finally: readback.close()
            require(json.loads(json.dumps(history)) == row['history'], 'R03_SESSION: history changed during late finalization')
            calls = json.loads((phase / 'calls.json').read_bytes())
            for call in calls:
                event = call['payload']['hook_event_name']
                expected = (selection['commands'][event] if 'commands' in selection else
                            [python, selection['script'], '--config', selection['engine_config']])
                require(call['payload']['session_id'] == session and call['argv'] == expected
                        and call.get('exit') == 0, 'R03_CALLBACK: late foreign, fallback or failed callback')
                if 'commands' in selection:
                    require(call['timeout'] == (150 if event in ('PreCompact', 'SessionEnd') else 13),
                            'R03_CALLBACK: H13/H150 changed')
            expected_prefix = previous_history if previous_history is not None else ready['baseline_history']
            if expected_prefix is not None:
                for index in (0, 1):
                    require(history[index][:len(expected_prefix[index])] == expected_prefix[index],
                            'R03_SESSION: history prefix lost, duplicated or replaced')
            users = [message for message in history[1] if message.get('role') == 'user']
            expected_turns = sum(prior['native_turns'] for prior in phases) + row['native_turns']
            require(len(users) == expected_turns, 'R03_SESSION: duplicated or missing real native turn')
            previous_history = history
            after = {table: host.count(table) for table in before}
            require(after['memory_record'] == before['memory_record'] + row['native_turns'], 'R03_EFFECTS: missing or duplicated native capture')
            require(after['recall_observation'] == before['recall_observation'] + row['native_turns'], 'R03_EFFECTS: missing or duplicated native recall')
            row.update(before=before, after=after, owned_pid=process.pid, child_reaped=True)
            write_json(phase / 'result.json', row); phases.append(row)

        with ScriptedChat(root/'transport-requests.json',port=urlsplit(provider_url).port) as transport:
            require(transport.url==provider_url,'R03_PROVIDER: owned listener target differs')
            run_phase('A', selected_a, transition=stage_b)
            require(selected_b is not None, 'R03_SELECTION: B was not staged')
            require(digest(home / 'config.yaml') == credential_sha, 'R03_PROVIDER: B staging changed credential selectors')
            transport.phase = 'B'; run_phase('B', selected_b)
            # Explicit reverse to the retained /2 image. No error path calls it.
            for path, raw in retained.items():
                pending = Path(path + '.reverse'); pending.write_bytes(raw); pending.chmod(0o600); pending.replace(path)
            require(all(digest(path) == sha for path, sha in legacy_members.items()), 'R03_REVERSE: retained Python code changed')
            require(digest(home / 'config.yaml') == credential_sha, 'R03_REVERSE: credential selectors changed')
            transport.phase = 'retained2'; run_phase('retained2', legacy)
        require(not transport.violations, 'R03_TRANSPORT: unexpected auxiliary/main/finalizer request')
        counts = {table: host.count(table) for table in ('memory_record', 'capture_observation', 'recall_observation',
                                                       'agent_session_attempt', 'agent_delivery')}
        require(counts['agent_session_attempt'] == counts['agent_delivery'] == 0,
                'R03_EFFECTS: unexpected coordination/auth registration effects')
        require(counts['memory_record'] == (5 if native else 1), 'R03_EFFECTS: total Store effects mismatch')
        bodies = host.sql('SELECT body FROM cairn.record_version')
        require('Synthetic continuity prompt' not in bodies, 'R03_PRIVACY: raw native dialogue stored')
        (root / 'stored-bodies.txt').write_text(bodies)
        for table in ('capture_observation', 'recall_observation'):
            (root / (table + '.jsonl')).write_text(host.sql('SELECT row_to_json(t) FROM cairn.' + table + ' t'))
        require(native or ('cli' not in sys.modules and 'run_agent' not in sys.modules), 'R03_SETUP: native client imported')
        write_json(root / 'result.json', dict(status='NATIVE_MECHANISM_PASSED' if native else 'SETUP_STUB_ONLY_PASSED',
            native=native, ac4_closed=False, session=session, phases=phases, counts=counts,
            credential_selector_sha256=credential_sha, source=plan['bindings'],
            shared_unit='Unknown/shared watcher/config/binding users remain for R06; no independence claim.'))
    finally:
        if host is not None: cleanup['api_exit'] = host.close()
        if cluster is not None: cleanup['postgres_stopped'] = cluster.stop()
        write_json(root / 'cleanup.json', cleanup)
        admission_pids = []
        for receipt in root.rglob('admission-readback.json'):
            for row in json.loads(receipt.read_bytes())['processes']:
                admission_pids.append(row['pid'])
        owned_daemons = {}
        if (root/'owned-processes.json').is_file():
            owned = json.loads((root/'owned-processes.json').read_bytes())
            owned_daemons = {owned[name]:owned[name+'_executable'] for name in ('api','postgres')}
        write_json(run_dir/'trace-policy.json', dict(allowed_exec=sorted(allowed_exec),allowed_unix=sorted(allowed_unix),
            phase_roots=phase_roots,provider_url=provider_url,python_path=python,admission_pids=admission_pids,
            exec_pins=trace_exec_pins,owned_daemons=owned_daemons,raw_argv_retained=False))
        require(os.environ['HOME']==plan['normal_home'] and os.environ['PATH']==plan['normal_path'],
                'R03_HOST: normal agent identity/environment changed')
        try: retain_evidence(root,run_dir/'retained')
        finally:
            if cluster is None or cleanup.get('postgres_stopped') is True:
                require(root.parent==Path(tempfile.gettempdir()) and root.name.startswith('cairn-r03h-')
                        and root.stat().st_uid==os.getuid(),'R03_CLEANUP: wrong owned scratch root')
                shutil.rmtree(root)
        require(cluster is None or cleanup.get('postgres_stopped') is True, 'R03_CLEANUP: owned PostgreSQL unresolved')


def native_readback_request(native_root, manifest, home, baseline):
    """Retain prep compatibility without emitting the superseded constructor."""
    return dict(schema=SCHEMA, status='DEPRECATED_NOT_EXECUTABLE', argv=None,
        replacement='direct-host mechanism-plan + authenticated native admission through maintained mise/host:exec',
        reason='Reviewer N1/N2: finalization can call auxiliary providers; old author-path request must not execute.',
        native_executed=False, ac4_closed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('prepare', 'session-api', 'native', 'mechanism-plan',
        'mechanism-setup'), default='prepare')
    parser.add_argument('--native-root', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--root-images', type=Path)
    parser.add_argument('--pg-root', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--spec', type=Path)
    parser.add_argument('--admission-context', type=Path)
    args = parser.parse_args(argv)
    # This branch deliberately precedes path inspection, imports and state
    # creation. Selecting a fixture phase cannot grant native admission.
    if args.phase == 'native':
        if args.plan is not None and args.admission_context is not None:
            try:
                run_mechanism_host(args.plan, 'native', args.admission_context)
            except AdmissionRefusal as error:
                print(json.dumps(dict(native_boundary(), reason=str(error))))
                return NOTRUN
            return 0
        print(json.dumps(native_boundary()))
        return NOTRUN
    if args.phase == 'mechanism-plan':
        require(all((args.native_root, args.manifest, args.root_images, args.pg_root,
                     args.evidence_root, args.output)), 'R03_PLAN: all frozen input paths required')
        make_mechanism_plan(args)
        return 0
    if args.phase == 'mechanism-setup':
        require(args.plan is not None, 'R03_PLAN: missing plan')
        run_mechanism_host(args.plan, 'setup')
        return 0
    if args.phase == 'session-api':
        require(all((args.home, args.native_root, args.report, args.manifest)),
                'R03_ARGS: home, native-root, manifest and report required')
        verify_inputs(args.native_root, args.manifest)
        session_api(args.home, args.native_root / 'hermes', args.report)
        return 0
    require(all((args.native_root, args.manifest, args.evidence_root, args.output)),
            'R03_ARGS: native-root, manifest, evidence-root and output required')
    output = private_output(args.evidence_root, args.output)
    pins = verify_inputs(args.native_root, args.manifest)
    source = source_pins()
    os.umask(0o077)
    output.mkdir(mode=0o700)
    home = output / 'hermes-home'
    home.mkdir(mode=0o700)
    write_json(output / 'prep-marker.json', dict(schema=SCHEMA, home=str(home),
               hermes_root=str(args.native_root / 'hermes'), evidence_root=str(args.evidence_root)))
    write_json(output / 'before-use.json', dict(held=pins, source=source,
        native_root=str(args.native_root), normal_home=os.environ.get('HOME'), client_started=False))
    command = [str(args.native_root / 'venv/bin/python'), '-I', '-B', str(Path(__file__).resolve()),
               '--phase', 'session-api', '--native-root', str(args.native_root),
               '--manifest', str(args.manifest),
               '--home', str(home), '--report', str(output / 'session-api.json')]
    result = subprocess.run(command, cwd=ROOT, env=dict(os.environ, HERMES_HOME=str(home)),
                            capture_output=True, text=True, timeout=120)
    write_json(output / 'session-process.json', dict(argv=command, exit=result.returncode,
        stdout=result.stdout, stderr=result.stderr))
    require(result.returncode == 0, 'R03_SESSION_API: held API prerequisite failed; retain session-process.json')
    baseline = json.loads((output / 'session-api.json').read_bytes())
    require(baseline['owned_process']['pid'] > 1, 'R03_SESSION_API: missing owned child identity')
    write_json(output / 'cleanup.json', dict(worker_reaped=True, worker_exit=result.returncode,
        application_database_closed=True, api_started=False, postgres_started=False,
        native_client_started=False, retained_state='synthetic evidence for Root readback admission only'))
    write_json(output / 'native-readback-request.json', native_readback_request(args.native_root, args.manifest, home, baseline))
    write_json(output / 'native-boundary.json', native_boundary())
    print(json.dumps(dict(schema=SCHEMA, status='PREPARED', session_api='passed',
                         native='NOTRUN', ac4_closed=False, output=str(output))))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except PrerequisiteError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
