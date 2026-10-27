"""Run the native capture fixture in a disposable local PostgreSQL cluster."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import disposable_host


def main():
    pg_bin = Path(subprocess.run(['pg_config', '--bindir'], check=True, capture_output=True,
                                 text=True, timeout=5).stdout.strip())
    claude = shutil.which('claude')
    if not claude:
        raise RuntimeError('installed Claude CLI required; no provider request will be attempted')
    root = Path(tempfile.mkdtemp(prefix='cairn-claude-capture-native-'))
    cluster = disposable_host.OwnedCluster.start(root, pg_bin)
    try:
        dsn = cluster.create_database('cairn_test')
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
        if not cluster.stop():
            raise RuntimeError('disposable PostgreSQL state unresolved; retained ' + str(root))
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
