"""Linux test boundary for unchanged semantic workers and their Go supervisors.

The mapped passwd home protects workers even when Go removes HOME. This is
test confinement, not a change to production worker supervision or telemetry.
"""

import hashlib
import argparse
import json
import os
from pathlib import Path
import pwd
import selectors
import shutil
import signal
import select
import stat
import subprocess
import threading
import time
import sys


WORKER_ENV = dict(
    PATH="/usr/bin:/bin",
    HF_HUB_OFFLINE="1",
    HF_HUB_DISABLE_TELEMETRY="1",
    TOKENIZERS_PARALLELISM="false",
)


class IsolationError(RuntimeError):
    def __init__(self, message, *, evidence=None):
        super().__init__(message)
        self.evidence = evidence or {}


SEALED_ORACLE_SHA256 = (
    "ef04e09d5bb7d906740da0f6406b14b4de3d14a6dfe664d313ce8ebb87815a4d"
)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check_sealed_oracle(root):
    """Read the one authorized, immutable oracle. This does not qualify its vectors."""
    root = Path(root).absolute()
    seal = root / "SHA256SUMS"
    if not seal.is_file() or seal.is_symlink() or digest(seal) != SEALED_ORACLE_SHA256:
        raise IsolationError(
            "oracle seal is missing or differs from the authorized seal"
        )
    entries = seal.read_text().splitlines()
    if len(entries) != 31:
        raise IsolationError("oracle seal has the wrong file inventory")
    names = set()
    for line in entries:
        expected, name = line.split("  ", 1)
        path = root / name
        if (
            name in names
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or root not in path.resolve().parents
            or digest(path) != expected
        ):
            raise IsolationError("sealed oracle file changed: " + name)
        names.add(name)
    metadata = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        metadata[str(path.relative_to(root))] = dict(
            inode=info.st_ino,
            device=info.st_dev,
            owner=info.st_uid,
            group=info.st_gid,
            mode=stat.S_IMODE(info.st_mode),
            mtime_ns=info.st_mtime_ns,
            size=info.st_size,
        )
    return dict(sha256=SEALED_ORACLE_SHA256, files=len(entries), metadata=metadata)


def augment_sealed_prefix(prefix, bindings):
    """Only append read-only bindings before W's final root remount."""
    if prefix[-2:] != ["--remount-ro", "/"] or "--" in prefix:
        raise IsolationError("sealed prefix has an unexpected command boundary")
    additions = [
        part
        for source, destination in bindings
        for part in ("--ro-bind", str(source), str(destination))
    ]
    return [*prefix[:-2], *additions, *prefix[-2:]]


def derive_w_prefix(prefix, old_backing, new_backing):
    if (
        prefix[33:36] != ["--bind", str(old_backing), "/sandbox"]
        or prefix.count("--bind") != 1
    ):
        raise IsolationError(
            "sealed W writable operand is not the authorized argument34"
        )
    derived = list(prefix)
    derived[34] = str(new_backing)
    return derived


