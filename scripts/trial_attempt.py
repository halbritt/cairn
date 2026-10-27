#!/usr/bin/env python3
"""One attempt of the paired task evaluation (stdlib only).

The module is the deep end of ``trial_task_eval.py agent``: one typed
execution profile read once, one provider seam with a Claude, a Codex and an
inert adapter, and one record builder that serves the completed and the failed
attempt alike.

- ``ExecutionProfile`` is the declaration an attempt runs under: harness,
  model, effort, wording, limits, the execution mode (plain legacy, original
  limits, Codex weekly limits or prospective input), the per-arm recall
  controls, the retained native binary, the owned writer policy and the
  account assignment. ``ExecutionProfile.from_args`` reads the harness CLI
  namespace once; ``profile_fields`` is the pinned document form that
  ``trial_task_eval.py agent --execution-profile`` accepts instead of the
  individual flags.
- A provider adapter owns the provider argv, its binds, its environment and
  the launchable command. ``ClaudeProvider`` and ``CodexProvider`` build the
  real sandboxed command through the harness sandbox; ``InertProvider`` runs a
  local inert actor through the same capture path and never reaches a model.
  Provider selection is fail-closed: the real adapter follows the declared
  harness, and the inert adapter is injected only through an explicit
  ``provider`` argument of the harness entry points, never by environment.
- ``Toolkit`` names the harness collaborators the attempt crosses: workspace
  preparation, corpus loading, grading, stream parsing, the sandbox builder,
  process capture and custody reading. ``trial_task_eval`` supplies them at
  call time so its module-level seams stay replaceable in tests.
- ``attempt_record`` and ``failed_attempt_record`` build the
  ``cairn.task-eval.agent/1`` row for a completed and for a failed attempt
  from typed results, so no field is derived twice.

Nothing here reads or writes an existing Cairn store; stores arrive as the
harness's disposable ``TrialStore`` objects.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import trial_codex_account_selection as codex_account_selection
import trial_codex_weekly_limits as codex_weekly_limits
import trial_legacy_limits as legacy_limits
import trial_legacy_pregrade as legacy_pregrade
import trial_native_copy as native_copy
import trial_original_arms as original_arm_controls
import trial_weekly_accounts as weekly_accounts
import trial_writer_release as writer_release
from trial_attempt_custody import AttemptBaseline
from trial_native_observation import measure
from trial_receipt_explanation import retain_hook_explanations
from trial_source_delivery import bind_origins
from trial_task_arms import checked_file
from trial_task_permissions import settings_permissions

ROOT = Path(__file__).resolve().parents[1]
TRIAL_REPO = "trial:task-eval"
CLAUDE_CONTEXT_BYTES = 9500  # scripts/install-claude-hooks.py
CODEX_CONTEXT_BYTES = 12000  # scripts/install-codex-hooks.py
MEMORY_INSTRUCTION = """# Shared memory

Use the `cairn` MCP tools. Search for relevant prior decisions, preferences and
lessons at the start of substantive work; pull relevant notes using the returned
`pull_arguments` and check them against current source. Saved notes are fallible."""
PROSPECTIVE_MEMORY_INSTRUCTION = MEMORY_INSTRUCTION + """

