"""Run the inactive operations fixtures without exposing the host environment."""
from pathlib import Path
import shutil
import subprocess
import tempfile


SOURCE = Path(__file__).resolve().parents[1] / 'integrations/lifecycle/operations'
F1_CLOSURES = {
    'collector-code': ('collector.py', 'parsers.py', 'read_process.py'),
    'helper-code': ('metadata_helper.py', 'parsers.py', 'read_process.py'),
}
F3_MODULES = ('claims.py', 'domains.py', 'effects.py', 'journal.py',
              'runner.py', 'sequence.py', 'transport.py')


def private_copy(source, destination):
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def stage(suite, destination):
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    source = SOURCE / suite
    if suite == 'f1':
        for directory, names in F1_CLOSURES.items():
            origin = source / directory
            if {p.name for p in origin.iterdir()} != set(names):
                raise RuntimeError('unexpected F1 code closure: ' + directory)
            target = destination / directory
            target.mkdir(mode=0o700)
            target.chmod(0o700)
            for name in names:
                private_copy(origin / name, target / name)
        private_copy(source / 'test_f1.py', destination / 'test_f1.py')
    else:
        for name in F3_MODULES:
            private_copy(source / name, destination / name)
        (destination / 'tests').mkdir(mode=0o700)
        (destination / 'tests').chmod(0o700)
        for path in sorted((source / 'tests').glob('*.py')):
            private_copy(path, destination / 'tests' / path.name)


def namespace_argv(source, command):
    return [
        '/usr/bin/bwrap', '--unshare-all', '--die-with-parent',
        '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
        '--ro-bind', '/lib64', '/lib64', '--tmpfs', '/home',
        '--tmpfs', '/tmp', '--dev', '/dev', '--dir', '/proc',
        '--ro-bind', str(source), '/work', '--clearenv',
        '--setenv', 'PATH', '/usr/bin', '--chdir', '/work',
        '/usr/bin/python3', '-I', '-B', *command,
    ]


def main():
    commands = {
        'f1': ['/work/test_f1.py'],
        'f3': ['-m', 'unittest', 'discover', '-s', '/work/tests', '-v'],
    }
    with tempfile.TemporaryDirectory(prefix='cairn-operations-tests-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        for suite, command in commands.items():
            source = root / suite
            stage(suite, source)
            print('Running inactive operations ' + suite + ' fixtures', flush=True)
            result = subprocess.run(namespace_argv(source, command),
                                    env={'PATH': '/usr/bin:/bin'}, timeout=60)
            if result.returncode:
                return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
