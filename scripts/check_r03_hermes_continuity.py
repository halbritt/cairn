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
import shutil
import signal
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
            and admission.get('operation') == 'one-isolated-hermes-continuity'
            and admission.get('attempt_limit') == 1, 'R03_ADMISSION: no exact native operation admitted')
    require(admission.get('bindings') == bindings, 'R03_ADMISSION: exact code/image/runtime/provider bindings differ')
    expires = datetime.fromisoformat(admission['expires_at'].replace('Z', '+00:00'))
    require(expires.tzinfo is not None and datetime.now(timezone.utc) < expires,
            'R03_ADMISSION: expired or unzoned admission')
    return dict(event_id=event['event_id'], delivery_id=context['delivery_id'], source=event['ref'],
                bindings=bindings, expires_at=admission['expires_at'])


def authenticated_admission(context_path, bindings):
    context = json.loads(Path(context_path).read_bytes())
    require(context['agent_id'] == AGENT_ID and context['execution_id'] == EXECUTION_ID,
            'R03_ADMISSION: foreign context')
    cli = ['/home/halbritt/.local/bin/cairn', '--token-file',
           '/home/halbritt/.local/share/cairn/hosted-agent.token',
           '--agent-id', AGENT_ID, '--execution-id', EXECUTION_ID]
    def read(operation, tail, stdin=None):
        argv = [cli[0], operation, *cli[1:], *tail]
        result = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'R03_ADMISSION: authenticated read failed: ' + operation)
        envelope = json.loads(result.stdout)
        require(envelope.get('ok') is True, 'R03_ADMISSION: server refused read')
        return envelope['data']
    status = read('event-status', ['--limit', '100', context['event_id']])
    # history uses ordinary memory authentication, not session-inbox flags.
    history_argv = [cli[0], 'agent', '--token-file', cli[2], 'history']
    result = subprocess.run(history_argv, input=json.dumps(context['source']),
                            capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, 'R03_ADMISSION: exact source unavailable')
    history = json.loads(result.stdout)
    require(history.get('ok') is True, 'R03_ADMISSION: source read refused')
    version = history['data']['versions'][0]
    require(version['version'] == context['source']['version'], 'R03_ADMISSION: wrong source version')
    admission = json.loads(version['body'])
    receipt = validate_admission(status, admission, context, bindings)
    receipt.update(server_status=status, admission=admission,
                   context={key: context[key] for key in ('event_id', 'source', 'agent_id', 'execution_id', 'delivery_id')})
    return receipt


class ScriptedChat:
    """A real local chat-completions transport, including SDK SSE streaming."""
    def __init__(self, output, port=43867):
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
                           authorization=self.headers.get('Authorization'))
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
    require(not source['status'], 'R03_PLAN: commit and freeze the mechanism before pinning')
    tools = {}
    for path in ('/usr/bin/bwrap', '/usr/bin/strace', '/usr/bin/git', '/usr/bin/env', '/bin/sh', '/usr/bin/locale'):
        tools[path] = digest(path)
    libraries = {}
    for executable in (args.native_root / 'python/bin/python3.11', *tools,
                       args.pg_root / 'usr/lib/postgresql/18/bin/postgres',
                       args.pg_root / 'usr/lib/postgresql/18/bin/psql'):
        result = subprocess.run(['ldd', str(executable)], text=True, capture_output=True, check=True)
        for path in re.findall(r'(?:=>\s*)?(/[^\s()]+)', result.stdout):
            libraries[path] = digest(path)
    os.umask(0o077); output.mkdir(mode=0o700)
    assets = output / 'runtime-assets'; assets.mkdir(mode=0o700)
    runtime_assets = {}
    for runtime_source in ('/etc/hosts', '/etc/nsswitch.conf'):
        target = assets / Path(runtime_source).name
        shutil.copyfile(runtime_source, target); target.chmod(0o400)
        runtime_assets[runtime_source] = digest(target)
    bindings = dict(case_id=str(uuid.uuid4()), source_head=source['head'], source_tree=source['tree'],
        checker_sha256=digest(Path(__file__)), source_files=source['files'],
        wrapper_sha256=digest(ROOT / 'scripts/test_hybrid_runtime_operations.py'),
        held_manifest_sha256=held['manifest_sha256'], hermes_revision=HERMES_REVISION,
        interpreter_sha256=digest(args.native_root / 'python/bin/python3.11'),
        images_source_head=IMAGE_SOURCE, images_source_tree=IMAGE_TREE,
        images={name: row['sha256'] for name, row in image_pins['images'].items()},
        pg_manifest_sha256=pg['sha256'], libraries=libraries, native_tools=tools,
        runtime_assets=runtime_assets,
        provider=dict(provider='custom', url=PROVIDER_URL, model=MODEL,
                      api_key_sha256=hashlib.sha256(b'r03-fixture-only').hexdigest(), fallback=[]))
    plan = dict(schema='cairn.r03-hermes-native-plan/1', bindings=bindings,
        native_root=str(args.native_root), manifest=str(args.manifest), pg_root=str(args.pg_root),
        root_images=str(args.root_images), image_directory=str(Path(image_pins['images']['api']['path']).parent),
        evidence_root=str(args.evidence_root), output=str(output), normal_home=os.environ['HOME'],
        host_net_namespace=os.readlink('/proc/self/ns/net'), held=held, pg=pg,
        verified_compiled_source_files=source_count,
        admission_required=True, native_executed=False)
    write_json(output / 'plan.json', plan)
    write_json(output / 'admission-template.json', dict(schema='cairn.r03-hermes-native-admission/1',
        operation='one-isolated-hermes-continuity', attempt_limit=1, bindings=bindings,
        expires_at='ROOT_MUST_SET_A_FRESH_RFC3339_EXPIRY'))
    print(json.dumps(dict(status='MECHANISM_PLAN_PINNED', plan=str(output / 'plan.json'),
                         native='NOTRUN', ac4_closed=False)))


