"""Write a tiny owner-only summary for the disposable Codex fixture."""
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path


SCHEMA = "cairn.codex-fixture-boundary/1"
OWNER_CLASSES = {"not_observed", "configured_name_match", "none", "not_applicable"}
HOOK_FIELDS = {"entry_seen", "exit_seen", "observation_count", "owner_process_class", "exit_code",
               "stderr_code", "stderr_class", "output_status", "output_code"}
CONTEXT_FIELDS = {"stage", "context_files", "matching_contexts", "result"}
ADMISSION_FIELDS = {"state_read", "agent_registered", "inbox_intent", "active_attempt",
                    "delivery_latch", "pending_query", "eligible_pending"}
READY_FIELDS = {"response_class", "ready_delivery_present", "event_delivery_match", "session_identity_match"}
READY_SCENARIOS = {"notice", "response", "batch_admission", "claimed_before_compact", "auto_in_turn_claimed"}
TURN_JOIN_FIELDS = {"attempt_turn_present", "compact_sessionstart_turn_present", "turn_equal",
                    "coordination_error_code", "source_gate"}
TURN_JOIN_CODES = {"NATIVE_TURN_MISMATCH"}
STAGES = {"not_reached", "batch_context", "auto_context", "notice_context"}
RESULTS = {"not_observed", "matched", "missing", "ambiguous"}


def _initial():
    return dict(schema=SCHEMA,
        user_prompt_submit=dict(entry_seen=False, exit_seen=False,
            observation_count=0, owner_process_class="not_observed", exit_code=None, stderr_code=None,
            stderr_class=None, output_status=None, output_code=None),
        context_lookup=dict(stage="not_reached", context_files=None,
            matching_contexts=None, result="not_observed"),
        post_hook_admission=dict(state_read="not_observed", agent_registered=None,
            inbox_intent=None, active_attempt=None, delivery_latch=None,
            pending_query="not_observed", eligible_pending=None),
        turn_join=dict(attempt_turn_present=None, compact_sessionstart_turn_present=None,
            turn_equal=None, coordination_error_code=None, source_gate="not_observed"))


def _validate_values(section, values):
    allowed = (HOOK_FIELDS if section == "user_prompt_submit" else
               CONTEXT_FIELDS if section == "context_lookup" else
               ADMISSION_FIELDS if section == "post_hook_admission" else
               TURN_JOIN_FIELDS if section == "turn_join" else READY_FIELDS)
    if not isinstance(values, dict) or not set(values) <= allowed:
        raise ValueError("invalid fixture diagnostic fields")
    if section == "user_prompt_submit":
        if values.get("owner_process_class") not in (None, *OWNER_CLASSES):
            raise ValueError("invalid owner process classification")
        for key in ("entry_seen", "exit_seen"):
            if key in values and type(values[key]) is not bool:
                raise ValueError("invalid hook observation flag")
        if "observation_count" in values and (type(values["observation_count"]) is not int or
                not 0 <= values["observation_count"] <= 1000):
            raise ValueError("invalid hook observation count")
        if "exit_code" in values and values["exit_code"] is not None and (
                type(values["exit_code"]) is not int or not 0 <= values["exit_code"] <= 255):
            raise ValueError("invalid hook exit code")
        for key in ("stderr_code", "output_code"):
            if key in values and values[key] is not None and not re.fullmatch(
                    r"[A-Z][A-Z0-9_]{0,63}", values[key]):
                raise ValueError("invalid hook code")
        for key in ("stderr_class", "output_status"):
            if key in values and values[key] is not None and not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{0,63}", values[key]):
                raise ValueError("invalid hook status")
    elif section == "context_lookup":
        if values.get("stage") not in STAGES or values.get("result") not in RESULTS:
            raise ValueError("invalid context diagnostic classification")
        for key in ("context_files", "matching_contexts"):
            value = values.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= 1000):
                raise ValueError("invalid context diagnostic count")
    elif section == "post_hook_admission":
        if values.get("state_read") not in ("observed", "unavailable") or values.get("pending_query") not in (
                "observed", "unavailable"):
            raise ValueError("invalid admission observation status")
        for key in ("agent_registered", "inbox_intent", "active_attempt", "delivery_latch", "eligible_pending"):
            if key in values and values[key] is not None and type(values[key]) is not bool:
                raise ValueError("invalid admission observation flag")
    elif section == "turn_join":
        if values.get("source_gate") not in ("not_observed", "owner_turn_guard", "unresolved"):
            raise ValueError("invalid turn join source gate")
        if values.get("coordination_error_code") not in (None, *TURN_JOIN_CODES):
            raise ValueError("invalid turn join error code")
        for key in ("attempt_turn_present", "compact_sessionstart_turn_present", "turn_equal"):
            if key in values and values[key] is not None and type(values[key]) is not bool:
                raise ValueError("invalid turn join equality flag")
    else:
        if values.get("response_class") not in ("not_observed", "ok", "error", "unavailable"):
            raise ValueError("invalid ready response class")
        for key in ("ready_delivery_present", "event_delivery_match", "session_identity_match"):
            if key in values and values[key] is not None and type(values[key]) is not bool:
                raise ValueError("invalid ready observation flag")


