"""Pre-actor baseline retained outside the writable attempt workspace.

This is harness custody under the existing mount boundary, not independent
attestation, process containment, final-artifact export or a resource-fit proof.
"""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from types import MappingProxyType

from trial_paired_request import FILE_LIMIT


class BaselineError(ValueError):
    pass


def _need(value, code='original_baseline_mismatch'):
    if not value:
        raise BaselineError(code)


def _git(cwd, *argv, plumbing=None, input=None, stdin=None):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0')
    options = []
    if plumbing is not None:
        private, worktree, objects, attributes = plumbing
        options = ['--git-dir='+str(private), '--work-tree='+str(worktree)]
        env['GIT_OBJECT_DIRECTORY'] = str(objects)
        if attributes is not None:
            options.append('--attr-source='+attributes)
    try:
        result = subprocess.run(['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
                                 '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null',
                                 *options, *argv], cwd=cwd, env=env, input=input, stdin=stdin,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise BaselineError('original_baseline_git_unavailable') from None
    _need(result.returncode == 0, 'original_baseline_git_unavailable')
    return result.stdout


def _repository(cwd):
    paths = dict(cwd=str(Path(cwd).resolve()),
                 worktree=os.fsdecode(_git(cwd, 'rev-parse', '--show-toplevel').rstrip(b'\n')),
                 git_dir=os.fsdecode(_git(cwd, 'rev-parse', '--absolute-git-dir').rstrip(b'\n')),
                 objects=os.fsdecode(_git(cwd, 'rev-parse', '--path-format=absolute', '--git-path', 'objects').rstrip(b'\n')))
    identities = {}
    for key, value in paths.items():
        path = Path(value)
        _need(path.resolve() == path and path.is_dir(), 'original_baseline_repository_changed')
        info = path.stat()
        identities[key] = [info.st_dev, info.st_ino]
    return dict(paths=paths, identities=identities)


@contextmanager
def _raw_repository(cwd, commit, repository):
    """Private config/index/attributes; existing content-addressed objects only.

    Explicit --attr-source needs Git 2.43+. An older Git refuses, rather than
    falling back to actor attributes. No clone or full worktree copy is made.
    """
    _need(_repository(cwd) == repository, 'original_baseline_repository_changed')
    worktree = Path(repository['paths']['worktree'])
    objects = Path(repository['paths']['objects'])
    with tempfile.TemporaryDirectory(prefix='baseline-index-') as scratch:
        private = Path(scratch)/'git'
        _git(cwd, 'init', '--bare', '--quiet', '--template=',
             '--object-format='+('sha256' if len(commit)==64 else 'sha1'), str(private))
        plumbing = (private, worktree, objects, None)
        empty_tree = _git(cwd, 'mktree', plumbing=plumbing, input=b'').decode().strip()
        plumbing = (private, worktree, objects, empty_tree)
        _git(cwd, 'fsck', '--strict', '--no-reflogs', '--no-dangling', commit, plumbing=plumbing)
        yield plumbing


def _raw_index(cwd, commit, plumbing):
    _git(cwd, 'read-tree', commit, plumbing=plumbing)
    names = _git(cwd, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                 '--full-name', '--', '.', plumbing=plumbing).split(b'\0')
    for name in sorted(set(filter(None, names))):
        name = name.rstrip(b'/')
        _need(not name.startswith(b'/') and not {b'..', b'.git'} & set(name.split(b'/')))
        path = plumbing[1] / os.fsdecode(name)
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is None:
            mode, oid = '0', '0'*len(commit)
        elif stat.S_ISREG(metadata.st_mode):
            mode = '100755' if metadata.st_mode & 0o111 else '100644'
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as content:
                _need(os.fstat(content.fileno()) == metadata, 'original_baseline_worktree_changed')
                oid = _git(cwd, 'hash-object', '-w', '--no-filters', '--stdin',
                           plumbing=plumbing, stdin=content).decode().strip()
        elif stat.S_ISLNK(metadata.st_mode):
            mode = '120000'
            oid = _git(cwd, 'hash-object', '-w', '--no-filters', '--stdin', plumbing=plumbing,
                       input=os.fsencode(os.readlink(path))).decode().strip()
        elif stat.S_ISDIR(metadata.st_mode):
            nested = Path(os.fsdecode(_git(path, 'rev-parse', '--show-toplevel').rstrip(b'\n')))
            if nested == path:
                # Gitlinks keep the nested repository's actual selected HEAD.
                mode = '160000'
                oid = _git(path, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
            else:
                # A tracked file replaced by a directory is removed before
                # its separately listed children are inserted.
                mode, oid = '0', '0'*len(commit)
        else:
            raise BaselineError('original_baseline_worktree_type')
        _git(cwd, 'update-index', '-z', '--index-info', plumbing=plumbing,
             input=(mode+' '+oid+'\t').encode()+name+b'\0')


def _identity(base, case, arm, seed):
    run_id = f"{case['id']}.{arm}.s{seed}"
    _need(base.name == run_id and base.parent.name == 'runs')
    work = base / 'work'
    cwd = work / case['cwd']
    _need(cwd.resolve().is_relative_to(work.resolve()))
    return dict(run_id=run_id, case=case['id'], arm=arm, seed=seed,
                cwd=cwd.relative_to(work).as_posix())


def _path(base, identity):
    # Neither the actor's work root nor the shell evaluator's cwd.parent bind
    # exposes this sibling of runs/. No caller-selected output path is used.
    return base.parent.parent / 'baselines' / (hashlib.sha256(identity['run_id'].encode()).hexdigest()+'.json')


def _private(path, *, directory=False):
    info = path.lstat()
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
          and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
          and (directory or info.st_nlink == 1), 'original_baseline_file_policy')


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def _pin(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


class AttemptBaseline:
    def __init__(self, document, pin):
        self.identity = MappingProxyType(dict(document['identity']))
        self.snapshot = MappingProxyType(dict(document['snapshot']))
        self.base_commit = document['base_commit']
        self.repository = document['repository']
        self.pin = dict(pin)

    @classmethod
    def capture(cls, base, case, arm, seed):
        base = Path(base)
        identity = _identity(base, case, arm, seed)
        cwd = base / 'work' / identity['cwd']
        commit = _git(cwd, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        _need(re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', commit))
        _need(_git(cwd, 'rev-parse', '--verify', 'refs/tags/eval-base^{commit}').decode().strip() == commit)
        repository = _repository(cwd)
        _need(all(Path(path).is_relative_to((base/'work').resolve())
                  for path in repository['paths'].values()), 'original_baseline_repository_outside_work')
        # Read immediately after trusted setup, before actor admission. The
        # original setup snapshot remains for compatibility but is never read
        # again by this owner, run_agent or regrade.
        snapshot = json.loads((base/'work/.eval-snapshot.json').read_bytes())
        _need(type(snapshot) is dict and all(type(k) is str and type(v) is str
              and re.fullmatch('[0-9a-f]{64}', v) for k,v in snapshot.items()))
        document = dict(schema='cairn.attempt-baseline/1', identity=identity,
                        base_commit=commit, snapshot=snapshot, repository=repository)
        raw = _encoded(document)
        _need(len(raw) <= FILE_LIMIT, 'original_baseline_record_bound')
        path = _path(base, identity)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        _private(path.parent, directory=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as retained:
            retained.write(raw)
            retained.flush()
            os.fsync(retained.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return cls.load(base, case, arm, seed, _pin(raw))

    @classmethod
    def load(cls, base, case, arm, seed, pin):
        base = Path(base)
        identity = _identity(base, case, arm, seed)
        _need(type(pin) is dict and set(pin) == {'bytes','sha256'}
              and type(pin['bytes']) is int and 0 < pin['bytes'] <= FILE_LIMIT
              and type(pin['sha256']) is str and re.fullmatch('[0-9a-f]{64}', pin['sha256']),
              'original_baseline_unavailable')
        path = _path(base, identity)
        try:
            _private(path.parent, directory=True)
            _private(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as retained:
                info = os.fstat(retained.fileno())
                _need(stat.S_ISREG(info.st_mode) and info.st_size == pin['bytes'])
                raw = retained.read(pin['bytes']+1)
            _need(_pin(raw) == pin)
            document = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise BaselineError('original_baseline_unavailable') from None
        _need(set(document) == {'schema','identity','snapshot','base_commit','repository'}
              and document['schema'] == 'cairn.attempt-baseline/1' and document['identity'] == identity)
        _need(type(document['base_commit']) is str and re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', document['base_commit']))
        _need(type(document['snapshot']) is dict and all(type(k) is str and type(v) is str
              and re.fullmatch('[0-9a-f]{64}', v) for k,v in document['snapshot'].items()))
        return cls(document, pin)

    def added_lines(self, cwd):
        commit = self.base_commit
        with _raw_repository(cwd, commit, self.repository) as plumbing:
            _raw_index(cwd, commit, plumbing)
            diff = _git(cwd, 'diff', '--cached', '--no-ext-diff', '--no-textconv',
                        '--no-color', '--unified=0', commit, plumbing=plumbing).decode(errors='replace')
        return [line[1:] for line in diff.splitlines() if line.startswith('+') and not line.startswith('+++')]

    def commit_files(self, cwd):
        head = _git(cwd, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        with _raw_repository(cwd, self.base_commit, self.repository) as plumbing:
            return _git(cwd, 'diff', '--no-ext-diff', '--no-textconv', '--name-only',
                        self.base_commit, head, plumbing=plumbing).decode().split()
