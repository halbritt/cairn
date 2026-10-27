"""Codex app-server session: one owner for "the turn is done".

Fixtures and probes drive Codex's app-server over JSON-RPC: newline-delimited
JSON over the process's stdio, or an RPC callable that an installer already
connected. Before this module each fixture carried its own framing, id
matching, lock, deadline, ``turn/completed`` loop and Stop-marker join, and the
2026-10-25 sequencing fixes landed one fixture at a time. This module owns:

- the wire seam (``send``/``receive``/``close``) with two adapters: a real
  app-server process (``AppServerProcess`` + ``StdioWire``) and
  ``FakeAppServer``, which speaks the same JSON-RPC subset in-process so the
  ordering invariants are testable offline;
- request/response matching, the RPC lock, deadlines, transport errors and
  notification routing (``WireCaller``), or delegation to an attached RPC
  callable such as the installer's closure (``RpcCaller``);
- ``Turn.wait_completed``: a turn is done when its ``turn/completed``
  notification arrived *and*, when asked, its Stop-hook marker landed;
- interrupt, interrupt-on-deadline, resume, and the one refusal vocabulary
  (``NATIVE_REFUSAL_CLASSES``) shared with the cairn220 queue trace.

The installed delivery adapter, ``integrations/lifecycle/codex_queue._Rpc``, is
a lifecycle-bundle member and stays separate; ``CodexAppServerSession.attach``
wraps any ``rpc(method, params)`` callable, including that one and the
installer's. Nothing here retains raw refusal text in receipts: refused
messages are redacted before they reach ``str(exc)``.
"""
from __future__ import annotations

import inspect
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from trial_codex_turn_deadline import ActiveTurnDeadline  # noqa: E402

DEFAULT_TIMEOUT = 15.0
MAX_LINE_BYTES = 4 * 1024 * 1024
BUSY_CODE = -32600
BUSY_MESSAGE = "thread already has an active or pending turn"
TERMINAL_FAILURE_METHODS = ("turn/failed", "turn/cancelled", "turn/canceled")

# One refusal vocabulary. ``scripts/fixtures/cairn220_queue_trace/trace_io.py``
# retains the same set for its trace validator; a test keeps them equal.
NATIVE_REFUSAL_CLASSES = frozenset({
    "busy", "submission_not_found", "invalid_request_other", "native_refusal_other",
    "thread_closing", "thread_not_found", "history_already_running", "stale_rollout_path",
    "unloaded_v2_child", "resume_listener_not_running", "resume_listener_channel_closed",
    "thread_no_persisted_rollout", "thread_resume_other",
})
REFUSAL_CONTEXTS = ("queue", "resume")


class AppServerError(RuntimeError):
    """Base for session failures that are not scenario assertions."""


class AppServerTransportError(AppServerError):
    """The wire closed, desynchronised or exceeded its bound."""


class AppServerTimeout(AppServerError, TimeoutError):
    """An RPC, notification wait or session deadline expired."""


class AppServerUnsupported(AppServerError):
    """The attached RPC callable cannot perform the requested operation."""


def redact(message: Any) -> str:
    """Bound and redact refusal text the way the installer's CodexRPCError does."""
    raw = message if isinstance(message, str) else ""
    safe = raw.lower()
    safe = re.sub(r"(?i)(token|authorization|secret|api[_ -]?key)\s*[:=]\s*\S+", r"\1=<redacted>", safe)
    safe = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<uuid>", safe)
    safe = re.sub(r"\b[a-z0-9_-]{32,}\b", "<id>", safe)
    safe = re.sub(r"/(?:[^\s,;]+)", "<path>", safe)
    return re.sub(r"\s+", " ", safe).strip()[:160]


class AppServerRefused(AppServerError):
    """A JSON-RPC error response. ``message`` is retained in-process only."""

    def __init__(self, method: str, error: Any):
        self.method = method
        self.error = error if isinstance(error, dict) else {}
        self.code = self.error.get("code")
        raw = self.error.get("message", "")
        self.message = raw if isinstance(raw, str) else ""
        self.safe_message = redact(self.message)
        self.refusal_class = None
        super().__init__("Codex app-server rejected " + str(method) + ": " + self.safe_message)


# --- refusal classification -------------------------------------------------

def _resume_class(message: Any) -> str:
    if not isinstance(message, str):
        return "thread_resume_other"
    if message.startswith("no rollout found for thread id "):
        return "thread_no_persisted_rollout"
    if (message.startswith("thread ") and " is closing; retry " in message and
            (message.endswith("after the thread is closed") or
             message.endswith("thread/resume after the thread is closed"))):
        return "thread_closing"
    if message.startswith("thread not found: "):
        return "thread_not_found"
    if (message.startswith("cannot resume thread ") and
            message.endswith("with history while it is already running")):
        return "history_already_running"
    if (message.startswith("cannot resume running thread ") and
            " with stale path: requested `" in message and ", active `" in message):
        return "stale_rollout_path"
    if message.startswith("cannot resume an unloaded multi-agent v2 sub-agent through its parent;"):
        return "unloaded_v2_child"
    if (message.startswith("failed to enqueue running thread resume for thread ") and
            message.endswith(": thread listener is not running")):
        return "resume_listener_not_running"
    if (message.startswith("failed to enqueue running thread resume for thread ") and
            message.endswith(": thread listener command channel is closed")):
        return "resume_listener_channel_closed"
    return "thread_resume_other"


