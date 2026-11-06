"""CAIRN-315: existing Python persistence, not power-loss or Go parity proof.

All state is synthetic and disposable. Children run the real writers; filesystem
wrappers only pause or raise at a named I/O boundary. SIGKILL targets an owned,
unreaped child, never a PID supplied by state or a process group.
"""
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid


HERE = Path(__file__).resolve().parent
SESSION = "11111111-1111-4111-8111-111111111111"
AGENT = "22222222-2222-4222-8222-222222222222"
EXECUTION = "33333333-3333-4333-8333-333333333333"
TURN = "55555555-5555-4555-8555-555555555555"
OLD_TURN = "66666666-6666-4666-8666-666666666666"
WAIT_SECONDS = 5
# Rename publishes in this running kernel. Neither fsync completion nor SIGKILL
# establishes survival of a power loss or a storage-controller failure.
PUBLICATION_POINTS = (
    ("before:file_sync", False), ("after:file_sync", False),
    ("before:replace", False), ("after:replace", True),
    ("before:directory_sync", True), ("after:directory_sync", True),
)


def source(name):
    spec = importlib.util.spec_from_file_location("foundation_characterization_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_source = source("grant_ledger")
coordination = source("coordination")
memory_source = source("memory")
inbox_source = source("inbox_recall")


def native_config(root):
    return dict(state_dir=str(root / "coordination"), binding="fixture", harness="codex")


def native_record():
    return dict(schema="cairn.native-session/1", process=dict(pid=7, start=11, boot="fixture"),
                agent=dict(agent_id=AGENT, execution_id=EXECUTION, native_session_id=SESSION),
                inbox_intent=dict(request_id="44444444-4444-4444-8444-444444444444"),
                unrelated=dict(sequence=[3, 1, 2], large=9007199254740993))


def ledger_paths(root, harness):
    ledger = ledger_source.GrantLedger(root / "memory", SESSION)
    target = ledger.inbox_path if harness == "claude" else ledger.codex_path
    return ledger, target, target.with_suffix(".initialized")


def load_ledger(ledger, harness):
    return ledger.load_inbox() if harness == "claude" else ledger.load_codex(9500)


def add_grant(ledger, harness, label):
    if harness == "claude":
        key = hashlib.sha256(label.encode()).hexdigest()
        ledger["grants"][key] = dict(identity=dict(native_owner="fixture-" + label, boundary="ordinary_prompt"),
                                     limit_bytes=9500, output_bytes=100, native_allowance_bytes=200)
        ledger["active"] = key
    else:
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, "cairn315:" + label))
        ledger["turns"][key] = dict(limit_bytes=9500, emitted_hook_bytes=100,
                                    reserved_native_bytes=200, granted=True, serialized_hook_bytes=100)
        ledger["active_turn_id"] = key
    return key


def save_ledger(ledger, harness, contents):
    if harness == "claude":
        ledger.save_inbox(contents)
    else:
        ledger.save_codex(contents)


def files(directory):
    """Include bytes and inode identity; exclude only the separately tested lock."""
    return {p.name: (p.read_bytes(), p.stat().st_ino) for p in directory.iterdir()
            if p.is_file() and not p.name.endswith(".lock")} if directory.exists() else {}


def observation(kind, **fields):
    if os.environ.get("CAIRN_CHARACTERIZATION_OBSERVATIONS") == "1":
        emit("observation", kind=kind, **fields)