A note's repository scope names the shared memory collection, independently of
the current working directory. Determine project applicability from the note's
content and the current task. Trial imports have fresh record IDs, version 1,
writer and written_at metadata; these identify trial records, not their original
authorship or date. Neither collection membership nor import metadata establishes
relevance, currentness or authority. Preserve source conditions and uncertainty;
use the supplied trial IDs and versions when citing retrieved evidence."""
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure")
HARNESSES = ("claude", "codex")
MODES = ("plain", "original", "codex_weekly", "prospective")
PROFILE_SCHEMA = "cairn.task-eval.execution-profile/1"
# The agent flags a pinned execution profile may carry (argparse destinations). The
# output directory and the fixture source selection stay on the command line.
PROFILE_FIELDS = (
    "harness", "model", "reasoning_effort", "wording", "max_turns", "timeout", "parallel",
    "seeds", "first_seed", "cases", "distractors", "arms", "memory", "semantic_recall",
    "selector_model", "selector_binary", "selector_auth_file", "semantic_worker", "embedding_worker",
    "paired_plan", "cold_readiness_timeout", "codex_install", "codex_binary", "codex_binary_sha256",
    "codex_auth_file", "codex_auth_sha256", "codex_account_assignment", "codex_account_assignment_sha256",
    "case_materials", "case_materials_sha256", "original_native_path", "original_native_sha256",
    "original_arms", "original_arms_sha256", "original_embedding_identity",
    "original_embedding_identity_sha256", "original_writer_cgroup", "original_writer_controls",
    "original_writer_controls_sha256", "original_limits", "codex_weekly_limits",
)


# ---------------------------------------------------------------- execution profile

@dataclasses.dataclass(frozen=True)
class CodexSelection:
    """The Codex entry point and subscription auth an attempt mounts (never ambient user config)."""
    install: str | None = None
    binary: str | None = None
    binary_sha256: str | None = None
    auth_file: str | None = None
    auth_sha256: str | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionProfile:
    """The declaration one attempt runs under; read once, never re-derived from flags."""
    harness: str = "claude"
    model: str | None = None
    effort: str | None = None
    wording: str = "task"
    max_turns: int | None = None
    timeout: int = 900
    mode: str = "plain"
    semantic_recall: tuple = ()
    selector_model: str | None = None
    selector_binary: str | None = None
    selector_auth_file: str | None = None
    codex: CodexSelection | None = None
    original_native: dict | None = None      # trial_native_copy.retain observation
    original_arms: dict | None = None        # literal per-arm controls (trial_original_arms)
    original_writer: dict | None = None      # dict(delegated, controls, reviewed)
    account_assignment: dict | None = None   # validated cairn.weekly-account-assignment document
    prospective: dict | None = None          # loaded cairn.task-eval.input/1 bundle
    # An explicitly injected provider adapter (tests, inert probes); never selected by environment.
    provider_adapter: object = dataclasses.field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.harness not in HARNESSES:
            raise ValueError("unknown harness " + str(self.harness))
        if self.mode not in MODES:
            raise ValueError("unknown execution mode " + str(self.mode))
        if self.mode == "prospective" and self.prospective is None:
            raise ValueError("prospective execution requires the frozen input")

    @property
    def original(self):
        return self.mode == "original"

    @property
    def codex_weekly(self):
        return self.mode == "codex_weekly"

    @property
    def is_prospective(self):
        return self.mode == "prospective"

    @property
    def weekly(self):
        """Original Claude limits or Codex weekly limits: the captured, custody-checked routes."""
        return self.mode in ("original", "codex_weekly")

    @property
    def memory_instruction(self):
        return PROSPECTIVE_MEMORY_INSTRUCTION if self.is_prospective else MEMORY_INSTRUCTION

    @property
    def limit_checker(self):
        return codex_weekly_limits if self.codex_weekly else legacy_limits

    @classmethod
    def from_args(cls, args, **selected):
        """One reading of the harness namespace plus the documents cmd_agent loaded before any store.

        ``selected`` names prospective, original_native, original_arms, original_writer,
        account_assignment and provider_adapter; absent entries fall back to the
        namespace's retained attributes so direct callers keep their shape.
        """
        get = lambda name, default=None: getattr(args, name, default)  # noqa: E731
        prospective = selected.get("prospective", get("_prospective"))
        if prospective:
            mode = "prospective"
        elif get("original_limits", False):
            mode = "original"
        elif get("codex_weekly_limits", False):
            mode = "codex_weekly"
        else:
            mode = "plain"
        codex = None
        if get("harness", "claude") == "codex":
            codex = CodexSelection(install=get("codex_install"), binary=get("codex_binary"),
                                   binary_sha256=get("codex_binary_sha256"), auth_file=get("codex_auth_file"),
                                   auth_sha256=get("codex_auth_sha256"))
        return cls(harness=get("harness", "claude"), model=get("model"), effort=get("reasoning_effort"),
                   wording=get("wording", "task"), max_turns=get("max_turns"), timeout=get("timeout", 900),
                   mode=mode, semantic_recall=tuple(get("semantic_recall", ()) or ()),
                   selector_model=get("selector_model"), selector_binary=get("selector_binary"),
                   selector_auth_file=get("selector_auth_file"), codex=codex,
                   original_native=None if prospective else selected.get("original_native", get("_original_native")),
                   original_arms=None if prospective else selected.get("original_arms", get("_original_arms")),
                   original_writer=None if prospective else selected.get("original_writer", get("_original_writer")),
                   account_assignment=selected.get("account_assignment", get("_codex_account_assignment")),
                   prospective=prospective, provider_adapter=selected.get("provider_adapter"))

    def with_provider(self, adapter):
        """The same declaration with an explicitly injected provider adapter."""
        return dataclasses.replace(self, provider_adapter=adapter)

    def assigned_account(self, case_id, arm, seed):
        """The pinned ChatGPT account for one Codex weekly attempt, or None without an assignment."""
        if self.codex_weekly and self.account_assignment is not None:
            return weekly_accounts.account_for(self.account_assignment, case_id, arm, seed)
        return None


def profile_fields(document):
    """The agent flag values a pinned ``cairn.task-eval.execution-profile/1`` document declares."""
    if not (isinstance(document, dict) and set(document) == {"schema", "agent"}
            and document["schema"] == PROFILE_SCHEMA and isinstance(document["agent"], dict)):
        raise ValueError("execution_profile_shape")
    unknown = sorted(set(document["agent"]) - set(PROFILE_FIELDS))
    if unknown:
        raise ValueError("execution_profile_unknown_field:" + unknown[0])
    return dict(document["agent"])


def profile_document(fields):
    """The pinned document for selected agent flag values (the inverse of ``profile_fields``)."""
    unknown = sorted(set(fields) - set(PROFILE_FIELDS))
    if unknown:
        raise ValueError("execution_profile_unknown_field:" + unknown[0])
    return dict(schema=PROFILE_SCHEMA, agent={key: fields[key] for key in PROFILE_FIELDS if key in fields})


# ---------------------------------------------------------------- harness toolkit

@dataclasses.dataclass(frozen=True)
class Toolkit:
    """The harness collaborators one attempt crosses; ``trial_task_eval`` supplies them at call time."""
    prepare_workspace: object     # prepare_workspace(case, root)
    load_corpus: object           # load_corpus() -> notes
    grade: object                 # grade(case, ctx) -> graded dict
    parse_stream: object          # parse_stream(text, harness) -> trace
    parse_tool_outputs: object    # parse_tool_outputs(text, harness, require_completed=...)
    sandbox: object               # sandbox_command(root, cwd_name, binds, env, argv, harness, **options)
    capture: object               # trial_native_capture.capture
    read_custody: object          # trial_process_custody.read_custody
    custody_failure: object       # codex_custody_failure(process_custody, writer_record)
    bind_ordinary_claude: object  # trial_task_arms.bind_ordinary_claude
    measure: object = measure     # trial_native_observation.measure (prospective selected input)
    hook_observations: object = None  # hook_observations(state_dir, store); defaults to this module's


# ---------------------------------------------------------------- provider adapters

@dataclasses.dataclass
class ProviderSetup:
    """What a provider contributes to one attempt before its memory arm is wired."""
    args: list
    binds: list = dataclasses.field(default_factory=list)
    env: dict = dataclasses.field(default_factory=dict)
    profile_dir: Path | None = None  # Codex's isolated profile; hooks.json lives there


@dataclasses.dataclass(frozen=True)
class MemoryWiring:
    """The recall hook and MCP server one memory arm exposes to the provider."""
    hook_cmd: str
    context_limit: int
    mcp_server: dict
    memory_instruction: str


def codex_weekly_command(codex_entry, model, effort):
    """Build a subscription-routed Codex command with no ambient user config."""
    return [codex_entry, "exec", "--json", "--ephemeral", "--ignore-user-config",
            "--ignore-rules", "--skip-git-repo-check", "--sandbox", "danger-full-access",
            "-c", 'model_provider="openai"', "-c", 'approval_policy="never"',
            "-c", "model_reasoning_effort=" + json.dumps(effort), "-m", model]


# Inside the sandbox, before the provider: Claude's task-key contract or Codex's
# native-turn handler, plus the owner-only inbox recall binding.
BINDING_CHECK = ["python3", "-c", "import json,runpy; config=json.load(open('/tmp/trial/hookconfig.json')); engine=runpy.run_path('/tmp/trial/hook/memory.py'); assert engine.get('RECALL_TASK_VERSION')==1; engine['recall_task_key'](config); runpy.run_path('/tmp/trial/hook/inbox_recall.py')['validate_binding'](config, require_enabled=True)"]
CODEX_BINDING_CHECK = ["python3", "-c", "import json,runpy; config=json.load(open('/tmp/trial/hookconfig.json')); engine=runpy.run_path('/tmp/trial/hook/memory.py'); assert config.get('harness')=='codex' and 'recall_task_key' not in config; assert engine.get('recall_task_key') is not None and engine['recall_task_key'](config) is None; assert engine.get('bound_codex') is not None and engine['bound_codex'](config); assert callable(engine.get('codex_agent_context')); runpy.run_path('/tmp/trial/hook/inbox_recall.py')['validate_binding'](config, require_enabled=True)"]


def binding_check_command(harness):
    if harness == "claude":
        return BINDING_CHECK
    if harness == "codex":
        return CODEX_BINDING_CHECK
    raise ValueError("unknown harness " + harness)


def prerequisite_command(argv):
    """Give a prerequisite a waiting non-init parent; preserve shell-style signal status."""
    supervisor = ("import subprocess,sys; result=subprocess.run(sys.argv[1:]); "
                  "sys.exit(result.returncode if result.returncode >= 0 else 128-result.returncode)")
    return ["python3", "-c", supervisor, *argv]


def trial_hook_observer_bindings(config_path, state_dir):
    """Mount the observer and its helper modules at their importable sandbox paths."""
    return [
        (ROOT / "scripts/trial_hook_observer.py", "/tmp/trial/hook-observer.py", "ro"),
        (ROOT / "scripts/trial_source_delivery.py", "/tmp/trial/trial_source_delivery.py", "ro"),
        (ROOT / "scripts/trial_observation_storage.py", "/tmp/trial/trial_observation_storage.py", "ro"),
        (config_path, "/tmp/trial/hookconfig.json", "ro"),
        (state_dir, "/tmp/trial/hookstate", "rw"),
    ]


def paired_json_pin(path, sha256):
    """A closed JSON control document pinned by path and SHA-256 (trial_paired_request rules)."""
    import trial_paired_request as paired
    try:
        return paired.read_json_pin(dict(path=path, sha256=sha256))
    except paired.Refusal as error:
        raise ValueError("original_writer_controls_unavailable") from error


class ClaudeProvider:
    """Claude Code under the harness sandbox: the legacy, original-limits and prospective argv shapes."""
    name = harness = "claude"
    SETTINGS_ARGUMENTS = ["--settings", "/tmp/trial/settings.json", "--mcp-config", "/tmp/trial/mcp.json"]

    def harness_version(self, profile, run):
        """The host-side version probe; the retained original copy reports its observed version instead."""
        if profile.original_native is not None:
            return None
        binary = (str(checked_file(profile.prospective["document"]["native"]["binary"]))
                  if profile.is_prospective else "claude")
        return run([binary, "--version"]).stdout.decode().strip()

    def setup(self, attempt):
        profile = attempt.profile
        provider = ["claude", "-p", "--model", profile.model, "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "bypassPermissions", "--no-session-persistence", "--strict-mcp-config",
                    "--max-turns", str(profile.max_turns)]
        env = {}
        if profile.original_native is not None:
            # The retained bytes run at the absolute sandbox target, never an ambient PATH lookup.
            provider = [profile.original_native["observed"]["target"], "-p", "--model", profile.model,
                        "--effort", profile.effort, *provider[4:]]
        if profile.is_prospective:
            provider = ["claude", "-p", "--model", profile.model, "--effort", profile.effort,
                        "--output-format", "stream-json", "--verbose", "--permission-prompts", "none",
                        "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
                        "--input-format", "stream-json", "--replay-user-messages", "--include-hook-events",
                        "--max-turns", str(profile.max_turns)]
            env["ENABLE_TOOL_SEARCH"] = "false"
        return ProviderSetup(args=provider, env=env)

    def memory_wiring(self, attempt, setup, wiring):
        setup.args += [*self.SETTINGS_ARGUMENTS, "--append-system-prompt", wiring.memory_instruction]

    def settings_arguments(self):
        return list(self.SETTINGS_ARGUMENTS)

    def command(self, attempt, binds, env, argv, *, prerequisite=False):
        return attempt.toolkit.sandbox(attempt.root, attempt.case["cwd"], binds, env, argv, self.harness,
                                       **attempt.sandbox_options)


class CodexProvider:
    """Codex exec through the reviewed npm entry point or a pinned binary, with subscription auth only."""
    name = harness = "codex"

    def harness_version(self, profile, run):
        codex = profile.codex or CodexSelection()
        entry = (str(Path(codex.binary).resolve(strict=True)) if codex.binary
                 else str(Path(codex.install) / "codex/bin/codex.js"))
        return run([entry, "--version"]).stdout.decode().strip()

    def setup(self, attempt):
        profile, home = attempt.profile, attempt.home
        codex = profile.codex or CodexSelection()
        account = attempt.assigned_account
        auth_file = codex.auth_file
        if profile.codex_weekly:
            auth_file = account["auth_file"] if account else codex.auth_file
            auth_sha256 = account["auth_sha256"] if account else codex.auth_sha256
            expected_account_id = account["account_id"] if account else None
            codex_account_selection.verify_auth_file(auth_file, auth_sha256, expected_account_id=expected_account_id)
        profile_dir = attempt.base / "codex-home"
        profile_dir.mkdir(mode=0o700)
        binds = [(profile_dir, home / ".codex", "rw"),
                 (Path(auth_file).resolve(strict=True), home / ".codex/auth.json", "ro")]
        env = dict(CODEX_HOME=str(home / ".codex"))
        if codex.binary:
            codex_binary = Path(codex.binary).resolve(strict=True)
            if native_copy.file_sha256(str(codex_binary)) != codex.binary_sha256:
                raise ValueError("codex_binary_mismatch")
            codex_entry = home / ".codex/codex"
            binds.append((codex_binary, codex_entry, "ro"))
            codex_entry = str(codex_entry)
        else:
            install = Path(codex.install).resolve()
            binds.append((install, install, "ro"))
            codex_entry = str(install / "codex/bin/codex.js")
        return ProviderSetup(args=codex_weekly_command(codex_entry, profile.model, profile.effort),
                             binds=binds, env=env, profile_dir=profile_dir)

    def memory_wiring(self, attempt, setup, wiring):
        # Only recall hooks; capture and production inbox hooks are absent.
        hooks = {event: [dict(hooks=[dict(type="command", command=wiring.hook_cmd,
                                          timeout=legacy_limits.hook_timeout_seconds() if attempt.profile.codex_weekly else 13,
                                          additionalContextLimit=min(CODEX_CONTEXT_BYTES, wiring.context_limit))])]
                 for event in ("SessionStart", "UserPromptSubmit")}
        (setup.profile_dir / "hooks.json").write_text(json.dumps(dict(hooks=hooks)))
        server = wiring.mcp_server
        setup.args += ["--dangerously-bypass-hook-trust",
                       "-c", "mcp_servers.cairn.command=" + json.dumps(server["command"]),
                       "-c", "mcp_servers.cairn.args=" + json.dumps(server["args"]),
                       "-c", "developer_instructions=" + json.dumps(wiring.memory_instruction)]

    def settings_arguments(self):
        raise ValueError("prospective settings apply to the Claude provider only")

    def command(self, attempt, binds, env, argv, *, prerequisite=False):
        return attempt.toolkit.sandbox(attempt.root, attempt.case["cwd"], binds, env, argv, self.harness,
                                       **attempt.sandbox_options)


INERT_ACTOR = r'''
import json, sys
from pathlib import Path
hookstate, seconds, answer, harness = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
if hookstate:
    rows = [dict(schema='cairn.task-hook-observation/1', event=event, recall_attempted=False, recall=None, exit_code=0,
                 stdout_bytes=40, stderr_bytes=0, process_seconds=seconds,
                 memory_call_attempts={'search': 1, 'pull': 0, 'pull-evidence': 0, 'history': 0, 'other': 0})
            for event in ('SessionStart', 'UserPromptSubmit')]
    (Path(hookstate) / 'observations.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
if harness == 'codex':
    print(json.dumps(dict(type='item.completed', item=dict(id='inert-1', type='agent_message', text=answer))))
    print(json.dumps(dict(type='turn.completed', usage=dict(input_tokens=0, output_tokens=0))))
else:
    print(json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)]))))
    print(json.dumps(dict(type='result', result=answer, is_error=False, num_turns=1)))
'''


class InertProvider:
    """A local inert actor through the real capture path: no sandbox, no model, no quota.

    It emits the declared harness's stream shape and, when a memory arm binds a
    hook state directory, the hook observations a recall-free attempt records.
    Prerequisite commands become a no-op; the adapter records every launch.
    """
    name = "inert"

    def __init__(self, harness="claude", *, answer="Done.", seconds=0.0, hook_observations=True):
        if harness not in HARNESSES:
            raise ValueError("unknown harness " + str(harness))
        self.harness = harness
        self.answer = answer
        self.seconds = seconds
        self.hook_observations = hook_observations
        self.launched = []

    def harness_version(self, profile, run):
        return "inert"

    def setup(self, attempt):
        return ProviderSetup(args=["inert-actor"])

    def memory_wiring(self, attempt, setup, wiring):
        return None

    def settings_arguments(self):
        return []

    def command(self, attempt, binds, env, argv, *, prerequisite=False):
        self.launched.append(dict(binds=list(binds), env=dict(env), argv=list(argv), prerequisite=prerequisite))
        if prerequisite:
            return [sys.executable, "-c", "pass"]
        hookstate = next((str(source) for source, target, _ in binds if str(target) == "/tmp/trial/hookstate"), "")
        return [sys.executable, "-c", INERT_ACTOR, hookstate if self.hook_observations else "",
                str(self.seconds), self.answer, self.harness]


def provider_for(profile):
    """The real adapter for the declared harness, or the explicitly injected one; never an ambient choice."""
    adapter = profile.provider_adapter
    if adapter is not None:
        if getattr(adapter, "harness", None) != profile.harness:
            raise ValueError("provider adapter does not match the declared harness")
        return adapter
    if profile.harness == "claude":
        return ClaudeProvider()
    if profile.harness == "codex":
        return CodexProvider()
    raise ValueError("unknown harness " + profile.harness)


# ---------------------------------------------------------------- attempt observations

def execution_failure(trace, code):
    if code == 124:
        return "timeout"
    if code:
        return "nonzero_exit"
    if trace.get("is_error"):
        return "provider_error"
    if not trace.get("completed"):
        return "unterminated_stream"
    return None


def codex_process_admission_failure(trace, exit_code):
    """Keep scheduling closed on an opaque failed or truncated Codex process."""
    failure = trace.get("admission_failure")
    if failure is not None:
        return failure
    if exit_code != 0 or not trace.get("completed"):
        return dict(provider="codex", reason="provider_error_unknown", api_error_status=None)
    return None


def selected_observation_error(phase, error):
    categories = (ValueError, TypeError, AttributeError, KeyError, OSError, RuntimeError)
    category = next((kind.__name__ for kind in categories if isinstance(error, kind)), 'Exception')
    return dict(phase=phase, exception_type=category)


def retain_observation_error(base, metadata, *, name="observation-error.json"):
    try:
        with (base / name).open('x') as retained:
            json.dump(metadata, retained)
            retained.flush()
            os.fsync(retained.fileno())
    except OSError:
        # A report-write error cannot erase an independently available grade.
        # The aggregate record still exposes the fixed failure and failed save.
        metadata['persistence_failed'] = True


def failed_selected_input(input_bytes, phase, error):
    return dict(schema='cairn.task-eval.selected-input/1', status='unknown', failures=[],
                unknown=['observation_failed'], observation_error=selected_observation_error(phase, error),
                input_bytes=input_bytes, hook_wire_bytes=None, native_memory_result_bytes=None,
                total_bytes=None, last_native_memory_seconds=None, memory_attempted=None,
                memory_calls=None, native_searches=None, hook_searches=None, total_actual_pull_calls=None,
                source_deliveries=[], source_delivery_events_omitted=None, source_delivery_empty_events=None, origin_map_sha256=None,
                public_actions=dict(schema='cairn.public-actions/1', status='unknown', actions=[],
                    time_basis='capture_start_monotonic', time_unit='seconds',
                    actions_omitted=None, unmatched_results=None, duplicate_events=None,
                    unknown=['observation_failed'], limit=256),
                native_prompt_events=[], native_prompt_events_omitted=None,
                limits=dict(selected_input_bytes=9500, hook_seconds=5, last_native_memory_seconds=30))


def add_delivery_evidence(memory, selected_input):
    deliveries = selected_input['source_deliveries']
    memory['source_deliveries'] = deliveries
    memory['origin_map_sha256'] = selected_input['origin_map_sha256']
    # Only actual emitted payloads establish exposure; seen/inspection do not.
    memory['delivered'] = sorted({item['input_id'] for row in deliveries for item in row['delivery']['items']
                                  if item.get('input_id') and item['extent'] != 'history_metadata'})
    memory['injected_bytes'] = selected_input['hook_wire_bytes']
    memory['injected_byte_basis'] = 'hook_stdout_wire'


def classify_relevance(memory, case, *, prospective=False):
    if not memory:
        return
    delivered = set(memory.get('delivered', []))
    memory['forbidden'] = sorted((set(case.get('must_not_deliver', [])) | set(case.get('over_applied', []))) & delivered)
    if prospective and not case.get('relevance_labels_complete', False):
        memory['relevance_status'] = 'unknown_unlabelled'
        memory.pop('irrelevant', None)
        memory.pop('delivered_expected', None)
        return
    if prospective:
        memory['relevance_status'] = 'declared_exhaustive'
    expected = set(case['expected'])
    memory['delivered_expected'] = sorted(expected & delivered)
    memory['irrelevant'] = sorted(delivered - expected - set(case.get('acceptable', [])))


def names_for(store, call):
    return dict(tool=call["tool"].replace("mcp__cairn__", ""), query=(call["input"] or {}).get("query"))


def parse_injection(output):
    """Return (record ids in the index, expanded record id, injected bytes) from hook stdout."""
    if not output.strip():
        return [], None, 0
    data = json.loads(output)
    text = (data.get("hookSpecificOutput") or {}).get("additionalContext", "")
    if not text:
        return [], None, 0
    start = text.index("{")
    view = json.loads(text[start:])
    index = [entry["record_id"] for entry in view.get("index", [])]
    expanded = ((view.get("expanded") or {}).get("selection") or {}).get("record", {}).get("record_id")
    selected = [item.get("record", {}).get("record_id") for item in view.get("selected", []) if isinstance(item, dict)]
    return index + [s for s in selected if s], expanded, len(text.encode())


def hook_observations(state_dir, store):
    """Read delivered context and per-invocation metrics; label legacy state-only evidence."""
    if store is None or not Path(state_dir).is_dir():
        return {}
    delivered, size, outcomes, recalls = [], 0, [], []
    calls = Path(state_dir) / "calls.jsonl"
    injections = []
    if calls.exists():
        for line in calls.read_text().splitlines():
            try:
                index, expanded, size_one = parse_injection(line)
            except (ValueError, KeyError):
                continue
            injections.append(dict(index=[store.names.get(r, "?") for r in index],
                                   expanded=store.names.get(expanded) if expanded else None, bytes=size_one))
            delivered += injections[-1]["index"]
            size += size_one
    for path in Path(state_dir).glob("*.json"):
        state = json.loads(path.read_text())
        recall = state.get("last_recall") or {}
        outcomes.append(recall.get("outcome"))
        recalls.append({key: recall[key] for key in ("outcome", "discovery", "coverage", "inspected", "rejected",
                                                    "shortlist_candidates", "model_seconds", "model_reported_cost_usd",
                                                    "preview_seconds", "preview_reported_cost_usd", "pull_seconds",
                                                    "elapsed_seconds", "bytes", "preview_input_bytes", "selector_input_bytes")
                        if key in recall})
        delivered += [store.names.get(r["record_id"], "?") for r in recall.get("records", [])]
        delivered += [store.names.get(r, "?") for r in state.get("seen", {})]
    observation_path = Path(state_dir) / "observations.jsonl"
    invocations = []
    if observation_path.exists():
        for line in observation_path.read_text().splitlines():
            row = json.loads(line)
            if (not isinstance(row, dict) or row.get("schema") != "cairn.task-hook-observation/1"
                    or type(row.get("recall_attempted")) is not bool
                    or "recall" not in row or "event" not in row
                    or (row["recall"] is not None and not isinstance(row["recall"], dict))
                    or (not row["recall_attempted"] and row["recall"] is not None)):
                raise ValueError("unsupported hook observation")
            invocations.append(row)
        recalls = [dict(row["recall"] or {}, event=row["event"],
                        metrics_available=row["recall"] is not None,
                        observed_seconds=row.get("recall_seconds"))
                   for row in invocations if row["recall_attempted"]]
        outcomes = [r.get("outcome") for r in recalls]
    return dict(delivered=sorted(set(delivered)), injected_bytes=size, injections=injections, outcomes=outcomes,
                recalls=recalls, hook_invocations=invocations,
                recall_observation="per_invocation" if observation_path.exists() else "last_state_only")


# ---------------------------------------------------------------- one attempt

@dataclasses.dataclass
class Attempt:
    """One planned attempt and the owned paths it runs in."""
    case: dict
    arm: str
    seed: int
    order: int
    profile: ExecutionProfile
    provider: object
    toolkit: Toolkit
    stores: dict
    out: Path
    run_id: str
    base: Path
    root: Path
    cwd: Path
    home: Path
    sandbox_options: dict
    assigned_account: dict | None
    baseline: AttemptBaseline | None = None


@dataclasses.dataclass
class AttemptResult:
    """What one completed attempt observed; ``attempt_record`` turns it into the agent row."""
    trace: dict
    graded: dict
    memory: dict
    code: int
    elapsed: float
    prompt: str
    baseline: AttemptBaseline
    stream_bytes: bytes | None = None
    limit_assessment: dict | None = None
    process_custody: dict | None = None
    writer_record: dict | None = None
    codex_custody_problem: str | None = None
    selected_input: dict | None = None
    failures: list = dataclasses.field(default_factory=list)
    receipt_export: dict | None = None
    observed_arm: dict | None = None
    native_sha256: str | None = None
    capture_error: str | None = None


def original_binding_check(attempt, binds, env):
    """Run BINDING_CHECK in the attempt sandbox for an original agent_tools arm; refuse before launch."""
    check = binding_check_command(attempt.profile.harness)
    checked = attempt.provider.command(attempt, binds, env, prerequisite_command(check), prerequisite=True)
    try:
        done = subprocess.run(checked, capture_output=True, timeout=min(120, attempt.profile.timeout))
        row = dict(argv=check, exit=done.returncode, stdout_sha256=hashlib.sha256(done.stdout).hexdigest(),
                   stderr_sha256=hashlib.sha256(done.stderr).hexdigest())
    except subprocess.TimeoutExpired:
        row = dict(argv=check, exit=None, error="prerequisite_timeout")
    (attempt.base / "original-binding-check.json").write_text(json.dumps(row, indent=2))
    if row["exit"] != 0:
        raise RuntimeError("original_recall_binding_invalid: binding check failed before provider launch")


# ---------------------------------------------------------------- execution

def open_attempt(case, arm, seed, order, profile, stores, out, toolkit):
    """Create the attempt's owned paths and workspace, then bind its provider adapter."""
    run_id = f"{case['id']}.{arm}.s{seed}"
    base = out / "runs" / run_id
    base.mkdir(parents=True)
    root = base / "work"
    root.mkdir()
    toolkit.prepare_workspace(case, root)
    return Attempt(case=case, arm=arm, seed=seed, order=order, profile=profile, provider=provider_for(profile),
                   toolkit=toolkit, stores=stores, out=out, run_id=run_id, base=base, root=root,
                   cwd=root / case["cwd"], home=Path.home(), sandbox_options={}, assigned_account=None)


