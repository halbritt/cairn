"""Explicit common offline Go/PostgreSQL runtime for prospective workers/graders.

Only owned toolchain inputs and fixed public system runtime paths are admitted.
No host account database, service state, arbitrary mounts or provider settings.
"""
import hashlib
import json
import os
from pathlib import Path

SCHEMA = 'cairn.task-runtime/go-postgres/1'
PUBLIC_PATHS = ('/usr/libexec/gcc', '/usr/include', '/usr/share/postgresql', '/usr/share/zoneinfo')


def runtime_descriptor(root):
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or root.resolve(strict=True) != root or root.stat().st_uid != os.getuid():
        raise ValueError('runtime root must be an owned absolute directory')
    if set(p.name for p in root.iterdir()) != {'go', 'module-cache'}:
        raise ValueError('runtime root must contain only go and module-cache')
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise ValueError('runtime inputs must be owned and have no symlinks')
        if path.is_file():
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            files[path.relative_to(root).as_posix()] = dict(sha256=digest, executable=bool(path.stat().st_mode & 0o111))
        elif not path.is_dir():
            raise ValueError('runtime inputs must be regular files/directories')
    if not (root/'go/VERSION').read_text().splitlines()[0].startswith('go1.25.') or not os.access(root/'go/bin/go', os.X_OK):
        raise ValueError('explicit Go1.25 runtime required')
    if not any(name.startswith('module-cache/') for name in files):
        raise ValueError('offline module cache empty')
    return dict(schema=SCHEMA, root=str(root), sha256=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest())


def validate_runtime(value):
    if not isinstance(value,dict) or set(value) != {'schema','root','sha256'} or value.get('schema') != SCHEMA:
        raise ValueError('unsupported common runtime descriptor')
    if runtime_descriptor(Path(value['root'])) != value:
        raise ValueError('common runtime changed')
    return value


def prepare_identity(directory):
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    for name, text in (
        ('passwd', f'fixture:x:{os.getuid()}:{os.getgid()}:Disposable fixture:{Path.home()}:/bin/bash\n'),
        ('group', f'fixture:x:{os.getgid()}:\n')):
        with (directory/name).open('x') as target:
            target.write(text)
        (directory/name).chmod(0o600)
    return directory


def runtime_mounts(runtime, identity, writable):
    root, identity, writable = Path(runtime['root']), Path(identity).resolve(strict=True), Path(writable).resolve(strict=True)
    # Parent/child overlap would expose a writable alias or conceal task files.
    for path in (root,identity):
        if path.is_relative_to(writable) or writable.is_relative_to(path):
            raise ValueError('runtime inputs must not overlap writable workspace')
    if identity.stat().st_uid != os.getuid() or identity.stat().st_mode & 0o077:
        raise ValueError('synthetic identity directory must be private and owned')
    command=[]
    mounts=[(root/'go',Path.home()/'.local/go'), (root/'module-cache',Path('/opt/cairn-runtime/module-cache'))]
    mounts += [(Path(name),Path(name)) for name in PUBLIC_PATHS]
    mounts += [(identity/'passwd',Path('/etc/passwd')),(identity/'group',Path('/etc/group'))]
    for source,target in mounts:
        if not source.exists() or source.is_symlink():
            raise ValueError('required common runtime asset unavailable')
        command += ['--ro-bind',str(source),str(target)]
    return command


def runtime_environment(runtime):
    if runtime is None:
        return {}
    return dict(GOTOOLCHAIN='local',GOFLAGS='-mod=mod',GOCACHE='/tmp/go-cache',GOPATH='/tmp/go',
                GOMODCACHE='/opt/cairn-runtime/module-cache',GOPROXY='off',GOSUMDB='off')
