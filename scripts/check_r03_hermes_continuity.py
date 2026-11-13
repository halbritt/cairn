#!/usr/bin/env python3
"""Prepare the held Hermes continuity experiment without launching a client.

The session-api phase uses the real held SessionDB, not a conversation stand-in.
It proves only the database prerequisite. Native CLI/gateway execution remains
an explicit NOTRUN boundary; neither a fixture flag nor this report admits it.
"""
import argparse
import ast
import hashlib
import http.server
import json
import os
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


class PrerequisiteError(RuntimeError):
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
    return validate_admission(status, admission, context, bindings)


class ScriptedChat:
    """A real local chat-completions transport, including SDK SSE streaming."""
    def __init__(self, output, port=43867):
        self.output, self.requests, self.phase = Path(output), [], 'A'
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length <= 2_000_000, 'R03_TRANSPORT: invalid request size')
                body = json.loads(self.rfile.read(length))
                row = dict(path=self.path, body=body, phase=owner.phase,
                           authorization=self.headers.get('Authorization'))
                owner.requests.append(row)
                write_json(owner.output, owner.requests)
                if (self.path != '/v1/chat/completions' or body.get('model') != MODEL
                        or self.headers.get('Authorization') != 'Bearer r03-fixture-only'):
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


def native_readback_request(native_root, manifest, home, baseline):
    """Produce an exact, unexecuted first-client request for Root admission.

    This requests only constructor + supported resume readback, not a turn.
    A successful readback would still leave A/B/reverse callbacks and R06 open.
    The command is data in the packet, never dispatched by this checker.
    """
    hermes_root = native_root / 'hermes'
    mixin = hermes_root / 'hermes_cli/cli_agent_setup_mixin.py'
    tree = ast.parse(mixin.read_text())
    resume = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == '_preload_resumed_session')
    cli_tree = ast.parse((hermes_root / 'cli.py').read_text())
    owner = next(node for node in cli_tree.body if isinstance(node, ast.ClassDef) and node.name == 'HermesCLI')
    constructor = next(node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
    require('resume' in [arg.arg for arg in constructor.args.args], 'R03_INTERFACE: held CLI lacks supported resume constructor')
    baseline_path = home.parent / 'session-api.json'
    baseline_sha = digest(baseline_path)
    database_sha = digest(home / 'state.db')
    code = '\n'.join([
        'import hashlib,importlib.util,json,os,pathlib,sys',
        'assert os.getpid() > 1',
        'checker=pathlib.Path(' + repr(str(Path(__file__).resolve())) + ')',
        'assert hashlib.sha256(checker.read_bytes()).hexdigest() == ' + repr(digest(Path(__file__))),
        'spec=importlib.util.spec_from_file_location("r03_continuity_prep",checker)',
        'prep=importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)',
        'prep.verify_inputs(pathlib.Path(' + repr(str(native_root)) + '),pathlib.Path(' + repr(str(manifest)) + '))',
        'home=pathlib.Path(' + repr(str(home)) + ')',
        'assert os.environ["HERMES_HOME"] == str(home)',
        'baseline_path=pathlib.Path(' + repr(str(baseline_path)) + ')',
        'assert hashlib.sha256(baseline_path.read_bytes()).hexdigest() == ' + repr(baseline_sha),
        'assert hashlib.sha256((home/"state.db").read_bytes()).hexdigest() == ' + repr(database_sha),
        'baseline=json.loads(baseline_path.read_text())',
        'assert baseline["session"] == ' + repr(baseline['session']),
        'sys.path.insert(0,' + repr(str(hermes_root)) + ')',
        'from cli import HermesCLI, _finalize_single_query',
        'cli=None',
        'try:',
        ' cli=HermesCLI(model="r03-scripted-fixture",provider="custom",api_key="r03-fixture-only",base_url="http://127.0.0.1:9/v1",toolsets=["mcp-cairn"],compact=True,resume=baseline["session"])',
        ' assert cli._fallback_model == []',
        ' assert cli._preload_resumed_session()',
        ' assert cli.session_id == baseline["session"]',
        ' expected=[m for m in baseline["model_history"] if m.get("role") != "session_meta"]',
        ' assert cli.conversation_history == expected',
        ' assert cli.api_key == "r03-fixture-only"',
        ' assert str(cli.base_url) == "http://127.0.0.1:9/v1"',
        ' selectors=home/"credential-selectors.json"',
        ' assert hashlib.sha256(selectors.read_bytes()).hexdigest() == baseline["credential_selectors_sha256"]',
        ' print(json.dumps(dict(session=cli.session_id,history_rows=len(cli.conversation_history),agent_initialized=cli.agent is not None,conversation_turn_run=False)))',
        'finally:',
        ' if cli is not None: _finalize_single_query(cli)',
    ])
    return dict(schema=SCHEMA, status='REQUESTED_NOT_EXECUTED',
        operation='One disposable native HermesCLI constructor and same-session resume/readback; no _init_agent, chat, model request or live profile.',
        source=dict(path=str(mixin), sha256=digest(mixin),
                    resume_method_lines=[resume.lineno, resume.end_lineno],
                    cli_path=str(hermes_root / 'cli.py'), cli_sha256=digest(hermes_root / 'cli.py')),
        environment=dict(HERMES_HOME=str(home), HOME=os.environ.get('HOME')),
        argv=['mise', 'run', 'host:exec', '--', '--timeout', '4m', '--weight', '2', '--',
              'env', 'HERMES_HOME=' + str(home),
              str(native_root / 'venv/bin/python'), '-I', '-B', '-c', code],
        interpreter_sha256=digest(native_root / 'venv/bin/python'),
        baseline_sha256=baseline_sha, database_sha256=database_sha,
        prerequisite='Root must pin and admit this exact code/input/request image before execution; the candidate never dispatches it.',
        limits=['Readback may import native client dependencies and load configuration; this is why admission is required.',
                'Loopback port 9 is a refusal destination, not a scripted model server.',
                'No native conversation turn, selected callbacks, A/B/reverse continuity, Store effects or activation-unit acceptance established.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('prepare', 'session-api', 'native'), default='prepare')
    parser.add_argument('--native-root', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    # This branch deliberately precedes path inspection, imports and state
    # creation. Selecting a fixture phase cannot grant native admission.
    if args.phase == 'native':
        print(json.dumps(native_boundary()))
        return NOTRUN
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