def base_environment(home):
    return dict(HOME=str(home), PATH=f"{home}/.local/bin:{home}/.local/go/bin:/usr/local/bin:/usr/bin:/bin",
                LANG="C.UTF-8", GOTOOLCHAIN="local", GOFLAGS="-mod=mod", TERM="dumb",
                GIT_AUTHOR_NAME="trial agent", GIT_AUTHOR_EMAIL="agent@example.invalid",
                GIT_COMMITTER_NAME="trial agent", GIT_COMMITTER_EMAIL="agent@example.invalid")


@dataclasses.dataclass
class Launch:
    """The provider command inputs one attempt assembles before launch."""
    prompt: str
    env: dict
    binds: list
    setup: ProviderSetup
    corpus: dict
    common_runtime: dict | None = None
    runtime_identity: object = None
    native_sha256: str | None = None
    arm_config: dict = dataclasses.field(default_factory=dict)
    observed_arm: dict | None = None
    native_input: bytes | None = None
    input_charge: int | None = None


def prepare_launch(attempt):
    """Everything before the provider process: runtime, provider setup, memory arm, prerequisites."""
    profile, case, arm, base = attempt.profile, attempt.case, attempt.arm, attempt.base
    prospective = profile.prospective
    corpus = {n["id"]: n for n in (prospective["notes"] if prospective else attempt.toolkit.load_corpus())}
    prompt = case["wordings"][profile.wording]
    env = base_environment(attempt.home)
    options = attempt.sandbox_options
    if prospective:
        options.update(native_executable=checked_file(prospective["document"]["native"]["binary"]))
    native_sha256 = None
    if profile.original_native is not None:
        # Re-hash the retained copy immediately before this attempt; mount it at the sandbox target.
        copy = profile.original_native["observed"]["source"]
        native_sha256 = native_copy.file_sha256(copy)
        if native_sha256 != profile.original_native["declared"]["sha256"]:
            raise RuntimeError("original native copy changed before launch")
        options.clear()
        options.update(native_executable=Path(copy), credentials_readonly=False)
    common_runtime = prospective['document'].get('runtime') if prospective else None
    runtime_identity = None
    if common_runtime is not None:
        from trial_task_runtime import validate_runtime, prepare_identity, runtime_environment
        validate_runtime(common_runtime)
        runtime_identity = prepare_identity(base / 'runtime-identity')
        options.update(common_runtime=common_runtime, runtime_identity=runtime_identity)
        env.update(runtime_environment(common_runtime))
    setup = attempt.provider.setup(attempt)
    env.update(setup.env)
    launch = Launch(prompt=prompt, env=env, binds=list(setup.binds), setup=setup, corpus=corpus,
                    common_runtime=common_runtime, runtime_identity=runtime_identity, native_sha256=native_sha256)
    if arm == "direct":
        notes = [corpus[n]["body"] for n in case["expected"]]
        if notes:
            launch.prompt = ("Relevant saved notes (fallible; check against the workspace):\n\n"
                             + "\n\n---\n\n".join(notes) + "\n\n---\n\n" + launch.prompt)
    elif arm != "none":
        wire_memory_arm(attempt, launch)
    if prospective:
        prepare_prospective(attempt, launch)
    if (not prospective and arm in attempt.stores and profile.original_arms
            and attempt.stores[arm]["configuration"]["recall_mode"] == "agent_tools"):
        original_binding_check(attempt, launch.binds, launch.env)
    if prospective:
        launch.native_input = (json.dumps(dict(type="user", message=dict(role="user", content=launch.prompt)),
                                          ensure_ascii=False) + "\n").encode()
        launch.input_charge = (len(launch.native_input) + 512
                               + (len(profile.memory_instruction.encode()) if arm not in ("none", "direct") else 0))
        if launch.input_charge > 8500:
            raise ValueError("task and framing leave less than1000 bytes; no provider launched")
    else:
        setup.args += ["--", launch.prompt]
    return launch