def sandbox_python(tail):
    code = ('import runpy,sys;sys.path.insert(0,"/opt/assets/held/venv/lib/python3.11/site-packages");'
            'sys.path.insert(0,"/work/scripts");sys.argv=["/work/scripts/check_r03_hermes_continuity.py",*sys.argv[1:]];'
            'runpy.run_path(sys.argv[0],run_name="__main__")')
    return ['/opt/assets/held/python/bin/python3.11', '-I', '-B', '-c', code, *tail]


def run_mechanism_wrapper(plan_path, mode, admission_context=None):
    plan = json.loads(Path(plan_path).read_bytes())
    current = source_pins()
    require(not current['status'] and current['files'] == plan['bindings']['source_files']
            and plan['bindings']['source_head'] == current['head']
            and plan['bindings']['source_tree'] == current['tree']
            and plan['bindings']['checker_sha256'] == digest(__file__), 'R03_PLAN: source moved')
    require(digest(ROOT / 'scripts/test_hybrid_runtime_operations.py') == plan['bindings']['wrapper_sha256'],
            'R03_ISOLATION: qualified wrapper changed')
    for path, expected in {**plan['bindings']['libraries'], **plan['bindings']['native_tools']}.items():
        require(digest(path) == expected, 'R03_ASSET: library/tool changed before namespace launch: ' + path)
    for name, expected in plan['bindings']['images'].items():
        require(digest(Path(plan['image_directory']) / name) == expected, 'R03_IMAGES: image changed before namespace launch')
    # The authenticated host gate is outside isolation; the ordinary host
    # profile is never bound into the sandbox. No inbox consumption occurs.
    grant = None
    if mode == 'native':
        try:
            require(admission_context is not None, 'R03_ADMISSION: no native context')
            grant = authenticated_admission(admission_context, plan['bindings'])
        except (PrerequisiteError, KeyError, ValueError, OSError) as error:
            raise AdmissionRefusal('R03_ADMISSION: ' + str(error)) from error
    verify_inputs(Path(plan['native_root']), Path(plan['manifest']))
    output = Path(plan['output'])
    for destination, expected in plan['bindings']['runtime_assets'].items():
        require(digest(output / 'runtime-assets' / Path(destination).name) == expected,
                'R03_ASSET: frozen libc configuration changed before use')
    run_dir = output / ('native-run' if mode == 'native' else 'setup-run')
    run_dir.mkdir(mode=0o700)
    if grant is not None:
        # O_EXCL is the one-attempt ledger. NOTRUN never reaches this point.
        ledger = Path.home() / '.cache/cairn301-native-admission-ledger-1467'
        ledger.mkdir(mode=0o700, exist_ok=True)
        require(ledger.resolve() == ledger and stat.S_IMODE(ledger.stat().st_mode) == 0o700 and ledger.stat().st_uid == os.getuid(),
                'R03_ADMISSION: private per-agent attempt ledger required')
        fd = os.open(ledger / (grant['event_id'] + '.json'), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as stream: json.dump(grant, stream)
        write_json(run_dir / 'grant.json', grant)
    write_json(run_dir / 'plan.json', plan)
    command = ['python3', '-I', '-B', str(ROOT / 'scripts/test_hybrid_runtime_operations.py'),
        '--receipt', str(run_dir / 'isolation-receipt.json'),
        '--ro-bind', plan['native_root'] + ':/opt/assets/held',
        '--ro-bind', plan['manifest'] + ':/opt/assets/held-pins.json',
        '--ro-bind', plan['pg_root'] + ':/opt/assets/pg',
        '--ro-bind', plan['image_directory'] + ':/opt/assets/images',
        '--ro-bind', str(run_dir) + ':/opt/assets/run',
        *[item for destination, expected in plan['bindings']['runtime_assets'].items()
          for item in ('--ro-bind', str(output / 'runtime-assets' / Path(destination).name) + ':' + destination)], '--',
        '/usr/bin/strace', '-f', '-qq', '-s', '1000', '-e', 'trace=network,process',
        '-o', '/tmp/r03-native-syscalls.log',
        *sandbox_python(['--phase', 'isolated-' + mode, '--plan', '/opt/assets/run/plan.json'])]
    write_json(run_dir / 'invocation.json', dict(argv=command, mode=mode, native_admitted=grant is not None))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=480)
    (run_dir / 'stdout').write_bytes(result.stdout); (run_dir / 'stderr').write_bytes(result.stderr)
    write_json(run_dir / 'process.json', dict(exit=result.returncode))
    for line in result.stdout.splitlines():
        if line.startswith(b'R03_EVIDENCE='):
            evidence = json.loads(line[len(b'R03_EVIDENCE='):])
            for name, raw in evidence.items():
                path = run_dir / 'retained' / name
                require('..' not in Path(name).parts and not Path(name).is_absolute(), 'R03_EVIDENCE: escaped path')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(base64.b64decode(raw))
    require(result.returncode == 0, 'R03_MECHANISM: owned ' + mode + ' run failed; STOP, retain raw output')
    print(json.dumps(dict(status='NATIVE_FIXTURE_PASSED' if mode == 'native' else 'SETUP_ONLY_PASSED',
                         native=mode == 'native', ac4_closed=False, output=str(run_dir))))


