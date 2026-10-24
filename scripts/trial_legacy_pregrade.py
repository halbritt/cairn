"""Runner-owned, bounded pre-grade snapshot for original legacy attempts.

The snapshot is eligible only after the reviewed bubblewrap pid-namespace init
exit proof and the owned writer-leaf release. It does not claim complete host
descendant custody. The workspace and Git metadata are copied into a private
retained record before the evaluator can mutate them.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat

from trial_attempt_custody import BaselineError, FILE_LIMIT, _git_common_identity, _repository

SCHEMA = 'cairn.legacy-pregrade-snapshot/1'
MAX_ENTRIES = 4096


class PregradeError(ValueError):
    pass


def _need(value, code='legacy_pregrade_unavailable'):
    if not value:
        raise PregradeError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def _sig(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _path64(value):
    return base64.b64encode(value).decode('ascii')


def _open_regular(path, before, *, remaining):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        _need(stat.S_ISREG(opened.st_mode) and _sig(opened) == _sig(before),
              'legacy_pregrade_file_replaced')
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, remaining - total + 1))
            if not chunk:
                break
            total += len(chunk)
            _need(total <= remaining, 'legacy_pregrade_byte_limit')
            chunks.append(chunk)
        after = os.fstat(fd)
        _need(_sig(opened) == _sig(after), 'legacy_pregrade_file_changed')
        return b''.join(chunks), after
    finally:
        os.close(fd)


def _inventory(root, *, retain, budget):
    """Return exact entries and a second-pass signature/digest map."""
    entries, observed, total = [], {}, 0

    def visit(directory, prefix=b''):
        nonlocal total
        before_dir = os.stat(directory, follow_symlinks=False)
        _need(stat.S_ISDIR(before_dir.st_mode), 'legacy_pregrade_directory_replaced')
        try:
            with os.scandir(directory) as listing:
                names = sorted((os.fsencode(item.name) for item in listing))
        except OSError:
            raise PregradeError('legacy_pregrade_directory_unreadable') from None
        _need(len(entries) + len(names) <= MAX_ENTRIES, 'legacy_pregrade_entry_limit')
        for name in names:
            _need(name not in (b'.', b'..') and b'\x00' not in name,
                  'legacy_pregrade_path_invalid')
            if not prefix and name == b'.git':
                continue
            path = directory / os.fsdecode(name)
            relative = prefix + (b'/' if prefix else b'') + name
            info = path.lstat()
            entry = dict(path_b64=_path64(relative), mode=stat.S_IMODE(info.st_mode))
            if stat.S_ISDIR(info.st_mode):
                entry['kind'] = 'directory'
                entries.append(entry)
                observed[relative] = ('directory', _sig(info))
                visit(path, relative)
                after = path.lstat()
                _need(_sig(info) == _sig(after), 'legacy_pregrade_directory_changed')
            elif stat.S_ISREG(info.st_mode):
                data, after = _open_regular(path, info, remaining=budget-total if retain else FILE_LIMIT-total)
                total += len(data)
                digest = hashlib.sha256(data).hexdigest()
                entry.update(kind='file', bytes=len(data), sha256=digest)
                if retain:
                    entry['content_b64'] = _path64(data)
                entries.append(entry)
                observed[relative] = ('file', _sig(after), digest)
            elif stat.S_ISLNK(info.st_mode):
                target = os.fsencode(os.readlink(path))
                after = path.lstat()
                _need(_sig(info) == _sig(after), 'legacy_pregrade_symlink_changed')
                total += len(target)
                _need(total <= budget, 'legacy_pregrade_byte_limit')
                entry.update(kind='symlink', bytes=len(target), sha256=hashlib.sha256(target).hexdigest())
                if retain:
                    entry['target_b64'] = _path64(target)
                entries.append(entry)
                observed[relative] = ('symlink', _sig(after), hashlib.sha256(target).hexdigest())
            else:
                raise PregradeError('legacy_pregrade_special_file')
        _need(_sig(before_dir) == _sig(os.stat(directory, follow_symlinks=False)),
              'legacy_pregrade_directory_changed')

    visit(root)
    return entries, observed, total


def _git_metadata(repository, git_common, *, retain, budget, max_entries):
    git_dir = Path(repository['paths']['git_dir'])
    common_dir = Path(git_common['path']) if git_common is not None else git_dir
    _need(not (git_dir / 'reftable').exists() and not (common_dir / 'reftable').exists(),
          'legacy_pregrade_git_ref_storage_unsupported')
    rows, observed, total = [], {}, 0
    paths = [(b'worktree/HEAD', git_dir/'HEAD', 'required'),
             (b'worktree/index', git_dir/'index', 'optional'),
             (b'worktree/index.lock', git_dir/'index.lock', 'optional'),
             (b'worktree/commondir', git_dir/'commondir', 'optional'),
             (b'common/packed-refs', common_dir/'packed-refs', 'optional'),
             (b'common/packed-refs.lock', common_dir/'packed-refs.lock', 'optional')]
    roots = [('common/refs', common_dir/'refs')]
    if common_dir != git_dir:
        roots.append(('worktree/refs', git_dir/'refs'))
    for label, refs in roots:
        if refs.is_symlink():
            raise PregradeError('legacy_pregrade_ref_indirection')
        if not refs.exists():
            paths.append((os.fsencode(label), refs, 'optional'))
            continue
        for parent, dirs, files in os.walk(refs, followlinks=False):
            dirs.sort(); files.sort()
            parent = Path(parent)
            relative = parent.relative_to(refs).as_posix()
            dir_label = label + ('/' + relative if relative != '.' else '')
            paths.append((os.fsencode(dir_label), parent, 'directory'))
            for name in dirs:
                _need(not (parent/name).is_symlink(), 'legacy_pregrade_ref_indirection')
            paths.extend((os.fsencode(label+'/'+(parent/name).relative_to(refs).as_posix()),
                          parent/name, 'optional') for name in files)
    _need(len(paths) <= max_entries, 'legacy_pregrade_entry_limit')
    for label, path, requirement in sorted(paths, key=lambda item: item[0]):
        try:
            info = path.lstat()
        except FileNotFoundError:
            if requirement == 'required':
                raise PregradeError('legacy_pregrade_git_metadata_missing') from None
            rows.append(dict(path_b64=_path64(label), kind='absent'))
            observed[label] = ('absent',)
            continue
        if requirement == 'directory':
            _need(stat.S_ISDIR(info.st_mode), 'legacy_pregrade_ref_indirection')
            rows.append(dict(path_b64=_path64(label), kind='directory', mode=stat.S_IMODE(info.st_mode)))
            observed[label] = ('directory', _sig(info))
            continue
        data, after = _open_regular(path, info, remaining=budget-total)
        total += len(data)
        row = dict(path_b64=_path64(label), kind='file', mode=stat.S_IMODE(info.st_mode),
                   bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        if retain:
            row['content_b64'] = _path64(data)
        rows.append(row)
        observed[label] = (_sig(after), hashlib.sha256(data).hexdigest())
    _need(len(rows) <= max_entries, 'legacy_pregrade_entry_limit')
    return rows, observed, total


def _proof_projection(record):
    return {key: value for key, value in record.items()
            if key not in ('final_artifact_custody', 'final_artifact_snapshot')}


def _validate_writer_proof(record, run_id, cwd):
    _need(type(record) is dict and record.get('schema') == 'cairn.legacy-writer-release/1'
          and record.get('state') == 'released' and record.get('release') == 'released'
          and isinstance(record.get('bootstrap'), dict) and record['bootstrap'].get('state') == 'member'
          and record.get('close_errors') == [] and record.get('complete_descendant_custody') is False,
          'legacy_pregrade_writer_release_unavailable')
    _need(record.get('attempt') == dict(run_id=run_id, workspace=str(cwd)),
          'legacy_pregrade_attempt_mismatch')
    quiescence = record.get('writer_quiescence')
    _need(type(quiescence) is dict and quiescence.get('state') == 'namespace_init_exited'
          and quiescence.get('basis') == 'reviewed_identity_source_zap_before_pidfd_notify'
          and type(quiescence.get('scope')) is dict,
          'legacy_pregrade_writer_quiescence_unproved')
    init = record.get('init') or {}
    evidence = record.get('launch_evidence') or {}
    runtime, reviewed = record.get('runtime_identity'), record.get('reviewed_identity')
    namespace = init.get('pid_namespace') if isinstance(init, dict) else None
    namespaces = evidence.get('namespaces') if isinstance(evidence, dict) else None
    reviewed_kernel = reviewed.get('kernel') if isinstance(reviewed, dict) else None
    reviewed_bwrap = reviewed.get('bwrap') if isinstance(reviewed, dict) else None
    _need(isinstance(init, dict) and init.get('state') == 'bound' and record.get('init_exit') == 'exited'
          and isinstance(namespace, dict) and type(namespace.get('inode')) is int
          and namespace['inode'] > 0 and type(namespace.get('dev')) is int and namespace['dev'] >= 0
          and isinstance(namespaces, dict) and type(namespaces.get('pid')) is int
          and namespaces['pid'] == namespace['inode']
          and isinstance(reviewed, dict)
          and reviewed.get('schema') == 'cairn.writer-reviewed-identity/2'
          and reviewed.get('execution') == 'sealed_memfd'
          and isinstance(reviewed.get('source_scope'), dict)
          and quiescence['scope'] == reviewed['source_scope']
          and isinstance(reviewed_kernel, dict) and set(reviewed_kernel) == {'release', 'version'}
          and all(type(value) is str and value for value in reviewed_kernel.values())
          and isinstance(reviewed_bwrap, dict) and set(reviewed_bwrap) == {'path', 'sha256'}
          and all(type(value) is str and value for value in reviewed_bwrap.values())
          and isinstance(runtime, dict) and set(runtime) == {'kernel', 'bwrap'}
          and runtime == reviewed_runtime(reviewed, runtime)
          and record.get('execution_binding') == dict(mode='sealed_memfd',
              sha256=(runtime.get('bwrap') or {}).get('sha256')),
          'legacy_pregrade_writer_quiescence_unbound')
    return quiescence


def reviewed_runtime(reviewed, runtime):
    if not isinstance(runtime, dict) or not isinstance(reviewed, dict):
        return None
    return dict(kernel=reviewed.get('kernel'), bwrap=reviewed.get('bwrap'))


def _private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    _need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
          and stat.S_IMODE(info.st_mode) == 0o700, 'legacy_pregrade_store_policy')


def capture(*, base, case, arm, seed, baseline, writer_record, stream_bytes, prompt):
    """Capture exact bounded final workspace/Git state before grading; return private pin."""
    base = Path(base)
    run_id = f"{case['id']}.{arm}.s{seed}"
    cwd = base / 'work' / case['cwd']
    quiescence = _validate_writer_proof(writer_record, run_id, cwd)
    _need(baseline.identity == dict(run_id=run_id, case=case['id'], arm=arm, seed=seed,
              cwd=case['cwd']), 'legacy_pregrade_baseline_identity_mismatch')
    try:
        current_repository = _repository(cwd)
    except BaselineError:
        raise PregradeError('legacy_pregrade_repository_changed') from None
    _need(current_repository == baseline.repository, 'legacy_pregrade_repository_changed')
    try:
        current_git_common = _git_common_identity(cwd, baseline.repository, work_root=base/'work')
    except BaselineError:
        raise PregradeError('legacy_pregrade_repository_changed') from None
    if baseline.git_common is None:
        _need(current_git_common['path'] == baseline.repository['paths']['git_dir']
              and current_git_common['commondir_sha256'] is None,
              'legacy_pregrade_git_common_unbound')
    else:
        _need(current_git_common == baseline.git_common, 'legacy_pregrade_repository_changed')
    root_info = cwd.stat()
    root_identity = [root_info.st_dev, root_info.st_ino]
    before_repo = baseline.repository
    entries, first, used = _inventory(cwd, retain=True, budget=FILE_LIMIT)
    git_rows, git_first, git_used = _git_metadata(before_repo, baseline.git_common, retain=True,
                                                  budget=FILE_LIMIT-used,
                                                  max_entries=MAX_ENTRIES-len(entries))
    after_entries, second, second_used = _inventory(cwd, retain=False, budget=FILE_LIMIT)
    after_git, git_second, _ = _git_metadata(before_repo, baseline.git_common, retain=False,
                                             budget=FILE_LIMIT-second_used,
                                             max_entries=MAX_ENTRIES-len(after_entries))
    _need(first == second and git_first == git_second
          and len(entries) == len(after_entries) and len(git_rows) == len(after_git),
          'legacy_pregrade_changed_during_capture')
    try:
        after_repository = _repository(cwd)
    except BaselineError:
        raise PregradeError('legacy_pregrade_repository_changed') from None
    _need(after_repository == before_repo and [cwd.stat().st_dev, cwd.stat().st_ino] == root_identity,
          'legacy_pregrade_repository_changed')
    try:
        after_git_common = _git_common_identity(cwd, before_repo, work_root=base/'work')
    except BaselineError:
        raise PregradeError('legacy_pregrade_repository_changed') from None
    _need(after_git_common == current_git_common, 'legacy_pregrade_repository_changed')
    file_paths = {_path for _path, value in second.items() if value[0] in ('file', 'symlink')}
    baseline_paths = {os.fsencode(path) for path in baseline.snapshot}
    deletions = [_path64(path) for path in sorted(baseline_paths - file_paths)]
    release_bytes = _json(_proof_projection(writer_record))
    stream_digest = hashlib.sha256(stream_bytes).hexdigest()
    prompt_digest = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    document = dict(schema=SCHEMA, attempt=dict(run_id=run_id, case=case['id'], arm=arm, seed=seed,
                                                workspace=str(cwd)),
                    baseline=dict(pin=baseline.pin, base_commit=baseline.base_commit,
                                  repository=baseline.repository),
                    stream=dict(bytes=len(stream_bytes), sha256=stream_digest),
                    prompt=dict(bytes=len(prompt.encode('utf-8')), sha256=prompt_digest),
                    writer_release=dict(sha256=hashlib.sha256(release_bytes).hexdigest(),
                                        state=writer_record['state'],
                                        writer_quiescence=quiescence),
                    workspace=dict(path=str(cwd), identity=root_identity, entries=entries,
                                   deletions=deletions),
                    git=dict(repository=before_repo, common_repository=current_git_common, index_refs=git_rows),
                    bytes_captured=used+git_used)
    raw = _json(document)
    _need(len(raw) <= FILE_LIMIT, 'legacy_pregrade_record_bound')
    digest = hashlib.sha256(raw).hexdigest()
    store = base.parent.parent / 'final-artifacts'
    _private_dir(store)
    path = store / (hashlib.sha256(run_id.encode()).hexdigest()+'.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    dirfd = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
    return dict(schema='cairn.legacy-pregrade-snapshot-pin/1',
                record_path=path.relative_to(base.parent.parent).as_posix(),
                bytes=len(raw), sha256=digest)
