#!/usr/bin/env python3
"""Filesystem bridge for the explicit native experiment, not a process supervisor.

Striatum invokes this with a private runtime configuration and its exact rendered
prompt argument. After preparing a source checkout from the sealed Product,
exec replaces this process with the confined coding harness. Striatum retains
ownership of its lifetime, outputs and native Cairn observation.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

from trial_agent_env import command_base
from trial_retrieval_tools import private_file


def materialize_source(work, expected_manifest):
    product = json.loads((work / 'inputs/01-base').read_text())
    if product['schema_version'] != 1 or not isinstance(product['files'], dict):
        raise ValueError('Expected an expanded Product source tree')
    actual = []
    for name, body in sorted(product['files'].items()):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or str(path) != name or not isinstance(body, str):
            raise ValueError('Invalid sealed source path or content')
        actual.append(dict(path=name, sha256=hashlib.sha256(body.encode()).hexdigest()))
    if actual != expected_manifest:
        raise ValueError('Sealed Product differs from the frozen source manifest')
    source = work / 'source'
    source.mkdir(mode=0o700)
    for name, body in product['files'].items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return source


def runtime_command(config, work, prompt):
    source = materialize_source(work, config['source_manifest'])
    home, cache = Path(config['home']), Path(config['cache'])
    home.mkdir(mode=0o700)
    cache.mkdir(mode=0o700)
    private_file(Path(config['observation_file']), hashlib.sha256(prompt.encode()).hexdigest() + '\n')
    base = command_base(Path(config['opencode']), Path(config['cairn']), work, work, home,
                        Path(config['opencode_config']), cache, False, go_paths=config['go_paths'])[:-1]
    # Match the working directory named in the sealed prompt. Both aliases of
    # the work tree are read-only, with only source scratch and outputs writable.
    index = base.index(str(work))
    if base[index - 1] != '--bind' or base[index + 1] != '/work':
        raise ValueError('Unexpected shared sandbox work mount')
    base[index - 1] = '--ro-bind'
    base += ['--ro-bind', str(work), str(work)]
    for root in ('/work', str(work)):
        base += ['--bind', str(source), root + '/source',
                 '--bind', str(work / 'outputs'), root + '/outputs']
    base[base.index('--chdir') + 1] = str(work)
    base += ['--', '/opt/opencode', 'run', '--pure', '--format', 'json',
             '-m', config['model'], prompt]
    return base


def main():
    if len(sys.argv) != 3:
        raise ValueError('Expected runtime configuration and exact prompt argument')
    config = json.loads(Path(sys.argv[1]).read_text())
    command = runtime_command(config, Path.cwd(), sys.argv[2])
    os.execvp(command[0], command)


if __name__ == '__main__':
    main()
