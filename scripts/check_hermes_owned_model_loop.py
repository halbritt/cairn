#!/usr/bin/env python3
"""Bounded real Hermes/OpenAI SDK loop against a namespace-local scripted model.

--preflight is static and does not import Hermes. --run requires the reviewed
netns harness. This exercises real native methods on an initialized AIAgent,
not full interactive CLI startup, production Cairn, or an external model.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

NATIVE_FILES = ('cli.py', 'run_agent.py', 'agent/request_ownership.py',
                'agent/request_coverage.py', 'agent/request_processes.py',
                'tools/environments/local.py', 'tools/process_registry.py')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def identity(root):
    root = root.resolve()
    hashes = {}
    for name in NATIVE_FILES:
        source = (root / name).read_bytes()
        ast.parse(source, filename=name)
        hashes[name] = hashlib.sha256(source).hexdigest()
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                           check=True, capture_output=True, text=True).stdout.strip()
    require(not dirty, 'native source has tracked changes; freeze it before execution')
    plugin = Path(__file__).resolve().parents[1] / 'integrations/hermes/coordination.py'
    hashes['cairn_coordination.py'] = hashlib.sha256(plugin.read_bytes()).hexdigest()
    return {'native_root': str(root), 'native_revision': revision, 'sha256': hashes}


def wait_for(predicate, description, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise AssertionError('timeout: ' + description)


def run(args):
    # Nothing before this point imports native code, reads profiles, or binds a server.
    verifier = args.harness_root.resolve() / 'verify.py'
    spec = importlib.util.spec_from_file_location('canary_namespace_verify', verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proof = module.verify_current()  # Must precede every environment addition.
    evidence = identity(args.native_root)
    root = Path(os.environ['HARNESS_FIXTURE_ROOT'])
    home, work = root / 'hermes', root / 'work'
    home.mkdir(mode=0o700)
    work.mkdir(mode=0o700)
    os.environ.update(HERMES_HOME=str(home), TERMINAL_CWD=str(work), TIRITH_ENABLED='false',
                      PYTHONDONTWRITEBYTECODE='1', TERM='dumb',
                      DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus')
    os.chdir(work)
    sys.path.insert(0, str(args.native_root.resolve()))
    report = dict(evidence, isolation=proof, scenarios=[], native_loop_executed=False,
                  limits=['loopback scripted model; no external provider acceptance',
                          'fixture lifecycle process replaces production Cairn API',
                          'real AIAgent constructed before admission; full CLI startup/routing/image preprocessing untested',
                          'only reviewed coordination plugin and controlled local terminal profile'])
    report['fixture_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['verifier_sha256'] = hashlib.sha256(verifier.read_bytes()).hexdigest()
    report['python'] = sys.version
    report_path = root / 'hermes-canary.json'
    report_path.write_text(json.dumps(report, indent=2))

    def record(name, **fields):
        report['scenarios'].append(dict(name=name, **fields))
        report_path.write_text(json.dumps(report, indent=2))
        print(json.dumps(dict(scenario=name, **fields)), flush=True)

    config = dict(model=dict(provider='openrouter', default='fixture/model', context_length=32768),
                  terminal=dict(backend='local', cwd=str(work), timeout=15),
                  compression=dict(enabled=False),
                  memory=dict(enabled=False, user_profile_enabled=False),
                  checkpoints=dict(enabled=False), auxiliary=dict(background_review=dict(enabled=False)),
                  approvals=dict(mode='manual'), security=dict(tirith_enabled=False),
                  plugins=dict(enabled=['cairn-coordination']))
    (home / 'config.yaml').write_text(json.dumps(config))
    lifecycle = root / 'lifecycle.py'
    lifecycle.write_text('''import json,sys\nfrom pathlib import Path\np=json.load(sys.stdin)\nout=Path(__file__).with_name("lifecycle.jsonl")\nwith out.open("a") as f: f.write(json.dumps({k:p.get(k) for k in ("hook_event_name","session_id","turn_id","request_ownership")})+"\\n")\nprint(json.dumps({"hookSpecificOutput":{"additionalContext":"Local scripted fixture context."}}))\n''')
    (home / 'cairn-coordination.json').write_text(json.dumps(dict(script=str(lifecycle), config=str(root/'fixture.json'))))
    (root / 'fixture.json').write_text('{}')
    plugin = home / 'plugins/cairn-coordination'
    plugin.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'integrations/hermes/coordination.py', plugin / '__init__.py')
    (plugin / 'plugin.yaml').write_text(json.dumps(dict(name='cairn-coordination', version='1.0')))

    # A separate real contained launch proves inherited kernel namespace before
    # constructing an SDK client. This is not an inventory-completeness claim.
    from agent.request_ownership import RequestOwnership, _tool_ownership
    from agent.request_processes import contained_popen, cleanup_request_process, request_process_inventory
    probe = RequestOwnership(threading.RLock(), 'scope-probe', 'scope-probe', 'probe', 'probe')
    marker = _tool_ownership.set(probe)
    child = None
    try:
        child = contained_popen(['/usr/bin/python3', '-I', '-c',
                                 'import os; print(os.readlink("/proc/self/ns/net"))'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = child.communicate(timeout=15)
        require(child.returncode == 0, 'contained namespace probe failed: ' + stderr[-1000:])
        require(stdout.strip() == proof['netns'], 'contained child escaped fixture network namespace')
        record('contained_child_namespace', same_namespace=True)
    finally:
        _tool_ownership.reset(marker)
        probe.cancelled = probe.tool_admission_closed = probe.turn_ended = True
        for item in request_process_inventory(probe)['tools']:
            require(cleanup_request_process(probe, probe.token, item['item_id'], item['process_id']) == 'terminated',
                    'namespace probe scope cleanup unavailable')
        if child is not None:
            for pipe in (child.stdout, child.stderr):
                if pipe:
                    pipe.close()

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import shlex
    active = {'case': None, 'entered': threading.Event(), 'release': threading.Event(), 'calls': 0}
    background = work / 'owned_background.py'
    child_code = ('import os,time; from pathlib import Path; '
                  'Path("background-child-netns").write_text(os.readlink("/proc/self/ns/net")); time.sleep(90)')
    background.write_text('import subprocess,sys\n'
                          f'child = {child_code!r}\n'
                          'subprocess.Popen([sys.executable,"-c",child], stdin=subprocess.DEVNULL, '
                          'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)\n')
    foreground = work / 'owned_foreground.py'
    foreground.write_text('from pathlib import Path\nimport time\nPath(__file__).with_name("foreground-ready").touch()\ntime.sleep(90)\n')
    commands = {
        'foreground': f'{shlex.quote(sys.executable)} {shlex.quote(str(foreground))}',
        'background': f'{shlex.quote(sys.executable)} {shlex.quote(str(background))}',
        'failed_cleanup': f'{shlex.quote(sys.executable)} {shlex.quote(str(background))}',
    }

    class Model(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_error(404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            if self.path != '/v1/chat/completions':
                self.send_error(404)  # Native local model metadata probes are unsupported.
                return
            payload = json.loads(raw)
            case = active['case']
            require(case is not None, 'model request outside explicit case')
            active['calls'] += 1
            require(active['calls'] <= 6, 'unbounded model loop')
            active['entered'].set()
            if case in {'revoked', 'owner_after'}:
                require(active['release'].wait(20), 'fixture model response not released')
            tool_seen = any(m.get('role') == 'tool' for m in payload['messages'])
            message = dict(role='assistant', content='fixture completed: ' + case)
            finish = 'stop'
            if case in commands and not tool_seen:
                arguments = dict(command=commands[case], timeout=15)
                if case != 'foreground':
                    arguments['background'] = True
                message = dict(role='assistant', content=None, tool_calls=[dict(
                    id='call_'+case, type='function', function=dict(name='terminal', arguments=json.dumps(arguments)))])
                finish = 'tool_calls'
            response = dict(id='fixture-'+uuid.uuid4().hex, object='chat.completion',
                            created=int(time.time()), model='fixture/model',
                            choices=[dict(index=0, message=message, finish_reason=finish)],
                            usage=dict(prompt_tokens=20, completion_tokens=10, total_tokens=30))
            if payload.get('stream'):
                delta = dict(message)
                for index, call in enumerate(delta.get('tool_calls', [])):
                    call['index'] = index
                chunk = dict(response, object='chat.completion.chunk', choices=[dict(index=0, delta=delta, finish_reason=None)])
                final = dict(response, object='chat.completion.chunk', choices=[dict(index=0, delta={}, finish_reason=finish)])
                body = ('data: '+json.dumps(chunk)+'\n\ndata: '+json.dumps(final)+'\n\ndata: [DONE]\n\n').encode()
                content_type = 'text/event-stream'
            else:
                body = json.dumps(response).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    server.daemon_threads = True
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    agent = None
    cli = None
    runs = []
    owned = []
    try:
        from hermes_cli.plugins import get_plugin_manager, QueuedMessage
        manager = get_plugin_manager()
        manager.discover_and_load()
        from run_agent import AIAgent
        from cli import HermesCLI
        agent = AIAgent(api_key='fixture-only', base_url=f'http://127.0.0.1:{server.server_port}/v1',
                        provider='openrouter', api_mode='chat_completions', model='fixture/model',
                        max_iterations=4, enabled_toolsets=['terminal'], platform='cli',
                        session_id='fixture-'+uuid.uuid4().hex, quiet_mode=True, skip_context_files=True,
                        skip_memory=True, skip_background_review=True, checkpoints_enabled=False)
        cli = HermesCLI.__new__(HermesCLI)
        cli.session_id, cli.agent = agent.session_id, agent
        cli._admission_lock, cli._agent_running = threading.RLock(), False
        cli._reset_admission_state()
        manager._cli_ref = cli

        def start(case, owner=False):
            active.update(case=case, calls=0, entered=threading.Event(), release=threading.Event())
            (work/'background-child-netns').unlink(missing_ok=True)
            prompt = 'CANARY_SCENARIO='+case
            item = prompt if owner else QueuedMessage(text=prompt, expected_session_id=cli.session_id,
                                                      request_id=str(uuid.uuid4()), delivery_id=str(uuid.uuid4()))
            require(cli._dequeue_pending_input(item) == prompt, 'native admission refused')
            if owner:
                selection = None
            else:
                selection = (item.request_id, item.turn_id, item.ownership_token)
                owned.append(selection)
            result = {}
            cli._agent_running = True
            cli._admission_state = 'running'

            def execute():
                try:
                    result['value'] = agent.run_conversation(prompt)
                except BaseException as exc:
                    result['error'] = repr(exc)
                finally:
                    cli._reset_admission_state()
                    cli._agent_running = False
            thread = threading.Thread(target=execute, daemon=True)
            runs.append(thread)
            thread.start()
            return selection, thread, result

        def finish(thread, result):
            thread.join(25)
            require(not thread.is_alive(), 'native generation did not finish')
            require('error' not in result, 'native run raised '+str(result.get('error')))
            require(active['calls'] > 0, 'real SDK never called scripted model')
            if active['case'] != 'foreground':
                require(result.get('value', {}).get('final_response') == 'fixture completed: '+active['case'],
                        'real model loop did not return its expected response')
            report['native_loop_executed'] = True

        def clean(selection):
            status = cli.request_process_status(*selection)
            outcomes = [cli.cleanup_owned_process(*selection, item['item_id'], item['process_id'])
                        for item in status['tools']]
            return outcomes

        selection, thread, result = start('no_tool')
        finish(thread, result)
        status = cli.request_process_status(*selection)
        require(status['terminal_scan'] == 'clear', 'no-tool profile not complete: '+json.dumps(status))
        record('no_tool_real_sdk', calls=active['calls'], status=status)

        selection, thread, result = start('foreground')
        wait_for(lambda: (work/'foreground-ready').exists(), 'real foreground command readiness')
        before = cli.request_process_status(*selection)
        require(before['ownership']['active_tool_calls'] > 0, 'foreground tool not counted')
        cli.abort_owned_request(*selection)
        clean(selection)
        finish(thread, result)
        clean(selection)
        status = cli.request_process_status(*selection)
        require(status['terminal_scan'] == 'clear', 'foreground scope not clear: '+json.dumps(status))
        record('foreground_cancel', before=before, after=status)

        for case in ('background', 'failed_cleanup'):
            selection, thread, result = start(case)
            finish(thread, result)
            before = cli.request_process_status(*selection)
            require(any(t['stop_state'] != 'terminated' for t in before['tools']), 'no live background scope captured')
            wait_for(lambda: (work/'background-child-netns').exists(), 'detached background child readiness')
            require((work/'background-child-netns').read_text() == proof['netns'],
                    'background descendant escaped fixture namespace')
            def launcher_ended(item):
                pid, expected_start = item['process_id'].split(':')
                try:
                    actual_start = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
                except FileNotFoundError:
                    return True
                return actual_start != expected_start
            wait_for(lambda: all(launcher_ended(item) for item in before['tools']),
                     'background launcher exits while detached child remains captured')
            cli.abort_owned_request(*selection)
            if case == 'failed_cleanup':
                from unittest.mock import patch
                with patch('agent.request_processes._verify', side_effect=PermissionError('fixture permission fault')):
                    outcomes = clean(selection)
                require('unavailable' in outcomes, 'permission fault not preserved')
            clean(selection)
            status = cli.request_process_status(*selection)
            expected = 'unknown_remaining' if case == 'failed_cleanup' else 'clear'
            require(status['terminal_scan'] == expected, 'cleanup evidence incorrect: '+json.dumps(status))
            record(case, before=before, after=status, launcher_ended_before_cleanup=True,
                   detached_child_same_namespace=True,
                   fault='injected verifier PermissionError' if case == 'failed_cleanup' else None)

        selection, thread, result = start('revoked')
        require(active['entered'].wait(15), 'revocation request never reached actual SDK')
        require(agent.steer('Owner input joins this exact live turn.'), 'actual steer rejected owner input')
        try:
            cli.abort_owned_request(*selection)
        except ValueError as exc:
            require(str(exc) == 'OWNERSHIP_REVOKED', 'incorrect revocation refusal')
        else:
            raise AssertionError('revoked request remained abortable')
        active['release'].set()
        finish(thread, result)
        revoked = cli.request_process_status(*selection)
        require(revoked['ownership']['revoked'] and revoked['terminal_scan'] == 'clear',
                'revoked natural completion did not become clean: '+json.dumps(revoked))
        record('owner_join_revocation', status=revoked)

        _, thread, result = start('owner_after', owner=True)
        require(active['entered'].wait(15), 'subsequent owner did not reach real SDK')
        try:
            cli.abort_owned_request(*selection)
        except ValueError as exc:
            require(str(exc) == 'OWNERSHIP_REVOKED', 'old-token refusal changed')
        else:
            raise AssertionError('old token cancelled later owner work')
        require(thread.is_alive(), 'later owner prematurely ended')
        active['release'].set()
        finish(thread, result)
        record('subsequent_owner_turn', calls=active['calls'], old_token_refused=True)
        lifecycle_records = [json.loads(line) for line in (root/'lifecycle.jsonl').read_text().splitlines()]
        require(any(p.get('request_ownership', {}).get('ownership_token') for p in lifecycle_records
                    if isinstance(p.get('request_ownership'), dict)), 'real plugin never emitted native attestation')
        report['lifecycle_events'] = len(lifecycle_records)
    finally:
        active['release'].set()
        if cli is not None:
            for selection in owned:
                try:
                    cli.abort_owned_request(*selection)
                    clean(selection)
                except ValueError as exc:
                    require(str(exc) == 'OWNERSHIP_REVOKED', 'unexpected final cleanup refusal')
            for thread in runs:
                thread.join(20)
            report['owned_workers_still_running'] = sum(thread.is_alive() for thread in runs)
            report['cleanup_statuses'] = [cli.request_process_status(*s) for s in owned]
            report['all_process_scopes_terminated'] = all(t['stop_state'] == 'terminated'
                        for status in report['cleanup_statuses'] for t in status['tools'])
        if agent is not None:
            agent.close()
        server.shutdown()
        server.server_close()
        serving.join(3)
        report_path.write_text(json.dumps(report, indent=2))
        print('EVIDENCE '+str(report_path), flush=True)
    require(report.get('owned_workers_still_running') == 0, 'native fixture worker leaked')
    require(report.get('all_process_scopes_terminated'), 'fixture retained a live or unverifiable process scope')
    report['passed'] = True
    report_path.write_text(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-root', type=Path, required=True)
    parser.add_argument('--harness-root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--preflight', action='store_true')
    mode.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(dict(identity(args.native_root), native_loop_executed=False,
                              execution_requires='reviewed netns wrapper; isolated profiles; local systemd scope bus'), indent=2))
    else:
        run(args)


if __name__ == '__main__':
    main()