def wire_memory_arm(attempt, launch):
    """Bind one memory arm's store, recall hook, observer and MCP server into the provider."""
    profile, arm, base, stores = attempt.profile, attempt.arm, attempt.base, attempt.stores
    prospective, prompt, binds = profile.prospective, launch.prompt, launch.binds
    memory_instruction = profile.memory_instruction
    store = stores[arm]
    task_wire = (json.dumps(dict(type="user", message=dict(role="user", content=prompt)), ensure_ascii=False) + "\n").encode()
    context_limit = 9500 - len(task_wire) - 512 - len(memory_instruction.encode()) if prospective else (
        codex_weekly_limits.hook_context_bytes(prompt, memory_instruction) if profile.codex_weekly else
        legacy_limits.hook_context_bytes(prompt, memory_instruction) if profile.original
        else CLAUDE_CONTEXT_BYTES if profile.harness == "claude" else CODEX_CONTEXT_BYTES)
    if context_limit < 1000:
        raise ValueError("task leaves insufficient hook allowance")
    session_state = base / "hook-state"
    session_state.mkdir()
    trial = Path("/tmp/trial/store")
    config = dict(cairn=str(trial / "cairn"), socket=str(trial / "sock/api.sock"), token_file=str(trial / "agent.token"),
                  repo=TRIAL_REPO, state_dir="/tmp/trial/hookstate",
                  context_bytes=context_limit,
                  harness=profile.harness, semantic_fallback=arm in profile.semantic_recall)
    original_arms = profile.original_arms
    arm_config = store.get("configuration", {}) if prospective or original_arms else {}
    launch.arm_config = arm_config
    if prospective or original_arms:
        # Literal per-arm controls (prospective input, or the original weekly contract).
        config["semantic_fallback"] = arm_config["semantic_fallback"]
        config["recall_mode"] = arm_config["recall_mode"]
    if original_arms:
        if config["semantic_fallback"] and config["recall_mode"] != "agent_tools":
            raise ValueError("original_execution_unsupported:semantic_fallback_selector")
        launch.observed_arm = original_arm_controls.observed_arm(arm_config)
    if config["semantic_fallback"] and config.get("recall_mode") != "agent_tools":
        config.update(claude="/tmp/trial/selector/claude", model=profile.selector_model)
        binds.append((Path(profile.selector_binary).resolve(), config["claude"], "ro"))
        if profile.harness == "codex":
            binds.append((Path(profile.selector_auth_file).resolve(), attempt.home / ".claude/.credentials.json", "ro"))
    (base / "hook-config.json").write_text(json.dumps(config))
    if (prospective or original_arms) and config["recall_mode"] == "agent_tools":
        # Claude's single child has an explicit one-task key. Codex uses its
        # own durable per-native-turn grant; never add Claude's key there.
        binds += attempt.toolkit.bind_ordinary_claude(base, config, arm_config["bridge"],
                                                      launcher_task=profile.harness == "claude",
                                                      bundle_pin=arm_config.get("bundle"))
    hook_cmd = "bash -o pipefail -c 'python3 -B /tmp/trial/hook-observer.py --engine /tmp/trial/hook/memory.py --config /tmp/trial/hookconfig.json --observations /tmp/trial/hookstate/observations.jsonl | tee -a /tmp/trial/hookstate/calls.jsonl'"
    if prospective:
        hook_cmd = "python3 -B /tmp/trial/hook-observer.py --engine /tmp/trial/hook/memory.py --config /tmp/trial/hookconfig.json --observations /tmp/trial/hookstate/observations.jsonl"
        hook_cmd += " --frozen-prompt-sha256 " + hashlib.sha256(prompt.encode()).hexdigest()
    hook_timeout = legacy_limits.hook_timeout_seconds() if profile.weekly else 13
    settings = dict(hooks={event: [dict(hooks=[dict(type="command", command=hook_cmd, timeout=hook_timeout)])]
                           for event in HOOK_EVENTS})
    (base / "settings.json").write_text(json.dumps(settings))
    mcp = dict(mcpServers=dict(cairn=dict(type="stdio", command=str(trial / "cairn"), args=[
        "mcp", "--socket", str(trial / "sock/api.sock"), "--token-file", str(trial / "agent.token"), "--repo", TRIAL_REPO,
        "--task", "task-eval/" + attempt.run_id, "--run", attempt.run_id,
        *(legacy_limits.mcp_tool_room_arguments() if profile.weekly else [])])))
    (base / "mcp.json").write_text(json.dumps(mcp))
    binds += [(store["store"].binary, trial / "cairn", "ro"), (store["store"].sockdir, trial / "sock", "rw"),
              (store["store"].token_file, trial / "agent.token", "ro"), (Path(store["hook"]).parent, "/tmp/trial/hook", "ro")]
    binds += trial_hook_observer_bindings(base / "hook-config.json", session_state)
    binds += [(base / "settings.json", "/tmp/trial/settings.json", "ro"), (base / "mcp.json", "/tmp/trial/mcp.json", "ro")]
    attempt.provider.memory_wiring(attempt, launch.setup, MemoryWiring(
        hook_cmd=hook_cmd, context_limit=context_limit, mcp_server=mcp["mcpServers"]["cairn"],
        memory_instruction=memory_instruction))


