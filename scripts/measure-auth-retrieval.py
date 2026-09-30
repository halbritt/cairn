#!/usr/bin/env python3
"""Opt-in synthetic CLI/API and agent_tools timing; owns its disposable cluster."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
REPO = 'fixture:authenticated-scale'
REQUIRED = 'REQUIRED_SCALE_CONTEXT: synthetic guidance must remain complete.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output.resolve()
    started = time.monotonic()
    deadline = started + 885  # 15 minutes total, reserving cleanup time.
    rows = []
    env = {k: v for k, v in os.environ.items() if not k.startswith('CAIRN_')}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    binary = None
    api = None
    pg_bin = Path(subprocess.check_output(['pg_config', '--bindir'], text=True).strip())

    def run(command, *, body=None, limit=8, run_env=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return dict(outcome='not_run_deadline', elapsed_ms=0), ''
        begin = time.monotonic()
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=run_env or env,
                                cwd=ROOT, start_new_session=True)
        spawn_ms = (time.monotonic() - begin) * 1000
        try:
            stdout, stderr = proc.communicate(body, timeout=min(limit, remaining))
            outcome = 'ok' if proc.returncode == 0 else 'nonzero'
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
            outcome = 'timeout'
        diagnostic = {}
        if outcome != 'ok' and stdout:
            try:
                diagnostic['error_response'] = json.loads(stdout)
            except ValueError:
                diagnostic['non_json_stdout'] = stdout[-2000:]
        if outcome == 'timeout' or 'timed out' in stderr:
            diagnostic['timeout_kind'] = 'outer' if outcome == 'timeout' else 'inner'
        return dict(outcome=outcome, returncode=proc.returncode,
                    elapsed_ms=round((time.monotonic() - begin) * 1000, 3),
                    spawn_ms=round(spawn_ms, 3), stderr=stderr[-2000:], **diagnostic), stdout

    def setup(command, **kwargs):
        row, stdout = run(command, limit=180, **kwargs)
        if row['outcome'] != 'ok':
            raise RuntimeError(f'setup {command[0]}: {row}')
        return stdout

    def save(row):
        rows.append(row)
        with (out / 'samples.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n')
            stream.flush()

    common_source = (ROOT / 'core/scale_measure_test.go').read_text()
    common = re.search(r'scaleCommon = strings.Fields\(`([^`]+)`\)', common_source).group(1).split()
    queries = {'common': 'retry backoff scheduler',
               'task': 'postgres migration rollback procedure before deploy',
               'rare': 'w2871 w1450 w0999', 'literal': '"bounded window" scheduler lease',
               'hook48': ' '.join(common[i * 7 % len(common)] for i in range(48)),
               'nomatch': 'zzqx nothingmatches'}
    plan = dict(schema='cairn.auth-scale/1', revision=setup(['git', 'rev-parse', 'HEAD']).strip(),
                generator_sha256=hashlib.sha256(common_source.encode()).hexdigest(),
                sizes=[1000, 5000, 10000], sizes_include_three_controls=True,
                generated_body_counts=[997, 4997, 9997],
                harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                hook_sha256=hashlib.sha256((ROOT / 'integrations/lifecycle/memory.py').read_bytes()).hexdigest(),
                queries=queries, warmups=1, measured_per_query=3,
                search_room=32000, context_bytes=9500, operation_timeout_seconds=8,
                hook_internal_search_timeout_seconds=5, hook_assessment_seconds=5,
                wall_bound_seconds=900, seed='PCG(235,116), existing scaleBody',
                controls=['mandatory shareable instruction', 'local note', 'other-task note'],
                recall_mode='agent_tools', model_calls=False, platform=platform.platform(),
                load_start=os.getloadavg(), go=setup(['go', 'version']).strip(),
                postgres=setup([str(pg_bin / 'postgres'), '--version']).strip())
    # Frozen before building, creating a DB, generating notes or measuring requests.
    (out / 'plan.json').write_text(json.dumps(plan, indent=2))
    try:
        with tempfile.TemporaryDirectory(prefix='cairn-auth-scale-') as directory:
            fixture = Path(directory)
            pg_data, socket_dir = fixture / 'pg', fixture / 'socket'
            home = fixture / 'api'
            home.mkdir(mode=0o700)
            socket_dir.mkdir()
            project = fixture / 'project'
            project.mkdir()
            (project / '.git').mkdir()
            binary = fixture / 'cairn'
            seed_binary = fixture / 'seed.test'
            setup(['go', 'build', '-o', str(binary), './cmd/cairn'])
            setup(['go', 'test', '-c', '-o', str(seed_binary), './core'])
            setup([str(pg_bin / 'initdb'), '-D', str(pg_data), '--auth-local=trust',
                   '--auth-host=reject', '--no-locale', '-E', 'UTF8'])
            try:
                setup([str(pg_bin / 'pg_ctl'), '-D', str(pg_data), '-l', str(fixture / 'pg.log'),
                       '-o', f"-F -k {socket_dir} -c listen_addresses=''", '-w', 'start'])
                setup([str(pg_bin / 'createdb'), '-h', str(socket_dir), 'scale'])
                env.update(CAIRN_DATABASE_URL=f'host={socket_dir} dbname=scale sslmode=disable',
                           CAIRN_TEST_DATABASE_URL=f'host={socket_dir} dbname=scale sslmode=disable',
                           CAIRN_HOME=str(home))
                setup([str(binary), 'migrate'])
                token = secrets.token_urlsafe(32)
                token_file = home / 'agent.token'
                token_file.write_text(token)
                token_file.chmod(0o600)
                identities = [dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                                   principal='agent:scale', repo=REPO, role='agent', destination='hosted')]
                (home / 'identities.json').write_text(json.dumps(identities))
                (home / 'identities.json').chmod(0o600)
                with (out / 'api.log').open('w') as log:
                    api = subprocess.Popen([str(binary), 'serve'], env=env, stdout=log, stderr=log,
                                           start_new_session=True)
                    ready_until = min(deadline, time.monotonic() + 10)
                    while not (home / 'api.sock').exists():
                        if api.poll() is not None or time.monotonic() >= ready_until:
                            raise RuntimeError('isolated API not ready')
                        time.sleep(.02)
                    client_env = dict(env, CAIRN_DATABASE_URL='disabled-client-database')
                    agent = [str(binary), 'agent', '--socket', str(home / 'api.sock'),
                             '--token-file', str(token_file)]
                    setup(agent + ['version'], run_env=client_env)
                    bad_token = fixture / 'invalid.token'
                    bad_token.write_text('invalid-fixture-token')
                    bad_token.chmod(0o600)
                    rejected, _ = run([str(binary), 'agent', '--socket', str(home / 'api.sock'),
                                       '--token-file', str(bad_token), 'version'], run_env=client_env)
                    save(dict(stage='authentication_negative_control', **rejected))
                    if rejected['outcome'] != 'nonzero' or rejected.get('error_response', {}).get('status') != 'AUTHORITY_DENIED':
                        raise RuntimeError('invalid credential did not receive expected authentication refusal')
                    config = dict(cairn=str(binary), socket=str(home / 'api.sock'),
                                  token_file=str(token_file), repo=REPO, task_id='task', run_id='run',
                                  state_dir=str(fixture / 'state'), harness='claude',
                                  recall_mode='agent_tools', context_bytes=9500, semantic_fallback=False)
                    config_path = fixture / 'config.json'
                    config_path.write_text(json.dumps(config))
                    previous = 0
                    for size in plan['sizes']:
                        seed_env = dict(env, CAIRN_AUTH_SCALE='1', CAIRN_SCALE_FROM=str(max(0, previous - 3)),
                                        CAIRN_SCALE_TO=str(size - 3), CAIRN_SCALE_SEED_RESULT=str(out / f'seed-{size}.json'))
                        seed_row, _ = run([str(seed_binary), '-test.run=^TestAuthScaleSeed$', '-test.v'],
                                          limit=180, run_env=seed_env)
                        save(dict(stage='seed', notes=size, **seed_row))
                        if seed_row['outcome'] != 'ok':
                            break
                        previous = size
                        for name, query in queries.items():
                            for repetition in range(4):
                                labels = dict(notes=size, query=name, repetition=repetition, warmup=repetition == 0)
                                row, _ = run([str(binary), 'version'], run_env=client_env)
                                save(dict(labels, stage='cli_startup_control', **row))
                                row, stdout = run(agent + ['search', '--repo', REPO, '--task', 'task', '--run', 'run',
                                                           '--tokens', '32000', '--', query], run_env=client_env)
                                data = {}
                                if row['outcome'] == 'ok':
                                    try:
                                        envelope = json.loads(stdout)
                                        data = envelope['data']
                                        row.update(api_status=envelope['status'], entries=len(data.get('index', [])),
                                                   response_bytes=len(stdout.encode()),
                                                   required_preserved=REQUIRED in json.dumps(data.get('selected', [])))
                                        if not envelope.get('ok') or not row['required_preserved'] or 'EXCLUDED_SCALE_CONTEXT' in stdout:
                                            row['outcome'] = 'contract_failure'
                                    except (ValueError, KeyError, TypeError):
                                        row['outcome'] = 'invalid_response'
                                save(dict(labels, stage='search', **row))
                                entries = data.get('index', []) if row['outcome'] == 'ok' else []
                                if entries:
                                    pulled, payload = run(agent + ['pull'], body=json.dumps(entries[0]['pull_arguments']), run_env=client_env)
                                    if pulled['outcome'] == 'ok':
                                        try:
                                            record = json.loads(payload)['data']['selection']['record']
                                            pulled['body_bytes'] = len(record['body'].encode())
                                            if record['record_id'] != entries[0]['record_id'] or record['version'] != entries[0]['version']:
                                                pulled['outcome'] = 'contract_failure'
                                        except (ValueError, KeyError, TypeError):
                                            pulled['outcome'] = 'invalid_response'
                                    save(dict(labels, stage='pull', **pulled))
                                else:
                                    save(dict(labels, stage='pull', outcome='not_run_no_handle', elapsed_ms=0))
                                session = str(uuid.uuid4())
                                event = dict(hook_event_name='UserPromptSubmit', session_id=session,
                                             cwd=str(project), prompt=query)
                                hook, stdout = run([sys.executable, str(ROOT / 'integrations/lifecycle/memory.py'),
                                                    '--config', str(config_path)], body=json.dumps(event), run_env=client_env)
                                state_path = fixture / 'state' / (session + '.json')
                                if state_path.exists():
                                    hook['recall_status'] = json.loads(state_path.read_text()).get('last_recall')
                                if hook['outcome'] == 'ok':
                                    try:
                                        text = json.loads(stdout).get('hookSpecificOutput', {}).get('additionalContext', '')
                                        hook.update(delivered_bytes=len(text.encode()), required_preserved=REQUIRED in text,
                                                    empty=not bool(text))
                                        if not hook['required_preserved'] or len(text.encode()) > 9500 or 'EXCLUDED_SCALE_CONTEXT' in text:
                                            hook['outcome'] = 'contract_failure'
                                    except (ValueError, TypeError):
                                        hook['outcome'] = 'invalid_response'
                                hook['over_5s'] = hook['elapsed_ms'] > 5000
                                save(dict(labels, stage='hook', **hook))
            finally:
                if api is not None and api.poll() is None:
                    os.killpg(api.pid, signal.SIGTERM)
                    try:
                        api.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(api.pid, signal.SIGKILL)
                        api.wait()
                if (pg_data / 'postmaster.pid').exists():
                    subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(pg_data), '-m', 'immediate', '-w', 'stop'],
                                   check=True, stdout=subprocess.DEVNULL, timeout=10)
    except Exception as exc:
        (out / 'failure.txt').write_text(str(exc))
        raise
    finally:
        summary = []
        for size in plan['sizes']:
            for stage in ('search', 'pull', 'hook', 'cli_startup_control'):
                samples = [r for r in rows if r.get('notes') == size and r.get('stage') == stage and not r.get('warmup', True)]
                counts = {key: sum(r['outcome'] == key for r in samples) for key in sorted({r['outcome'] for r in samples})}
                attempted = [r['elapsed_ms'] for r in samples if not r['outcome'].startswith('not_run')]
                attempted.sort()
                summary.append(dict(notes=size, stage=stage, planned=18, recorded=len(samples), outcomes=counts,
                                    all_attempt_median_ms=attempted[len(attempted)//2] if attempted else None,
                                    all_attempt_p95_ms=attempted[max(0, math.ceil(.95*len(attempted))-1)] if attempted else None))
        (out / 'summary.json').write_text(json.dumps(dict(rows=summary, wall_seconds=time.monotonic()-started,
                                                         load_end=os.getloadavg()), indent=2))


if __name__ == '__main__':
    main()
