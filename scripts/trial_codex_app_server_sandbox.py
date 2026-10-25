"""Build a narrow Codex app-server sandbox using the existing trial boundary."""
import json
import os
from pathlib import Path


def write_launcher(path, *, workspace, profile, codex_entry, selected_auth,
                   codex_root=None, extra_binds=()):
    """Create a host-side launcher; its Codex child sees only declared mounts.

    The selected auth file is readable by Codex and mounted read-only. The
    caller must keep controller files outside ``workspace`` and include only
    disposable sockets/configuration in ``extra_binds``.
    """
    workspace = Path(workspace).resolve(strict=True)
    profile = Path(profile).resolve(strict=True)
    codex_entry = Path(codex_entry).resolve(strict=True)
    codex_root = Path(codex_root or codex_entry.parent).resolve(strict=True)
    selected_auth = Path(selected_auth).resolve(strict=True)
    try:
        profile.relative_to(workspace)
    except ValueError as exc:
        raise ValueError("Codex profile must be inside the actor workspace") from exc
    auth_target = profile / "auth.json"
    binds = [(codex_root, str(codex_root), "ro")]
    targets = {str(auth_target)}
    for source, target, mode in extra_binds:
        if mode not in ("ro", "rw"):
            raise ValueError("extra bind mode must be ro or rw")
        if not Path(target).is_absolute() or target in targets:
            raise ValueError("extra bind target must be absolute and unique")
        targets.add(target)
        binds.append((Path(source).resolve(strict=True), str(target), mode))
    payload = {
        "workspace": str(workspace), "profile": str(profile),
        "codex_entry": str(codex_entry), "binds": [
            [str(source), target, mode] for source, target, mode in binds],
    }
    script = """#!/usr/bin/env python3
import json, os, sys
sys.path.insert(0, {scripts!r})
import trial_task_eval
spec = json.loads({payload!r})
env = {{'HOME': spec['profile'], 'CODEX_HOME': spec['profile'],
       'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}}
command = trial_task_eval.sandbox_command(
    spec['workspace'], '.', spec['binds'], env,
    ['/usr/bin/node', spec['codex_entry'], *sys.argv[1:]], harness='codex',
    include_home_go=False)
auth_fd = os.open(spec['selected_auth'], os.O_RDONLY)
os.set_inheritable(auth_fd, True)
command[command.index('/usr/bin/node'):command.index('/usr/bin/node')] = [
    '--ro-bind-data', str(auth_fd), spec['auth_target']]
os.execvpe(command[0], command, os.environ.copy())
""".format(scripts=str(Path(__file__).resolve().parent),
           payload=json.dumps({**payload, "selected_auth": str(selected_auth),
                               "auth_target": str(auth_target)}))
    path = Path(path)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path