def prepare_prospective(attempt, launch):
    """Prospective settings and the in-sandbox prerequisites; any failure refuses before launch."""
    case, arm, base = attempt.case, attempt.arm, attempt.base
    settings_path, mcp_path = base / "settings.json", base / "mcp.json"
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.update(permissions=settings_permissions(), autoMemoryEnabled=False, claudeMdExcludes=["**"])
    settings_path.write_text(json.dumps(settings))
    if arm in ("none", "direct"):
        mcp_path.write_text(json.dumps(dict(mcpServers={})))
        launch.binds += [(settings_path, "/tmp/trial/settings.json", "ro"), (mcp_path, "/tmp/trial/mcp.json", "ro")]
        launch.setup.args += attempt.provider.settings_arguments()
    prerequisites = []
    binding_check = ([binding_check_command(attempt.profile.harness)]
                     if arm not in ("none", "direct") and launch.arm_config["recall_mode"] == "agent_tools" else [])
    prerequisite_commands = (binding_check + ([["go", "version"], ["go", "list", "./..."]]
                                              if (attempt.cwd / "go.mod").exists() else []) + case["preflight"])
    for argv in prerequisite_commands:
        checked = attempt.provider.command(attempt, launch.binds, launch.env, prerequisite_command(argv), prerequisite=True)
        try:
            done = subprocess.run(checked, capture_output=True, timeout=min(120, attempt.profile.timeout))
            row = dict(argv=argv, exit=done.returncode, stdout_sha256=hashlib.sha256(done.stdout).hexdigest(),
                       stderr_sha256=hashlib.sha256(done.stderr).hexdigest())
        except subprocess.TimeoutExpired:
            row = dict(argv=argv, exit=None, error="prerequisite_timeout")
        prerequisites.append(row)
        (base / "preflight.json").write_text(json.dumps(prerequisites, indent=2))
        if row["exit"] != 0:
            raise RuntimeError("prospective prerequisite failed before provider launch")


