"""Run the Claude compact fixture against a fresh local PostgreSQL and API."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid

from check_claude_compact_native import check


def main():
    pg_bin = Path(subprocess.run(['pg_config', '--bindir'], check=True, capture_output=True,
                                 text=True, timeout=5).stdout.strip())
    root = Path(tempfile.mkdtemp(prefix='cairn-claude-native-'))
    root.chmod(0o700)
    data, socket = root / 'data', root / 'socket'
    socket.mkdir(mode=0o700)
    pg_started = api = None
    try:
        subprocess.run([str(pg_bin / 'initdb'), '-D', str(data), '--auth-local=trust',
                        '--auth-host=reject', '--no-locale', '-E', 'UTF8'],
                       check=True, capture_output=True, text=True, timeout=30)
        subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-o', f"-F -k {socket} -c listen_addresses=''", '-w', 'start'],
                       check=True, capture_output=True, text=True, timeout=30)
        pg_started = True
        subprocess.run([str(pg_bin / 'createdb'), '-h', str(socket), 'cairn_test'],
                       check=True, capture_output=True, text=True, timeout=10)
        dsn = f'host={socket} dbname=cairn_test sslmode=disable'
        env = dict(os.environ, CAIRN_DATABASE_URL=dsn, CAIRN_TEST_DATABASE_URL=dsn)
        binary = root / 'cairn'
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/cairn'], check=True,
                       capture_output=True, timeout=180, env=env)
        subprocess.run([str(binary), 'migrate'], check=True, capture_output=True, text=True,
                       timeout=30, env=env)
        repo = 'fixture:cairn-216-claude-compact-native'
        identities, tokens = [], {}
        for name in ('alice', 'bob'):
            token = secrets.token_urlsafe(32)
            tokens[name] = token
            path = root / (name + '.token')
            path.write_text(token, encoding='utf-8')
            path.chmod(0o600)
            identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                principal='agent/' + name, repo=repo, role='agent', destination='hosted'))
        (root / 'identities.json').write_text(json.dumps(identities), encoding='utf-8')
        (root / 'identities.json').chmod(0o600)
        env.update(CAIRN_HOME=str(root), CAIRN_DATABASE_URL=dsn)
        api = subprocess.Popen([str(binary), 'serve'], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while not (root / 'api.sock').exists():
            if api.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('disposable Cairn API did not start')
            time.sleep(.02)
        def api_call(name, command, *args, body=None, raw=False):
            common = ['--token-file', str(root / (name + '.token'))]
            argv = [str(binary), 'agent', *common, command, *args] if raw else [str(binary), command]
            if not raw:
                if command == 'agents':
                    argv += [args[0], *common, *args[1:]]
                else:
                    argv += [*common, *args]
            result = subprocess.run(argv, input=body, env=env, text=True,
                capture_output=True, timeout=15)
            payload = json.loads(result.stdout)
            if payload.get('status') != 'OK' or result.returncode:
                raise AssertionError('disposable API call failed: ' + str(payload.get('status')))
            return payload['data']
        source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='fixture source seed', sensitivity='shareable', claim_type='self',
            scope=dict(repo=repo, task_id='*', run_id='*')))))
        config = dict(cairn=str(binary), socket=str(root / 'api.sock'), token_file=str(root / 'bob.token'),
                      repo=repo)
        claude = shutil.which('claude')
        if not claude:
            raise RuntimeError('installed Claude CLI required')
        check(str(Path(claude).resolve(strict=True)), root / 'claude', config, api_call)
        print(json.dumps(dict(result='PASS', environment='private loopback and disposable PostgreSQL',
                              seed_record=source['record_id'], subscription_provider_called=False), sort_keys=True))
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)
        if pg_started:
            subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                           check=True, capture_output=True, text=True, timeout=20)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