def _queue_class(code: Any, message: Any) -> str:
    if code == BUSY_CODE and message == BUSY_MESSAGE:
        return "busy"
    if code == BUSY_CODE and isinstance(message, str) and message.startswith("queued submission not found:"):
        return "submission_not_found"
    if code == BUSY_CODE:
        return "invalid_request_other"
    return "native_refusal_other"


def classify_refusal(code: Any = None, message: Any = None, *, context: str = "queue") -> str:
    """Map a JSON-RPC refusal to one fixed class.

    ``context="queue"`` classifies ``thread/queue/*`` refusals by code (busy,
    submission_not_found, invalid_request_other, native_refusal_other);
    ``context="resume"`` classifies ``thread/resume`` refusals by pinned
    message prefixes (thread_resume_other when none matches). Every value is a
    member of ``NATIVE_REFUSAL_CLASSES``; raw text never leaves this function.
    """
    if context == "resume":
        return _resume_class(message)
    if context == "queue":
        return _queue_class(code, message)
    raise ValueError("unknown refusal context: " + repr(context))


def classify_refusal_exception(exc: BaseException, *, context: str = "queue") -> str:
    """Classify an exception carrying ``code``/``message`` attributes."""
    return classify_refusal(getattr(exc, "code", None), getattr(exc, "message", None), context=context)


# --- Stop-hook marker ---------------------------------------------------------

_MARKER_DETAIL_KEYS = ("exit_code", "output_status", "output_code", "stderr_bytes", "stderr_code")


def read_stop_marker(path: Path | str) -> dict | None:
    """Read a Stop-hook completion marker; None while absent or unreadable."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def wait_stop_marker(path: Path | str, turn_id: str, timeout: float = 30, *,
                     allow_stale: bool = False, poll: float = 0.05) -> dict:
    """Wait for the Stop hook marker of ``turn_id`` to complete with exit 0.

    ``turn/completed`` can arrive before Codex's Stop command hook exits, so a
    turn is only done once this marker landed. With ``allow_stale`` a marker
    that still names another turn is polled past instead of refused.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        completed = read_stop_marker(path)
        if completed is None:
            time.sleep(poll)
            continue
        if completed.get("event") != "Stop" or completed.get("turn_id") != turn_id:
            if allow_stale:
                time.sleep(poll)
                continue
            raise AssertionError("Stop hook marker does not match the awaited turn")
        if completed.get("exit_code") != 0:
            raise AssertionError("Stop hook returned nonzero: " + json.dumps(
                {key: completed.get(key) for key in _MARKER_DETAIL_KEYS}, sort_keys=True))
        return completed
    raise AppServerTimeout("Stop hook did not complete before the fixture deadline")


# --- wire adapters ------------------------------------------------------------

def _turn_id_of(params: dict) -> str | None:
    turn = params.get("turn")
    current = turn.get("id") if isinstance(turn, dict) else None
    return current or params.get("turnId")