@dataclasses.dataclass
class Capture:
    """The provider process as the harness observed it."""
    stdout: str
    stderr: str
    code: int
    elapsed: float
    raw_stdout: bytes = b''
    raw_stderr: bytes = b''
    timings: list = dataclasses.field(default_factory=list)
    capture_error: str | None = None
    custody_failure: str | None = None
    process_custody: dict | None = None
    limit_assessment: dict | None = None
    stream_bytes: bytes | None = None
    writer_record: dict | None = None


def launch_provider(attempt, launch):
    """Run the provider inside the owned writer leaf when one is selected; always release and close it."""
    profile, base, case, arm = attempt.profile, attempt.base, attempt.case, attempt.arm
    toolkit, prospective = attempt.toolkit, profile.prospective
    provider_args, binds = launch.setup.args, launch.binds
    writer_leaf = None
    if profile.weekly and profile.original_writer:
        # Root-selected owned leaf: the actor joins it before exec. Unavailable refuses before launch.
        try:
            writer_leaf = writer_release.WriterLeaf(profile.original_writer["delegated"], profile.original_writer["controls"],
                                                    reviewed=profile.original_writer.get("reviewed"))
        except writer_release.WriterUnavailable as error:
            raise RuntimeError("original_writer_containment_unavailable: " + str(error)) from None
    # The owned leaf's whole lifetime, from capture to release and close, is guarded: any failure
    # still releases and closes it, records the observations, and never claims a successful release.
    try:
        if writer_leaf is not None:
            # The in-sandbox launch-evidence probe runs first and execs the unchanged provider argv.
            provider_args, probe_bind = writer_leaf.probe(provider_args)
            binds = binds + [probe_bind]
        command = attempt.provider.command(attempt, binds, launch.env, provider_args)
        started = time.monotonic()
        observed = Capture(stdout='', stderr='', code=0, elapsed=0.0)
        try:
            if prospective:
                observed.raw_stdout, observed.raw_stderr, observed.code, observed.timings = toolkit.capture(
                    command, profile.timeout, base / "stream-timing.json", input_bytes=launch.native_input,
                    custody_path=base / "native-process-custody.json")
                observed.stdout = observed.raw_stdout.decode(errors="replace")
                observed.stderr = observed.raw_stderr.decode(errors="replace")
            elif profile.weekly:
                # The existing capture records per-line receive times for the last-memory bound.
                observed.raw_stdout, observed.raw_stderr, observed.code, observed.timings = toolkit.capture(
                    writer_leaf.wrap(command) if writer_leaf is not None else command, profile.timeout,
                    base / "stream-timing.json", custody_path=base / "native-process-custody.json",
                    pass_fds=writer_leaf.pass_fds if writer_leaf is not None else (),
                    observer=writer_leaf.observe_init if writer_leaf is not None else None)
                observed.stdout = observed.raw_stdout.decode(errors="replace")
                observed.stderr = observed.raw_stderr.decode(errors="replace")
            else:
                result = subprocess.run(command, capture_output=True, timeout=profile.timeout)
                observed.stdout = result.stdout.decode(errors="replace")
                observed.stderr = result.stderr.decode(errors="replace")
                observed.code = result.returncode
        except subprocess.TimeoutExpired as expired:
            observed.stdout = (expired.stdout or b"").decode(errors="replace")
            observed.stderr, observed.code = "timeout", 124
            if prospective:
                observed.capture_error = 'capture_timeout_exception'
        except Exception:
            if not prospective:
                raise
            observed.stdout, observed.stderr, observed.code = '', '', 125
            observed.capture_error = 'capture_exception'
        except (KeyboardInterrupt, SystemExit):
            if not prospective:
                raise
            observed.stdout, observed.stderr, observed.code = '', '', 125
            observed.capture_error = 'observer_interrupted'
        if prospective:
            observed.process_custody = toolkit.read_custody(base / "native-process-custody.json")
            if observed.process_custody['classification'] == 'unknown' or observed.capture_error:
                selected_failure = dict(schema='cairn.process-custody-unknown/1',
                                        reason=observed.capture_error or 'process_custody_unknown',
                                        process_custody=observed.process_custody)
                retain_observation_error(base, selected_failure, name='process-custody-unknown.json')
        observed.elapsed = time.monotonic() - started
        if not prospective:
            retain_legacy_stream(attempt, launch, observed)
        else:
            capture_error, raw_stdout, raw_stderr = observed.capture_error, observed.raw_stdout, observed.raw_stderr
            (base / "native-output-metadata.json").write_text(json.dumps(dict(
                stdout_bytes=len(raw_stdout) if capture_error is None else None,
                stdout_sha256=hashlib.sha256(raw_stdout).hexdigest() if capture_error is None else None,
                stderr_bytes=len(raw_stderr) if capture_error is None else None,
                stderr_sha256=hashlib.sha256(raw_stderr).hexdigest() if capture_error is None else None,
                capture_returned=capture_error is None,
                raw_stream_retained=False)))
    except BaseException as primary:
        if writer_leaf is not None:
            writer_observed = writer_leaf.finish(time.monotonic() + 5)
            failed = writer_release.result(run_id=attempt.run_id, workspace=attempt.cwd, leaf=writer_leaf,
                                           observed=writer_observed, custody=None, attempt_failed=True)
            # Sanitized observations travel with the primary error, whatever happens to the receipt file.
            primary.add_note("writer_release: " + json.dumps(dict(state=failed["state"], release=failed["release"],
                             bootstrap=failed["bootstrap"].get("state"), close_errors=failed["close_errors"]),
                             sort_keys=True))
            try:
                (base / "writer-release.json").write_text(json.dumps(failed, indent=2))
            except OSError as error:
                primary.add_note("writer_release_receipt_unwritten: " + type(error).__name__)
        raise
    if writer_leaf is not None:
        release_writer(attempt, launch, writer_leaf, observed)
    return observed