class DisposableBacking:
    """Exclusive directory creation is the cleanup authority; pre-existing data refuses."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink() or any(p.is_symlink() for p in self.path.parents):
            raise IsolationError("disposable backing path contains a symlink")
        try:
            self.path.mkdir(mode=0o700)
        except FileExistsError as error:
            raise IsolationError(
                f"disposable backing already exists: {self.path}"
            ) from error
        self.identity = self.path.stat()
        self.removed = False
        if (
            self.identity.st_uid != os.getuid()
            or self.identity.st_mode & 0o777 != 0o700
        ):
            raise IsolationError("disposable backing is not owned/private")

    def verify(self, *, empty=False):
        current = self.path.lstat()
        if (
            self.removed
            or not stat.S_ISDIR(current.st_mode)
            or current.st_ino != self.identity.st_ino
            or current.st_dev != self.identity.st_dev
            or current.st_uid != os.getuid()
            or stat.S_IMODE(current.st_mode) != 0o700
            or (empty and any(self.path.iterdir()))
        ):
            raise IsolationError(
                "disposable backing is replaced, reused or not private"
            )

    def remove(self):
        if self.removed:
            return
        self.verify()
        shutil.rmtree(self.path)
        self.removed = True


def process_start(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]


class _ProcessTree:
    """PID descriptors keep cleanup tied to observed processes after leader exit."""

    def __init__(self, process):
        self.process = process
        self.members = {}
        self.lock = threading.Lock()
        self.start = process_start(process.pid)
        self.observe(process.pid)

    def observe(self, pid):
        descriptor = None
        try:
            start = process_start(pid)
            key = (pid, start)
            with self.lock:
                if key in self.members:
                    return
            descriptor = os.pidfd_open(pid)
            if process_start(pid) != start:
                return
            with self.lock:
                if key in self.members:
                    return
                else:
                    self.members[key] = descriptor
                    descriptor = None
        except (FileNotFoundError, ProcessLookupError):
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def stop(self):
        # Kill the process group only while the original group leader still exists.
        # Tracked pidfds handle descendants when wait()/poll() already reaped it.
        try:
            if process_start(self.process.pid) == self.start:
                os.killpg(self.process.pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass
        with self.lock:
            descriptors = list(self.members.values())
        for descriptor in descriptors:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait(timeout=5)
        deadline = time.monotonic() + 5
        pending = descriptors
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise IsolationError(
                    "observed descendant did not exit after namespace cleanup"
                )
            exited, _, _ = select.select(pending, [], [], remaining)
            pending = [descriptor for descriptor in pending if descriptor not in exited]

    def close(self):
        with self.lock:
            for descriptor in self.members.values():
                os.close(descriptor)
            self.members.clear()


def stop_process(process):
    process._semantic_tree.stop()
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def bounded_communicate(process, data, *, timeout, stdout_limit, stderr_limit=65536):
    """Drain both output pipes while feeding stdin; reject before retaining excess."""
    deadline = time.monotonic() + timeout
    output = {"stdout": bytearray(), "stderr": bytearray()}
    remaining = memoryview(data or b"")
    try:
        with selectors.DefaultSelector() as selector:
            for stream, name in (
                (process.stdout, "stdout"),
                (process.stderr, "stderr"),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            if remaining:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map():
                wait = deadline - time.monotonic()
                if wait <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                for event, _ in selector.select(wait):
                    stream, name = event.fileobj, event.data
                    if name == "stdin":
                        try:
                            written = os.write(stream.fileno(), remaining[:65536])
                        except BrokenPipeError:
                            remaining = remaining[:0]
                        else:
                            remaining = remaining[written:]
                        if not remaining:
                            selector.unregister(stream)
                            stream.close()
                    else:
                        chunk = os.read(stream.fileno(), 65536)
                        if not chunk:
                            selector.unregister(stream)
                            continue
                        limit = stdout_limit if name == "stdout" else stderr_limit
                        if len(chunk) > limit - len(output[name]):
                            output[name].extend(chunk[: limit - len(output[name])])
                            raise IsolationError(f"worker {name} exceeds limit")
                        output[name].extend(chunk)
            process.wait(timeout=max(0, deadline - time.monotonic()))
        return bytes(output["stdout"]), bytes(output["stderr"])
    except BaseException as error:
        stop_process(process)
        evidence = dict(
            argv=process.args,
            exit_code=process.returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
        )
        if isinstance(error, (IsolationError, subprocess.TimeoutExpired)):
            raise IsolationError(str(error), evidence=evidence) from error
        raise


_PREFLIGHT = r"""
import errno,json,os,pathlib,pwd,socket,sys
home=pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir)
assert 'HOME' not in os.environ
assert os.path.expanduser('~')==str(home)
for relative in ('.local/share/cairn/hosted-agent.token',
                 '.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db'):
    assert not (home/relative).exists(), 'owner path exposed'
    assert not (pathlib.Path(sys.argv[1])/relative).exists(), 'real owner path exposed'
