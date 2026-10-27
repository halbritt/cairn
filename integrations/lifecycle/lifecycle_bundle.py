"""Stage and load the complete, adjacent Cairn lifecycle implementation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import types


SCHEMA = "cairn.lifecycle-bundle/1"
BINDING_SCHEMA = "cairn.inbox-recall-binding/2"
INSTALLED = False
MANIFEST = "lifecycle-bundle.json"
MEMBER_BYTES = 2 * 1024 * 1024
MODULE_FILES = (
    "lifecycle_bundle.py", "memory.py", "coordination.py", "inbox_recall.py", "inbox_binding.py",
    "grant_ledger.py", "transcript_recovery.py", "unix_peer_transport.py",
    "wake_transport.py", "process_scan.py", "codex_queue.py", "claude_channel.py",
    "opencode_queue.py", "hermes_queue.py", "recall_dispatch.py", "hook_admission.py",
)


def _digest(files):
    raw = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _read(path, *, controlled=False):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MEMBER_BYTES:
            raise ValueError("lifecycle bundle member is not a bounded regular file")
        if controlled and (info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise ValueError("lifecycle bundle member is not owner-controlled")
        raw = source.read(MEMBER_BYTES + 1)
        after = os.fstat(source.fileno())
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_ctime_ns)
        if len(raw) != info.st_size or identity(info) != identity(after):
            raise ValueError("lifecycle bundle member changed during read")
        return raw


def bundle_descriptor(root):
    root = Path(root)
    files = {name: hashlib.sha256(_read(root / name, controlled=True)).hexdigest()
             for name in MODULE_FILES}
    return dict(schema=SCHEMA, sha256=_digest(files), files=files)


def read_manifest(root):
    return json.loads(_read(Path(root) / MANIFEST, controlled=True))


def stage_runtime(source_dir, destination, entrypoints=None):
    """Copy one complete source snapshot; callers own activation and quiescence."""
    source_dir = Path(source_dir)
    sources = {name: _read(source_dir / name) for name in MODULE_FILES}
    if sources["lifecycle_bundle.py"].count(b"INSTALLED = True\n") == 1:
        sources = checked_sources(source_dir, read_manifest(source_dir))
    return stage_sources(sources, destination, entrypoints)


def stage_sources(sources, destination, entrypoints=None):
    """Stage the caller's complete checked byte snapshot without rereading its origin."""
    if (set(sources) != set(MODULE_FILES)
            or any(type(raw) is not bytes or len(raw) > MEMBER_BYTES for raw in sources.values())):
        raise ValueError("incomplete or invalid lifecycle source snapshot")
    sources = dict(sources)
    destination = Path(destination)
    aliases = dict(entrypoints or {})
    if any(name not in MODULE_FILES or Path(alias).name != alias or alias in MODULE_FILES
           for name, alias in aliases.items()):
        raise ValueError("invalid lifecycle entrypoint alias")
    loader = sources["lifecycle_bundle.py"]
    marker = b"INSTALLED = False\n"
    if loader.count(marker) == 1:
        sources["lifecycle_bundle.py"] = loader.replace(marker, b"INSTALLED = True\n", 1)
    elif loader.count(b"INSTALLED = True\n") != 1:
        raise ValueError("unknown lifecycle installation mode")
    sources.update({alias: sources[name] for name, alias in aliases.items()})
    files = {name: hashlib.sha256(sources[name]).hexdigest() for name in MODULE_FILES}
    sources[MANIFEST] = (json.dumps(dict(schema=SCHEMA, sha256=_digest(files), files=files),
                                    sort_keys=True) + "\n").encode()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, raw in sources.items():
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o700)
                output.close()
                temporary.replace(destination / name)
            finally:
                temporary.unlink(missing_ok=True)
    return {name: destination / name for name in sources}


class Bundle:
    """A process-local byte snapshot and module cache, never a global import name."""

    def __init__(self, root, *, sources=None, bootstrap_source=None, entry_path=None, entry_name=None):
        self.root = Path(root).resolve()
        if sources is None and INSTALLED:
            sources = checked_sources(self.root, read_manifest(self.root))
        self.sources = dict(sources) if sources is not None else {
            name: _read(self.root / name) for name in MODULE_FILES}
        if set(self.sources) != set(MODULE_FILES):
            raise ValueError("incomplete lifecycle bundle")
        if bootstrap_source is not None and self.sources["lifecycle_bundle.py"] != bootstrap_source:
            raise ValueError("lifecycle bundle loader changed during startup")
        if INSTALLED and entry_path is not None and _read(Path(entry_path), controlled=True) != self.sources[entry_name]:
            raise ValueError("installed lifecycle entrypoint differs from its bundle")
        self.modules = {}

    def load(self, name, *, entry_path=None):
        filename = name + ".py"
        if filename not in MODULE_FILES:
            raise ImportError("unknown lifecycle bundle member: " + name)
        if name in self.modules:
            return self.modules[name]
        module = types.ModuleType("cairn_lifecycle_" + name)
        module.__file__ = str(entry_path or self.root / filename)
        raw = self.sources[filename]
        module.__dict__.update(_CAIRN_BUNDLE=self, _CAIRN_VALIDATED_SOURCE=raw)
        self.modules[name] = module
        try:
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
        except BaseException:
            self.modules.clear()
            raise
        return module

    def load_pinned(self, descriptor, role):
        """Execute a complete checked snapshot using its own checked loader bytes."""
        path, sources = self.check_pinned(descriptor, role)
        loader = types.ModuleType("cairn_pinned_lifecycle_bundle")
        loader.__file__ = str(path.parent / "lifecycle_bundle.py")
        exec(compile(sources["lifecycle_bundle.py"], loader.__file__, "exec"), loader.__dict__)
        return loader.Bundle(path.parent, sources=sources).load(role, entry_path=path)

    def check_pinned(self, descriptor, role):
        """Validate a role and its complete implementation without executing it."""
        return pinned_sources(descriptor, role)


def checked_sources(root, descriptor):
    """Read a pinned byte snapshot without executing any member."""
    root = Path(root)
    if not isinstance(descriptor, dict):
        raise ValueError("invalid lifecycle bundle descriptor")
    files = descriptor.get("files")
    if (descriptor.get("schema") != SCHEMA or not isinstance(files, dict)
            or set(files) != set(MODULE_FILES) or descriptor.get("sha256") != _digest(files)):
        raise ValueError("invalid lifecycle bundle descriptor")
    sources = {}
    for filename, digest in files.items():
        raw = _read(root / filename, controlled=True)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("pinned lifecycle bundle changed")
        sources[filename] = raw
    return sources


def pinned_sources(descriptor, role):
    if role not in ("memory", "inbox_recall"):
        raise ValueError("unsupported pinned lifecycle role")
    path = Path(descriptor["path"])
    if not path.is_absolute():
        raise ValueError("pinned lifecycle entrypoint must be absolute")
    sources = checked_sources(path.parent, descriptor["bundle"])
    raw = sources[role + ".py"] if path.name == role + ".py" else _read(path, controlled=True)
    if (hashlib.sha256(raw).hexdigest() != descriptor["sha256"]
            or raw != sources[role + ".py"]):
        raise ValueError("pinned lifecycle entrypoint differs from its bundle")
    return path, sources