def retain_legacy_stream(attempt, launch, observed):
    """Legacy stream bytes, process custody and the weekly limit assessment."""
    profile, base, arm = attempt.profile, attempt.base, attempt.arm
    # Record the exact stream bytes written, so blind grading can bind the
    # original attempt's answer (CAIRN-107 legacy blind selection).
    observed.stream_bytes = observed.stdout.encode("utf-8")
    (base / "stream.jsonl").write_bytes(observed.stream_bytes)
    (base / "stderr.txt").write_text(observed.stderr[-20000:])
    if profile.weekly:
        # Retain scoped process observations for both weekly provider routes. They
        # remain separate from the writer namespace-init proof.
        custody = observed.process_custody = attempt.toolkit.read_custody(base / "native-process-custody.json")
        if profile.original and (custody["classification"] == "unknown" or not custody["leader_reaped"]
                                 or custody["cleanup"]["status"] != "observed_clear"):
            observed.custody_failure = "process_custody_unknown"
    if profile.weekly and arm in attempt.stores:
        hook_rows = None  # malformed observations stay unknown in the assessment
        observations = base / "hook-state/observations.jsonl"
        try:
            hook_rows = [json.loads(line) for line in observations.read_text().splitlines()] if observations.exists() else []
        except (OSError, ValueError):
            pass
        checker = profile.limit_checker
        observed.limit_assessment = checker.assess(observed.stdout, hook_rows, timings=observed.timings,
                                                   input_bytes=checker.input_charge(launch.prompt, profile.memory_instruction))


def release_writer(attempt, launch, writer_leaf, observed):
    """Owner-derived release before any grading: bootstrap membership, then the leaf release."""
    base = attempt.base
    writer_observed = writer_leaf.finish(time.monotonic() + 5)
    writer_record = observed.writer_record = writer_release.result(
        run_id=attempt.run_id, workspace=attempt.cwd, leaf=writer_leaf, observed=writer_observed,
        custody=observed.process_custody)
    (base / "writer-release.json").write_text(json.dumps(writer_record, indent=2))
    if writer_record["state"] != "released" and observed.custody_failure is None:
        observed.custody_failure = "writer_release_" + writer_record["state"]
    if writer_record["state"] != "released":
        return
    try:
        final_artifacts = legacy_pregrade.capture(
            base=base, case=attempt.case, arm=attempt.arm, seed=attempt.seed, baseline=attempt.baseline,
            writer_record=writer_record, stream_bytes=observed.stream_bytes, prompt=launch.prompt)
    except (legacy_pregrade.PregradeError, OSError) as error:
        writer_record["final_artifact_capture"] = dict(
            state="not_established", reason=str(error) if isinstance(error, legacy_pregrade.PregradeError)
            else "legacy_pregrade_storage_failed")
    else:
        writer_record["final_artifact_custody"] = final_artifacts
    # Persist the post-capture custody result before any grading begins.
    release_path = base / "writer-release.json"
    temporary = base / ".writer-release.json.tmp"
    with temporary.open("x") as retained:
        json.dump(writer_record, retained, indent=2)
        retained.flush()
        os.fsync(retained.fileno())
    os.replace(temporary, release_path)
    directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_once(path, document):
    with path.open("x") as retained:
        json.dump(document, retained)
        retained.flush()
        os.fsync(retained.fileno())


def observe(attempt, launch, observed):
    """Trace, failures, selected input, grade and memory evidence of one completed attempt."""
    profile, base, case, arm, stores = attempt.profile, attempt.base, attempt.case, attempt.arm, attempt.stores
    toolkit, prospective = attempt.toolkit, profile.prospective
    trace = toolkit.parse_stream(observed.stdout, profile.harness)
    codex_custody_problem = None
    if profile.codex_weekly:
        # Codex can terminate on auth/quota failures without emitting a typed
        # terminal event. Do not infer which failure occurred from prose; stop
        # the campaign as an opaque provider failure before admitting another
        # account attempt.
        trace["admission_failure"] = codex_process_admission_failure(trace, observed.code)
        codex_custody_problem = toolkit.custody_failure(observed.process_custody, observed.writer_record)
    if prospective:
        # Retain only fixed terminal admission metadata before fallible grading.
        # Prospective runs intentionally have no raw stream recovery path.
        write_once(base / "native-terminal.json",
                   dict(schema="cairn.native-terminal/1", admission_failure=trace["admission_failure"]))
    failure = execution_failure(trace, observed.code)
    failures = [failure] if failure else []
    selected_input = None
    if prospective:
        custody = observed.process_custody
        if custody["input_bytes_written"] is not None and custody["input_bytes_written"] != len(launch.native_input):
            failures.append("input_delivery_incomplete")
        if custody["cleanup"]["status"] != "observed_clear" or not custody["leader_reaped"]:
            failures.append("process_custody_unknown")
        if observed.capture_error:
            failures.append(observed.capture_error)
        failure = failures[0] if failures else None
        selected_input = observe_selected_input(attempt, launch, observed)
    receipt_export = (retain_hook_explanations(stores[arm]["store"], base)
                      if prospective and arm in stores else None)
    ctx = dict(cwd=attempt.cwd, commands=trace["commands"], answer=trace["answer"],
               tool_outputs=toolkit.parse_tool_outputs(observed.stdout, profile.harness, require_completed=bool(prospective)),
               snapshot=attempt.baseline.snapshot, baseline=attempt.baseline,
               common_runtime=launch.common_runtime, runtime_identity=launch.runtime_identity)
    if observed.custody_failure is None:
        graded = toolkit.grade(case, ctx)
    else:
        graded = dict(outcome="harness_error", execution_failure=observed.custody_failure, error=observed.custody_failure)
    if failure and observed.custody_failure is None:
        graded = dict(graded, check_outcome=graded["outcome"], outcome="harness_error", execution_failure=failure,
                      error="prospective_native_failure" if prospective else observed.stderr[-600:])
    try:
        memory = (toolkit.hook_observations or hook_observations)(base / "hook-state", stores.get(arm, {}).get("store"))
    except Exception as error:
        if not prospective:
            raise
        memory = dict(observation_error=selected_observation_error("hook_summary", error))
        selected_input['status'] = 'unknown'
        selected_input['unknown'].append('hook_summary_failed')
        retain_observation_error(base, memory['observation_error'], name="hook-summary-error.json")
    return AttemptResult(trace=trace, graded=graded, memory=memory, code=observed.code, elapsed=observed.elapsed,
                         prompt=launch.prompt, baseline=attempt.baseline, stream_bytes=observed.stream_bytes,
                         limit_assessment=observed.limit_assessment, process_custody=observed.process_custody,
                         writer_record=observed.writer_record, codex_custody_problem=codex_custody_problem,
                         selected_input=selected_input, failures=failures, receipt_export=receipt_export,
                         observed_arm=launch.observed_arm, native_sha256=launch.native_sha256,
                         capture_error=observed.capture_error)


def observe_selected_input(attempt, launch, observed):
    """Prospective selected-input measurement; observation failure stays visible, never fatal."""
    base, case, arm, stores, profile = attempt.base, attempt.case, attempt.arm, attempt.stores, attempt.profile
    observation_phase = "hook_metadata"
    try:
        observations = base / "hook-state/observations.jsonl"
        hook_rows = [json.loads(line) for line in observations.read_text().splitlines()] if observations.exists() else []
        observation_phase = "measurement"
        selected_input = attempt.toolkit.measure(observed.stdout, observed.timings, hook_rows, input_bytes=launch.input_charge,
                                 expects_hooks=arm not in ("none", "direct"), model=profile.model, prompt=launch.prompt,
                                 workspace=attempt.cwd, public_validation_commands=case.get("public_validation_commands"))
        observation_phase = "origin_binding"
        origin_path = stores[arm]["store"].root / "import-provenance.json" if arm in stores else None
        origins = json.loads(origin_path.read_bytes()) if origin_path else []
        selected_input['origin_map_sha256'] = hashlib.sha256(origin_path.read_bytes()).hexdigest() if origin_path else None
        if arm == 'direct' and case['expected']:
            selected_input['status'] = 'unknown'
            selected_input['unknown'].append('direct_prompt_source_delivery_unobserved')
        for delivery in selected_input['source_deliveries']:
            delivery['delivery'] = bind_origins(delivery['delivery'], origins,
                                                bodies={n['id']: n['body'] for n in profile.prospective['notes']})
            if delivery['delivery']['status'] == 'unknown':
                selected_input['status'] = 'unknown'
                if 'source_delivery_or_origin_unknown' not in selected_input['unknown']:
                    selected_input['unknown'].append('source_delivery_or_origin_unknown')
        # Preserve source evidence even when the independent task grader fails.
        observation_phase = "selected_input_write"
        write_once(base / "selected-input.json", selected_input)
    except Exception as error:
        # Observation failure must remain visible without erasing independent
        # technical grading. Never retain exception text or raw native data.
        selected_input = failed_selected_input(launch.input_charge, observation_phase, error)
        retain_observation_error(base, selected_input["observation_error"])
    return selected_input


