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

import disposable_host

from check_claude_compact_native import check


def main():
    pg_bin = Path(subprocess.run(['pg_config', '--bindir'], check=True, capture_output=True,
                                 text=True, timeout=5).stdout.strip())
    root = Path(tempfile.mkdtemp(prefix='cairn-claude-native-'))
    cluster = disposable_host.OwnedCluster.start(root, pg_bin)
    host = None
    try:
        dsn = cluster.create_database('cairn_test')
        env = dict(os.environ, CAIRN_DATABASE_URL=dsn, CAIRN_TEST_DATABASE_URL=dsn)
        binary = root / 'cairn'
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/cairn'], check=True,
                       capture_output=True, timeout=180, env=env)
        subprocess.run([str(binary), 'migrate'], check=True, capture_output=True, text=True,
                       timeout=30, env=env)
        repo = 'fixture:cairn-216-claude-compact-native'
        host = disposable_host.DisposableHost.start(
            cluster, binary=binary, repo=repo, home=root, roles=('alice', 'bob'),
            principals=lambda name: 'agent/' + name, label='claude_compact_native')
        env = host.env
        def api_call(name, command, *args, body=None, raw=False):
            common = ['--token-file', str(root / (name + '.token'))]
            argv = ['agent', *common, command, *args] if raw else [command]
            if not raw:
                if command == 'agents':
                    argv += [args[0], *common, *args[1:]]
                elif command == 'inbox':
                    argv += ['next', *common, *args]
                else:
                    argv += [*common, *args]
            return host.command(argv, body, timeout=15, label=command)
        config = dict(cairn=str(binary), socket=str(root / 'api.sock'), token_file=str(root / 'alice.token'),
                      repo=repo)
        claude = shutil.which('claude')
        if not claude:
            raise RuntimeError('installed Claude CLI required')
        native_binary = str(Path(claude).resolve(strict=True))
        check(native_binary, root / 'claude-notice', config, api_call, scenario='notice')
        check(native_binary, root / 'claude-response', config, api_call, scenario='response')
        print(json.dumps(dict(result='PASS', environment='private loopback and disposable PostgreSQL',
                              subscription_provider_called=False), sort_keys=True))
    finally:
        if host is not None:
            host.close()
        if not cluster.stop():
            raise RuntimeError('disposable PostgreSQL state unresolved; retained ' + str(root))
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