class AppServerProcess:
    """One owned app-server process group: spawn, readiness, stderr digest, stop."""

    def __init__(self, process: subprocess.Popen, *, label: str = "Codex app-server",
                 stderr_digest: bool = False):
        self.process = process
        self.label = label
        self.stop_error: str | None = None
        self._stop_lock = threading.Lock()
        self._stopped = False
        self._stderr_hash = sha256()
        self._stderr_count = 0
        self._stderr_thread = None
        if stderr_digest and process.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

    @classmethod
    def spawn(cls, argv: list[str], *, env: dict | None = None, cwd: str | Path | None = None,
              stdio: bool = True, stderr: str = "devnull", text: bool = True,
              label: str = "Codex app-server") -> "AppServerProcess":
        """Start ``argv`` in its own session. ``stderr``: devnull, pipe or digest."""
        if stderr not in ("devnull", "pipe", "digest"):
            raise ValueError("stderr must be devnull, pipe or digest")
        process = subprocess.Popen(
            argv, cwd=None if cwd is None else str(cwd), env=env,
            stdin=subprocess.PIPE if stdio else subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdio else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL if stderr == "devnull" else subprocess.PIPE,
            text=text if stdio else False, bufsize=1 if (stdio and text) else -1,
            start_new_session=True)
        return cls(process, label=label, stderr_digest=(stderr == "digest"))

    def _drain_stderr(self) -> None:
        stream = self.process.stderr
        while True:
            try:
                chunk = stream.read(1024)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            self._stderr_hash.update(chunk)
            self._stderr_count += len(chunk)

    @property
    def stderr_sha256(self) -> str:
        return self._stderr_hash.hexdigest()

    @property
    def stderr_bytes(self) -> int:
        return self._stderr_count

    @property
    def pid(self) -> int:
        return self.process.pid

    def poll(self):
        return self.process.poll()

    def wait_socket(self, path: Path | str, timeout: float = 10, poll: float = 0.02) -> bool:
        """True once ``path`` exists while the process is alive."""
        target = Path(path)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.process.poll() is None and not target.exists():
            time.sleep(poll)
        return self.process.poll() is None and target.exists()

    def _group_exists(self) -> bool:
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def stop(self, *, grace: float = 1.0, wait: float = 1.0) -> None:
        """Terminate the owned process group (TERM, then KILL); idempotent."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            try:
                if self.process.poll() is None:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    deadline = time.monotonic() + grace
                    while time.monotonic() < deadline and self._group_exists():
                        time.sleep(0.02)
                    if self._group_exists():
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    try:
                        self.process.wait(timeout=wait)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        self.process.wait(timeout=wait)
            except Exception as exc:  # noqa: BLE001 - recorded, never raised during cleanup
                self.stop_error = type(exc).__name__
            finally:
                for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass
                if self._stderr_thread is not None:
                    self._stderr_thread.join(timeout=2)


class StdioWire:
    """Newline-delimited JSON over an app-server's stdin/stdout."""

    def __init__(self, process: subprocess.Popen | AppServerProcess, *, jsonrpc: bool = False,
                 max_line_bytes: int = MAX_LINE_BYTES, label: str = "Codex app-server"):
        self.process = process.process if isinstance(process, AppServerProcess) else process
        self.jsonrpc = jsonrpc
        self.max_line_bytes = max_line_bytes
        self.label = label
        self._inbound: queue.Queue = queue.Queue()
        self._closed = False
        if self.process.stdin is None or self.process.stdout is None:
            raise ValueError(label + " must be spawned with stdin and stdout pipes")
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def send(self, message: dict) -> None:
        if self._closed:
            raise AppServerTransportError(self.label + " wire is closed")
        payload = dict(jsonrpc="2.0", **message) if self.jsonrpc else message
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        stream = self.process.stdin
        try:
            stream.write(line if _is_text_stream(stream) else line.encode())
            stream.flush()
        except (OSError, ValueError) as exc:
            raise AppServerTransportError(self.label + " stdin is closed") from exc

    def receive(self, timeout: float) -> dict:
        return self._inbound.get(timeout=timeout)

    def _read(self) -> None:
        stream = self.process.stdout
        try:
            while True:
                line = stream.readline(self.max_line_bytes + 1)
                if not line:
                    return
                if len(line) > self.max_line_bytes:
                    self._inbound.put({"transport_error": self.label + " response exceeded fixture bound"})
                    return
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    self._inbound.put({"transport_error": "invalid JSON"})
                    return  # desynchronised; the installer's reader stops here too
                if not isinstance(value, dict):
                    self._inbound.put({"transport_error": "non-object message"})
                    continue
                self._inbound.put(value)
        except (OSError, ValueError):
            return
        finally:
            self._inbound.put({"transport_error": "app server closed"})

    def close(self) -> None:
        self._closed = True
        self._reader.join(timeout=1)


def _is_text_stream(stream) -> bool:
    return hasattr(stream, "encoding") and stream.encoding is not None


# --- callers: matching over a wire, or delegation to an attached RPC ---------

_CLOSED = {"transport_error": "app server closed"}


