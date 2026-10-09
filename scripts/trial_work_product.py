"""Runner-owned local workspace and selected export; no process supervision.

The required terminal verifier is trusted runner code, never request JSON. Its
supervisor adapter owns authentication, actual wait and containment observations.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from trial_paired_request import (Refusal, canonical_sha, identifier,
                                  need, plain_path, read_bytes, scan_refusal, shape, write_selected)

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
    for directory, names, filenames in os.walk(root, followlinks=False, onerror=scan_refusal):
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


def reachable_objects(actor, base_commit):
    identities = {line.split(b' ', 1)[0].decode('ascii') for line in
                  git(actor, 'rev-list', '--objects', base_commit).splitlines()}
    need(0 < len(identities) <= 200000 and
         all(re.fullmatch('[0-9a-f]{40}', value) for value in identities), 'git_object_inventory')
    return identities


def prepare_workspace(source, base_commit, actor, binding):
    source, actor = plain_path(str(source)), plain_path(str(actor))
    need(isinstance(base_commit, str) and re.fullmatch('[0-9a-f]{40}', base_commit), 'base_commit')
    shape(binding, 'request_id occurrence_id request_sha256 supervisor_source_sha256', 'workspace_binding')
    identifier(binding['request_id']); identifier(binding['occurrence_id'])
    need(all(isinstance(binding[k], str) and re.fullmatch('[0-9a-f]{64}', binding[k])
             for k in ('request_sha256','supervisor_source_sha256')), 'workspace_binding_digest')
    need(not actor.exists() and source.is_dir() and source != actor and source not in actor.parents, 'workspace_target')
    check_tree(source, base_commit)
    actor.mkdir(mode=0o700)
    git(actor, 'init', '--quiet', '--template=')
    git(actor, 'config', 'core.hooksPath', '/dev/null')
    git(actor, '-c', 'protocol.file.allow=always', 'fetch', '--quiet', '--no-tags',
        '--no-write-fetch-head', '--no-recurse-submodules', '--', source.as_uri(), base_commit)
    git(actor, 'checkout', '--quiet', '--detach', base_commit)
    need(git(actor, 'for-each-ref', '--format=%(refname)') == b'', 'unexpected_initial_refs')
    need(git(actor, 'rev-parse', '--symbolic-full-name', 'HEAD').strip() == b'HEAD', 'base_not_detached')
    objects = reachable_objects(actor, base_commit)
    all_objects = set(git(actor, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').decode('ascii').splitlines())
    need(all_objects == objects, 'unexpected_initial_objects')
    initial = snapshot(actor)
    config_sha = hashlib.sha256(read_bytes(actor/'.git/config')).hexdigest()
    prepared = dict(schema='cairn.prepared-workspace/1', actor_path=str(actor),
                    base_commit=base_commit, binding=dict(binding), initial=initial,
                    git_config_sha256=config_sha, initial_refs={},
                    initial_objects_sha256=canonical_sha(sorted(objects)), initial_objects_count=len(objects))
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
    for directory, names, files in os.walk(gitdir, followlinks=False, onerror=scan_refusal):
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
    objects = reachable_objects(actor, prepared['base_commit'])
    need(canonical_sha(sorted(objects)) == prepared['initial_objects_sha256'] and
         len(objects) == prepared['initial_objects_count'], 'base_objects_changed')
    check_tree(actor, 'HEAD')


def _check_prepared(prepared):
    shape(prepared, 'schema actor_path base_commit binding initial git_config_sha256 initial_refs initial_objects_sha256 initial_objects_count workspace_sha256', 'prepared_shape')
    need(prepared['schema'] == 'cairn.prepared-workspace/1' and prepared['workspace_sha256'] ==
         canonical_sha({k:v for k,v in prepared.items() if k != 'workspace_sha256'}), 'workspace_manifest_changed')


def _inspect_paths(prepared, output, allowed_paths):
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
    return actor, output, current, allowed, changed


def _inspect_bytes(actor, prepared, current, allowed):
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
    return commit, exported, patches


def _write_export_files(output, exported, patches):
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


def export_work_product(prepared, output, *, allowed_paths, test_log, verify_terminal):
    _check_prepared(prepared)
    terminal = closure(prepared, verify_terminal)
    actor, output, current, allowed, changed = _inspect_paths(prepared, output, allowed_paths)
    need('DESIGN.md' in allowed and relative_name(test_log) in allowed and
         'DESIGN.md' in current and test_log in current, 'required_work_product_missing')
    commit, exported, patches = _inspect_bytes(actor, prepared, current, allowed)
    _write_export_files(output, exported, patches)
    patch = patches['diff.patch']
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


ATTEMPT_STOP_CAUSES = frozenset({'completed', 'wall_deadline', 'native_budget_limit',
    'output_limit', 'timing_limit', 'resource_limit', 'provider_admission_stop',
    'observer_stop', 'unknown'})
ATTEMPT_OBSERVATIONS = frozenset({'input_delivery_incomplete', 'frame_reader_refused',
    'terminal_result_missing', 'permission_denial_reported', 'action_observation_unknown',
    'hook_observation_unknown', 'capture_fault', 'parser_fault', 'sink_fault',
    'observer_interrupted', 'native_budget_reported', 'resource_limit_observed'})


def attempt_closure(prepared, verify_attempt):
    """The callback is trusted owner code; this schema never authenticates a run."""
    need(callable(verify_attempt), 'trusted_attempt_adapter_required')
    try:
        terminal = verify_attempt(dict(prepared['binding']))
    except Exception:
        # A failed verifier cannot authorize inspection/export; no exception text.
        raise Refusal('attempt_terminal_unavailable') from None
    shape(terminal, 'schema binding workspace_sha256 actual_wait_status actual_returncode '
          'containment_status failure_evidence_status stop_cause observations proof_sha256',
          'attempt_terminal_shape')
    need(terminal['schema'] == 'cairn.work-product-attempt-terminal/1' and
         terminal['binding'] == prepared['binding'] and
         terminal['workspace_sha256'] == prepared['workspace_sha256'], 'attempt_terminal_binding')
    raw, code = terminal['actual_wait_status'], terminal['actual_returncode']
    need(type(raw) is int and 0 <= raw <= 65535 and type(code) is int,
         'attempt_wait_unknown')
    need((os.WIFEXITED(raw) and raw == os.WEXITSTATUS(raw) << 8) or
         (os.WIFSIGNALED(raw) and raw <= 255), 'attempt_wait_nonterminal')
    need(os.waitstatus_to_exitcode(raw) == code, 'attempt_wait_inconsistent')
    need(terminal['containment_status'] == 'released' and
         terminal['failure_evidence_status'] == 'closed', 'attempt_closure_unknown')
    need(isinstance(terminal['stop_cause'], str) and
         terminal['stop_cause'] in ATTEMPT_STOP_CAUSES, 'attempt_stop_cause')
    facts = terminal['observations']
    need(isinstance(facts, list) and len(facts) <= len(ATTEMPT_OBSERVATIONS) and
         all(isinstance(fact, str) and fact in ATTEMPT_OBSERVATIONS for fact in facts) and
         len(set(facts)) == len(facts), 'attempt_observations')
    need(isinstance(terminal['proof_sha256'], str) and
         re.fullmatch('[0-9a-f]{64}', terminal['proof_sha256']), 'attempt_terminal_proof')
    return terminal


def export_attempt_evidence(prepared, output, *, allowed_paths, required_paths, verify_attempt):
    """Export known closed partial work without grading it or changing success export.

    Root supplies `verify_attempt` as trusted runner code, never through JSON or
    actor-loaded modules. Known nonzero/signal exits remain observations. Missing
    work is recorded only after successful inspection; uncertain inspection does
    not become an absent-file claim. Exclusive fsynced writes are not a batch
    atomic publication: later I/O failure may leave a consumed partial target.
    """
    _check_prepared(prepared)
    terminal = attempt_closure(prepared, verify_attempt)
    need(isinstance(required_paths, list) and len(required_paths) <= COUNT_LIMIT and
         all(isinstance(name, str) for name in required_paths) and
         len(set(required_paths)) == len(required_paths), 'required_paths')
    need(isinstance(allowed_paths, list) and len(allowed_paths) <= COUNT_LIMIT and
         all(isinstance(name, str) for name in allowed_paths), 'allowed_paths')
    try:
        required = {relative_name(name) for name in required_paths}
        actor, output, current, allowed, changed = _inspect_paths(prepared, output, allowed_paths)
        need(required <= allowed, 'required_paths_unselected')
        commit, exported, patches = _inspect_bytes(actor, prepared, current, allowed)
        selected = dict(schema='cairn.work-product-attempt-evidence/1', status='evidence_exported',
            base_commit=prepared['base_commit'], commit=commit,
            files={name:current[name] for name in exported},
            deleted=sorted(name for name in changed if name not in current),
            added=sorted(name for name in exported if name not in prepared['initial']),
            patch_sha256={name:hashlib.sha256(content).hexdigest() for name,content in patches.items()},
            required_presence={name:name in current for name in sorted(required)},
            required_sha256={name:current[name]['sha256'] if name in current else None for name in sorted(required)},
            terminal=dict(terminal), terminal_sha256=canonical_sha(terminal),
            workspace_sha256=prepared['workspace_sha256'], task_acceptance=False,
            basis='trusted caller attempt adapter; reported stop/facts are observations, not validity; '
                  'schema and hashes are not runtime authentication')
        # The new presence/terminal metadata also fits the unchanged total cap.
        metadata_bytes = len(json.dumps(selected, ensure_ascii=False, allow_nan=False,
                                        sort_keys=True).encode('utf-8'))+1
        need(sum(map(len, exported.values()))+sum(map(len, patches.values()))+
             metadata_bytes <= TOTAL_LIMIT, 'export_limit')
        _write_export_files(output, exported, patches)
        write_selected(output/'attempt-evidence.json', selected)
        return selected
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise Refusal('attempt_export_io') from None