# ---------------------------------------------------------------- records

def account_fields(account):
    """The pinned Codex weekly account an attempt was assigned, as the agent row carries it."""
    if account is None:
        return {}
    return dict(account_id=account['account_id'], account_auth_sha256=account['auth_sha256'])


def attempt_record(attempt, result):
    """The ``cairn.task-eval.agent/1`` row for an attempt that ran to grading."""
    profile, case, arm, stores = attempt.profile, attempt.case, attempt.arm, attempt.stores
    prospective = profile.prospective
    trace, prompt = result.trace, result.prompt
    record = dict(run_id=attempt.run_id, case=case["id"], category=case["category"], primary=case.get("primary", True),
                  provenance=case["provenance"]["type"], arm=arm, seed=attempt.seed, order=attempt.order,
                  harness=profile.harness, model=profile.model, wording=profile.wording, exit=result.code,
                  seconds=round(result.elapsed, 2), prompt_bytes=len(prompt.encode()),
                  **result.graded, trace={k: v for k, v in trace.items() if k not in ("answer", "commands")},
                  commands=len(trace["commands"]), answer_chars=len(trace["answer"] or ""), memory=result.memory)
    record.update(account_fields(attempt.assigned_account))
    record['baseline_custody'] = result.baseline.pin
    if not prospective:
        record["stream_sha256"] = hashlib.sha256(result.stream_bytes).hexdigest()
        record["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if profile.original_native is not None:
            record["original_native"] = dict(binary_sha256=result.native_sha256, effort=profile.effort,
                                             target=profile.original_native["observed"]["target"])
        if profile.codex_weekly and profile.codex is not None and profile.codex.binary:
            record["original_codex"] = dict(binary_sha256=profile.codex.binary_sha256, effort=profile.effort,
                                            target=str(attempt.home / ".codex/codex"))
        if arm in stores and profile.original_arms:
            record["original_arm"] = result.observed_arm
        if profile.original:
            record["original_limits"] = result.limit_assessment
            record["process_custody"] = result.process_custody
        if profile.codex_weekly:
            record["codex_weekly_limits"] = result.limit_assessment
            record["process_custody"] = result.process_custody
        if result.codex_custody_problem:
            record["observer_integrity_unknown"] = True
            record["observer_failure_reason"] = result.codex_custody_problem
        if result.writer_record is not None:
            record["writer_release"] = result.writer_record
            record["final_artifact_custody"] = result.writer_record.get(
                "final_artifact_custody", "not_established")
    else:
        record["selected_input"] = result.selected_input
        record["process_custody"] = result.process_custody
        record["execution_failures"] = result.failures
        record["observer_integrity_unknown"] = bool(result.capture_error
                                                    or result.process_custody['cleanup']['status'] != 'observed_clear')
        record["receipt_explanation_export"] = result.receipt_export
    record["memory_tool_pulls"] = [names_for(stores.get(arm, {}).get("store"), call) for call in trace["memory_calls"]]
    if prospective:
        add_delivery_evidence(result.memory, result.selected_input)
    classify_relevance(result.memory, case, prospective=bool(prospective))
    if prospective:
        record["trace"] = {key: value for key, value in record["trace"].items() if key not in ("writes", "memory_calls")}
        record["memory_tool_pulls"] = [dict(tool=row["tool"]) for row in record["memory_tool_pulls"]]
        record["memory"]["selected_input"] = record["selected_input"]
    return record


NATIVE_TERMINAL_FAILURES = tuple(dict(provider=provider, reason=reason, api_error_status=status)
                                 for provider, reason, status in (
                                     ("claude", "authentication_failed", 401),
                                     ("claude", "capacity_exhausted", 429),
                                     ("codex", "authentication_failed", 401),
                                     ("codex", "capacity_exhausted", 429),
                                     ("codex", "provider_error_unknown", None)))


def failed_attempt_record(case, arm, seed, profile, error, base, toolkit, *, account=None):
    """The agent row for an attempt that raised; recover only retained, typed evidence."""
    prospective = profile.prospective
    cid = case["id"]
    record = dict(run_id=f"{cid}.{arm}.s{seed}", case=cid, arm=arm, seed=seed,
                  outcome="harness_error", error=selected_observation_error('runner', error)['exception_type'] if prospective else str(error)[-600:],
                  harness=profile.harness, model=profile.model, wording=profile.wording)
    if profile.harness == "codex" and profile.codex_weekly:
        record.update(account_fields(account))
        record["observer_integrity_unknown"] = True
        record["observer_failure_reason"] = "codex_attempt_exception"
    # A later grading failure must not hide a provider stop.
    if prospective:
        record['process_custody'] = toolkit.read_custody(base / 'native-process-custody.json')
        record['execution_failures'] = ['runner_exception']
        record['observer_integrity_unknown'] = record['process_custody']['cleanup']['status'] != 'observed_clear'
        if record['observer_integrity_unknown']:
            record['execution_failures'].append('process_custody_unknown')
        custody = record['process_custody']
        if custody['legacy_exit_code']:
            record['execution_failures'].append('timeout' if custody['legacy_exit_code'] == 124 else 'nonzero_exit')
        if custody['input_delivery_complete'] is False:
            record['execution_failures'].append('input_delivery_incomplete')
    terminal = base / "native-terminal.json"
    stream = base / "stream.jsonl"
    if prospective and terminal.is_file():
        try:
            selected = json.loads(terminal.read_text())
            failure = selected.get("admission_failure")
            if selected.get("schema") != "cairn.native-terminal/1" or (
                    failure is not None and failure not in NATIVE_TERMINAL_FAILURES):
                raise ValueError("invalid selected terminal metadata")
            record["trace"] = dict(admission_failure=failure)
        except (OSError, ValueError, TypeError, AttributeError) as parse_error:
            record["trace_parse_error"] = type(parse_error).__name__
    elif not prospective and stream.is_file():
        try:
            trace = toolkit.parse_stream(stream.read_text(), profile.harness)
            record["trace"] = {k: v for k, v in trace.items() if k not in ("answer", "commands")}
        except (OSError, ValueError, TypeError, AttributeError) as parse_error:
            record["trace_parse_error"] = str(parse_error)[-600:]
    selected = base / "selected-input.json"
    if prospective and selected.is_file():
        try:
            record['selected_input'] = json.loads(selected.read_bytes())
            record['memory'] = {}
            add_delivery_evidence(record['memory'], record['selected_input'])
            classify_relevance(record['memory'], case, prospective=True)
        except (OSError, ValueError, TypeError, KeyError):
            record['source_delivery_error'] = 'selected_evidence_unavailable'
    return record


def execute(case, arm, seed, order, profile, stores, out, toolkit):
    """Run one planned attempt under ``profile`` and return its agent row (also written as result.json)."""
    attempt = open_attempt(case, arm, seed, order, profile, stores, out, toolkit)
    attempt.baseline = AttemptBaseline.capture(attempt.base, case, arm, seed)
    attempt.assigned_account = profile.assigned_account(case["id"], arm, seed)
    launch = prepare_launch(attempt)
    observed = launch_provider(attempt, launch)
    result = observe(attempt, launch, observed)
    record = attempt_record(attempt, result)
    if profile.prospective:
        (attempt.base / "answer.md").write_text(result.trace["answer"] or "")
    (attempt.base / "result.json").write_text(json.dumps(record, indent=2))
    return record
