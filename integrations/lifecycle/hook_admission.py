"""One admission decision for every lifecycle hook entry point.

Memory, coordination and the inbox recall binding used to answer "should this
hook run, for which account, in which workspace?" separately, with different
environment guards, marker walks and identity shapes. This module is the single
owner of that decision. It is pure: the filesystem and the environment are
injected, so the whole table of (environment, markers, home, event) -> decision
is testable offline. Callers translate an ``invalid`` verdict into their own
refusal type; the module never raises on host input.

Privacy rule (one rule, documented in docs/native-inbox.md):
  * ``.cairn-no-memory`` or ``.cairn-no-coordination`` in the working directory
    or any of its ancestors excludes coordination and bound inbox recall.
  * ``.cairn-no-memory`` in the working directory, an explicit ``project_path``,
    or any ancestor of either excludes memory and bound inbox recall.
  * ``.cairn-no-coordination`` never silences the independent memory hook.
"""
import os
from pathlib import Path
import re
import uuid


MEMORY_MARKER = ".cairn-no-memory"
COORDINATION_MARKER = ".cairn-no-coordination"
LIFECYCLE_DISABLE_VARIABLES = ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD")
COORDINATION_DISABLE_VARIABLE = "CAIRN_COORDINATION_DISABLED"
WAKE_CONTEXT_VARIABLE = "CAIRN_WAKE_CONTEXT"
HOME_VARIABLES = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
NATIVE_ID_BYTES = 256
SHAPED_ID_CHARS = 128
MODEL_CHARS = 256

# Coordination hook vocabulary: event name -> presence phase, with harness limits.
COORDINATION_PHASES = {
    "SessionStart": "start", "UserPromptSubmit": "busy", "PreInvocation": "busy",
    "TurnStart": "busy", "ToolStart": "busy", "ToolEnd": "busy",
    "Stop": "idle", "TurnEnd": "idle", "SessionEnd": "leave",
    "Interrupt": "interrupted", "ProviderObservation": "provider",
}
COORDINATION_EVENT_HARNESSES = {
    "ToolStart": (("opencode",), "tool capture hooks are OpenCode-only"),
    "ToolEnd": (("opencode",), "tool capture hooks are OpenCode-only"),
    "Interrupt": (("codex",), "interrupt observation is a Codex-only hook"),
}
CUE_EVENT = "PostToolUse"
CUE_HARNESSES = ("claude", "codex", "opencode")
# Memory hook vocabulary: the events the engine handles at all.
MEMORY_EVENTS = ("SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd",
                 "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop")
INBOX_RECALL_EVENTS = ("SessionStart", "UserPromptSubmit")
# Native ownership keys: Claude owns a request by its channel prompt_id; the others by turn_id.
OWNER_FIELDS = {"claude": "prompt_id", "codex": "turn_id", "hermes": "turn_id", "opencode": "turn_id"}

# Hook kinds. ``markers`` are the privacy markers that exclude the kind;
# ``explicit_project`` adds the event's project_path (and its ancestors) to the
# roots that are walked; ``identity`` selects the session identity shape.
KINDS = {
    "memory": dict(markers=(MEMORY_MARKER,), disable=LIFECYCLE_DISABLE_VARIABLES,
                   wake_context_excludes=False, explicit_project=True, identity="shaped"),
    "coordination": dict(markers=(MEMORY_MARKER, COORDINATION_MARKER),
                         disable=LIFECYCLE_DISABLE_VARIABLES, wake_context_excludes=True,
                         explicit_project=False, identity="native"),
    "inbox_recall": dict(markers=(MEMORY_MARKER, COORDINATION_MARKER),
                         disable=LIFECYCLE_DISABLE_VARIABLES + (COORDINATION_DISABLE_VARIABLE,),
                         wake_context_excludes=True, explicit_project=True, identity="shaped"),
}


class FilesystemProbe:
    """The real filesystem; tests substitute a table with the same three methods."""

    def exists(self, path):
        return Path(path).exists()

    def is_dir(self, path):
        return Path(path).is_dir()

    def resolve(self, path):
        return Path(path).resolve()


class TableProbe:
    """An in-memory filesystem for offline decision tables."""

    def __init__(self, directories=(), files=()):
        self.directories = {str(Path(p)) for p in directories}
        self.files = {str(Path(p)) for p in files}

    def exists(self, path):
        return str(Path(path)) in self.files or self.is_dir(path)

    def is_dir(self, path):
        return str(Path(path)) in self.directories

    def resolve(self, path):
        return Path(os.path.normpath(str(Path(path))))


PROBE = FilesystemProbe()