probe=home/'.semantic-isolation-probe'
probe.write_text('mapped-home')
try:
    with open('/semantic-read-only-probe','w') as stream:
        stream.write('wrong')
except OSError as error:
    assert error.errno in (errno.EROFS,errno.EACCES)
else:
    raise RuntimeError('read-only mount writable')
try:
    with open('/outside-write-probe','w') as stream:
        stream.write('wrong')
except OSError as error:
    assert error.errno in (errno.EROFS,errno.EACCES)
else:
    raise RuntimeError('root filesystem writable')
assert all(name=='lo' for _,name in socket.if_nameindex()), 'external network interface'
sock=socket.socket()
sock.settimeout(.2)
try:
    sock.connect(('192.0.2.1',9))
except OSError as error:
    assert error.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH), str(error)
else:
    raise RuntimeError('network isolation failed')
finally:
    sock.close()
print(json.dumps(dict(network_namespace=os.readlink('/proc/self/ns/net'),
                     uid=os.getuid(),getpwuid_home=str(home),expanduser_home=os.path.expanduser('~'),
                     home=str(home),home_probe_inode=probe.stat().st_ino)))
"""


class SemanticIsolation:
    def __init__(self, *, read_only_paths, output_dir):
        self.read_only_paths = [Path(path).absolute() for path in read_only_paths]
        self.output_dir = Path(output_dir).absolute()
        self.owner_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        self.home_dir = self.output_dir / "mapped-home"
        self.proof = None
        self._processes = []
        self._observed = {}
        self._observation_errors = []
        self._lock = threading.Lock()
        self._observers = []
        self._owner_network = os.readlink("/proc/self/ns/net")
        self._backing = None
        self._sealed_root = None
        self._sealed_prefix = None
        self._original_prefix = None
        self._seal_before = None
        self._invocations = []
        self._sealed_environment = None
        self._closed = False

    @classmethod
    def sealed_oracle(cls, *, sealed_root, backing, read_only_paths, output_dir):
        """W only: caller creates the exclusive backing; this context removes it.

        No model is imported by construction or preflight. The sealed environment
        is literal for model commands. Additional mounts are read-only; capture
        output travels through bounded stdout/stderr pipes.
        """
        root = Path(sealed_root).absolute()
        check_sealed_oracle(root)
        if not isinstance(backing, DisposableBacking):
            raise IsolationError(
                "derived W requires a newly owned external scratch backing"
            )
        backing.verify(empty=True)
        manifest = json.loads((root / "inventory/experiment-manifest.json").read_text())
        attempt = json.loads((root / "oracle-attempt.json").read_text())
        prefix = manifest["sandbox_prefix"]
        if (
            attempt["command"][: len(prefix)] != prefix
            or attempt["command"][len(prefix)] != "--"
            or manifest["sandbox_environment"] != attempt["sandbox_environment"]
        ):
            raise IsolationError("sealed W prefix/environment records disagree")
        derived = derive_w_prefix(prefix, root / "disposable", backing.path)
        instance = cls(read_only_paths=read_only_paths, output_dir=output_dir)
        protected = [
            root,
            instance.owner_home / ".cache/Microsoft",
            instance.owner_home / ".local/share/cairn/hosted-agent.token",
            instance.owner_home / ".ssh",
            *instance.owner_home.glob(".codex*"),
        ]
        protected += [
            Path(prefix[i + 1]).resolve()
            for i, arg in enumerate(prefix)
            if arg == "--ro-bind"
        ]
        if (
            backing.path == instance.owner_home
            or backing.path in instance.owner_home.parents
            or any(
                backing.path == p
                or p in backing.path.parents
                or backing.path in p.parents
                for p in protected
            )
        ):
            raise IsolationError(
                "derived W backing overlaps sealed or protected live state"
            )
        if instance.output_dir == root or root in instance.output_dir.parents:
            raise IsolationError(
                "new W evidence must be outside the frozen oracle root"
            )
        instance._sealed_root = root
        instance._seal_before = check_sealed_oracle(root)
        instance._original_prefix = prefix
        instance._sealed_prefix = derived
        instance._sealed_environment = manifest["sandbox_environment"]
        instance._backing = backing
        instance.home_dir = backing.path / "home"
        return instance

    def _check_mounts(self):
        protected = (
            self.owner_home / ".cache/Microsoft",
            self.owner_home / ".local/share/cairn/hosted-agent.token",
            self.owner_home / ".ssh",
            self.owner_home / ".aws",
            *self.owner_home.glob(".codex*"),
        )
        allowed = [
            Path("/usr"),
            *[p.resolve(strict=True) for p in self.read_only_paths],
        ]
        for path in self.read_only_paths:
            resolved = path.resolve(strict=True)
            if not any(
                base in resolved.parents
                for base in (Path("/usr"), self.owner_home, Path("/tmp"))
            ):
                raise IsolationError(
                    "runtime mount is outside the selected user/system tool roots"
                )
            if (
                resolved == self.owner_home
                or resolved in self.owner_home.parents
                or any(
                    resolved == p or resolved in p.parents or p in resolved.parents
                    for p in protected
                )
            ):
                raise IsolationError("unsafe broad home/cache or owner-state mount")
            links = [path] if path.is_symlink() else []
            if path.is_dir():
                links += [child for child in path.rglob("*") if child.is_symlink()]
            for link in links:
                target = link.resolve(strict=True)
                if not any(
                    target == base or base in target.parents for base in allowed
                ):
                    raise IsolationError(
                        f"symlink escapes selected runtime closure: {link}"
                    )

    def __enter__(self):
        if self.output_dir.is_symlink() or any(
            p.is_symlink() for p in self.output_dir.parents
        ):
            raise IsolationError("output path contains a symlink")
        try:
            self.output_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            raise IsolationError(
                "output directory already exists; preserve prior evidence"
            ) from error
        try:
            if self._sealed_root is None:
                self._backing = DisposableBacking(self.home_dir)
            else:
                self._backing.verify(empty=True)
                check_sealed_oracle(self._sealed_root)
                for name in (
                    "home",
                    "cache",
                    "config",
                    "data",
                    "state",
                    "tmp",
                    "fastembed",
                ):
                    (self._backing.path / name).mkdir(mode=0o700)
            return self._prepare()
        except BaseException:
            self._cleanup()
            raise

    def _prepare(self):
        bubblewrap = shutil.which("bwrap")
        if not bubblewrap:
            raise IsolationError("bubblewrap is required; no unisolated fallback")
        try:
            self._check_mounts()
        except OSError as error:
            raise IsolationError(f"unverifiable runtime closure: {error}") from error
        probe = self.output_dir / "read-only-probe"
        probe.write_text("unchanged")
        if self._sealed_root is not None:
            bindings = [(p, p) for p in self.read_only_paths]
            bindings.append((probe, "/semantic-read-only-probe"))
            self._prefix = augment_sealed_prefix(self._sealed_prefix, bindings)
        else:
            self._prepare_default_prefix(bubblewrap, probe)
        command = [
            "/usr/bin/python3",
            "-I",
            "-S",
            "-c",
            _PREFLIGHT,
            str(self.owner_home),
        ]
        if self._sealed_root is not None:
            # This HOME removal is a nonmodel diagnostic only, not a W env rewrite.
            command = ["/usr/bin/env", "-u", "HOME", *command]
        process = self._spawn(
            command,
            env=None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = bounded_communicate(
            process, b"", timeout=10, stdout_limit=65536
        )
        stop_process(process)
        if process.returncode:
            raise IsolationError(
                "isolation preflight failed: " + stderr.decode(errors="replace")
            )
        try:
            self.proof = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as error:
            raise IsolationError(
                "isolation preflight returned no valid proof"
            ) from error
        expected_home = "/sandbox/home" if self._sealed_root else str(self.owner_home)
        if (
            self.proof["network_namespace"] == self._owner_network
            or self.proof["home"] != expected_home
            or self.proof["home_probe_inode"]
            != (self.home_dir / ".semantic-isolation-probe").stat().st_ino
        ):
            raise IsolationError("network/home isolation proof mismatch")
        self.proof.update(
            wrapper_sha256=digest(__file__),
            profile="derived-W" if self._sealed_root else "default",
            original_prefix=self._original_prefix,
            backing_argument_diff=(
                [
                    dict(
                        index=34,
                        original=self._original_prefix[34],
                        derived=self._sealed_prefix[34],
                    )
                ]
                if self._sealed_root
                else []
            ),
            read_only_argument_insertion=(
                dict(
                    original_index=len(self._original_prefix) - 2,
                    arguments=self._prefix[len(self._sealed_prefix) - 2 : -2],
                )
                if self._sealed_root
                else None
            ),
            effective_prefix=self._prefix,
            additional_read_only_paths=[str(p) for p in self.read_only_paths],
            sealed_oracle=check_sealed_oracle(self._sealed_root)
            if self._sealed_root
            else None,
            model_environment=self._sealed_environment or WORKER_ENV,
        )
        if self._sealed_root:
            files = [p for p in self.read_only_paths if p.is_file()]
            files += [
                child
                for p in self.read_only_paths
                if p.is_dir()
                for child in sorted(p.rglob("*"))
                if child.is_file()
            ]
            self.proof["additional_read_only_files"] = [
                dict(path=str(p), sha256=digest(p)) for p in files
            ]
            self.proof["canary_sha256"] = digest(probe)
        (self.output_dir / "isolation-proof.json").write_text(
            json.dumps(self.proof, indent=2) + "\n"
        )
        return self

    def _prepare_default_prefix(self, bubblewrap, probe):
        argv = [
            bubblewrap,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
        ]
        for name in ("/bin", "/lib", "/lib64", "/sbin"):
            path = Path(name)
            if path.is_symlink():
                argv += ["--symlink", os.readlink(path), name]
            elif path.exists():
                argv += ["--ro-bind", name, name]
        argv += [
            "--proc",
            "/proc",
            "--tmpfs",
            "/proc/sys",
            "--remount-ro",
            "/proc/sys",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/etc",
            "--dir",
            "/home",
            "--bind",
            str(self.home_dir),
            str(self.owner_home),
        ]
        for name in ("passwd", "group", "nsswitch.conf", "ld.so.cache", "localtime"):
            path = Path("/etc") / name
            if path.exists():
                argv += ["--ro-bind", str(path), str(path)]
        for path in self.read_only_paths:
            if not path.exists():
                raise IsolationError(f"missing read-only runtime/source path: {path}")
            if path == self.owner_home or path in self.owner_home.parents:
                raise IsolationError("cannot expose the owner home or its ancestors")
            argv += ["--ro-bind", str(path), str(path)]
        argv += [
            "--bind",
            str(self.output_dir),
            str(self.output_dir),
            "--ro-bind",
            str(probe),
            "/semantic-read-only-probe",
            "--chdir",
            "/",
            "--remount-ro",
            "/",
        ]
        self._prefix = argv

    def _sample_processes(self, parent):
        parents = {}
        for path in Path("/proc").iterdir():
            if path.name.isdigit():
                try:
                    fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
                    parents[int(path.name)] = int(fields[1])
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except PermissionError:
                    continue  # Unrelated host processes are not observation subjects.
        selected = {parent}
        while True:
            descendants = {pid for pid, ppid in parents.items() if ppid in selected}
            if descendants <= selected:
                return selected
            selected |= descendants

    def _observe(self, parent, start_ticks, stop, tree):
        libraries = {}
        while not stop.is_set() and Path(f"/proc/{parent}").exists():
            try:
                current = (
                    Path(f"/proc/{parent}/stat")
                    .read_text()
                    .rsplit(") ", 1)[1]
                    .split()[19]
                )
            except (FileNotFoundError, ProcessLookupError):
                return
            if current != start_ticks:
                return
            for pid in self._sample_processes(parent):
                process = Path(f"/proc/{pid}")
                try:
                    tree.observe(pid)
                    executable = os.readlink(process / "exe")
                    identity = (process / "exe").stat()
                    observed_start = process_start(pid)
                    image = (pid, observed_start, identity.st_ino)
                    with self._lock:
                        entry = self._observed.get(image)
                    if entry is None:
                        with (process / "exe").open("rb") as stream:
                            executable_digest = hashlib.file_digest(
                                stream, "sha256"
                            ).hexdigest()
                        entry = dict(
                            pid=pid,
                            launcher_pid=parent,
                            process_start_ticks=observed_start,
                            uid=[
                                int(v)
                                for line in (process / "status")
                                .read_text()
                                .splitlines()
                                if line.startswith("Uid:")
                                for v in line.split()[1:]
                            ],
                            pid_namespace=os.readlink(process / "ns/pid"),
                            executable=executable,
                            executable_inode=identity.st_ino,
                            executable_sha256=executable_digest,
                            network_namespace=os.readlink(process / "ns/net"),
                            libraries=[],
                        )
                        with self._lock:
                            self._observed[image] = entry
                    for line in (process / "maps").read_text().splitlines():
                        fields = line.split(None, 5)
                        if (
                            len(fields) != 6
                            or not fields[5].startswith("/")
                            or ".so" not in fields[5]
                        ):
                            continue
                        path = fields[5]
                        inode = int(fields[4])
                        key = (image, path, inode)
                        if key in libraries:
                            continue
                        mapped = process / "root" / path.lstrip("/")
                        stat = mapped.stat()
                        device = tuple(int(part, 16) for part in fields[3].split(":"))
                        if stat.st_ino != inode or device != (
                            os.major(stat.st_dev),
                            os.minor(stat.st_dev),
                        ):
                            raise IsolationError(
                                f"loaded library inode changed: {path}"
                            )
                        with mapped.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        library = dict(
                            path=path, inode=inode, device=fields[3], sha256=digest
                        )
                        if (
                            process / "exe"
                        ).stat().st_ino != identity.st_ino or process_start(
                            pid
                        ) != observed_start:
                            break  # exec changed the image during this observation; resample it.
                        libraries[key] = library
                        with self._lock:
                            entry["libraries"].append(library)
                except (FileNotFoundError, ProcessLookupError):
                    continue  # A child can exit between procfs observations.
                except (OSError, IsolationError) as error:
                    with self._lock:
                        self._observation_errors.append(f"{pid}: {error}")
                    return
            stop.wait(0.02)

    def runtime_evidence(self, *, require_ort=False):
        with self._lock:
            evidence = json.loads(
                json.dumps(
                    dict(
                        processes=list(self._observed.values()),
                        errors=self._observation_errors,
                    )
                )
            )
        evidence["model_workers"] = [
            p
            for p in evidence["processes"]
            if any(
                "onnxruntime_pybind11_state" in lib["path"]
                or "libonnxruntime.so" in lib["path"]
                for lib in p["libraries"]
            )
        ]
        if require_ort and (
            not evidence["model_workers"]
            or evidence["errors"]
            or any(
                p["network_namespace"] == self._owner_network
                for p in evidence["model_workers"]
            )
        ):
            raise IsolationError(
                "missing or incomplete actual ORT worker mapping evidence"
            )
        return evidence

    def _command(self, argv, env):
        command = list(self._prefix)
        if self._sealed_root is not None:
            if env is not None:
                raise IsolationError(
                    "sealed W environment is fixed; overrides are forbidden"
                )
            return command + ["--", *map(str, argv)]
        for key, value in (WORKER_ENV if env is None else env).items():
            command += ["--setenv", key, str(value)]
        return command + ["--", *map(str, argv)]

    def _spawn(self, argv, *, env, **kwargs):
        process = subprocess.Popen(
            self._command(argv, env), start_new_session=True, **kwargs
        )
        try:
            process._semantic_tree = _ProcessTree(process)
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise
        self._processes.append(process)
        self._invocations.append(
            dict(
                pid=process.pid,
                argv=process.args,
                start_ticks=process._semantic_tree.start,
            )
        )
        stop = threading.Event()
        observer = threading.Thread(
            target=self._observe,
            args=(
                process.pid,
                process._semantic_tree.start,
                stop,
                process._semantic_tree,
            ),
            daemon=True,
        )
        self._observers.append((stop, observer))
        observer.start()
        return process

    def popen(self, argv, *, env=None, **kwargs):
        if self.proof is None or self._closed:
            raise IsolationError("preflight must succeed before launching a worker")
        return self._spawn(argv, env=env, **kwargs)

    def run(self, argv, *, input=None, timeout, env=None, stdout_limit=4 * 1024 * 1024):
        process = self.popen(
            argv,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = bounded_communicate(
            process, input, timeout=timeout, stdout_limit=stdout_limit
        )
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def _cleanup(self):
        if self._closed:
            return
        self._closed = True
        errors = []
        for process in self._processes:
            try:
                stop_process(process)
            except (IsolationError, OSError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
        for stop, observer in self._observers:
            stop.set()
            observer.join(timeout=5)
            if observer.is_alive():
                errors.append("runtime observer did not stop")
        (self.output_dir / "runtime-observations.json").write_text(
            json.dumps(self.runtime_evidence(), indent=2) + "\n"
        )
        telemetry = (
            self.home_dir
            / ".cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db"
        )
        (self.output_dir / "disposable-home.json").write_text(
            json.dumps(
                dict(
                    telemetry_store_created=telemetry.exists(),
                    payload_inspected=False,
                    backing=str(self._backing.path) if self._backing else None,
                    profile="derived-W" if self._sealed_root else "default",
                    initial_identity=(
                        dict(
                            inode=self._backing.identity.st_ino,
                            device=self._backing.identity.st_dev,
                            owner=self._backing.identity.st_uid,
                            mode=stat.S_IMODE(self._backing.identity.st_mode),
                        )
                        if self._backing
                        else None
                    ),
                )
            )
            + "\n"
        )
        for process in self._processes:
            process._semantic_tree.close()
        if self._backing is not None and not errors:
            try:
                self._backing.remove()
            except (IsolationError, OSError) as error:
                errors.append(str(error))
        seal_after = None
        if self._sealed_root is not None:
            try:
                seal_after = check_sealed_oracle(self._sealed_root)
                if seal_after != self._seal_before:
                    errors.append(
                        "sealed artifact metadata changed during derived W use"
                    )
            except (IsolationError, OSError) as error:
                errors.append(str(error))
        (self.output_dir / "cleanup.json").write_text(
            json.dumps(
                dict(
                    all_observed_processes_exited=not errors,
                    errors=errors,
                    launcher_exit_codes=[p.returncode for p in self._processes],
                    invocations=self._invocations,
                    backing_removed=self._backing.removed if self._backing else True,
                    sealed_oracle_after=seal_after,
                )
            )
            + "\n"
        )
        if errors:
            raise IsolationError("isolation cleanup failed: " + "; ".join(errors))

    def __exit__(self, exc_type, exc, traceback):
        self._cleanup()
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout", required=True, type=float)
    parser.add_argument("--require-ort", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0:
        parser.error("a command and positive timeout are required")
    try:
        with SemanticIsolation(
            read_only_paths=args.read_only, output_dir=args.output_dir
        ) as isolation:
            completed = isolation.run(command, timeout=args.timeout)
            if args.require_ort:
                isolation.runtime_evidence(require_ort=True)
        sys.stdout.buffer.write(completed.stdout)
        sys.stderr.buffer.write(completed.stderr)
        return (
            completed.returncode
            if completed.returncode >= 0
            else 128 - completed.returncode
        )
    except (IsolationError, OSError, subprocess.TimeoutExpired) as error:
        print(f"semantic isolation refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
