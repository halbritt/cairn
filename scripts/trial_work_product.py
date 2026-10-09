"""Runner-owned local workspace and selected export; no process supervision.

The required terminal verifier is trusted runner code, never request JSON. Its
supervisor adapter owns authentication, actual wait and containment observations.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from trial_paired_request import (Refusal, canonical_sha, identifier,
                                  need, plain_path, read_bytes, shape, write_selected)

TOTAL_LIMIT = 64 * 1024 * 1024
COUNT_LIMIT = 4096
GIT_OUTPUT_LIMIT = 16 * 1024 * 1024


def git_environment():
    environment = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                       GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    return environment


def git(root, *argv):
    command = ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
               '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=/dev/null',
               '-c', 'core.excludesFile=/dev/null', '-C', str(root), *argv]
    completed = subprocess.run(command, env=git_environment(), stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=20, check=False)
    need(completed.returncode == 0, 'git_failed')
    need(len(completed.stdout) <= GIT_OUTPUT_LIMIT, 'git_output_limit')
    return completed.stdout


def relative_name(text):
    need(isinstance(text, str) and 0 < len(text.encode()) <= 512 and
         not any(ord(c) < 32 or ord(c) == 127 for c in text), 'export_path')
    path = Path(text)
    need(not path.is_absolute() and '..' not in path.parts and '.git' not in path.parts
         and path.as_posix() == text and text != '.', 'export_path')
    return text


def snapshot(root):
    files, total, entries = {}, 0, 0
    for directory, names, filenames in os.walk(root, followlinks=False):
        for name in list(names):
            path = Path(directory)/name
            if name == '.git' and path == root/'.git':
                names.remove(name)
                continue
            need(not path.is_symlink() and name != '.git', 'workspace_indirection')
        entries += len(names) + len(filenames)
        need(entries <= COUNT_LIMIT, 'workspace_limit')
        for name in filenames:
            path = Path(directory)/name
            relative = relative_name(path.relative_to(root).as_posix())
            metadata = path.lstat()
            need(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1, 'workspace_special_file')
            content = read_bytes(path)
            total += len(content)
            need(total <= TOTAL_LIMIT and len(files) < COUNT_LIMIT, 'workspace_limit')
            files[relative] = dict(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content),
                                   mode=stat.S_IMODE(metadata.st_mode))
    return files


def check_tree(source, revision):
    entries = git(source, 'ls-tree', '-rz', revision).split(b'\0')
    for entry in filter(None, entries):
        metadata, name = entry.split(b'\t', 1)
        need(metadata.split()[0] in (b'100644', b'100755'), 'source_special_file')
        relative_name(name.decode('utf-8'))


def prepare_workspace(source, base_commit, actor, binding):
    source, actor = plain_path(str(source)), plain_path(str(actor))
    need(isinstance(base_commit, str) and re.fullmatch('[0-9a-f]{40}', base_commit), 'base_commit')
    shape(binding, 'request_id occurrence_id request_sha256 supervisor_source_sha256', 'workspace_binding')
    identifier(binding['request_id']); identifier(binding['occurrence_id'])
    need(all(isinstance(binding[k], str) and re.fullmatch('[0-9a-f]{64}', binding[k])
             for k in ('request_sha256','supervisor_source_sha256')), 'workspace_binding_digest')
    need(not actor.exists() and source.is_dir() and source != actor and source not in actor.parents, 'workspace_target')
    check_tree(source, base_commit)
    git(source, '-c', 'protocol.file.allow=always', 'clone', '--quiet', '--no-local',
        '--no-hardlinks', '--no-checkout', '--template=', '--', source.as_uri(), str(actor))
    actor.chmod(0o700)
    git(actor, 'remote', 'remove', 'origin')
    git(actor, 'checkout', '--quiet', '--detach', base_commit)
    initial = snapshot(actor)
    config_sha = hashlib.sha256(read_bytes(actor/'.git/config')).hexdigest()
    prepared = dict(schema='cairn.prepared-workspace/1', actor_path=str(actor),
                    base_commit=base_commit, binding=dict(binding), initial=initial,
                    git_config_sha256=config_sha)
    prepared['workspace_sha256'] = canonical_sha(prepared)
    return prepared


def closure(prepared, verify_terminal):
    need(callable(verify_terminal), 'trusted_terminal_adapter_required')
    terminal = verify_terminal(dict(prepared['binding']))
    shape(terminal, 'schema binding workspace_sha256 actual_exit containment_status retention_status proof_sha256', 'terminal_shape')
    need(terminal['schema'] == 'cairn.work-product-terminal/1' and
         terminal['binding'] == prepared['binding'] and
         terminal['workspace_sha256'] == prepared['workspace_sha256'], 'terminal_binding')
    need(type(terminal['actual_exit']) is int and terminal['actual_exit'] == 0 and
         terminal['containment_status'] == 'released' and terminal['retention_status'] == 'closed', 'terminal_unknown_or_failed')
    need(isinstance(terminal['proof_sha256'], str) and re.fullmatch('[0-9a-f]{64}', terminal['proof_sha256']), 'terminal_proof')
    return terminal


def check_git_state(actor, prepared):
    gitdir = actor/'.git'
    need(gitdir.is_dir() and not gitdir.is_symlink(), 'git_indirection')
    need(hashlib.sha256(read_bytes(gitdir/'config')).hexdigest() == prepared['git_config_sha256'], 'git_config_changed')
    forbidden = ('commondir','gitdir','objects/info/alternates','objects/info/http-alternates',
                 'info/grafts','refs/replace','worktrees','modules','shallow','MERGE_HEAD',
                 'CHERRY_PICK_HEAD','REVERT_HEAD','rebase-apply','rebase-merge')
    need(not any((gitdir/name).exists() or (gitdir/name).is_symlink() for name in forbidden), 'extra_git_state')
    count = 0
    for directory, names, files in os.walk(gitdir, followlinks=False):
        count += len(names)
        need(count <= 20000, 'extra_git_state')
        for name in names:
            need(not (Path(directory)/name).is_symlink(), 'git_indirection')
        for name in files:
            path = Path(directory)/name
            metadata = path.lstat()
            count += 1
            need(count <= 20000 and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
                 and not name.endswith('.lock') and 'hooks' not in path.relative_to(gitdir).parts, 'extra_git_state')
    need(git(actor, 'merge-base', prepared['base_commit'], 'HEAD').decode().strip() == prepared['base_commit'], 'base_lineage')
    check_tree(actor, 'HEAD')


def export_work_product(prepared, output, *, allowed_paths, test_log, verify_terminal):
    shape(prepared, 'schema actor_path base_commit binding initial git_config_sha256 workspace_sha256', 'prepared_shape')
    need(prepared['schema'] == 'cairn.prepared-workspace/1' and prepared['workspace_sha256'] ==
         canonical_sha({k:v for k,v in prepared.items() if k != 'workspace_sha256'}), 'workspace_manifest_changed')
    terminal = closure(prepared, verify_terminal)
    actor, output = plain_path(prepared['actor_path']), plain_path(str(output))
    need(not output.exists() and output != actor and actor not in output.parents, 'export_target')
    check_git_state(actor, prepared)
    current = snapshot(actor)
    need(isinstance(allowed_paths, list) and len(set(allowed_paths)) == len(allowed_paths), 'allowed_paths')
    allowed = {relative_name(name) for name in allowed_paths}
    changed = {name for name in set(current)|set(prepared['initial']) if current.get(name) != prepared['initial'].get(name)}
    committed_paths = {relative_name(name.decode()) for name in filter(None,
        git(actor, 'diff', '--name-only', '-z', '--no-ext-diff', '--no-textconv',
            prepared['base_commit'], 'HEAD', '--').split(b'\0'))}
    changed |= committed_paths
    need(changed <= allowed, 'unexpected_task_path')
    need('DESIGN.md' in allowed and relative_name(test_log) in allowed and
         'DESIGN.md' in current and test_log in current, 'required_work_product_missing')
    commit = git(actor, 'rev-parse', 'HEAD').decode().strip()
    patch = git(actor, 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames',
                prepared['base_commit'], '--', *sorted(allowed))
    committed_patch = git(actor, 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames',
                          prepared['base_commit'], 'HEAD', '--', *sorted(allowed))
    working_patch = git(actor, 'diff', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames',
                        'HEAD', '--', *sorted(allowed))
    exported = {name:read_bytes(actor/name) for name in sorted(allowed) if name in current}
    patches = {'diff.patch':patch, 'committed.patch':committed_patch, 'working.patch':working_patch}
    need(sum(map(len, exported.values())) + sum(map(len,patches.values())) + 1024*1024 <= TOTAL_LIMIT, 'export_limit')
    need(snapshot(actor) == current and git(actor, 'rev-parse', 'HEAD').decode().strip() == commit,
         'workspace_changed_during_export')
    output.mkdir(mode=0o700)
    (output/'files').mkdir(mode=0o700)
    for name, content in exported.items():
        target = output/'files'/name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open('xb') as sink:
            sink.write(content)
            sink.flush()
            os.fsync(sink.fileno())
        target.chmod(0o600)
    for name, content in patches.items():
        with (output/name).open('xb') as sink:
            sink.write(content)
            sink.flush()
            os.fsync(sink.fileno())
        (output/name).chmod(0o600)
    selected = dict(schema='cairn.work-product-export/1', status='exported_for_review',
        base_commit=prepared['base_commit'], commit=commit, files={name:current[name] for name in exported},
        deleted=sorted(name for name in changed if name not in current), diff_sha256=hashlib.sha256(patch).hexdigest(),
        patch_sha256={name:hashlib.sha256(content).hexdigest() for name,content in patches.items()},
        added=sorted(name for name in exported if name not in prepared['initial']),
        design_sha256=current['DESIGN.md']['sha256'], test_log_sha256=current[test_log]['sha256'],
        terminal_sha256=canonical_sha(terminal), workspace_sha256=prepared['workspace_sha256'],
        task_acceptance=False, basis='trusted caller terminal adapter; no independent task grade')
    write_selected(output/'export.json', selected)
    return selected