class Admission:
    """The decision one hook invocation received."""

    __slots__ = ("kind", "verdict", "reason", "message", "event", "phase", "native_id", "workspace",
                 "project", "observed_model", "native_turn_id", "home", "exclusions")

    def __init__(self, kind, verdict, reason=None, message=None, **fields):
        self.kind, self.verdict, self.reason, self.message = kind, verdict, reason, message
        for name in self.__slots__[4:]:
            setattr(self, name, fields.get(name))
        self.exclusions = tuple(self.exclusions or ())

    @property
    def run(self):
        return self.verdict == "run"

    def observation(self):
        """The coordination presence observation (the former ``normalize`` result)."""
        return dict(native_id=self.native_id, workspace=self.workspace, project=self.project,
                    observed_model=self.observed_model, phase=self.phase, event=self.event,
                    native_turn_id=self.native_turn_id)


def _skip(kind, reason, **fields):
    return Admission(kind, "skip", reason, None, **fields)


def _invalid(kind, message, **fields):
    return Admission(kind, "invalid", "invalid_host", message, **fields)


def active_home(harness, environ=None):
    """The account home this process runs under, for harnesses with per-account hooks."""
    environ = os.environ if environ is None else environ
    variable = HOME_VARIABLES.get(harness, "CODEX_HOME")
    return Path(environ.get(variable, str(Path.home() / ("." + str(harness)))))


def other_account_home(config, environ=None, probe=PROBE):
    """True when merged configuration layers brought another account's hook here."""
    if not config.get("config_home") or config.get("harness") not in HOME_VARIABLES:
        return False
    return probe.resolve(active_home(config["harness"], environ)) != probe.resolve(config["config_home"])


IDENTITY_MESSAGES = frozenset({"missing or invalid host session identity", "invalid OpenCode session identity",
                               "invalid Hermes session identity", "host session identity must be a UUID"})


def disabled(kind, environ=None, *, wake_accepted=False):
    """The environment reason this kind must not run, or None.

    ``wake_accepted`` (a bool, or a callable consulted only when a guard would
    otherwise skip) lets coordination run inside a fresh wake worker: an
    accepted wake context is itself the authorization, even under the
    lifecycle-child guard. The callable may raise the caller's refusal.
    """
    environ = os.environ if environ is None else environ
    rules = KINDS[kind]
    if kind == "coordination" and environ.get(COORDINATION_DISABLE_VARIABLE) == "1":
        return "coordination_disabled"
    guarded = any(environ.get(name) == "1" for name in rules["disable"])
    waking = bool(rules["wake_context_excludes"] and environ.get(WAKE_CONTEXT_VARIABLE))
    if kind == "coordination" and (guarded or waking):
        if wake_accepted() if callable(wake_accepted) else bool(wake_accepted):
            return None
    if guarded:
        return "disabled_env"
    if waking:
        return "wake_context"
    return None


def guard(kind, config, event, event_name=None, *, environ=None, probe=PROBE, wake_accepted=False):
    """Environment, account-home and (for the tool cue) event admission, without validating the body.

    ``decide`` is guard plus body validation plus the privacy walk; coordination's
    hook composes the three itself so its presence observation stays reusable.
    """
    environ = os.environ if environ is None else environ
    name = event_name or (event.get("hook_event_name") if event else None)
    if kind == "coordination" and environ.get(COORDINATION_DISABLE_VARIABLE) == "1":
        return _skip(kind, "coordination_disabled", event=name)
    if other_account_home(config, environ, probe):
        return _skip(kind, "other_account_home", event=name)
    if kind == "coordination" and name == CUE_EVENT:
        if config.get("harness") not in CUE_HARNESSES:
            return _skip(kind, "unsupported_event", event=name)
        reason = disabled(kind, environ)
        if reason:
            return _skip(kind, reason, event=name)
        return Admission(kind, "run", event=name, phase="cue", native_id=event.get("session_id"))
    reason = disabled(kind, environ, wake_accepted=wake_accepted)
    if reason:
        return _skip(kind, reason, event=name)
    return Admission(kind, "run", event=name)


def privacy_roots(event, *, explicit_project, probe=PROBE):
    roots = []
    names = ("cwd", "project_path") if explicit_project else ("cwd",)
    for name in names:
        value = event.get(name) if event else None
        if value and isinstance(value, str):
            roots.append(probe.resolve(value))
    return roots


def privacy_exclusions(roots, markers, probe=PROBE):
    """Every marker found in the roots or any of their ancestors (one walk for every hook)."""
    found = []
    for root in roots:
        root = Path(root)
        for directory in (root, *root.parents):
            for marker in markers:
                candidate = directory / marker
                if probe.exists(candidate) and str(candidate) not in found:
                    found.append(str(candidate))
    return tuple(found)


def valid_native_session_id(value):
    """Coordination identity: any printable native conversation ID within the API byte limit."""
    return (isinstance(value, str) and bool(value.strip())
            and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)
            and len(value.encode("utf-8")) <= NATIVE_ID_BYTES)


def shaped_identity_error(harness, value):
    """Memory identity: each harness's own session identity shape, or the refusal message."""
    if not isinstance(value, str) or not value or len(value) > SHAPED_ID_CHARS:
        return "missing or invalid host session identity"
    if harness == "opencode":
        return None if re.fullmatch(r"ses_[A-Za-z0-9]+", value) else "invalid OpenCode session identity"
    if harness == "hermes":
        return "invalid Hermes session identity" if any(ord(c) < 32 for c in value) else None
    try:
        uuid.UUID(value)
    except ValueError:
        return "host session identity must be a UUID"
    return None