def inside_isolation(plan, native=False):
    require(os.readlink('/proc/self/ns/net') != plan['host_net_namespace'], 'R03_ISOLATION: host network namespace refused')
    require(str(ROOT) == '/work' and os.environ.get('HOME') == '/tmp/home',
            'R03_ISOLATION: qualified wrapper layout required')
    require(digest(__file__) == plan['bindings']['checker_sha256'], 'R03_ISOLATION: checker changed')
    for name, expected in plan['bindings']['images'].items():
        require(digest(Path('/opt/assets/images') / name) == expected, 'R03_IMAGES: bound image changed')
    for path, expected in plan['bindings']['libraries'].items():
        require(digest(path) == expected, 'R03_ASSET: native library changed: ' + path)
    for path, expected in plan['bindings']['native_tools'].items():
        require(digest(path) == expected, 'R03_ASSET: native tool changed: ' + path)
    for path, expected in plan['bindings']['runtime_assets'].items():
        require(digest(path) == expected, 'R03_ASSET: qualified libc runtime asset changed: ' + path)
    if native:
        grant = json.loads(Path('/opt/assets/run/grant.json').read_bytes())
        validate_admission(grant['server_status'], grant['admission'], grant['context'], plan['bindings'])
    return True


class NativeAudit:
    """Passive Python call/engine observation plus fail-closed socket/exec audit.

    No held client, provider method or hook dispatcher is replaced. strace in
    the enclosing unchanged wrapper additionally records every child exec and
    socket syscall, including Go/selector/auxiliary/finalizer descendants.
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
            self.events.append(dict(kind=event, address=str(address)))
            allowed = ((isinstance(address, tuple) and address[:2] == ('127.0.0.1', 43867))
                       or (isinstance(address, str) and address == self.spec['api_socket']))
            if not allowed: self.reject('R03_NETWORK: unexpected connection ' + str(address))
        elif event == 'socket.getaddrinfo':
            self.events.append(dict(kind=event, host=str(args[0])))
            if args[0] not in ('127.0.0.1', 'localhost'):
                self.reject('R03_NETWORK: external DNS refused')
        elif event in ('subprocess.Popen', 'os.exec'):
            executable, argv = str(args[0]), args[1]
            self.events.append(dict(kind=event, executable=executable,
                argv=[str(value) for value in argv] if isinstance(argv, (list, tuple)) else str(argv)))
            import shutil
            resolved = str(Path(shutil.which(executable) or executable).resolve())
            if resolved not in self.spec['exec_pins']:
                self.reject('R03_EXEC: unexpected executable ' + executable)
            if digest(resolved) != self.spec['exec_pins'][resolved]:
                self.reject('R03_EXEC: executable changed ' + executable)

    def profile(self, frame, event, result):
        filename, name = frame.f_code.co_filename, frame.f_code.co_name
        if name == 'run_engine' and filename.endswith('/plugins/cairn/__init__.py'):
            if event == 'call':
                row = dict(argv=list(frame.f_locals['command']), payload=dict(frame.f_locals['payload']),
                           timeout=frame.f_locals.get('timeout'), started=time.monotonic())
                self.calls.append(row); self.frames[id(frame)] = row
            elif event == 'return' and id(frame) in self.frames:
                row = self.frames.pop(id(frame))
                if result is not None:
                    row.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
                row['seconds'] = time.monotonic() - row['started']
        watched = ('/agent/auxiliary_client.py', '/hermes_cli/runtime_provider.py', '/openai/', '/httpx/', '/agent/backends/')
        if event == 'call' and (any(part in filename for part in watched)
                or name in ('chat', '_init_agent', '_finalize_single_query', '_run_cleanup')):
            row = dict(kind='python-call', file=filename, function=name)
            for key in ('provider', 'requested_provider', 'model', 'base_url', 'task'):
                value = frame.f_locals.get(key)
                if isinstance(value, (str, int, bool)): row[key] = value
            self.events.append(row)
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
    plan = json.loads(Path('/opt/assets/run/plan.json').read_bytes())
    inside_isolation(plan, native=True)  # MUST precede every held client import.
    output, home = Path(spec['output']), Path(spec['home'])
    os.environ['HERMES_HOME'] = str(home)
    os.environ['TERMINAL_CWD'] = spec['project']
    audit = NativeAudit(output, spec); audit.install()
    sys.path.insert(0, '/opt/assets/held/hermes')
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
        cli = HermesCLI(model=MODEL, provider='custom', api_key='r03-fixture-only', base_url=PROVIDER_URL,
                        toolsets=['mcp-cairn'], max_turns=1, compact=True, resume=spec['session'])
        require(cli._fallback_model == [], 'R03_PROVIDER: fallback configured')
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
        write_json(output / 'ready.json', dict(pid=os.getpid(), session=cli.session_id,
                    cached_selection=provider.config, cached_command=selected))
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
        require(cli.session_id == (spec['session'] or cli.session_id), 'R03_SESSION: chat rotated conversation')
        require(cli.api_key == 'r03-fixture-only' and str(cli.base_url).rstrip('/') == PROVIDER_URL,
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
                      credential_selector_sha256=spec['credential_selector_sha256'])
        write_json(output / 'result.json', result)
    finally:
        if cli is not None and not finalized:
            _finalize_single_query(cli)
        audit.flush()


def auxiliary_routes():
    tree = ast.parse(Path('/opt/assets/held/hermes/hermes_cli/config_defaults.py').read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict): continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == 'auxiliary' and isinstance(value, ast.Dict):
                return {key.value: dict(provider='custom', model=MODEL, base_url=PROVIDER_URL,
                    api_key='r03-fixture-only', fallback_chain=[], timeout=10)
                    for key, item in zip(value.keys, value.values)
                    if isinstance(key, ast.Constant) and isinstance(item, ast.Dict)}
    raise PrerequisiteError('R03_INTERFACE: held auxiliary routing defaults unavailable')


def emit_evidence(root):
    selected, binaries = {}, {}
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file() or path.is_socket() or 'cluster/data' in str(path): continue
        with path.open('rb') as source: header = source.read(4)
        if header == b'\x7fELF':
            binaries[str(path.relative_to(root))] = dict(bytes=path.stat().st_size, sha256=digest(path))
            continue
        if path.stat().st_size > 32_000_000:
            raise PrerequisiteError('R03_EVIDENCE: raw receipt exceeded declared bound: ' + path.name)
        selected[str(path.relative_to(root))] = base64.b64encode(path.read_bytes()).decode()
    selected['installed-binary-pins.json'] = base64.b64encode(json.dumps(binaries, indent=2).encode()).decode()
    trace = Path('/tmp/r03-native-syscalls.log')
    if trace.exists(): selected['syscalls.log'] = base64.b64encode(trace.read_bytes()).decode()
    print('R03_EVIDENCE=' + json.dumps(selected), flush=True)


def audit_syscalls(allowed_exec, allowed_unix, phase_roots=None, trace_path='/tmp/r03-native-syscalls.log'):
    trace = Path(trace_path).read_text()
    violations, executions, connections, runtime_probes = [], [], [], []
    lines, pending = [], {}
    for line in trace.splitlines():
        if not line.strip(): continue
        pid = line.split()[0]
        if '<unfinished ...>' in line:
            pending[pid] = line.split('<unfinished ...>')[0]
        elif '<... ' in line and ' resumed>' in line:
            if pid in pending: lines.append(pending.pop(pid) + line.split(' resumed>', 1)[1])
        else: lines.append(line)
    for line in pending.values():
        if 'connect(' in line or 'execve(' in line: violations.append('unfinished audited operation: ' + line)
    parents = {}
    for line in lines:
        if re.search(r'(clone|fork)', line):
            child = re.search(r'= (\d+)\s*$', line)
            if child: parents[int(child[1])] = int(line.split()[0])
    def phase_for(pid):
        seen = set()
        while pid not in seen:
            seen.add(pid)
            if pid in (phase_roots or {}): return phase_roots[pid]
            if pid not in parents: return None
            pid = parents[pid]
        return None
    for line in lines:
        if 'execve(' in line:
            match = re.search(r'execve\(("(?:[^"\\]|\\.)*")', line)
            if match:
                path = json.loads(match[1]); executions.append(path)
                # Failed PATH candidates and the deterministic maintained
                # installer launch-anchor probe are explicitly declared;
                # neither permits an unknown executable that actually runs.
                negative_git = (path in ('/opt/toolchains/go/bin/git', '/opt/toolchains/rust/bin/git')
                                and '= -1 ENOENT' in line)
                anchor = re.fullmatch(r'/tmp/home/\.cache/r03-mechanism/project/release/\.prepare-[^/]+/probe', path)
                if negative_git or anchor:
                    runtime_probes.append(line)
                elif str(Path(path).resolve()) not in allowed_exec:
                    violations.append(line)
                pid = int(line.split()[0]); phase = phase_for(pid)
                if phase in ('A', 'B') and path == '/opt/assets/held/python/bin/python3.11' and pid not in (phase_roots or {}):
                    if '/r03-mechanism/selector' not in line:
                        violations.append('Source Python fallback in Go phase: ' + line)
            else: violations.append(line)
        if 'connect(' in line:
            connections.append(line)
            if 'AF_UNIX' in line:
                match = re.search(r'sun_path="([^\"]+)"', line)
                if match and match[1] == '/var/run/nscd/socket' and '= -1 ENOENT' in line:
                    runtime_probes.append(line)
                elif not match or match[1] not in allowed_unix: violations.append(line)
            elif 'AF_INET' in line:
                if 'sin_port=htons(43867)' not in line or 'inet_addr("127.0.0.1")' not in line:
                    violations.append(line)
            else: violations.append(line)
    return dict(execve=executions, connect=connections, declared_negative_or_anchor_probes=runtime_probes,
                violations=violations)


def isolated_suite(plan_path, native=False):
    plan = json.loads(Path(plan_path).read_bytes()); inside_isolation(plan, native=native)
    verify_inputs(Path('/opt/assets/held'), Path('/opt/assets/held-pins.json'))
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
    root = Path('/tmp/home/.cache/r03-mechanism'); root.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)
    pg_bin = Path('/opt/assets/pg/usr/lib/postgresql/18/bin')
    cluster = host = None
    cleanup = {}; phases = []; session = None; previous_history = None
    allowed_exec = {str(Path(path).resolve()) for path in plan['bindings']['native_tools']}
    allowed_exec.update(str(path.resolve()) for path in Path('/opt/assets/images').iterdir() if path.is_file())
    allowed_exec.update(str(path.resolve()) for path in pg_bin.iterdir() if path.is_file())
    allowed_exec.add('/opt/assets/held/python/bin/python3.11')
    allowed_unix = set()
    try:
        cluster = disposable_host.OwnedCluster.start(root / 'cluster', pg_bin=pg_bin)
        host = disposable_host.DisposableHost.start(cluster, binary=Path('/opt/assets/images/api'),
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
        selector.write_text('#!/opt/assets/held/python/bin/python3.11\n' +
            'import json,pathlib,sys\np=pathlib.Path(__file__).parent\n'
            'with (p/"selector-calls.jsonl").open("a") as f: f.write(json.dumps(dict(argv=sys.argv[1:],stdin=sys.stdin.read()))+"\\n")\n'
            'print(json.dumps(json.loads((p/"selection.json").read_text())))\n')
        selector.chmod(0o500); allowed_exec.add(str(selector))
        installed.SOURCE_REVISION = IMAGE_SOURCE
        f = installed.HermesInstalledFixture(root / 'project', Path('/opt/assets/images/control'),
                                             stager=Path('/opt/assets/images/stager'), harness='hermes', enabled=False)
        for config in (f.memory, f.coord):
            config.update(cairn='/opt/assets/images/api', socket=str(host.socket),
                          token_file=str(host.token('worker')), repo=host.repo)
        f.memory.update(claude=str(selector), model=MODEL, recall_mode='agent_tools',
                        semantic_fallback=False, capture_observations=True, recall_observations=True)
        f.publish_configuration(enabled=False)
        home = f.account
        write_json(home / 'config.yaml', dict(model=dict(default=MODEL, provider='custom', base_url=PROVIDER_URL,
                api_key='r03-fixture-only'), fallback_model=[], fallback_providers=[],
                auxiliary=auxiliary_routes(), memory=dict(memory_enabled=True), plugins=dict(enabled=[])))
        skill = root / 'fixture-skill.md'; skill.write_text('Synthetic retained continuity fixture.\n')
        native_config = dict(executable='/opt/assets/images/api', socket=str(host.socket),
                             token_file=str(host.token('worker')), repo=host.repo)
        installer.install(home, native_config, str(selector), skill, model=MODEL, recall_mode='agent_tools')
        legacy = json.loads((home / 'cairn-lifecycle.json').read_bytes())
        engine_path = Path(legacy['engine_config'])
        engine = json.loads(engine_path.read_bytes())
        engine.update(state_dir=f.memory['state_dir'], semantic_fallback=False,
                      capture_observations=True, recall_observations=True)
        installer.shared.write_json(engine_path, engine)
        retained = {str(path): path.read_bytes() for path in
                    (home / 'cairn-lifecycle.json', home / 'cairn/installed.json', engine_path)}
        legacy_members = {str(path): digest(path) for path in (home / 'plugins/cairn').rglob('*') if path.is_file()}
        write_json(root / 'retained2-before-use.json', dict(schema='cairn.r03-retained-python/2',
                    selection=legacy, members=legacy_members, engine_sha256=digest(engine_path)))
        def install_go():
            installer.install(home, native_config, str(selector), skill,
                               lifecycle_selection=f.selected, stager='/opt/assets/images/stager')
            return json.loads((home / 'cairn-lifecycle.json').read_bytes())
        selected_a = install_go()
        credential_sha = digest(home / 'config.yaml')
        write_json(root / 'credential-selectors.json', dict(path=str(home / 'config.yaml'), sha256=credential_sha,
                    provider='custom', model=MODEL, base_url=PROVIDER_URL, key_sha256=hashlib.sha256(b'r03-fixture-only').hexdigest()))
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
            exec_pins = {str(Path(path).resolve()): digest(path) for path in allowed_exec}
            for config in (selection, selected_a, selected_b or {}):
                for argv in config.get('commands', {}).values():
                    exec_pins[str(Path(argv[0]).resolve())] = digest(argv[0]); allowed_exec.add(str(Path(argv[0]).resolve()))
            spec = dict(label=label, selection=selection, home=str(home), project=str(f.root),
                output=str(phase), session=session, previous_history=previous_history,
                stage_b=transition is not None, api_socket=str(host.socket),
                exec_pins=exec_pins, credential_selector_sha256=credential_sha)
            write_json(phase / 'spec.json', spec)
            argv = sandbox_python(['--phase', 'native-worker', '--spec', str(phase / 'spec.json')])
            process = subprocess.Popen(argv, cwd=f.root, stdin=subprocess.DEVNULL,
                stdout=(phase / 'stdout').open('wb'), stderr=(phase / 'stderr').open('wb'), start_new_session=True)
            require(process.pid > 1, 'R03_PROCESS: invalid owned CLI PID')
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
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
            row = json.loads((phase / 'result.json').read_bytes())
            require(row['finalized'] is True and row['session'] == session, 'R03_BOUNDARY: no supported finalized owner')
            require_idle_boundary(True, session, row['session'])
            audit = json.loads((phase / 'audit.json').read_bytes())
            require(not audit['violations'], 'R03_AUDIT: late finalizer/atexit violation')
            sys.path.insert(0, '/opt/assets/held/hermes')
            from hermes_state import SessionDB
            readback = SessionDB(db_path=home / 'state.db')
            try: history = readback.get_resume_conversations(session)
            finally: readback.close()
            require(json.loads(json.dumps(history)) == row['history'], 'R03_SESSION: history changed during late finalization')
            calls = json.loads((phase / 'calls.json').read_bytes())
            for call in calls:
                event = call['payload']['hook_event_name']
                expected = (selection['commands'][event] if 'commands' in selection else
                            ['/opt/assets/held/python/bin/python3.11', selection['script'], '--config', selection['engine_config']])
                require(call['payload']['session_id'] == session and call['argv'] == expected
                        and call.get('exit') == 0, 'R03_CALLBACK: late foreign, fallback or failed callback')
                if 'commands' in selection:
                    require(call['timeout'] == (150 if event in ('PreCompact', 'SessionEnd') else 13),
                            'R03_CALLBACK: H13/H150 changed')
            if previous_history is not None:
                for index in (0, 1):
                    require(history[index][:len(previous_history[index])] == previous_history[index],
                            'R03_SESSION: history prefix lost, duplicated or replaced')
            users = [message for message in history[1] if message.get('role') == 'user']
            require(len(users) == len(phases) + 1, 'R03_SESSION: duplicated or missing real native turn')
            previous_history = history
            after = {table: host.count(table) for table in before}
            require(after['memory_record'] == before['memory_record'] + 1, 'R03_EFFECTS: missing or duplicated native capture')
            require(after['recall_observation'] == before['recall_observation'] + 1, 'R03_EFFECTS: missing or duplicated native recall')
            row.update(before=before, after=after, owned_pid=process.pid, child_reaped=True)
            write_json(phase / 'result.json', row); phases.append(row)

        with ScriptedChat(root / 'transport-requests.json') as transport:
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
        require(counts['memory_record'] == (4 if native else 1), 'R03_EFFECTS: total Store effects mismatch')
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
        syscall = audit_syscalls(allowed_exec, allowed_unix,
                                {row['owned_pid']: row['label'] for row in phases if 'owned_pid' in row})
        write_json(root / 'syscall-audit.json', syscall)
        emit_evidence(root)
        require(cluster is None or cleanup.get('postgres_stopped') is True, 'R03_CLEANUP: owned PostgreSQL unresolved')
        require(not syscall['violations'], 'R03_AUDIT: unexpected child exec/socket target; retain syscall journal')


def native_readback_request(native_root, manifest, home, baseline):
    """Retain prep compatibility without emitting the superseded constructor."""
    return dict(schema=SCHEMA, status='DEPRECATED_NOT_EXECUTABLE', argv=None,
        replacement='mechanism-plan + authenticated native admission + unchanged qualified wrapper',
        reason='Reviewer N1/N2: finalization can call auxiliary providers; old author-path request must not execute.',
        native_executed=False, ac4_closed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('prepare', 'session-api', 'native', 'mechanism-plan',
        'mechanism-setup', 'isolated-setup', 'isolated-native', 'native-worker'), default='prepare')
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
                run_mechanism_wrapper(args.plan, 'native', args.admission_context)
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
        run_mechanism_wrapper(args.plan, 'setup')
        return 0
    if args.phase in ('isolated-setup', 'isolated-native'):
        require(args.plan is not None, 'R03_PLAN: missing isolated plan')
        isolated_suite(args.plan, native=args.phase == 'isolated-native')
        return 0
    if args.phase == 'native-worker':
        require(args.spec is not None, 'R03_PLAN: missing owned worker spec')
        native_worker(args.spec)
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
