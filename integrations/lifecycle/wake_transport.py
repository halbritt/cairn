"""Uniform WakeTransport adapter seam and registry for per-harness idle wake delivery."""
from pathlib import Path
import importlib.util
import os
import re
import sys
import time

UUID_RE = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def _runtime_bundle():
    if "_CAIRN_BUNDLE" in globals():
        return _CAIRN_BUNDLE
    path = Path(__file__).resolve().with_name("lifecycle_bundle.py")
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError("lifecycle bundle loader is not a bounded regular file")
        raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) != info.st_size:
            raise ValueError("lifecycle bundle loader changed during read")
    spec = importlib.util.spec_from_file_location("cairn_lifecycle_bundle", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module.Bundle(path.parent, bootstrap_source=raw, entry_path=__file__, entry_name="wake_transport.py")


_BUNDLE = _runtime_bundle()

def _attach_event_id(wake_record, ready):
    event_id = ready.get("event_id") if isinstance(ready, dict) else None
    if isinstance(event_id, str) and UUID_RE.fullmatch(event_id):
        wake_record["event_id"] = event_id


class WakeTransport:
    """Base interface for harness idle wake discovery, preparation, submission, and hook binding."""

    name = None
    harness = None
    unavailable_reason = "no native wake transport for this process"

    def can_resume_queued(self, prior, endpoint, agent):
        return False

    def prepare(self, coord, config, state, path, agent, delivery, request_id, ready, endpoint=None):
        return None

    def submit(self, coord, config, path, prepared, wake, text):
        raise NotImplementedError

    def bind_event(self, coord, state, event, joined=False):
        return {}


class CodexQueueWakeTransport(WakeTransport):
    name = "codex-queue"
    harness = "codex"
    unavailable_reason = "no native Codex queue endpoint (explicit Unix listener) for this process"

    def can_resume_queued(self, prior, endpoint, agent):
        return bool(
            endpoint
            and prior.get("transport") == self.name
            and prior.get("status") == "queued"
            and prior.get("queued_submission_id")
            and prior.get("endpoint") == endpoint["endpoint"]
            and prior.get("native_id") == agent["native_session_id"]
        )

    def prepare(self, coord, config, state, path, agent, delivery, request_id, ready, endpoint=None):
        resolved = endpoint if endpoint is not None else coord.codex_queue_endpoint(config, state["process"])
        if not resolved:
            return None
        state.pop("wake_refusal", None)
        coord.note_codex_error_wake(config, state, delivery)
        state["idle_wake"] = dict(
            delivery_id=delivery,
            client_id=delivery,
            request_id=request_id,
            session=coord.session_ref(agent),
            transport=self.name,
            endpoint=resolved["endpoint"],
            owner=resolved["owner"],
            native_id=agent["native_session_id"],
            status="uncertain",
            attempted_at=time.time(),
        )
        _attach_event_id(state["idle_wake"], ready)
        coord.write_state(path, state)
        return dict(wake=state["idle_wake"], process=state["process"])

    def submit(self, coord, config, path, prepared, wake, text):
        try:
            queue = coord.queue_helper()
        except coord.CoordinationError as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            if wake.get("queued_submission_id"):
                return None
            coord.drop_idle_wake(path, wake)
            return None
        queued_id = None
        start_outcome = None
        if wake.get("status") == "queued" and wake.get("queued_submission_id"):
            try:
                start_outcome = queue.start_outcome(
                    wake["endpoint"],
                    prepared["process"],
                    wake["native_id"],
                    wake["queued_submission_id"],
                    owner=wake.get("owner", "process"),
                )
            except queue.QueueUnavailable as exc:
                print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
                start_outcome = "unavailable"
            started = start_outcome == "started"
        else:
            try:
                queued_id, started = queue.enqueue(
                    wake["endpoint"],
                    prepared["process"],
                    wake["native_id"],
                    text,
                    wake["delivery_id"],
                    owner=wake.get("owner", "process"),
                )
            except queue.QueueUnavailable as exc:
                print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
                coord.drop_idle_wake(path, wake)
                return None
            except queue.QueueError as exc:
                raise coord.CoordinationError("WAKE_UNCERTAIN", str(exc)) from exc
        return dict(queued_id=queued_id, started=started, start_outcome=start_outcome)

    def bind_event(self, coord, state, event, joined=False):
        return coord.queued_wake_binding(state, event)


class ClaudeChannelWakeTransport(WakeTransport):
    name = "claude-channel"
    harness = "claude"
    unavailable_reason = "no live channel bridge registry for this process; its cairn-events MCP server is not running"

    def prepare(self, coord, config, state, path, agent, delivery, request_id, ready, endpoint=None):
        channel = endpoint if endpoint is not None else coord.claude_channel_endpoint(config, state["process"])
        if not channel:
            return None
        state.pop("wake_refusal", None)
        state["idle_wake"] = dict(
            delivery_id=delivery,
            request_id=request_id,
            session=coord.session_ref(agent),
            transport=self.name,
            endpoint=channel["socket"],
            bridge=channel["process"],
            parent=channel["parent"],
            native_id=agent["native_session_id"],
            status="uncertain",
            attempted_at=time.time(),
        )
        _attach_event_id(state["idle_wake"], ready)
        coord.write_state(path, state)
        return dict(wake=state["idle_wake"], process=state["process"])

    def submit(self, coord, config, path, prepared, wake, text):
        try:
            channel = coord.channel_helper()
        except coord.CoordinationError as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            coord.drop_idle_wake(path, wake)
            return None
        meta = dict(
            native_session_id=wake["native_id"],
            agent_id=wake["session"]["agent_id"],
            execution_id=wake["session"]["execution_id"],
            delivery_id=wake["delivery_id"],
        )
        try:
            channel.write(wake["endpoint"], wake["bridge"], wake["parent"], text, meta)
        except channel.ChannelUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            coord.drop_idle_wake(path, wake)
            return None
        except channel.ChannelError as exc:
            raise coord.CoordinationError("WAKE_UNCERTAIN", str(exc)) from exc
        return dict(queued_id=None, started=True, start_outcome=None)

    def bind_event(self, coord, state, event, joined=False):
        return coord.channel_wake_binding(state, event, joined)


class OpenCodeQueueWakeTransport(WakeTransport):
    name = "opencode-queue"
    harness = "opencode"
    unavailable_reason = "no verified OpenCode prompt_idle bridge for this process; owner prompts remain eligible"

    def prepare(self, coord, config, state, path, agent, delivery, request_id, ready, endpoint=None):
        opencode = (
            endpoint
            if endpoint is not None
            else coord.opencode_idle_endpoint(config, state["process"], agent["native_session_id"], state)
        )
        if not opencode:
            return None
        state.pop("wake_refusal", None)
        cancel_capable = False
        if config.get("opencode_cancel_enabled") and coord.opencode_capture_available():
            try:
                opencode_queue = _BUNDLE.load("opencode_queue")
            except ImportError:
                pass
            else:
                try:
                    cancel_capable = opencode_queue.supports_cancel(
                        opencode, state["process"], agent["native_session_id"]
                    ) and opencode_queue.supports_tool_capture(
                        opencode, state["process"], agent["native_session_id"]
                    )
                except opencode_queue.QueueUnavailable:
                    pass
        state["idle_wake"] = dict(
            delivery_id=delivery,
            request_id=delivery,
            session=coord.session_ref(agent),
            transport=self.name,
            endpoint=opencode,
            native_id=agent["native_session_id"],
            cancel_capable=cancel_capable,
            status="uncertain",
            attempted_at=time.time(),
        )
        coord.write_state(path, state)
        return dict(wake=state["idle_wake"], process=state["process"])

    def submit(self, coord, config, path, prepared, wake, text):
        try:
            opencode_queue = _BUNDLE.load("opencode_queue")
        except ImportError as exc:
            raise coord.CoordinationError("WAKE_UNAVAILABLE", f"opencode queue client is unavailable: {exc}") from exc
        try:
            queued_id, started = opencode_queue.enqueue(
                wake["endpoint"],
                prepared["process"],
                wake["native_id"],
                text,
                wake["delivery_id"],
                request_id=wake["request_id"],
            )
        except opencode_queue.QueueUnsupported as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            with coord.session_lock(path):
                state = coord.json.loads(path.read_text())
                if state.get("idle_wake") == wake:
                    state.pop("idle_wake")
                    state["opencode_unsupported"] = {
                        key: prepared["process"].get(key) for key in ("pid", "start", "boot")
                    }
                    coord.write_state(path, state)
            return None
        except opencode_queue.QueueUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            coord.drop_idle_wake(path, wake)
            return None
        except opencode_queue.QueueRefused as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            with coord.session_lock(path):
                state = coord.json.loads(path.read_text())
                if state.get("idle_wake") == wake:
                    state["idle_wake"]["status"] = "refused"
                    state["idle_wake"]["refusal"] = str(exc)
                    coord.write_state(path, state)
            return None
        except opencode_queue.QueueAlreadyAdmitted as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            with coord.session_lock(path):
                state = coord.json.loads(path.read_text())
                if state.get("idle_wake") == wake:
                    state.pop("idle_wake")
                    state["opencode_replay"] = dict(delivery_id=wake["delivery_id"], session=wake["session"])
                    coord.write_state(path, state)
            return None
        except opencode_queue.QueueError as exc:
            raise coord.CoordinationError("WAKE_UNCERTAIN", str(exc)) from exc
        return dict(queued_id=queued_id, started=started, start_outcome=None)

    def bind_event(self, coord, state, event, joined=False):
        return coord.opencode_wake_binding(state, event)


class HermesQueueWakeTransport(WakeTransport):
    name = "hermes-queue"
    harness = "hermes"
    unavailable_reason = "no Hermes queue endpoint for this process"

    def prepare(self, coord, config, state, path, agent, delivery, request_id, ready, endpoint=None):
        hermes = endpoint if endpoint is not None else coord.hermes_queue_endpoint(config, state["process"])
        if not hermes:
            return None
        state.pop("wake_refusal", None)
        state["idle_wake"] = dict(
            delivery_id=delivery,
            request_id=request_id,
            session=coord.session_ref(agent),
            transport=self.name,
            endpoint=hermes,
            native_id=agent["native_session_id"],
            status="uncertain",
            attempted_at=time.time(),
        )
        coord.write_state(path, state)
        return dict(wake=state["idle_wake"], process=state["process"])

    def submit(self, coord, config, path, prepared, wake, text):
        try:
            hermes_queue = _BUNDLE.load("hermes_queue")
        except ImportError as exc:
            raise coord.CoordinationError("WAKE_UNAVAILABLE", f"hermes queue client is unavailable: {exc}") from exc
        try:
            queued_id, started = hermes_queue.enqueue(
                wake["endpoint"],
                prepared["process"],
                wake["native_id"],
                text,
                wake["delivery_id"],
                request_id=wake.get("request_id"),
                delivery_id=wake["delivery_id"],
            )
        except hermes_queue.QueueUnavailable as exc:
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
            coord.drop_idle_wake(path, wake)
            return None
        except hermes_queue.QueueError as exc:
            raise coord.CoordinationError("WAKE_UNCERTAIN", str(exc)) from exc
        return dict(queued_id=queued_id, started=started, start_outcome=None)

    def bind_event(self, coord, state, event, joined=False):
        return coord.hermes_wake_binding(state, event)


class TerminalWakeTransport(WakeTransport):
    name = "terminal"
    harness = None

    def prepare_terminal(self, coord, config, state, path, agent, delivery, request_id, cycle):
        environment = coord.herdr_environment(state["process"])
        if environment is None:
            return coord.note_wake_refusal(
                config,
                state,
                delivery,
                "native process has no Herdr host environment",
                "terminal_host_unavailable",
                cycle,
            )
        candidates = coord.herdr_call(config, environment, "agent", "list")["agents"]
        identified = [h for h in candidates if coord.host_identifies_session(config, state, h, environment)]
        matches = [h for h in identified if coord.host_eligible(h)]
        if len(matches) != 1:
            parent = (
                coord.agy_subagent_parent(config, state, path, candidates, environment) if not identified else None
            )
            if parent:
                return coord.note_wake_refusal(
                    config,
                    state,
                    delivery,
                    f"Agy subagent conversation inside {parent['display_name']}'s process (native "
                    f"{parent['native_session_id']}); it runs only when its parent messages it, so it has no wake "
                    "transport",
                    "native_transport_unavailable",
                    cycle,
                )
            reason, condition = coord.terminal_target_diagnosis(identified, matches)
            return coord.note_wake_refusal(config, state, delivery, reason, condition, cycle)
        host = matches[0]
        current = coord.herdr_call(config, environment, "agent", "get", host["pane_id"])["agent"]
        keys = ("pane_id", "terminal_id", "revision", "state_change_seq")
        if any(current.get(k) != host.get(k) for k in keys) or not coord.host_matches(
            config, state, current, environment
        ):
            return None
        if coord.call(config, "session-inbox-ready", coord.session_ref(agent)).get("delivery_id") != delivery:
            return None
        state.pop("wake_refusal", None)
        state["idle_wake"] = dict(
            delivery_id=delivery,
            request_id=request_id,
            session=coord.session_ref(agent),
            target={k: current[k] for k in keys},
            status="uncertain",
            attempted_at=time.time(),
        )
        coord.write_state(path, state)
        return dict(environment=environment, wake=state["idle_wake"], process=state["process"])

    def submit(self, coord, config, path, prepared, wake, text):
        response = coord.herdr_call(config, prepared["environment"], "agent", "prompt", wake["target"]["pane_id"], text)
        confirmed = response.get("agent", {})
        if response.get("type") != "agent_prompted" or any(
            confirmed.get(k) != wake["target"][k] for k in ("pane_id", "terminal_id")
        ):
            raise coord.CoordinationError("WAKE_UNCERTAIN", "host did not confirm prompt submission; no automatic resend")
        return dict(queued_id=None, started=True, start_outcome=None)


CODEX_WAKE_TRANSPORT = CodexQueueWakeTransport()
CLAUDE_WAKE_TRANSPORT = ClaudeChannelWakeTransport()
OPENCODE_WAKE_TRANSPORT = OpenCodeQueueWakeTransport()
HERMES_WAKE_TRANSPORT = HermesQueueWakeTransport()
TERMINAL_WAKE_TRANSPORT = TerminalWakeTransport()

NATIVE_WAKE_TRANSPORTS = (
    CODEX_WAKE_TRANSPORT,
    CLAUDE_WAKE_TRANSPORT,
    OPENCODE_WAKE_TRANSPORT,
    HERMES_WAKE_TRANSPORT,
)
WAKE_TRANSPORTS_BY_NAME = {t.name: t for t in (*NATIVE_WAKE_TRANSPORTS, TERMINAL_WAKE_TRANSPORT)}
WAKE_TRANSPORTS_BY_HARNESS = {t.harness: t for t in NATIVE_WAKE_TRANSPORTS}
NATIVE_WAKE_HARNESSES = tuple(WAKE_TRANSPORTS_BY_HARNESS)


def get_wake_transport(transport_name):
    return WAKE_TRANSPORTS_BY_NAME.get(transport_name, TERMINAL_WAKE_TRANSPORT)


def get_harness_wake_transport(harness):
    return WAKE_TRANSPORTS_BY_HARNESS.get(harness)


def unavailable_wake_reason(harness):
    adapter = get_harness_wake_transport(harness)
    return adapter.unavailable_reason if adapter is not None else "no native wake transport for this process"


def prepare_native_wake(coord, config, state, path, agent, delivery, request_id, ready, codex_endpoint=None):
    for adapter in NATIVE_WAKE_TRANSPORTS:
        pre = codex_endpoint if adapter is CODEX_WAKE_TRANSPORT else None
        prepared = adapter.prepare(coord, config, state, path, agent, delivery, request_id, ready, endpoint=pre)
        if prepared is not None:
            return prepared
    return None


def bind_wake_event(coord, config, state, event, joined=False):
    adapter = get_harness_wake_transport(config.get("harness"))
    if adapter is None:
        return {}
    return adapter.bind_event(coord, state, event, joined=joined)