def observe(config, event, event_name=None, *, probe=PROBE):
    """Validate a coordination hook event into a presence observation; returns an Admission."""
    kind = "coordination"
    harness = config["harness"]
    name = event_name or event.get("hook_event_name")
    if harness == "agy":
        native = event.get("conversationId")
        roots = event.get("workspacePaths")
        cwd = roots[0] if isinstance(roots, list) and roots else None
        model = event.get("modelName", "")
    else:
        native, cwd, model = event.get("session_id"), event.get("cwd"), event.get("model", "")
    if not valid_native_session_id(native):
        return _invalid(kind, "native conversation ID required", event=name)
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not probe.is_dir(cwd):
        return _invalid(kind, "native workspace must be an existing absolute directory", event=name)
    if not isinstance(model, str) or len(model) > MODEL_CHARS:
        return _invalid(kind, "invalid observed model", event=name)
    if name not in COORDINATION_PHASES:
        return _invalid(kind, "unsupported coordination hook event", event=name)
    allowed = COORDINATION_EVENT_HARNESSES.get(name)
    if allowed and harness not in allowed[0]:
        return _invalid(kind, allowed[1], event=name)
    phase = COORDINATION_PHASES[name]
    if harness == "agy" and name == "Stop" and event.get("fullyIdle") is not True:
        phase = "busy"
    workspace = probe.resolve(cwd)
    root = workspace
    for parent in (workspace, *workspace.parents):
        if probe.exists(parent / ".git"):
            root = parent
            break
    owner = ""
    if harness in OWNER_FIELDS:
        owner = event.get(OWNER_FIELDS[harness], "")
        if (not isinstance(owner, str) or len(owner.encode()) > NATIVE_ID_BYTES or "\0" in owner
                or any(ord(c) < 32 for c in owner)):
            return _invalid(kind, "invalid native ownership key", event=name)
        if owner:
            owner = ("claude-channel:" + owner) if harness == "claude" else owner
    return Admission(kind, "run", event=name, phase=phase, native_id=native, workspace=str(workspace),
                     project=root.name or str(workspace), observed_model=model, native_turn_id=owner)


def decide(kind, config, event, event_name=None, *, environ=None, probe=PROBE, wake_accepted=False):
    """Decide one hook invocation for ``kind`` (memory, coordination or inbox_recall)."""
    environ = os.environ if environ is None else environ
    rules = KINDS[kind]
    name = event_name or (event.get("hook_event_name") if event else None)
    admission = guard(kind, config, event, name, environ=environ, probe=probe, wake_accepted=wake_accepted)
    if not admission.run or admission.phase == "cue" or event is None:
        return admission
    if kind == "coordination":
        admission = observe(config, event, name, probe=probe)
        if not admission.run:
            return admission
        admission.exclusions = privacy_exclusions([admission.workspace], rules["markers"], probe)
        if admission.exclusions:
            admission.verdict, admission.reason = "skip", "privacy_marker"
        return admission
    if kind == "inbox_recall":
        if name not in INBOX_RECALL_EVENTS:
            return _skip(kind, "unsupported_event", event=name)
        exclusions = privacy_exclusions(privacy_roots(event, explicit_project=True, probe=probe),
                                        rules["markers"], probe)
        if exclusions:
            return _skip(kind, "privacy_marker", event=name, exclusions=exclusions)
        return Admission(kind, "run", event=name, native_id=event.get("session_id"))
    return _decide_memory(config, event, name, probe)


def _decide_memory(config, event, name, probe):
    kind = "memory"
    harness = config.get("harness")
    error = shaped_identity_error(harness, event.get("session_id"))
    if error:
        return _invalid(kind, error, event=name)
    if name == "Stop" and (harness != "codex" or event.get("stop_hook_active")):
        return _skip(kind, "stop_not_boundary", event=name, native_id=event["session_id"])
    if name not in MEMORY_EVENTS:
        return _skip(kind, "unsupported_event", event=name, native_id=event["session_id"])
    if not isinstance(event.get("cwd"), str) or not probe.is_dir(event["cwd"]):
        return _invalid(kind, "host working directory is unavailable", event=name)
    project = event.get("project_path")
    if project and (not isinstance(project, str) or not Path(project).is_absolute() or not probe.is_dir(project)):
        return _invalid(kind, "explicit project directory is unavailable", event=name)
    exclusions = privacy_exclusions(privacy_roots(event, explicit_project=True, probe=probe),
                                    KINDS[kind]["markers"], probe)
    if exclusions:
        return _skip(kind, "privacy_marker", event=name, native_id=event["session_id"], exclusions=exclusions)
    return Admission(kind, "run", event=name, native_id=event["session_id"], workspace=str(probe.resolve(event["cwd"])))