def update(path, section, values, *, increment=()):
    """Merge one fixed metadata section and atomically replace the bounded file."""
    if section not in ("user_prompt_submit", "context_lookup", "post_hook_admission", "turn_join"):
        raise ValueError("invalid fixture diagnostic section")
    _validate_values(section, values)
    if increment and (any(key != "observation_count" for key in increment) or
                      section != "user_prompt_submit"):
        raise ValueError("invalid fixture diagnostic increment")
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = target.parent.stat()
    if target.parent.is_symlink() or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise PermissionError("fixture diagnostic directory must be owner-only")
    lock_path = target.with_name(target.name + ".lock")
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if target.exists():
            if target.is_symlink() or target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077:
                raise PermissionError("fixture diagnostic file must be owner-only")
            current = json.loads(target.read_text(encoding="utf-8"))
            if current.get("schema") != SCHEMA:
                raise ValueError("unsupported fixture diagnostic schema")
        else:
            current = _initial()
        current.setdefault(section, _initial()[section])
        current[section].update(values)
        for key in increment:
            current[section][key] = min(1000, current[section][key] + 1)
        payload = json.dumps(current, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        if len(payload) > 2048:
            raise ValueError("fixture diagnostic exceeds size limit")
        fd, temp_name = tempfile.mkstemp(prefix=".fixture-diagnostic-", dir=target.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
            dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    finally:
        os.close(lock_fd)


def turn_join_observation(fixture):
    """Reduce the auto-compaction receipt to fixed turn-join evidence."""
    if not isinstance(fixture, dict):
        raise ValueError("invalid auto-compaction fixture receipt")
    attempt_turn = fixture.get("attempt_native_turn_id")
    attempt_present = isinstance(attempt_turn, str) and bool(attempt_turn.strip())
    compact_turns = [row.get("turn_id") for row in fixture.get("observed_hook_events", [])
        if isinstance(row, dict) and row.get("event") == "SessionStart" and row.get("source") == "compact"
        and row.get("phase") == "exit"]
    valid_compact_turns = [turn for turn in compact_turns if isinstance(turn, str) and turn.strip()]
    error = (fixture.get("compact_sessionstart_outcomes", {}).get("coordination", {}).get("error_code")
             if isinstance(fixture.get("compact_sessionstart_outcomes"), dict) else None)
    if error not in TURN_JOIN_CODES:
        error = None
    equal = any(attempt_turn == turn for turn in valid_compact_turns) if attempt_present else False
    gate = "owner_turn_guard" if error == "NATIVE_TURN_MISMATCH" and not equal else "unresolved"
    return dict(attempt_turn_present=attempt_present,
        compact_sessionstart_turn_present=bool(valid_compact_turns), turn_equal=equal,
        coordination_error_code=error, source_gate=gate)


def append_ready_observation(path, scenario, phase, values):
    """Append one sanitized, ordered ready-window observation and fsync it."""
    if scenario not in READY_SCENARIOS or phase not in ("before", "after"):
        raise ValueError("invalid ready observation identity")
    _validate_values("ready_observation", values)
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = target.parent.stat()
    if target.parent.is_symlink() or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise PermissionError("ready diagnostic directory must be owner-only")
    lock_fd = os.open(target.with_name(target.name + ".lock"),
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        sequence = 1
        try:
            read_fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            read_fd = None
        if read_fd is not None:
            with os.fdopen(read_fd, "r", encoding="utf-8") as stream:
                if os.fstat(stream.fileno()).st_size > 65_536:
                    raise ValueError("ready diagnostic exceeds size limit")
                rows = [json.loads(line) for line in stream if line.strip()]
            if len(rows) >= 1000:
                raise ValueError("ready diagnostic row limit exceeded")
            if rows:
                last = rows[-1]
                if last.get("schema") != "cairn.codex-ready-window/1" or type(last.get("sequence")) is not int:
                    raise ValueError("ready diagnostic sequence is invalid")
                sequence = last["sequence"] + 1
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_mode & 0o077:
                raise PermissionError("ready diagnostic file must be owner-only")
            payload = json.dumps(dict(schema="cairn.codex-ready-window/1", sequence=sequence,
                scenario=scenario, phase=phase, **values), sort_keys=True, separators=(",", ":")).encode() + b"\n"
            if os.fstat(fd).st_size + len(payload) > 65_536:
                raise ValueError("ready diagnostic exceeds size limit")
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(lock_fd)