class WireCaller:
    """Request/response matching, lock, deadlines and routing over a wire.

    One reader thread routes every inbound message: responses to ``call``,
    notifications to ``next_notification``, server requests (method and id)
    to ``server_requests``. A transport error ends the reader and surfaces in
    both queues, so a waiter on either side fails instead of hanging.
    """

    def __init__(self, wire, *, deadline: float | None = None,
                 default_timeout: float = DEFAULT_TIMEOUT):
        self.wire = wire
        self.deadline = deadline
        self.default_timeout = default_timeout
        self._lock = threading.Lock()
        self._sequence = 0
        self._responses: queue.Queue = queue.Queue()
        self._notifications: queue.Queue = queue.Queue()
        self.server_requests: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._route, daemon=True)
        self._reader.start()

    def _route(self) -> None:
        while True:
            try:
                value = self.wire.receive(timeout=0.5)
            except queue.Empty:
                continue
            except Exception as exc:  # noqa: BLE001 - the wire itself failed
                value = {"transport_error": type(exc).__name__}
            if "transport_error" in value:
                self._responses.put(value)
                self._notifications.put(value)
                return
            if "method" in value:
                (self.server_requests if "id" in value else self._notifications).put(value)
            else:
                self._responses.put(value)

    def remaining(self, default: float | None = None) -> float:
        default = self.default_timeout if default is None else default
        if self.deadline is None:
            return default
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise AppServerTimeout("Codex app-server session exceeded its deadline")
        return min(default, left)

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        wait_limit = self.remaining(timeout)
        if not self._lock.acquire(timeout=wait_limit):
            raise AppServerTimeout("Codex app-server RPC lock timed out")
        try:
            self._sequence += 1
            request_id = self._sequence
            self.wire.send(dict(id=request_id, method=method, params={} if params is None else params))
            deadline = time.monotonic() + wait_limit
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AppServerTimeout("Codex app-server " + method + " RPC timed out")
                left = min(left, self.remaining(left))
                try:
                    value = self._responses.get(timeout=left)
                except queue.Empty:
                    continue
                if "transport_error" in value:
                    self._responses.put(value)  # keep the sentinel for later callers
                    raise AppServerTransportError(str(value["transport_error"]))
                if value.get("id") != request_id:
                    continue  # stale or foreign response
                if "error" in value:
                    raise AppServerRefused(method, value["error"])
                return value.get("result", {})
        finally:
            self._lock.release()

    def notify(self, method: str, params: dict | None = None) -> None:
        message = dict(method=method)
        if params is not None:
            message["params"] = params
        self.wire.send(message)

    def next_notification(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        """Next notification; ``queue.Empty`` on timeout like the installer's."""
        try:
            value = self._notifications.get(timeout=self.remaining(timeout))
        except queue.Empty:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise AppServerTimeout("Codex app-server session exceeded its deadline")
            raise
        if "transport_error" in value:
            self._notifications.put(value)
            raise AppServerTransportError(str(value["transport_error"]))
        return value

    def close(self) -> None:
        try:
            self.wire.close()
        finally:
            self._reader.join(timeout=1)


class RpcCaller:
    """Delegate to an attached ``rpc(method, params)`` callable.

    Understands the installer closure's optional ``_timeout`` keyword and its
    ``next_notification`` / ``arm_turn_deadline`` attributes, and the bundle's
    ``codex_queue._Rpc`` (call only).
    """

    def __init__(self, rpc: Callable[..., Any]):
        self.rpc = rpc
        try:
            self._accepts_timeout = "_timeout" in inspect.signature(rpc).parameters
        except (TypeError, ValueError):
            self._accepts_timeout = False
        self._next = getattr(rpc, "next_notification", None)
        self._arm = getattr(rpc, "arm_turn_deadline", None)

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        params = {} if params is None else params
        if timeout is not None and self._accepts_timeout:
            return self.rpc(method, params, _timeout=timeout)
        return self.rpc(method, params)

    def notify(self, method: str, params: dict | None = None) -> None:
        raise AppServerUnsupported("an attached RPC cannot send client notifications")

    def next_notification(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        if self._next is None:
            raise AppServerUnsupported("attached RPC does not deliver notifications")
        return self._next(timeout=timeout)

    def arm_turn_deadline(self, thread_id: str, turn_id: str, seconds: float):
        if self._arm is None:
            return None
        return self._arm(thread_id, turn_id, seconds)

    def close(self) -> None:
        return None


# --- turns ------------------------------------------------------------------

class Completion:
    """What a wait observed: the terminal notification, marker and evidence."""

    def __init__(self):
        self.notification: dict | None = None
        self.completed = False
        self.terminal_class: str | None = None
        self.stopped_early = False
        self.stop: dict | None = None
        self.observed: list[dict] = []

    @property
    def params(self) -> dict:
        return (self.notification or {}).get("params") or {}

    @property
    def status(self) -> str | None:
        turn = self.params.get("turn")
        return turn.get("status") if isinstance(turn, dict) else None

    def turn_ids(self, method: str) -> set:
        """Turn ids seen for ``method`` during the wait (all threads)."""
        found = set()
        for note in self.observed:
            if note.get("method") == method:
                current = _turn_id_of(note.get("params") or {})
                if current:
                    found.add(current)
        return found

    def summary(self, *, items: bool = False) -> list[dict]:
        """Bounded evidence rows: method, turn id and, optionally, item type."""
        rows = []
        for note in self.observed:
            params = note.get("params") or {}
            row = dict(method=note.get("method"), turn_id=_turn_id_of(params))
            if items:
                item = params.get("item")
                row["item_type"] = item.get("type") if isinstance(item, dict) else None
            rows.append(row)
        return rows


class ThreadHandle:
    def __init__(self, session: "CodexAppServerSession", thread_id: str, result: dict | None = None):
        self.session = session
        self.id = thread_id
        self.result = result or {}

    def turn(self, text: str | None = None, **kwargs) -> "Turn":
        return self.session.turn(self.id, text, **kwargs)


class Turn:
    """One started turn; owns completion, the Stop-marker join and interrupt."""

    def __init__(self, session: "CodexAppServerSession", thread_id: str, turn_id: str,
                 result: dict | None = None, started: dict | None = None):
        self.session = session
        self.thread_id = thread_id
        self.id = turn_id
        self.result = result or {}
        self.started = started

    def _accepts(self, accept, current) -> bool:
        if current is None:
            return False
        if accept is None:
            return current == self.id
        if callable(accept):
            return bool(accept(current))
        return current in accept

    def wait_completed(self, timeout: float, *, stop_marker: Path | str | None = None,
                       stop_timeout: float | None = None, allow_stale_marker: bool = False,
                       accept=None, until: Callable[[], bool] | None = None,
                       drain_until: Callable[[], bool] | None = None,
                       guard: Callable[[], None] | None = None, poll: float = 1.0,
                       stop_on_failure: bool = False,
                       observe: Callable[[dict], None] | None = None) -> Completion:
        """Wait for this turn's ``turn/completed``, then its Stop marker.

        ``accept`` widens which turn ids count as this turn (callable or set).
        ``until`` stops the wait early when true (``stopped_early``).
        ``drain_until`` keeps consuming notifications after completion until it
        is true, so a loopback handler can finish its own bookkeeping.
        ``observe`` is called with every notification as it arrives, for
        callers whose concurrent handlers read live state (seen turn ids).
        ``guard`` runs each iteration and may raise (for example on handler
        failures). ``turn/failed`` and ``turn/cancelled`` are recorded as
        ``terminal_class`` and, unless ``stop_on_failure``, waiting continues,
        matching the fixtures' historical behaviour. Every notification seen is
        retained in ``observed`` for evidence rows.
        """
        deadline = time.monotonic() + timeout
        completion = Completion()
        while True:
            if until is not None and until():
                completion.stopped_early = True
                break
            if completion.completed and (drain_until is None or drain_until()):
                break
            # The guard runs only while still waiting, after the exit checks,
            # matching the fixtures' historical loop order.
            if guard is not None:
                guard()
            left = deadline - time.monotonic()
            if left <= 0:
                break
            try:
                note = self.session.next_notification(timeout=min(poll, max(0.1, left)))
            except queue.Empty:
                continue
            completion.observed.append(note)
            if observe is not None:
                observe(note)
            params = note.get("params") or {}
            if params.get("threadId") not in (None, self.thread_id):
                continue
            method = note.get("method")
            current = _turn_id_of(params)
            if method == "turn/completed" and self._accepts(accept, current):
                completion.notification = note
                completion.completed = True
                completion.terminal_class = "completed"
            elif method in TERMINAL_FAILURE_METHODS and self._accepts(accept, current):
                completion.terminal_class = "failed" if method == "turn/failed" else "cancelled"
                if stop_on_failure:
                    completion.notification = note
                    break
        if completion.completed and stop_marker is not None:
            completion.stop = self.wait_stop_marker(
                stop_marker, timeout if stop_timeout is None else stop_timeout,
                allow_stale=allow_stale_marker)
        return completion

    def wait_stop_marker(self, path: Path | str, timeout: float = 30, *, allow_stale: bool = False) -> dict:
        return wait_stop_marker(path, self.id, timeout, allow_stale=allow_stale)

    def interrupt(self, timeout: float = 2) -> Any:
        return self.session.interrupt(self.thread_id, self.id, timeout=timeout)

    def arm_deadline(self, seconds: float, *, stop_server: Callable[[], None] | None = None):
        """Interrupt this turn and stop the owned server when ``seconds`` elapse."""
        armed = self.session.arm_turn_deadline(self.thread_id, self.id, seconds)
        if armed is not None:
            return armed
        stop = stop_server if stop_server is not None else self.session.stop_owned
        return ActiveTurnDeadline(seconds, lambda: self.interrupt(timeout=2), stop).start()


# --- the session --------------------------------------------------------------

class CodexAppServerSession:
    """A connected app-server: threads, turns, resume, interrupt, notifications."""

    def __init__(self, caller, *, process: AppServerProcess | None = None):
        self._caller = caller
        self.process = process
        self.initialize_result: dict | None = None

    @classmethod
    def start(cls, wire, *, client_name: str, client_version: str = "1",
              client_title: str | None = None, experimental_api: bool = True,
              capabilities: dict | None = None, deadline: float | None = None,
              default_timeout: float = DEFAULT_TIMEOUT, process: AppServerProcess | None = None,
              initialized_params: dict | None = None) -> "CodexAppServerSession":
        """Open a session over ``wire`` and complete the initialize handshake."""
        caller = WireCaller(wire, deadline=deadline, default_timeout=default_timeout)
        session = cls(caller, process=process)
        client = dict(name=client_name, version=client_version)
        if client_title is not None:
            client["title"] = client_title
        if capabilities is None:
            capabilities = dict(experimentalApi=True) if experimental_api else {}
        try:
            session.initialize_result = caller.call("initialize", dict(clientInfo=client, capabilities=capabilities))
            caller.notify("initialized", initialized_params)
        except BaseException:
            session.close()
            raise
        return session

    @classmethod
    def spawn(cls, argv: list[str], *, client_name: str, env: dict | None = None,
              cwd: str | Path | None = None, stderr: str = "devnull", text: bool = True,
              jsonrpc: bool = False, max_line_bytes: int = MAX_LINE_BYTES,
              label: str = "Codex app-server", **start_kwargs) -> "CodexAppServerSession":
        """Spawn ``argv`` as an owned stdio app-server and open a session on it."""
        process = AppServerProcess.spawn(argv, env=env, cwd=cwd, stderr=stderr, text=text, label=label)
        try:
            wire = StdioWire(process, jsonrpc=jsonrpc, max_line_bytes=max_line_bytes, label=label)
            return cls.start(wire, client_name=client_name, process=process, **start_kwargs)
        except BaseException:
            process.stop()
            raise

    @classmethod
    def attach(cls, rpc: Callable[..., Any]) -> "CodexAppServerSession":
        """Wrap an already-connected RPC callable (installer closure, codex_queue._Rpc)."""
        return cls(RpcCaller(rpc))

    # low-level
    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        return self._caller.call(method, params, timeout=timeout)

    def notify(self, method: str, params: dict | None = None) -> None:
        self._caller.notify(method, params)

    def next_notification(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        return self._caller.next_notification(timeout=timeout)

    def drain_notifications(self) -> list[dict]:
        """Every notification already queued, without waiting."""
        drained = []
        while True:
            try:
                drained.append(self._caller.next_notification(timeout=0))
            except (queue.Empty, AppServerUnsupported):
                return drained

    def arm_turn_deadline(self, thread_id: str, turn_id: str, seconds: float):
        arm = getattr(self._caller, "arm_turn_deadline", None)
        return None if arm is None else arm(thread_id, turn_id, seconds)

    # threads and turns
    def thread(self, cwd: str | Path, model: str | None = None, *, approval_policy: str | None = "never",
               sandbox: str | None = None, ephemeral: bool | None = None,
               timeout: float | None = None, **extra) -> ThreadHandle:
        params: dict[str, Any] = dict(cwd=str(cwd))
        if model is not None:
            params["model"] = model
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if sandbox is not None:
            params["sandbox"] = sandbox
        if ephemeral is not None:
            params["ephemeral"] = ephemeral
        params.update(extra)
        result = self.call("thread/start", params, timeout=timeout)
        return ThreadHandle(self, result["thread"]["id"], result)

    def turn(self, thread, text: str | None = None, *, input: list | None = None,
             timeout: float | None = None, **extra) -> Turn:
        thread_id = thread.id if isinstance(thread, ThreadHandle) else thread
        if input is None:
            input = [dict(type="text", text="" if text is None else text, text_elements=[])]
        result = self.call("turn/start", dict(threadId=thread_id, input=input, **extra), timeout=timeout)
        return Turn(self, thread_id, result["turn"]["id"], result)

    def await_turn_started(self, thread, timeout: float, *, poll: float = 1.0) -> Turn:
        """Wait for a ``turn/started`` on ``thread`` (for example after compact)."""
        thread_id = thread.id if isinstance(thread, ThreadHandle) else thread
        deadline = time.monotonic() + timeout
        observed = []
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AppServerTimeout("no turn/started arrived for the thread before the deadline")
            try:
                note = self.next_notification(timeout=min(poll, max(0.1, left)))
            except queue.Empty:
                continue
            observed.append(note)
            params = note.get("params") or {}
            if params.get("threadId") not in (None, thread_id):
                continue
            if note.get("method") == "turn/started" and _turn_id_of(params):
                turn = Turn(self, thread_id, _turn_id_of(params), started=note)
                turn.observed_before_start = observed
                return turn

    def compact(self, thread, *, timeout: float | None = None) -> Any:
        thread_id = thread.id if isinstance(thread, ThreadHandle) else thread
        return self.call("thread/compact/start", dict(threadId=thread_id), timeout=timeout)

    def interrupt(self, thread_id: str, turn_id: str, *, timeout: float = 2) -> Any:
        return self.call("turn/interrupt", dict(threadId=thread_id, turnId=turn_id), timeout=timeout)

    def loaded_threads(self, *, limit: int = 100, timeout: float | None = None) -> list:
        return list(self.call("thread/loaded/list", dict(limit=limit), timeout=timeout).get("data", []))

    def resume(self, thread_id: str, *, timeout: float | None = None) -> Any:
        """``thread/resume``; a refusal is re-raised with ``refusal_class`` set."""
        try:
            return self.call("thread/resume", dict(threadId=thread_id), timeout=timeout)
        except Exception as exc:
            try:
                exc.refusal_class = classify_refusal_exception(exc, context="resume")
            except Exception:  # noqa: BLE001 - immutable exception types
                pass
            raise

    # lifecycle
    def stop_owned(self) -> None:
        if self.process is not None:
            self.process.stop()

    def close(self) -> None:
        try:
            self._caller.close()
        finally:
            self.stop_owned()

    def __enter__(self) -> "CodexAppServerSession":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


# --- the fake adapter ---------------------------------------------------------

class _Refuse(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.error = dict(code=code, message=message)


class FakeAppServer:
    """In-process app-server speaking the fixtures' JSON-RPC subset.

    Scripted per turn: ``script_turn(delay=..., status=..., items=[...],
    started_delay=..., before_completed=callable, after_completed=callable)``
    so tests can place a Stop marker before or after ``turn/completed``. Also
    supports dropped responses (timeouts), server requests, raw injection and
    configurable resume refusals.
    """

    def __init__(self, *, hooks: list | None = None, drop: tuple = (), turn_delay: float = 0.0,
                 started_delay: float = 0.0, turn_status: str = "completed",
                 allow_concurrent_turns: bool = False, jsonrpc_required: bool = False):
        self.hooks = [] if hooks is None else hooks
        self.drop = set(drop)
        self.turn_delay = turn_delay
        self.started_delay = started_delay
        self.turn_status = turn_status
        self.allow_concurrent_turns = allow_concurrent_turns
        self.jsonrpc_required = jsonrpc_required
        self.sent: list[dict] = []
        self.client_notifications: list[dict] = []
        self.threads: dict[str, dict] = {}
        self.turns: dict[str, dict] = {}
        self.resume_refusals: dict[str, dict] = {}
        self.resume_results: dict[str, dict] = {}
        self.mcp_status: list = []
        self.config_writes: list[dict] = []
        self._scripts: list[dict] = []
        self._inbound: queue.Queue = queue.Queue()
        self._timers: list[threading.Timer] = []
        self._lock = threading.Lock()
        self._counter = 0
        self._server_counter = 0
        self.closed = False
        self._handlers = {
            "initialize": self._initialize, "hooks/list": self._hooks_list,
            "config/value/write": self._config_write, "thread/start": self._thread_start,
            "thread/loaded/list": self._loaded_list, "thread/resume": self._resume,
            "thread/archive": self._archive, "turn/start": self._turn_start,
            "turn/interrupt": self._interrupt, "thread/compact/start": self._compact,
            "mcpServerStatus/list": self._mcp_status,
        }

    # wire interface
    def send(self, message: dict) -> None:
        if self.closed:
            raise AppServerTransportError("fake app-server closed")
        self.sent.append(message)
        if self.jsonrpc_required and message.get("jsonrpc") != "2.0":
            raise AppServerTransportError("fake app-server requires jsonrpc framing")
        if "id" not in message:
            self.client_notifications.append(message)
            return
        method = message.get("method")
        if method in self.drop:
            return
        handler = self._handlers.get(method)
        if handler is None:
            self._respond(message["id"], error=dict(code=-32601, message="unknown method: " + str(method)))
            return
        try:
            result = handler(message.get("params") or {})
        except _Refuse as refuse:
            self._respond(message["id"], error=refuse.error)
            return
        self._respond(message["id"], result=result)

    def receive(self, timeout: float) -> dict:
        return self._inbound.get(timeout=timeout)

    def close(self) -> None:
        self.closed = True
        for timer in self._timers:
            timer.cancel()
        self._inbound.put(dict(_CLOSED))

    # scripting
    def script_turn(self, **overrides) -> None:
        """Overrides for the next ``turn/start``: delay, started_delay, status,
        items, before_completed, after_completed, no_completion."""
        self._scripts.append(overrides)

    def inject(self, value: dict) -> None:
        """Place a raw inbound message (stale response, server request, ...)."""
        self._inbound.put(value)

    def emit(self, method: str, params: dict | None = None, *, delay: float = 0.0) -> None:
        self._schedule(delay, lambda: self._inbound.put(dict(method=method, params=params or {})))

    def server_request(self, method: str, params: dict | None = None) -> None:
        # Server-initiated request ids are their own namespace on the real
        # app-server; they must not shift thread/turn numbering.
        with self._lock:
            self._server_counter += 1
            request_id = "server-" + str(self._server_counter)
        self._inbound.put(dict(id=request_id, method=method, params=params or {}))

    # internals
    def _next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return str(self._counter)

    def _respond(self, request_id, *, result=None, error=None) -> None:
        message: dict[str, Any] = dict(id=request_id)
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        self._inbound.put(message)

    def _schedule(self, delay: float, action: Callable[[], None]) -> None:
        if self.closed:
            return
        if delay <= 0:
            action()
            return
        timer = threading.Timer(delay, action)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    def _initialize(self, params: dict) -> dict:
        self.client_info = params.get("clientInfo")
        return dict(userAgent="fake-app-server/1")

    def _hooks_list(self, params: dict) -> dict:
        return dict(data=self.hooks)

    def _config_write(self, params: dict) -> dict:
        self.config_writes.append(params)
        return {}

    def _thread_start(self, params: dict) -> dict:
        thread_id = "thread-" + self._next_id()
        self.threads[thread_id] = dict(params=params, loaded=True, active=None)
        return dict(thread=dict(id=thread_id, path="/fake/rollouts/" + thread_id + ".jsonl"))

    def _loaded_list(self, params: dict) -> dict:
        return dict(data=[thread_id for thread_id, row in self.threads.items() if row["loaded"]])

    def _resume(self, params: dict) -> dict:
        thread_id = params.get("threadId")
        refusal = self.resume_refusals.get(thread_id)
        if refusal is not None:
            raise _Refuse(refusal["code"], refusal["message"])
        if thread_id not in self.threads:
            raise _Refuse(BUSY_CODE, "thread not found: " + str(thread_id))
        return self.resume_results.get(thread_id, dict(thread=dict(id=thread_id)))

    def _archive(self, params: dict) -> dict:
        row = self.threads.get(params.get("threadId"))
        if row is not None:
            row["loaded"] = False
        return {}

    def _mcp_status(self, params: dict) -> dict:
        return dict(data=self.mcp_status)

    def _turn_start(self, params: dict) -> dict:
        thread_id = params.get("threadId")
        row = self.threads.get(thread_id)
        if row is None:
            raise _Refuse(BUSY_CODE, "thread not found: " + str(thread_id))
        if row["active"] is not None and not self.allow_concurrent_turns:
            raise _Refuse(BUSY_CODE, BUSY_MESSAGE)
        return dict(turn=dict(id=self._begin_turn(thread_id, params)))

    def _begin_turn(self, thread_id: str, params: dict) -> str:
        row = self.threads[thread_id]
        script = self._scripts.pop(0) if self._scripts else {}
        turn_id = "turn-" + self._next_id()
        row["active"] = turn_id
        self.turns[turn_id] = dict(thread_id=thread_id, params=params, status=None, timer=None)
        started_delay = script.get("started_delay", self.started_delay)
        self.emit("turn/started", dict(threadId=thread_id, turn=dict(id=turn_id)), delay=started_delay)
        for index, item in enumerate(script.get("items", ())):
            item_id = "item-" + turn_id + "-" + str(index)
            for method in ("item/started", "item/completed"):
                self.emit(method, dict(threadId=thread_id, turnId=turn_id, item=dict(id=item_id, **item)),
                          delay=started_delay)
        if not script.get("no_completion"):
            delay = script.get("delay", self.turn_delay)
            status = script.get("status", self.turn_status)
            action = lambda: self._complete(turn_id, status, script)  # noqa: E731
            if delay <= 0:
                action()
            else:
                timer = threading.Timer(delay, action)
                timer.daemon = True
                self.turns[turn_id]["timer"] = timer
                self._timers.append(timer)
                timer.start()
        return turn_id

    def _complete(self, turn_id: str, status: str, script: dict | None = None) -> None:
        turn = self.turns.get(turn_id)
        if turn is None or turn["status"] is not None or self.closed:
            return
        script = script or {}
        before = script.get("before_completed")
        if before is not None:
            before(turn_id)
        turn["status"] = status
        self.threads[turn["thread_id"]]["active"] = None
        self._inbound.put(dict(method="turn/completed", params=dict(
            threadId=turn["thread_id"], turn=dict(id=turn_id, status=status))))
        after = script.get("after_completed")
        if after is not None:
            after(turn_id)

    def _interrupt(self, params: dict) -> dict:
        turn_id = params.get("turnId")
        turn = self.turns.get(turn_id)
        if turn is None or turn["thread_id"] != params.get("threadId"):
            raise _Refuse(BUSY_CODE, "turn not found: " + str(turn_id))
        if turn["timer"] is not None:
            turn["timer"].cancel()
        self._schedule(0.0, lambda: self._complete(turn_id, "interrupted"))
        return {}

    def _compact(self, params: dict) -> dict:
        thread_id = params.get("threadId")
        if thread_id not in self.threads:
            raise _Refuse(BUSY_CODE, "thread not found: " + str(thread_id))
        script = dict(items=[dict(type="contextCompaction")])
        if self._scripts:
            script.update(self._scripts.pop(0))
        self._scripts.insert(0, script)
        self._begin_turn(thread_id, dict(compact=True))
        return {}