@contextmanager
def bridge_case(root, harness):
    """Real bridge and engine; only the external required-search reply is synthetic."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    config = dict(harness=harness, state_dir=str(root / "coordination"))
    memory = dict(state_dir=str(root / "memory"), context_bytes=9500, repo="fixture",
                  harness=harness, cairn=str(root / "absent-fixture-cairn"),
                  socket=str(root / "absent-api.sock"), token_file=str(root / "absent-token"))
    delivery = "77777777-7777-4777-8777-777777777777"
    prompt = coordination.wake_message(dict(session=dict(agent_id=AGENT, execution_id=EXECUTION),
                                             delivery_id=delivery))
    native = TURN
    if harness == "claude":
        prompt = (f'<channel source="cairn-events" native_session_id="{SESSION}" '
                  f'agent_id="{AGENT}" execution_id="{EXECUTION}" delivery_id="{delivery}">\n' +
                  prompt + "\n</channel>")
        native = "claude-channel:fixture"
    event = dict(session_id=SESSION, cwd=str(root), hook_event_name="UserPromptSubmit",
                 prompt=prompt, prompt_id="fixture", turn_id=TURN)
    state = dict(agent=dict(agent_id=AGENT, execution_id=EXECUTION), workspace=str(root))
    reply = dict(destination=dict(name="hosted", allow_local=False), selected=[])
    with patch.object(memory_source.Memory, "search", return_value=reply) as lookup, \
         patch.object(memory_source.Memory, "call", side_effect=AssertionError("unexpected external call")):
        yield (lambda: inbox_source.deliver(
            memory_source, memory, config, event, dict(native_turn_id=native, event="UserPromptSubmit"),
            state, "fixture inbox control", revalidate=lambda _: None,
            outer_deadline=time.monotonic() + 10)), lookup
    if Path(memory["token_file"]).exists():
        raise AssertionError("fixture must never create a token file")


def emit(stage, **fields):
    print(json.dumps(dict(stage=stage, **fields)), flush=True)


def pause_at(checkpoint):
    emit("checkpoint", name=checkpoint)
    if not select.select([sys.stdin.buffer], [], [], WAIT_SECONDS)[0]:
        raise TimeoutError("fixture parent did not release checkpoint")
    if sys.stdin.buffer.read(1) != b"x":
        raise RuntimeError("fixture checkpoint release missing")


class FilesystemBoundary:
    def __init__(self, targets, checkpoint=None, fail_at=None, *, codex_role="ledger", unlink_target=None):
        self.targets, self.checkpoint, self.fail_at = targets, checkpoint, fail_at
        self.codex_role, self.unlink_target = codex_role, unlink_target
        self.current = "state"
        self.fsync, self.replace, self.unlink = os.fsync, Path.replace, Path.unlink

    def hit(self, when, operation):
        checkpoint = when + ":" + self.current + ":" + operation
        if checkpoint == self.fail_at:
            raise OSError(errno.EIO, "fixture I/O failure")
        if checkpoint == self.checkpoint:
            pause_at(checkpoint)

    def sync(self, fd):
        operation = "directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync"
        if operation == "file_sync":
            # Activation owns a nested writer with no save_fn seam. Inspect only
            # this child's flushed temporary file to label its real I/O boundary.
            contents = json.loads(Path("/proc/self/fd/" + str(fd)).read_text(encoding="utf-8"))
            self.current = ("state" if contents.get("schema") == "cairn.native-session/1" else
                            "ledger" if "grants" in contents else self.codex_role if "turns" in contents else "marker")
        self.hit("before", operation)
        self.fsync(fd)
        self.hit("after", operation)

    def renamed(self, temporary, target):
        self.current = self.targets[Path(target)]
        self.hit("before", "replace")
        result = self.replace(temporary, target)
        self.hit("after", "replace")
        return result

    def unlinked(self, path, *args, **kwargs):
        result = self.unlink(path, *args, **kwargs)
        if path == self.unlink_target:
            self.current = "ledger"
            self.hit("after", "unlink")
        return result

    @contextmanager
    def installed(self):
        with patch.object(os, "fsync", self.sync), \
             patch.object(Path, "replace", lambda a, b: self.renamed(a, b)), \
             patch.object(Path, "unlink", lambda a, *b, **c: self.unlinked(a, *b, **c)):
            yield


def child_work(request):
    root, harness = Path(request["root"]), request["harness"]
    ledger, target, marker = ledger_paths(root, harness)
    native_path = coordination.state_path(native_config(root), SESSION)
    bridge = request["operation"] == "bridge"
    boundary = FilesystemBoundary(
        {ledger.inbox_path: "ledger", ledger.inbox_path.with_suffix(".initialized"): "marker",
         ledger.codex_path: "budget" if bridge else "ledger",
         ledger.codex_path.with_suffix(".initialized"): "marker", native_path: "state"},
        request.get("checkpoint"), codex_role="budget" if bridge else "ledger",
        unlink_target=ledger.inbox_path if bridge else None)
    if "umask" in request:
        os.umask(request["umask"])
    original_flock, reported_busy = fcntl.flock, False

    def observed_flock(lock, flags):
        nonlocal reported_busy
        try:
            original_flock(lock, flags)
        except BlockingIOError:
            if not reported_busy:
                emit("blocked")
                reported_busy = True
            raise
        fd = lock if isinstance(lock, int) else lock.fileno()
        metadata = os.fstat(fd)
        emit("locked", device=metadata.st_dev, inode=metadata.st_ino, mode=stat.S_IMODE(metadata.st_mode))

    with patch.object(fcntl, "flock", observed_flock), boundary.installed():
        if request["operation"] == "native_hold":
            with coordination.session_lock(native_path):
                pause_at("holder")
        elif request["operation"] == "native":
            with coordination.open_native_session(native_config(root), SESSION) as session:
                session.mark_turn_ended()
        elif bridge:
            with bridge_case(root, harness) as (deliver, _):
                deliver()
        elif request["operation"] == "activation":
            enabled = ledger_source.activation_decision(
                dict(state_dir=str(ledger.directory), context_bytes=9500), harness,
                SESSION, TURN, True)
            emit("decision", enabled=enabled)
        else:
            with ledger.locked():
                if request["operation"] == "hold":
                    pause_at("holder")
                else:
                    contents = load_ledger(ledger, harness)
                    add_grant(contents, harness, request.get("label", "next"))
                    save_ledger(ledger, harness, contents)
    emit("done")


def native_frontend(root):
    # Admission/configuration and the production consumer still run. Host process
    # discovery is synthetic, and any attempted service call fails the fixture.
    sys.argv = [str(HERE / "coordination.py"), "hook", "--config", str(root / "config.json")]
    with patch.object(coordination, "owner_process", return_value=native_record()["process"]), \
         patch.object(coordination, "call", side_effect=AssertionError("unexpected external call")):
        return coordination.main()


class OwnedChild:
    def __init__(self, case, request):
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--owned-child",
                                         json.dumps(request)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.pid, self.buffer = self.process.pid, b""
        case.addCleanup(self.close)

    def event(self, stage):
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise AssertionError("owned child did not report " + stage)
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    _, errors = self.process.communicate(timeout=WAIT_SECONDS)
                    raise AssertionError("owned child exited before " + stage + ": " + errors.decode())
                self.buffer += chunk
            line, self.buffer = self.buffer.split(b"\n", 1)
            event = json.loads(line)
            if event["stage"] == stage:
                return event

    def release(self):
        self.process.stdin.write(b"x")
        self.process.stdin.flush()

    def kill(self):
        if type(self.pid) is not int or self.pid <= 1 or self.process.pid != self.pid:
            raise AssertionError("refusing a non-owned or invalid child PID")
        if self.process.poll() is None:
            os.kill(self.pid, signal.SIGKILL)
        self.process.wait(timeout=WAIT_SECONDS)

    def finish(self):
        self.event("done")
        _, errors = self.process.communicate(timeout=WAIT_SECONDS)
        if self.process.returncode != 0 or errors:
            raise AssertionError("owned child failed: " + errors.decode())

    def close(self):
        self.kill()
        self.process.communicate(timeout=WAIT_SECONDS)


class SessionLedgerCharacterizationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="cairn315-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def child(self, operation, harness="claude", **fields):
        return OwnedChild(self, dict(root=str(self.root), harness=harness, operation=operation, **fields))

    def seeded(self, harness, root):
        ledger, target, marker = ledger_paths(root, harness)
        ledger.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        contents = load_ledger(ledger, harness)
        contents["unrelated"] = dict(sequence=[3, 1, 2], large=9007199254740993)
        add_grant(contents, harness, "seed")
        save_ledger(ledger, harness, contents)
        return ledger, target, marker, contents

    def death_residue(self, target, expected, published, *, previous_inode=None, profile="grant", checkpoint=None):
        temporary = [p for p in target.parent.iterdir() if p.name.startswith("tmp")]
        encoded = json.dumps(expected, ensure_ascii=profile == "activation",
                             **({} if profile == "native" else dict(separators=(",", ":"))))
        encoded = (encoded + ("\n" if profile == "native" else "")).encode()
        self.assertEqual(len(temporary), 0 if published else 1)
        self.assertEqual((target if published else temporary[0]).read_bytes(), encoded)
        inode = target.stat().st_ino if target.exists() else None
        if previous_inode is not None:
            if published:
                self.assertNotEqual(inode, previous_inode)
            else:
                self.assertEqual(inode, previous_inode)
        observation("writer_death", test=self._testMethodName, checkpoint=checkpoint,
                    target=target.name, published=published,
                    previous_inode=previous_inode, current_inode=inode,
                    temporary=[dict(name=p.name, inode=p.stat().st_ino,
                                    sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in temporary])

    def test_marker_and_first_grant_writer_death_preserves_bootstrap_states(self):
        for harness in ("claude", "codex"):
            for role in ("marker", "ledger"):
                for checkpoint, published in PUBLICATION_POINTS:
                    with self.subTest(harness=harness, role=role, checkpoint=checkpoint):
                        with tempfile.TemporaryDirectory(dir=self.root) as directory:
                            root = Path(directory)
                            ledger, target, marker = ledger_paths(root, harness)
                            expected = load_ledger(ledger, harness)
                            add_grant(expected, harness, "next")
                            child = OwnedChild(self, dict(root=str(root), harness=harness, operation="grant",
                                                         checkpoint=checkpoint.replace(":", ":" + role + ":", 1)))
                            child.event("checkpoint")
                            child.kill()
                            self.assertEqual(marker.exists(), role == "ledger" or published)
                            self.assertEqual(target.exists(), role == "ledger" and published)
                            marker_value = (dict(schema=ledger_source.INBOX_INIT_SCHEMA, session=SESSION)
                                            if harness == "claude" else
                                            dict(schema=ledger_source.CODEX_LEDGER_SCHEMA, session_id=SESSION))
                            self.death_residue(marker if role == "marker" else target,
                                               marker_value if role == "marker" else expected, published,
                                               checkpoint=checkpoint)
                            if target.exists():
                                self.assertEqual(load_ledger(ledger, harness), expected)
                            elif marker.exists():
                                message = "inbox recall grant ledger missing" if harness == "claude" else "native-turn memory ledger is missing"
                                with self.assertRaisesRegex(ValueError, "^" + message + "$"):
                                    load_ledger(ledger, harness)
                            else:
                                self.assertEqual(load_ledger(ledger, harness)["grants" if harness == "claude" else "turns"], {})
                            with ledger.locked(wait_seconds=0):
                                pass

    def test_existing_grant_writer_death_leaves_previous_or_complete_next_record(self):
        for harness in ("claude", "codex"):
            for checkpoint, published in PUBLICATION_POINTS:
                with self.subTest(harness=harness, checkpoint=checkpoint):
                    with tempfile.TemporaryDirectory(dir=self.root) as directory:
                        root = Path(directory)
                        ledger, target, marker, old = self.seeded(harness, root)
                        before, old_inode = target.read_bytes(), target.stat().st_ino
                        expected = json.loads(json.dumps(old))
                        add_grant(expected, harness, "next")
                        child = OwnedChild(self, dict(root=str(root), harness=harness, operation="grant",
                                                     checkpoint=checkpoint.replace(":", ":ledger:", 1)))
                        child.event("checkpoint")
                        child.kill()
                        self.death_residue(target, expected, published, previous_inode=old_inode, checkpoint=checkpoint)
                        self.assertEqual(load_ledger(ledger, harness), expected if published else old)
                        if not published:
                            self.assertEqual(target.read_bytes(), before)
                        self.assertTrue(marker.exists())

    def test_activation_writer_death_keeps_marker_only_refusal_or_cached_decision(self):
        for harness in ("claude", "codex"):
            for role in ("marker", "ledger"):
                for checkpoint, published in PUBLICATION_POINTS:
                    with self.subTest(harness=harness, role=role, checkpoint=checkpoint):
                        with tempfile.TemporaryDirectory(dir=self.root) as directory:
                            root = Path(directory)
                            ledger, target, marker = ledger_paths(root, harness)
                            child = OwnedChild(self, dict(root=str(root), harness=harness, operation="activation",
                                                         checkpoint=checkpoint.replace(":", ":" + role + ":", 1)))
                            child.event("checkpoint")
                            child.kill()
                            self.assertEqual(marker.exists(), role == "ledger" or published)
                            self.assertEqual(target.exists(), role == "ledger" and published)
                            marker_value = (dict(schema=ledger_source.INBOX_INIT_SCHEMA, session=SESSION)
                                            if harness == "claude" else
                                            dict(schema=ledger_source.CODEX_LEDGER_SCHEMA, session_id=SESSION))
                            expected = load_ledger(ledger_source.GrantLedger(root / "absent", SESSION), harness)
                            if harness == "codex":
                                # Activation's constructor orders turns before
                                # pending; the generic budget loader does not.
                                expected = dict(schema=expected["schema"], session_id=SESSION, active_turn_id=None,
                                                turns={}, pending=expected["pending"])
                            expected["inbox_activation"] = {TURN: True}
                            self.death_residue(marker if role == "marker" else target,
                                               marker_value if role == "marker" else expected, published,
                                               profile="activation", checkpoint=checkpoint)
                            with ledger.locked(wait_seconds=0):
                                pass
                            memory = dict(state_dir=str(ledger.directory), context_bytes=9500)
                            if target.exists():
                                before = target.read_bytes()
                                self.assertTrue(ledger_source.activation_decision(memory, harness, SESSION, TURN, False))
                                self.assertEqual(target.read_bytes(), before)
                                contents = load_ledger(ledger, harness)
                                self.assertEqual(contents["inbox_activation"], {TURN: True})
                                self.assertEqual(contents["grants" if harness == "claude" else "turns"], {})
                            elif marker.exists():
                                message = ("inbox recall grant ledger missing" if harness == "claude" else
                                           "activation cannot reset initialized turn accounting")
                                with self.assertRaisesRegex(ValueError, "^" + message + "$"):
                                    ledger_source.activation_decision(memory, harness, SESSION, TURN, False)
                                self.assertFalse(target.exists())
                            else:
                                self.assertFalse(ledger_source.activation_decision(memory, harness, SESSION, TURN, False))
                                self.assertEqual(load_ledger(ledger, harness)["inbox_activation"], {TURN: False})

    def test_activation_update_death_preserves_old_grants_and_exact_cached_turn(self):
        for harness in ("claude", "codex"):
            for checkpoint, published in PUBLICATION_POINTS:
                with self.subTest(harness=harness, checkpoint=checkpoint):
                    with tempfile.TemporaryDirectory(dir=self.root) as directory:
                        root = Path(directory)
                        ledger, target, marker, _ = self.seeded(harness, root)
                        memory = dict(state_dir=str(ledger.directory), context_bytes=9500)
                        self.assertFalse(ledger_source.activation_decision(memory, harness, SESSION, OLD_TURN, False))
                        old, marker_bytes = load_ledger(ledger, harness), marker.read_bytes()
                        old_inode = target.stat().st_ino
                        expected = json.loads(json.dumps(old))
                        expected["inbox_activation"][TURN] = True
                        child = OwnedChild(self, dict(root=str(root), harness=harness, operation="activation",
                                                     checkpoint=checkpoint.replace(":", ":ledger:", 1)))
                        child.event("checkpoint")
                        child.kill()
                        self.death_residue(target, expected, published, previous_inode=old_inode,
                                           profile="activation", checkpoint=checkpoint)
                        with ledger.locked(wait_seconds=0):
                            pass
                        self.assertEqual(load_ledger(ledger, harness), expected if published else old)
                        self.assertEqual(marker.read_bytes(), marker_bytes)
                        before = target.read_bytes()
                        self.assertFalse(ledger_source.activation_decision(memory, harness, SESSION, OLD_TURN, True))
                        self.assertEqual(target.read_bytes(), before)
                        if published:
                            self.assertTrue(ledger_source.activation_decision(memory, harness, SESSION, TURN, False))
                            self.assertEqual(target.read_bytes(), before)

    def test_native_writer_death_keeps_request_identity_and_releases_lock(self):
        for checkpoint, published in PUBLICATION_POINTS:
            with self.subTest(checkpoint=checkpoint):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    config = native_config(root)
                    target = coordination.state_path(config, SESSION)
                    old = native_record()
                    coordination.write_state(target, old)
                    old_inode = target.stat().st_ino
                    child = OwnedChild(self, dict(root=str(root), harness="codex", operation="native",
                                                 checkpoint=checkpoint.replace(":", ":state:", 1)))
                    child.event("checkpoint")
                    child.kill()
                    next_record = dict(old, inbox_turn_ended=True)
                    self.death_residue(target, next_record, published, previous_inode=old_inode,
                                       profile="native", checkpoint=checkpoint)
                    with coordination.open_native_session(config, SESSION) as session:
                        self.assertEqual(session.record, next_record if published else old)
                        session.mark_turn_ended()
                    once = (target.read_bytes(), target.stat().st_ino)
                    with coordination.open_native_session(config, SESSION) as session:
                        session.mark_turn_ended()
                    self.assertEqual((target.read_bytes(), target.stat().st_ino), once)

    def test_competing_grant_writers_keep_both_updates_on_the_same_inode(self):
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                ledger, target, _, old = self.seeded(harness, self.root)
                first = self.child("grant", harness, label="left", checkpoint="before:ledger:file_sync")
                locked = first.event("locked")
                first.event("checkpoint")
                second = self.child("grant", harness, label="right")
                second.event("blocked")
                self.assertEqual(load_ledger(ledger, harness), old)
                first.release()
                first.finish()
                acquired = second.event("locked")
                second.finish()
                self.assertEqual((locked["device"], locked["inode"]), (acquired["device"], acquired["inode"]))
                contents = load_ledger(ledger, harness)
                expected = json.loads(json.dumps(old))
                add_grant(expected, harness, "left")
                add_grant(expected, harness, "right")
                self.assertEqual(contents, expected)
                grants = "grants" if harness == "claude" else "turns"
                self.assertEqual(list(contents[grants]), list(expected[grants]))

    def test_owned_holder_death_preserves_existing_lock_modes_and_symlink_inode(self):
        for mode, symlinked in ((0o644, False), (0o664, False), (0o664, True)):
            with self.subTest(mode=mode, symlinked=symlinked):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    ledger, target, _ = ledger_paths(root, "claude")
                    ledger.directory.mkdir(mode=0o700)
                    actual = ledger.directory / "existing-lock" if symlinked else ledger.lock_path
                    actual.touch(mode=mode)
                    actual.chmod(mode)
                    if symlinked:
                        ledger.lock_path.symlink_to(actual)
                    holder = OwnedChild(self, dict(root=str(root), harness="claude", operation="hold"))
                    held = holder.event("locked")
                    holder.event("checkpoint")
                    writer = OwnedChild(self, dict(root=str(root), harness="claude", operation="grant"))
                    writer.event("blocked")
                    self.assertFalse(target.exists())
                    holder.kill()
                    acquired = writer.event("locked")
                    writer.finish()
                    metadata = actual.stat()
                    self.assertEqual((held["device"], held["inode"]), (acquired["device"], acquired["inode"]))
                    self.assertEqual((metadata.st_dev, metadata.st_ino), (held["device"], held["inode"]))
                    self.assertEqual(stat.S_IMODE(metadata.st_mode), mode)
                    self.assertEqual(ledger.lock_path.is_symlink(), symlinked)
                    self.assertEqual(len(ledger.load_inbox()["grants"]), 1)

    def test_lock_creation_profiles_and_native_holder_death(self):
        for operation, umask, expected_mode in (("hold", 0o027, 0o640), ("native_hold", 0o027, 0o600),
                                                ("hold", 0o000, 0o666), ("native_hold", 0o000, 0o600)):
            with self.subTest(operation=operation, umask=umask):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    child = OwnedChild(self, dict(root=str(root), harness="codex", operation=operation, umask=umask))
                    locked = child.event("locked")
                    child.event("checkpoint")
                    self.assertEqual(locked["mode"], expected_mode)
                    child.kill()
        for mode, symlinked in ((0o644, False), (0o664, False), (0o664, True)):
            with self.subTest(mode=mode, symlinked=symlinked):
                with tempfile.TemporaryDirectory(dir=self.root) as directory:
                    root = Path(directory)
                    config = native_config(root)
                    target = coordination.state_path(config, SESSION)
                    coordination.write_state(target, native_record())
                    lock_path = target.with_suffix(".lock")
                    actual = target.parent / "existing-lock" if symlinked else lock_path
                    actual.touch(mode=mode)
                    actual.chmod(mode)
                    if symlinked:
                        lock_path.symlink_to(actual)
                    holder = OwnedChild(self, dict(root=str(root), harness="codex", operation="native_hold"))
                    locked = holder.event("locked")
                    holder.event("checkpoint")
                    with self.assertRaises(coordination.CoordinationError) as caught:
                        with coordination.open_native_session(config, SESSION):
                            pass
                    self.assertEqual(str(caught.exception), "SESSION_BUSY: another lifecycle operation is active")
                    holder.kill()
                    with coordination.open_native_session(config, SESSION) as session:
                        self.assertEqual(session.record, native_record())
                        session.mark_turn_ended()
                    metadata = actual.stat()
                    self.assertEqual((metadata.st_dev, metadata.st_ino), (locked["device"], locked["inode"]))
                    self.assertEqual(stat.S_IMODE(metadata.st_mode), mode)
                    self.assertEqual(lock_path.is_symlink(), symlinked)

    def test_short_and_expired_deadlines_keep_the_initial_lock_attempt(self):
        ledger, _, _ = ledger_paths(self.root, "claude")
        ledger.directory.mkdir(mode=0o700)
        holder = self.child("hold")
        holder.event("checkpoint")
        with ledger.lock_path.open("a") as lock:
            started = time.monotonic()
            with self.assertRaises(BlockingIOError):
                ledger_source.acquire_session_lock(lock, started + 0.05)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.04)
            self.assertLess(elapsed, 1.0, "a 50ms deadline must not turn into the full 1.5s wait")
            expired = time.monotonic()
            with patch.object(fcntl, "flock", wraps=fcntl.flock) as attempts:
                with self.assertRaises(BlockingIOError):
                    ledger_source.acquire_session_lock(lock, time.monotonic() - 1)
                self.assertEqual(attempts.call_count, 1)
            self.assertLess(time.monotonic() - expired, 1.0)
        holder.kill()
        with ledger.lock_path.open("a") as lock:
            with patch.object(fcntl, "flock", wraps=fcntl.flock) as attempts:
                ledger_source.acquire_session_lock(lock, time.monotonic() - 1)
                self.assertEqual(attempts.call_count, 1)
            with ledger.lock_path.open("a") as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        holder = self.child("hold")
        holder.event("checkpoint")
        timer = threading.Timer(0.03, holder.release)
        timer.start()
        try:
            with ledger.lock_path.open("a") as lock:
                started = time.monotonic()
                ledger_source.acquire_session_lock(lock, started + 0.5)
                self.assertLess(time.monotonic() - started, 0.5)
        finally:
            timer.join(timeout=1)
        self.assertFalse(timer.is_alive())
        holder.finish()

    def activation_refusal(self, memory, harness, message):
        directory = Path(memory["state_dir"])
        before = files(directory) if directory.is_absolute() else None
        with self.assertRaises(ValueError) as caught:
            ledger_source.activation_decision(memory, harness, SESSION, TURN, True)
        self.assertEqual(str(caught.exception), message)
        if before is not None:
            self.assertEqual(files(directory), before)
        observation("activation_refusal", harness=harness, message=message,
                    files=[] if before is None else list(before))

    def test_activation_directory_and_initial_budget_refusals(self):
        self.activation_refusal(dict(state_dir="relative-fixture"), "codex",
                                "activation ledger directory must be absolute")
        for mode in (0o720, 0o702):
            with self.subTest(mode=mode):
                directory = self.root / ("mode-" + str(mode))
                directory.mkdir(mode=mode)
                directory.chmod(mode)
                self.activation_refusal(dict(state_dir=str(directory)), "codex",
                                        "activation ledger directory is not owner-controlled")
        directory = self.root / "synthetic-uid"
        directory.mkdir(mode=0o700)
        foreign = os.getuid() + 1
        with patch.object(os, "getuid", return_value=foreign):
            self.activation_refusal(dict(state_dir=str(directory)), "codex",
                                    "activation ledger directory is not owner-controlled")
        for budget in (True, 999, 65537, 1000.0):
            with self.subTest(budget=budget):
                directory = self.root / ("invalid-" + repr(budget))
                self.activation_refusal(dict(state_dir=str(directory), context_bytes=budget), "codex",
                                        "invalid initial activation accounting limit")
        for budget in (None, 1000, 9500, 9501, 65536):
            with self.subTest(budget=budget):
                directory = self.root / ("valid-" + repr(budget))
                memory = dict(state_dir=str(directory))
                if budget is not None:
                    memory["context_bytes"] = budget
                self.assertTrue(ledger_source.activation_decision(memory, "codex", SESSION, TURN, True))
                path = directory / (SESSION + ".memory-budget.json")
                self.assertEqual(json.loads(path.read_text())["pending"]["limit_bytes"], 9500 if budget is None else budget)

    def test_activation_identity_decisions_and_ascii_writer(self):
        for harness in ("claude", "codex"):
            for change, message in (
                    ("marker", "inbox recall initialization marker corrupt" if harness == "claude" else
                     "activation cannot reset initialized turn accounting"),
                    ("missing", "inbox recall grant ledger missing" if harness == "claude" else
                     "activation cannot reset initialized turn accounting"),
                    ("schema", "inbox recall grant ledger corrupt" if harness == "claude" else "activation ledger identity differs"),
                    ("session", "inbox recall grant ledger corrupt" if harness == "claude" else "activation ledger identity differs"),
                    ("list", "invalid activation decisions"), ("non-bool", "invalid activation decisions"),
                    ("corrupt-grant", "inbox recall grant ledger corrupt" if harness == "claude" else None)):
                if message is None:
                    continue
                with self.subTest(harness=harness, change=change):
                    with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                        ledger, path, marker, contents = self.seeded(harness, Path(temporary))
                        if change == "marker":
                            marker.write_text("{}")
                        elif change == "missing":
                            path.unlink()
                        else:
                            if change in ("schema", "session"):
                                contents["session_id" if harness == "codex" and change == "session" else change] = "wrong"
                            elif change == "corrupt-grant":
                                contents["grants"][contents["active"]]["output_bytes"] = -1
                            else:
                                contents["inbox_activation"] = [] if change == "list" else {TURN: 1}
                            path.write_text(json.dumps(contents))
                        self.activation_refusal(dict(state_dir=str(ledger.directory)), harness, message)
            with self.subTest(harness=harness, writer="ensure_ascii"):
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    ledger, path, marker, contents = self.seeded(harness, Path(temporary))
                    contents["unicode"] = "日本語\u2028<>&"
                    save_ledger(ledger, harness, contents)
                    self.assertIn("日本語".encode(), path.read_bytes())
                    marker_before = files(ledger.directory)[marker.name]
                    memory = dict(state_dir=str(ledger.directory))
                    self.assertTrue(ledger_source.activation_decision(memory, harness, SESSION, TURN, True))
                    contents["inbox_activation"] = {TURN: True}
                    self.assertEqual(path.read_bytes(), json.dumps(contents, separators=(",", ":")).encode())
                    self.assertEqual(files(ledger.directory)[marker.name], marker_before)
                    before = files(ledger.directory)
                    self.assertTrue(ledger_source.activation_decision(memory, harness, SESSION, TURN, False))
                    self.assertEqual(files(ledger.directory), before)

    def test_inbox_malformed_roots_and_marker_identity_keep_source_refusals(self):
        ledger, target, marker = ledger_paths(self.root, "claude")
        ledger.directory.mkdir(mode=0o700)
        for raw, kind, message in (("[]", AttributeError, "'list' object has no attribute 'get'"),
                                    ("null", AttributeError, "'NoneType' object has no attribute 'get'"),
                                    ("{}", ValueError, "inbox recall grant ledger corrupt"),
                                    ('{"schema":"cairn.inbox-recall-grants/1","session":"wrong","grants":{}}',
                                     ValueError, "inbox recall grant ledger corrupt"),
                                    ('{"schema":"foreign","session":"wrong","grants":{}}', ValueError,
                                     "inbox recall grant ledger corrupt")):
            with self.subTest(raw=raw):
                target.write_text(raw)
                with self.assertRaises(kind) as caught:
                    ledger.load_inbox()
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(target.read_text(), raw)
        target.unlink()
        for contents in (dict(schema=ledger_source.INBOX_INIT_SCHEMA, session="wrong"),
                         dict(schema="foreign", session=SESSION),
                         dict(schema=ledger_source.INBOX_INIT_SCHEMA, session=SESSION, extra=True), {}, [], None):
            raw = json.dumps(contents)
            with self.subTest(marker=raw):
                marker.write_text(raw)
                with self.assertRaisesRegex(ValueError, "^inbox recall initialization marker corrupt$"):
                    ledger.load_inbox()
        marker.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            ledger.load_inbox()

    def test_codex_malformed_roots_and_marker_identity_never_renew_allowance(self):
        ledger, target, marker = ledger_paths(self.root, "codex")
        ledger.directory.mkdir(mode=0o700)
        for raw in ("[]", "null", "{}", '{"schema":"foreign","session_id":"wrong"}'):
            with self.subTest(raw=raw):
                target.write_text(raw)
                with self.assertRaisesRegex(ValueError, "^native-turn memory ledger is corrupt; no allowance renewed$"):
                    ledger.load_codex(9500)
                self.assertEqual(target.read_text(), raw)
        target.unlink()
        for raw in ("{broken", '{}', '[]', 'null',
                    json.dumps(dict(schema=ledger_source.CODEX_LEDGER_SCHEMA, session_id=SESSION, extra=True)),
                    json.dumps(dict(schema="foreign", session_id=SESSION)),
                    '{"schema":"cairn.codex-memory-grants/1","session_id":"wrong"}'):
            with self.subTest(marker=raw):
                marker.write_text(raw)
                with self.assertRaisesRegex(ValueError, "^native-turn memory initialization marker is corrupt$"):
                    ledger.load_codex(9500)

    def test_native_opener_preserves_unchecked_identity_and_root_shapes(self):
        config = native_config(self.root)
        target = coordination.state_path(config, SESSION)
        target.parent.mkdir(mode=0o700)
        for contents in ([], None, dict(schema="foreign", agent=dict(native_session_id="another-session"))):
            with self.subTest(contents=contents):
                target.write_text(json.dumps(contents))
                before = target.read_bytes()
                with coordination.open_native_session(config, SESSION) as session:
                    self.assertEqual(session.record, contents)
                self.assertEqual(target.read_bytes(), before)
        target.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            with coordination.open_native_session(config, SESSION):
                pass

    def test_real_coordination_frontend_keeps_escaping_root_errors_and_unchecked_identity(self):
        config = dict(native_config(self.root), cairn=str(self.root / "absent-fixture-cairn"),
                      socket=str(self.root / "absent-api.sock"), token_file=str(self.root / "absent-token"), repo="fixture")
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        target = coordination.state_path(config, SESSION)
        target.parent.mkdir(mode=0o700)
        wrong = dict(native_record(), schema="foreign", retired=True)
        wrong["agent"]["native_session_id"] = "another-session"
        event = dict(session_id=SESSION, cwd=str(self.root), hook_event_name="SessionEnd")
        for raw, error in (("[]", "AttributeError: 'list' object has no attribute 'get'\n"),
                           ("null", "AttributeError: 'NoneType' object has no attribute 'get'\n"),
                           ("{broken", "Cairn coordination: JSONDecodeError; session presence unavailable\n"),
                           (json.dumps(wrong), None)):
            with self.subTest(raw=raw):
                target.write_text(raw)
                before = files(target.parent)
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--native-frontend", str(self.root)],
                                        input=json.dumps(event).encode(), capture_output=True, timeout=2,
                                        env=dict(PATH=os.defpath, HOME=str(self.root), PYTHONDONTWRITEBYTECODE="1"))
                self.assertEqual(result.returncode, 1 if error else 0, result.stderr)
                self.assertEqual(result.stdout, b"" if error else b"{}\n")
                if error and raw in ("[]", "null"):
                    self.assertTrue(result.stderr.startswith(b"Traceback (most recent call last):\n"), result.stderr)
                    self.assertTrue(result.stderr.endswith(error.encode()), result.stderr)
                else:
                    self.assertEqual(result.stderr, error.encode() if error else b"")
                self.assertEqual(files(target.parent), before)
                observation("native_frontend", input=raw, stdout=result.stdout.decode(),
                            stderr=result.stderr.decode(), exit=result.returncode)

    def test_bridge_marker_only_after_activation_death_refuses_before_lookup(self):
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    root = Path(temporary)
                    ledger, target, marker = ledger_paths(root, harness)
                    child = OwnedChild(self, dict(root=str(root), harness=harness, operation="activation",
                                                 checkpoint="after:marker:replace"))
                    child.event("checkpoint")
                    child.kill()
                    with bridge_case(root, harness) as (deliver, lookup):
                        if harness == "claude":
                            with self.assertRaisesRegex(ValueError, "^inbox recall grant ledger missing$"):
                                deliver()
                            lookup.assert_not_called()
                        else:
                            # The bridge reaches its own pristine inbox write and
                            # required lookup before refusing the missing Codex budget.
                            with self.assertRaisesRegex(memory_source.HookError, "^native-turn memory ledger is missing$"):
                                deliver()
                            self.assertEqual(lookup.call_count, 1)
                    self.assertTrue(marker.exists())
                    self.assertFalse(target.exists())
                    with ledger.locked(wait_seconds=0):
                        pass

    def test_bridge_death_after_unlink_and_codex_budget_split_keeps_anti_reset(self):
        for harness, checkpoint in (("claude", "after:ledger:unlink"),
                                    ("codex", "after:ledger:unlink"),
                                    ("codex", "after:budget:directory_sync")):
            with self.subTest(harness=harness, checkpoint=checkpoint):
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    root = Path(temporary)
                    ledger, _, _ = ledger_paths(root, harness)
                    child = OwnedChild(self, dict(root=str(root), harness=harness, operation="bridge", checkpoint=checkpoint))
                    locked = child.event("locked")
                    child.event("checkpoint")
                    child.kill()
                    inbox = ledger.inbox_path
                    self.assertTrue(inbox.with_suffix(".initialized").exists())
                    self.assertEqual(inbox.exists(), checkpoint != "after:ledger:unlink")
                    if inbox.exists():
                        self.assertEqual(ledger.load_inbox()["grants"], {})
                    before = files(ledger.directory)
                    budget = None
                    if harness == "codex":
                        budget = ledger.load_codex(9500)
                        self.assertTrue(budget["turns"][TURN]["granted"])
                        self.assertEqual(budget["active_turn_id"], TURN)
                    with bridge_case(root, harness) as (deliver, lookup):
                        message = ("inbox recall grant ledger missing" if checkpoint == "after:ledger:unlink" else
                                   "whole required inbox context exceeds delivery budget")
                        with self.assertRaises(ValueError) as caught:
                            deliver()
                        self.assertEqual(str(caught.exception), message)
                        self.assertEqual(lookup.call_count, 0 if checkpoint == "after:ledger:unlink" else 1)
                    self.assertEqual(files(ledger.directory), before)
                    if budget is not None:
                        self.assertEqual(ledger.load_codex(9500), budget)
                    with ledger.locked(wait_seconds=0):
                        metadata = ledger.lock_path.stat()
                        self.assertEqual((metadata.st_dev, metadata.st_ino), (locked["device"], locked["inode"]))
                    self.assertFalse(any(p.name.startswith("tmp") for p in ledger.directory.iterdir()))
                    observation("bridge_split_death", harness=harness, checkpoint=checkpoint,
                                files={name: dict(inode=value[1], sha256=hashlib.sha256(value[0]).hexdigest())
                                       for name, value in before.items()}, next_error=message)

    def test_bridge_changed_identity_under_existing_key_refuses_without_lookup(self):
        for harness in ("claude", "codex"):
            with self.subTest(harness=harness):
                with tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    root = Path(temporary)
                    with bridge_case(root, harness) as (deliver, lookup):
                        self.assertIn("Cairn inbox task data", deliver())
                        self.assertEqual(lookup.call_count, 1)
                    ledger, _, _ = ledger_paths(root, harness)
                    contents = ledger.load_inbox()
                    self.assertEqual(len(contents["grants"]), 1)
                    contents["grants"][contents["active"]]["identity"]["native_owner"] = "changed-owner"
                    ledger.save_inbox(contents)
                    before = files(ledger.directory)
                    with bridge_case(root, harness) as (deliver, lookup):
                        with self.assertRaisesRegex(ValueError, "^inbox recall delivery identity changed$"):
                            deliver()
                        lookup.assert_not_called()
                    self.assertEqual(files(ledger.directory), before)

    def test_io_failure_before_replace_keeps_old_state_but_post_replace_error_keeps_new(self):
        config = native_config(self.root)
        target = coordination.state_path(config, SESSION)
        for failure, published in (("before:state:file_sync", False), ("before:state:directory_sync", True)):
            with self.subTest(failure=failure):
                old = native_record()
                coordination.write_state(target, old)
                next_record = dict(old, inbox_turn_ended=True)
                boundary = FilesystemBoundary({target: "state"}, fail_at=failure)
                with boundary.installed(), self.assertRaises(OSError) as caught:
                    coordination.write_state(target, next_record)
                self.assertEqual(caught.exception.errno, errno.EIO)
                self.assertEqual(json.loads(target.read_text()), next_record if published else old)
                self.assertEqual(list(target.parent.iterdir()), [target])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--owned-child":
        child_work(json.loads(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--native-frontend":
        sys.exit(native_frontend(Path(sys.argv[2])))
    else:
        unittest.main()
