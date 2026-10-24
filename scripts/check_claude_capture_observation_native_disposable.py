"""Run the native capture fixture in a disposable local PostgreSQL cluster."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    pg_bin = Path(subprocess.run(['pg_config', '--bindir'], check=True, capture_output=True,
                                 text=True, timeout=5).stdout.strip())
    claude = shutil.which('claude')
    if not claude:
        raise RuntimeError('installed Claude CLI required; no provider request will be attempted')
    root = Path(tempfile.mkdtemp(prefix='cairn-claude-capture-native-'))
    root.chmod(0o700)
    data, socket = root / 'data', root / 'socket'
    socket.mkdir(mode=0o700)
    started = False
    try:
        subprocess.run([str(pg_bin / 'initdb'), '-D', str(data), '--auth-local=trust',
                        '--auth-host=reject', '--no-locale', '-E', 'UTF8'],
                       check=True, capture_output=True, text=True, timeout=30)
        subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(data), '-l', str(root / 'postgres.log'),
                        '-o', f"-F -k {socket} -c listen_addresses=''", '-w', 'start'],
                       check=True, capture_output=True, text=True, timeout=30)
        started = True
        subprocess.run([str(pg_bin / 'createdb'), '-h', str(socket), 'cairn_test'],
                       check=True, capture_output=True, text=True, timeout=10)
        dsn = f'host={socket} dbname=cairn_test sslmode=disable'
        env = dict(os.environ, CAIRN_DATABASE_URL=dsn, CAIRN_TEST_DATABASE_URL=dsn,
                   CAIRN_CLAUDE_BINARY=str(Path(claude).resolve(strict=True)))
        binary = root / 'cairn'
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/cairn'], cwd=Path(__file__).resolve().parents[1],
                       check=True, capture_output=True, timeout=180, env=env)
        subprocess.run([str(binary), 'migrate'], check=True, capture_output=True,
                       text=True, timeout=30, env=env)
        completed = subprocess.run(['python3', 'scripts/check_capture_observation.py', str(binary),
                                    str(root / 'capture-observation-home')],
            cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, timeout=150)
        if completed.returncode:
            final_error = next((line.strip() for line in reversed(completed.stderr.splitlines())
                                if line.strip()), 'no diagnostic')
            raise RuntimeError('capture-observation fixture failed with exit ' + str(completed.returncode) +
                               ': ' + final_error[:240])
        print(completed.stdout.strip())
    finally:
        if started:
            subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                           check=True, capture_output=True, text=True, timeout=20)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
