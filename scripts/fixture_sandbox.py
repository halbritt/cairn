"""Bubblewrap command prefixes for provider-free native fixtures."""
import os
from pathlib import Path


def runtime_sandbox(*, include_home_go=True):
    """Mount system runtimes, not the host root or its application/service data.

    Network isolation is selected by the caller. This filesystem/IPC boundary
    is not an egress policy.
    """
    home = Path.home()
    command = ["bwrap", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
               "--die-with-parent", "--cap-drop", "ALL", "--clearenv",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--dir", str(home)]
    for name in ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64",
                 "/usr/share/git-core", "/usr/share/ca-certificates", "/etc/ssl/certs",
                 "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ld.so.cache"):
        path = Path(name)
        if path.exists():
            command += ["--ro-bind", str(path.resolve()), name]
    for name in ("/bin", "/sbin", "/lib", "/lib64"):
        path = Path(name)
        if path.is_symlink():
            command += ["--symlink", os.readlink(path), name]
        elif path.exists():
            command += ["--ro-bind", name, name]
    go = home / ".local/go"
    if include_home_go and go.exists():
        command += ["--ro-bind", str(go.resolve()), str(go)]
    return command


def sandbox_command(root, cwd_name, extra_binds, env, provider_args, *, include_home_go=True):
    """Add an owned writable root, declared binds and environment, then the command."""
    command = runtime_sandbox(include_home_go=include_home_go)
    command += ["--bind", str(root), str(root), "--chdir", str(Path(root) / cwd_name)]
    for source, target, mode in extra_binds:
        command += ["--ro-bind" if mode == "ro" else "--bind", str(source), str(target)]
    for key, value in env.items():
        command += ["--setenv", key, value]
    return command + provider_args
