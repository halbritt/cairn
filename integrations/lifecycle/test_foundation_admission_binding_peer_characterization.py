"""CAIRN-314: preserve current /2 and peer behavior, including permissive cases."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("foundation_characterization_bundle", SOURCE / "lifecycle_bundle.py")
bundle_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle_module)
BUNDLE = bundle_module.Bundle(SOURCE)
admission = BUNDLE.load("hook_admission")
binding_module = BUNDLE.load("inbox_binding")
peer = BUNDLE.load("unix_peer_transport")
OBSERVATIONS = []
SESSION = "11111111-1111-4111-8111-111111111111"
TURN = "22222222-2222-4222-8222-222222222222"


def observe(family, case, **values):
    OBSERVATIONS.append(dict(family=family, case=case, **values))


def stop_owned_child(child):
    if child.poll() is None:
        if type(child.pid) is not int or child.pid <= 1 or child.pid == os.getpid():
            raise AssertionError("refusing process control without an owned child PID > 1")
        if os.getpgid(child.pid) != child.pid:
            raise AssertionError("owned child did not retain its private process group > 1")
        child.terminate()
    try:
        child.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        if type(child.pid) is not int or child.pid <= 1 or os.getpgid(child.pid) != child.pid:
            raise AssertionError("refusing kill without an owned child PID > 1")
        child.kill()
        child.communicate(timeout=3)


class AdmissionCharacterizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cwd = self.root / "work" / "child"
        self.project = self.root / "project" / "child"
        self.cwd.mkdir(parents=True)
        self.project.mkdir(parents=True)
        self.environ = {}
        for harness, variable in (("codex", "CODEX_HOME"), ("claude", "CLAUDE_CONFIG_DIR")):
            home = self.root / harness
            home.mkdir()
            self.environ[variable] = str(home)

    def config(self, harness):
        config = dict(harness=harness)
        if harness in ("claude", "codex"):
            config["config_home"] = self.environ[admission.HOME_VARIABLES[harness]]
        return config

    def event(self, harness, name):
        identity = "ses_FoundationFixture" if harness == "opencode" else SESSION
        return dict(hook_event_name=name, session_id=identity, cwd=str(self.cwd),
                    project_path=str(self.project), model="fixture", turn_id=TURN,
                    prompt_id="fixture-prompt", conversationId=identity,
                    workspacePaths=[str(self.cwd)], modelName="fixture")

    def decision(self, kind, harness, event, **kwargs):
        result = admission.decide(kind, self.config(harness), event,
                                  environ=self.environ, **kwargs)
        view = (result.verdict, result.reason, result.message, result.phase)
        observe("admission", [kind, harness, event], result=view,
                environment=dict(self.environ), exclusions=result.exclusions)
        return view

    def test_helper_event_vocabulary_for_every_harness_with_real_directories(self):
        # This is the helper vocabulary, not a claim that every cell is installed.
        phases = dict(SessionStart="start", UserPromptSubmit="busy", PreInvocation="busy",
                      TurnStart="busy", ToolStart="busy", ToolEnd="busy", Stop="idle",
                      TurnEnd="idle", SessionEnd="leave", Interrupt="interrupted",
                      ProviderObservation="provider")
        memory_events = {"SessionStart", "UserPromptSubmit", "PreCompact", "SessionEnd",
                         "PreToolUse", "PostToolUse", "PostToolUseFailure"}
        events = (*memory_events, "Stop", "PreInvocation", "TurnStart", "TurnEnd",
                  "ToolStart", "ToolEnd", "Interrupt", "ProviderObservation", "Unknown")
        for harness in ("claude", "codex", "opencode", "agy", "hermes"):
            for name in sorted(events):
                event = self.event(harness, name)
                for kind in ("memory", "coordination", "inbox_recall"):
                    with self.subTest(harness=harness, event=name, kind=kind):
                        expected = ("run", None, None, None)
                        if kind == "memory":
                            if name == "Stop" and harness != "codex":
                                expected = ("skip", "stop_not_boundary", None, None)
                            elif name not in memory_events and name != "Stop":
                                expected = ("skip", "unsupported_event", None, None)
                        elif kind == "inbox_recall":
                            if name not in ("SessionStart", "UserPromptSubmit"):
                                expected = ("skip", "unsupported_event", None, None)
                        elif name == "PostToolUse":
                            expected = (("run", None, None, "cue") if harness in
                                        ("claude", "codex", "opencode") else
                                        ("skip", "unsupported_event", None, None))
                        elif name not in phases:
                            expected = ("invalid", "invalid_host", "unsupported coordination hook event", None)
                        elif name in ("ToolStart", "ToolEnd") and harness != "opencode":
                            expected = ("invalid", "invalid_host", "tool capture hooks are OpenCode-only", None)
                        elif name == "Interrupt" and harness != "codex":
                            expected = ("invalid", "invalid_host", "interrupt observation is a Codex-only hook", None)
                        else:
                            phase = "busy" if harness == "agy" and name == "Stop" else phases[name]
                            expected = ("run", None, None, phase)
                        self.assertEqual(self.decision(kind, harness, event), expected)
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_guard_order_nonliteral_flags_and_account_mismatch(self):
        for variable in ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD"):
            for value in ("true", "0"):
                for kind in ("memory", "coordination", "inbox_recall"):
                    with self.subTest(variable=variable, value=value, kind=kind):
                        self.environ[variable] = value
                        self.assertEqual(self.decision(kind, "codex", self.event("codex", "SessionStart"))[0], "run")
                self.environ.pop(variable)
        for harness in ("codex", "claude"):
            event = self.event(harness, "SessionStart")
            config = dict(self.config(harness), config_home=str(self.root / "other"))
            for kind in ("memory", "coordination", "inbox_recall"):
                with self.subTest(harness=harness, kind=kind):
                    result = admission.decide(kind, config, event,
                                             environ=dict(self.environ, CAIRN_LIFECYCLE_CHILD="1"),
                                             wake_accepted=lambda: self.fail("wrong-account hook consulted wake"))
                    self.assertEqual((result.verdict, result.reason), ("skip", "other_account_home"))
                    observe("guard-order", [harness, kind], config=config,
                            environment=dict(self.environ, CAIRN_LIFECYCLE_CHILD="1"),
                            result=(result.verdict, result.reason))
            result = admission.decide("coordination", config, event,
                                     environ=dict(self.environ, CAIRN_COORDINATION_DISABLED="1"))
            self.assertEqual((result.verdict, result.reason), ("skip", "coordination_disabled"))

    def test_real_privacy_walk_and_body_validation_precedence(self):
        for location in (self.cwd.parent, self.project.parent):
            for marker in (".cairn-no-memory", ".cairn-no-coordination"):
                path = location / marker
                path.touch()
                try:
                    for kind in ("memory", "coordination", "inbox_recall"):
                        with self.subTest(location=str(location), marker=marker, kind=kind):
                            excluded = (kind == "inbox_recall" or
                                        kind == "memory" and marker == ".cairn-no-memory" or
                                        kind == "coordination" and location == self.cwd.parent)
                            result = self.decision(kind, "codex", self.event("codex", "SessionStart"))
                            self.assertEqual(result[:2], ("skip", "privacy_marker") if excluded else ("run", None))
                    invalid = dict(self.event("codex", "SessionStart"), session_id="")
                    self.assertEqual(self.decision("memory", "codex", invalid)[:2], ("invalid", "invalid_host"))
                    self.assertEqual(self.decision("coordination", "codex", invalid)[:2], ("invalid", "invalid_host"))
                    # Cue guard and inbox prefix intentionally have different body checks.
                    cue = dict(invalid, hook_event_name="PostToolUse")
                    self.assertEqual(self.decision("coordination", "codex", cue), ("run", None, None, "cue"))
                finally:
                    path.unlink()

    def test_permissive_identity_spellings_and_escaping_owner_error(self):
        for identity in ("{" + SESSION + "}", "abcdef01-2345-4678-9abc-def012345678".upper(), SESSION.replace("-", "")):
            event = dict(self.event("codex", "SessionStart"), session_id=identity)
            self.assertEqual(self.decision("memory", "codex", event)[0], "run")
        event = dict(self.event("codex", "SessionStart"), session_id=" native\x7f ")
        self.assertEqual(self.decision("coordination", "codex", event)[0], "run")
        event["session_id"] = {}
        self.assertEqual(self.decision("inbox_recall", "codex", event)[0], "run")
        event = dict(self.event("claude", "UserPromptSubmit"), prompt_id="\ud800")
        with self.assertRaises(UnicodeEncodeError):
            admission.decide("coordination", self.config("claude"), event, environ=self.environ)
        observe("identity", "lone-surrogate-owner", error_class="UnicodeEncodeError",
                classification="escaping source error; no activation was called")

    def test_combined_invalid_fields_preserve_refusal_order(self):
        memory_cases = (
            (dict(session_id="", hook_event_name="Stop", cwd="missing"),
             "missing or invalid host session identity"),
            (dict(session_id="bad-uuid", hook_event_name="Unknown", cwd="missing"),
             "host session identity must be a UUID"),
        )
        for changes, message in memory_cases:
            event = dict(self.event("codex", "SessionStart"), **changes)
            self.assertEqual(self.decision("memory", "codex", event)[:3],
                             ("invalid", "invalid_host", message))
        envelope = dict(schema="cairn.owner-input-attestation/1", payload="e30", signature="A" * 86)
        cases = (
            (dict(session_id="", cwd="relative", model=1, hook_event_name="Unknown", turn_id=1),
             "native conversation ID required"),
            (dict(cwd="relative", model=1, hook_event_name="Unknown", turn_id=1),
             "native workspace must be an existing absolute directory"),
            (dict(model=1, hook_event_name="Unknown", turn_id=1), "invalid observed model"),
            (dict(hook_event_name="Unknown", turn_id=1), "unsupported coordination hook event"),
            (dict(hook_event_name="ToolStart", turn_id=1), "tool capture hooks are OpenCode-only"),
            (dict(turn_id=1, owner_input_attestation={}), "invalid native ownership key"),
            (dict(owner_input_attestation={}, owner_input_origin={}), "invalid owner input attestation envelope"),
            (dict(owner_input_attestation=envelope, owner_input_origin={}), "invalid owner input origin"),
            (dict(owner_input_attestation=envelope, owner_input_origin=dict(
                version=1, source="owner_composer", admission="fresh_start", input_id="invalid")),
             "invalid owner input identity"),
        )
        for changes, message in cases:
            event = dict(self.event("codex", "UserPromptSubmit"), **changes)
            self.assertEqual(self.decision("coordination", "codex", event)[:3],
                             ("invalid", "invalid_host", message))
        for fully_idle, phase in ((True, "idle"), (1, "busy"), (False, "busy")):
            event = dict(self.event("agy", "Stop"), fullyIdle=fully_idle)
            self.assertEqual(self.decision("coordination", "agy", event)[3], phase)


class BindingCharacterizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "binding.json"
        runtime = self.root / "runtime"
        bundle_module.stage_runtime(SOURCE, runtime)
        descriptor = bundle_module.bundle_descriptor(runtime)
        def pin(name):
            path = runtime / name
            return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        bundle=descriptor)
        common = dict(harness="codex", cairn=str(self.root / "unused-cli"),
                      socket=str(self.root / "unused.sock"), token_file=str(self.root / "never-open-token"),
                      repo="fixture", inbox_recall_binding=str(self.path))
        self.memory = dict(common, state_dir=str(self.root / "memory"),
                           recall_mode="agent_tools", context_bytes=9500)
        self.coordination = dict(common, state_dir=str(self.root / "coordination"),
                                 native_delivery=True, binding="fixture")
        self.manifest = dict(schema="cairn.inbox-recall-binding/2", enabled=True,
                             bridge=pin("inbox_recall.py"), engine=pin("memory.py"))
        self.policy = binding_module.InboxBinding(BUNDLE)
        self.write_profiles()

    def write_profiles(self):
        def pin(name, value):
            path = self.root / name
            path.write_text(json.dumps(value))
            path.chmod(0o600)
            return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.manifest["memory_config"] = pin("memory.json", self.memory)
        self.manifest["coordination_configs"] = [pin("coordination.json", self.coordination)]
        self.save_manifest()

    def save_manifest(self):
        self.path.write_text(json.dumps(self.manifest))
        self.path.chmod(0o600)

    def test_message_level_refusal_table_uses_real_pinned_files(self):
        for description in binding_refusal_cases():
            with self.subTest(case=description["name"]):
                fixture = BindingCharacterizationTests()
                fixture.setUp()
                try:
                    configure_binding_refusal(fixture, description["name"])
                    method = (lambda: fixture.policy.activation_decision(
                        fixture.memory, dict(hook_event_name="UserPromptSubmit", session_id=SESSION,
                                             turn_id=TURN, cwd=str(fixture.root)), True)
                              ) if description.get("prefix") else lambda: fixture.policy.validate(fixture.memory)
                    with self.assertRaises(description["exception"]) as caught:
                        method()
                    if description["message"] is not None:
                        self.assertEqual(str(caught.exception), description["message"])
                    observe("binding-refusal", description["name"],
                            error_class=type(caught.exception).__name__, error=str(caught.exception),
                            token_exists=Path(fixture.memory["token_file"]).exists())
                finally:
                    fixture.doCleanups()

    def test_manifest_schema_enabled_type_and_missing_path_error_families(self):
        # The refusal table covers schema/type variants. These entry points render
        # the same unknown schema differently, before checking any code pin.
        self.manifest["schema"] = "cairn.inbox-recall-binding/3"
        self.save_manifest()
        for method, message in (
                (lambda: self.policy.effective_config(self.memory, None, lambda e: False),
                 "unsupported inbox recall activation state"),
                (lambda: self.policy.load_bridge(self.memory),
                 "unsupported inbox recall binding; restage its complete lifecycle bundle")):
            with self.subTest(message=message), self.assertRaises(ValueError) as caught:
                method()
            self.assertEqual(str(caught.exception), message)
            observe("binding-entrypoint", message, error_class="ValueError", error=message)

    def test_owner_modes_absolute_paths_and_legacy_symlink_following(self):
        for target, function, argument, message in (
                (self.path, binding_module._owner_manifest, self.memory,
                 "inbox recall binding must be owner-only"),
                (self.root / "memory.json", binding_module._checked_file,
                 self.manifest["memory_config"], "inbox recall requires owner-controlled absolute paths")):
            # A changed expected UID is synthetic mismatch evidence, not a cross-user run.
            uid = os.getuid()
            with self.subTest(target=target.name, case="synthetic-uid"), patch.object(os, "getuid", return_value=uid + 1):
                with self.assertRaises(ValueError) as caught:
                    function(argument)
                self.assertEqual(str(caught.exception), message)
            alias = self.root / ("alias-" + target.name)
            alias.symlink_to(target)
            aliased = dict(argument)
            aliased["inbox_recall_binding" if target == self.path else "path"] = str(alias)
            result = function(aliased)
            self.assertEqual(result[0], alias)
            observe("binding-file-policy", target.name, real_uid=uid,
                    synthetic_expected_uid=uid + 1, synthetic_uid_error=message, symlink_followed=True)
        # Configuration pins permit read access to other users; manifests do not.
        path = self.root / "memory.json"
        path.chmod(0o644)
        self.assertEqual(binding_module._checked_file(self.manifest["memory_config"])[1], path.read_bytes())

    def test_disabled_snapshot_only_refuses_when_enabled_is_required(self):
        self.manifest["enabled"] = False
        self.save_manifest()
        engine, memory = self.policy.validate(self.memory)
        self.assertEqual((engine.INBOX_RECALL_VERSION, memory), (1, self.memory))
        with self.assertRaises(ValueError) as caught:
            self.policy.validate(self.memory, require_enabled=True)
        self.assertEqual(str(caught.exception), "inbox recall binding is disabled")
        observe("binding-enabled", False, validate="accepted",
                require_enabled_error=str(caught.exception))

    def test_legacy_profile_controls_remain_permissive(self):
        original_memory, original_coordination = copy.copy(self.memory), copy.copy(self.coordination)
        for name, profile, key, value in (
                ("minimum-budget", "memory", "context_bytes", 1000),
                ("truthy-native-delivery", "coordination", "native_delivery", "truthy legacy value"),
                ("relative-memory-home", "memory", "config_home", "relative")):
            self.memory, self.coordination = copy.copy(original_memory), copy.copy(original_coordination)
            (self.memory if profile == "memory" else self.coordination)[key] = value
            self.write_profiles()
            engine, memory = self.policy.validate(self.memory)
            self.assertEqual((engine.INBOX_RECALL_VERSION, memory), (1, self.memory))
            self.assertFalse(Path(self.memory["token_file"]).exists())
            self.assertFalse(Path(self.memory["state_dir"]).exists())
            self.assertFalse(Path(self.coordination["state_dir"]).exists())
            observe("binding-profile", name, result="accepted", memory=memory,
                    coordination=self.coordination, token_exists=False)

    def test_coordinates_wake_ambiguity_precedes_guards_and_missing_home_is_wildcard(self):
        event = dict(hook_event_name="UserPromptSubmit", session_id=SESSION, cwd=str(self.root))
        other = self.root / "other-coordination.json"
        other.write_text(json.dumps(dict(self.coordination, state_dir=str(self.root / "other-state"))))
        other.chmod(0o600)
        self.manifest["coordination_configs"].append(dict(
            path=str(other), sha256=hashlib.sha256(other.read_bytes()).hexdigest()))
        self.save_manifest()
        (self.root / ".cairn-no-coordination").touch()
        environment = dict(CODEX_HOME=str(self.root / "unrelated-home"), CAIRN_COORDINATION_DISABLED="1")
        with patch.dict(os.environ, environment, clear=True), self.assertRaises(ValueError) as caught:
            self.policy.coordinates_wake(self.memory, event)
        self.assertEqual(str(caught.exception), "ambiguous coordination profile")
        observe("coordinates-wake", "ambiguity-before-disabled-and-privacy", error=str(caught.exception),
                environment=environment)
        (self.root / ".cairn-no-coordination").unlink()
        self.write_profiles()
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "unrelated-home")}, clear=True):
            self.assertIs(self.policy.coordinates_wake(self.memory, event), True)
            self.coordination["config_home"] = str(self.root / "specific-home")
            self.write_profiles()
            self.assertIs(self.policy.coordinates_wake(self.memory, event), False)
        observe("coordinates-wake", "missing-config-home-is-wildcard", no_home=True, unmatched_home=False)

    def test_activation_prefix_claude_owner_codex_uuid_and_memory_config(self):
        event = dict(hook_event_name="UserPromptSubmit", session_id=SESSION, turn_id=TURN,
                     cwd=str(self.root), prompt_id="fixture-prompt")
        claude = dict(self.memory, harness="claude")
        for owner, error, message in (
                (None, ValueError, "inbox recall requires actual Claude prompt ownership"),
                ("", ValueError, "inbox recall requires actual Claude prompt ownership"),
                ("\ud800", UnicodeEncodeError, None)):
            with self.subTest(owner=repr(owner)), self.assertRaises(error) as caught:
                self.policy.activation_decision(claude, dict(event, prompt_id=owner), True)
            if message:
                self.assertEqual(str(caught.exception), message)
            observe("activation-prefix", ["claude-owner", owner], error_class=type(caught.exception).__name__,
                    error=str(caught.exception), state_written=False)
        for changed in (dict(event, session_id="not-a-uuid"), dict(event, turn_id="not-a-uuid"),
                        dict(event, turn_id="\ud800"), dict(event, session_id="{" + SESSION + "}"),
                        dict(event, turn_id="{" + TURN + "}"),
                        dict(event, session_id="ABCDEF01-2345-4678-9ABC-DEF012345678")):
            self.assertIs(self.policy.activation_decision(self.memory, changed, True), True)
            observe("activation-prefix", ["codex-invalid-uuid", changed], enabled=True, state_written=False)
        for session in ("not-a-uuid", "{" + SESSION + "}"):
            for enabled in (False, True):
                self.assertIs(self.policy.activation_decision(claude, dict(event, session_id=session), enabled), enabled)
                observe("activation-prefix", ["claude-invalid-uuid", session], enabled=enabled, state_written=False)
        marker = self.root / ".cairn-no-memory"
        marker.touch()
        self.path.unlink()
        for enabled in (False, True):
            self.assertIs(self.policy.activation_decision(self.memory, event, enabled), enabled)
            observe("activation-prefix", "memory-marker-before-manifest", enabled=enabled, state_written=False)
        marker.unlink()
        for config, changed in ((dict(self.memory, harness="opencode"), event),
                                (self.memory, dict(event, hook_event_name="SessionStart"))):
            for enabled in (False, True):
                self.assertIs(self.policy.activation_decision(config, changed, enabled), enabled)
                observe("activation-prefix", "harness-or-event-before-manifest", config=config,
                        event=changed, enabled=enabled, state_written=False)
        self.save_manifest()
        for name in ("relative-path", "writable-config", "synthetic-uid", "missing-state-dir",
                     "nonstring-state-dir", "nonobject-memory"):
            self.write_profiles()
            expected = "activation ledger configuration is not owner-controlled"
            if name == "relative-path":
                self.manifest["memory_config"]["path"] = "relative.json"
                self.save_manifest()
            elif name == "writable-config":
                (self.root / "memory.json").chmod(0o622)
            elif name in ("missing-state-dir", "nonstring-state-dir", "nonobject-memory"):
                value = dict(self.memory)
                value.pop("state_dir")
                if name == "nonstring-state-dir":
                    value["state_dir"] = 1
                elif name == "nonobject-memory":
                    value = []
                (self.root / "memory.json").write_text(json.dumps(value))
                expected = "activation ledger configuration lacks a state directory"
            uid = os.getuid()
            with self.subTest(case=name):
                with patch.object(os, "getuid", return_value=uid + (name == "synthetic-uid")):
                    with self.assertRaises(ValueError) as caught:
                        self.policy.activation_decision(self.memory, event, True)
                self.assertEqual(str(caught.exception), expected)
                observe("activation-prefix", name, error_class="ValueError", error=expected,
                        synthetic_expected_uid=uid + 1 if name == "synthetic-uid" else None,
                        state_written=False)
        self.assertFalse(Path(self.memory["state_dir"]).exists())
        self.assertFalse(Path(self.memory["token_file"]).exists())

    def test_disabled_staging_reads_only_its_selected_prefix_and_no_token(self):
        self.manifest["enabled"] = False
        self.manifest["engine"]["path"] = str(self.root / "absent-engine.py")
        self.manifest["memory_config"]["sha256"] = "0" * 64
        self.manifest["coordination_configs"] = [
            dict(path=str(self.root / "absent-coordination.json"), sha256="0" * 64)]
        self.save_manifest()
        state = Path(self.memory["state_dir"])
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        for wake in (False, True):
            payload = dict(config=self.memory, event=dict(session_id=SESSION, turn_id=TURN,
                           cwd=str(self.root), hook_event_name="UserPromptSubmit" if wake else "SessionStart"),
                           wake=wake)
            child = subprocess.Popen([sys.executable, "-c", PREFIX_AUDIT_CHILD, str(SOURCE)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, start_new_session=True)
            self.addCleanup(stop_owned_child, child)
            stdout, stderr = child.communicate(json.dumps(payload), timeout=3)
            self.assertEqual((child.returncode, stderr), (0, ""))
            result = json.loads(stdout)
            self.assertIs(result["resolved_has_binding"], False)
            self.assertIs(result["original_has_binding"], True)
            opened = [row[0] for row in result["opens"]]
            self.assertNotIn(self.memory["token_file"], opened)
            self.assertNotIn(self.manifest["engine"]["path"], opened)
            self.assertNotIn(self.manifest["coordination_configs"][0]["path"], opened)
            if wake:
                self.assertIn(self.manifest["memory_config"]["path"], opened)
                ledger = json.loads((state / (SESSION + ".memory-budget.json")).read_text())
                self.assertIs(ledger["inbox_activation"][TURN], False)
            else:
                self.assertEqual(opened, [str(self.path)])
                self.assertEqual(list(state.iterdir()), [])
            observe("disabled-prefix-open-audit", wake, input=payload, **result, token_opened=False)


PREFIX_AUDIT_CHILD = r'''
import importlib.util,json,sys
from pathlib import Path
source=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("prefix_bundle",source/"lifecycle_bundle.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
bundle=module.Bundle(source)
policy=bundle.load("inbox_binding").InboxBinding(bundle)
payload=json.load(sys.stdin)
opens=[]
def audit(name,args):
    if name=="open" and isinstance(args[0],str):
        opens.append([args[0],args[1],args[2]])
sys.addaudithook(audit)
config=payload["config"]
resolved=policy.effective_config(config,payload["event"],lambda event:payload["wake"])
print(json.dumps(dict(resolved_has_binding="inbox_recall_binding" in resolved,
                      original_has_binding="inbox_recall_binding" in config,opens=opens)))
'''


def binding_refusal_cases():
    owner = "inbox recall binding must be owner-only"
    config = "inbox recall requires owner-controlled absolute paths"
    profile = "incompatible inbox recall profile binding"
    rows = [
        ("manifest-missing", FileNotFoundError, None),
        ("manifest-relative", ValueError, owner),
        ("manifest-invalid-json", json.JSONDecodeError, "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"),
        ("manifest-null", AttributeError, "'NoneType' object has no attribute 'get'"),
        ("manifest-array", AttributeError, "'list' object has no attribute 'get'"),
        ("manifest-string", AttributeError, "'str' object has no attribute 'get'"),
        ("schema-1", ValueError, "unknown inbox recall binding"),
        ("schema-3", ValueError, "unknown inbox recall binding"),
        ("schema-missing", ValueError, "unknown inbox recall binding"),
        ("enabled-integer", ValueError, "unknown inbox recall binding"),
        ("enabled-string", ValueError, "unknown inbox recall binding"),
        ("enabled-null", ValueError, "unknown inbox recall binding"),
        ("config-relative", ValueError, config),
        ("config-group-writable", ValueError, config),
        ("config-other-writable", ValueError, config),
        ("config-missing", FileNotFoundError, None),
        ("config-changed", ValueError, "inbox recall binding changed"),
        ("coordinators-0", ValueError, "one to eight explicit coordination configurations required"),
        ("coordinators-9", ValueError, "one to eight explicit coordination configurations required"),
        ("homes-duplicate", ValueError, "shared memory binding requires distinct explicit configuration homes"),
        ("homes-missing", ValueError, "shared memory binding requires distinct explicit configuration homes"),
        ("current-not-in-profiles", ValueError, profile),
        ("relative-memory-state", ValueError, profile),
        ("relative-coordination-state", ValueError, profile),
        ("shared-state", ValueError, profile),
        ("relative-coordination-home", ValueError, profile),
        ("recall-mode", ValueError, profile),
        ("native-delivery-false", ValueError, profile),
        ("budget-999", ValueError, profile),
        ("budget-9501", ValueError, profile),
        ("budget-bool", ValueError, profile),
        ("budget-float", ValueError, profile),
        ("engine-without-contract", ValueError, "pinned memory engine lacks the inbox recall contract"),
        ("incomplete-bundle", ValueError, "invalid lifecycle bundle descriptor"),
    ]
    rows += [("manifest-mode-" + oct(bit), ValueError, owner)
             for bit in (0o040, 0o020, 0o010, 0o004, 0o002, 0o001)]
    rows += [("field-" + key, ValueError, profile)
             for key in ("harness", "cairn", "socket", "token_file", "repo", "inbox_recall_binding")]
    result = [dict(name=name, exception=error, message=message) for name, error, message in rows]
    result += [dict(name=name, exception=ValueError, message="activation ledger configuration lacks a state directory", prefix=True)
               for name in ("prefix-missing-state-dir", "prefix-nonstring-state-dir")]
    return result


def configure_binding_refusal(case, name):
    """Only this fixture's files and descriptors are changed; reference code stays intact."""
    if name == "manifest-missing":
        case.path.unlink()
    elif name == "manifest-relative":
        case.memory["inbox_recall_binding"] = case.coordination["inbox_recall_binding"] = "relative.json"
        case.write_profiles()
    elif name.startswith("manifest-mode-"):
        case.path.chmod(0o600 | int(name.removeprefix("manifest-mode-"), 8))
    elif name.startswith("manifest-"):
        value = {"manifest-invalid-json": "{", "manifest-null": "null",
                 "manifest-array": "[]", "manifest-string": '"text"'}[name]
        case.path.write_text(value)
    elif name.startswith("schema-"):
        if name == "schema-missing":
            case.manifest.pop("schema")
        else:
            case.manifest["schema"] = "cairn.inbox-recall-binding/" + name[-1]
        case.save_manifest()
    elif name.startswith("enabled-"):
        case.manifest["enabled"] = {"enabled-integer": 1, "enabled-string": "true", "enabled-null": None}[name]
        case.save_manifest()
    elif name == "config-relative":
        case.manifest["memory_config"]["path"] = "relative.json"
        case.save_manifest()
    elif name in ("config-group-writable", "config-other-writable"):
        (case.root / "memory.json").chmod(0o620 if name == "config-group-writable" else 0o602)
    elif name == "config-missing":
        (case.root / "memory.json").unlink()
    elif name == "config-changed":
        path = case.root / "memory.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif name.startswith("coordinators-"):
        case.manifest["coordination_configs"] *= int(name.rsplit("-", 1)[1])
        case.save_manifest()
    elif name in ("homes-duplicate", "homes-missing"):
        case.coordination["config_home"] = str(case.root / "shared-home")
        case.write_profiles()
        other = dict(case.coordination, state_dir=str(case.root / "coordination-two"))
        if name == "homes-missing":
            other.pop("config_home")
        path = case.root / "coordination-two.json"
        path.write_text(json.dumps(other))
        path.chmod(0o600)
        case.manifest["coordination_configs"].append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        case.save_manifest()
    elif name == "current-not-in-profiles":
        case.memory["unlisted_current_configuration"] = True
    elif name.startswith("field-"):
        key = name.removeprefix("field-")
        case.coordination[key] = str(case.root / ("different-" + key)) if key not in ("harness", "repo") else "different-fixture"
        case.write_profiles()
    elif name in ("relative-memory-state", "relative-coordination-state", "shared-state",
                  "relative-coordination-home", "recall-mode", "native-delivery-false") or name.startswith("budget-"):
        if name == "relative-memory-state":
            case.memory["state_dir"] = "relative"
        elif name == "relative-coordination-state":
            case.coordination["state_dir"] = "relative"
        elif name == "shared-state":
            case.memory["state_dir"] = case.coordination["state_dir"]
        elif name == "relative-coordination-home":
            case.coordination["config_home"] = "relative"
        elif name == "recall-mode":
            case.memory["recall_mode"] = "automatic"
        elif name == "native-delivery-false":
            case.coordination["native_delivery"] = False
        else:
            case.memory["context_bytes"] = {"budget-999": 999, "budget-9501": 9501,
                                           "budget-bool": True, "budget-float": 1000.0}[name]
        case.write_profiles()
    elif name == "engine-without-contract":
        root = case.root / "engine-without-contract"
        bundle_module.stage_runtime(SOURCE, root)
        engine = root / "memory.py"
        engine.write_text("INBOX_RECALL_VERSION = 0\n")
        case.manifest["engine"] = dict(path=str(engine), sha256=hashlib.sha256(engine.read_bytes()).hexdigest(),
                                       bundle=bundle_module.bundle_descriptor(root))
        case.save_manifest()
    elif name == "incomplete-bundle":
        case.manifest["bridge"]["bundle"]["files"].pop("native_session.py")
        case.save_manifest()
    elif name.startswith("prefix-"):
        value = dict(case.memory)
        value.pop("state_dir")
        if name == "prefix-nonstring-state-dir":
            value["state_dir"] = 1
        (case.root / "memory.json").write_text(json.dumps(value))
    else:
        raise AssertionError("unknown characterization trigger: " + name)


class PublicHookCharacterizationTests(unittest.TestCase):
    def test_public_skip_invalid_identity_and_event_streams_without_api_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            called = root / "unexpected-api-call"
            cli = root / "fixture-cairn"
            cli.write_text(f"#!{sys.executable}\nfrom pathlib import Path\n"
                           f"Path({str(called)!r}).touch()\nraise SystemExit(91)\n")
            cli.chmod(0o700)
            for role, harnesses in (("memory", ("claude", "codex", "opencode", "hermes")),
                                    ("coordination", ("claude", "codex", "opencode", "agy", "hermes"))):
                for harness in harnesses:
                    config = dict(harness=harness, cairn=str(cli), socket=str(root / "unused.sock"),
                                  token_file=str(root / "never-open-token"), repo="fixture",
                                  state_dir=str(root / "state"), binding="fixture")
                    config_path = root / "config.json"
                    config_path.write_text(json.dumps(config))
                    config_path.chmod(0o600)
                    for scenario in ("disabled", "invalid-identity", "unsupported-event"):
                        with self.subTest(role=role, harness=harness, scenario=scenario):
                            event = dict(session_id="ses_Fixture" if harness == "opencode" else SESSION,
                                         conversationId=SESSION, workspacePaths=[str(root)], cwd=str(root),
                                         hook_event_name="SessionStart")
                            environment = dict(os.environ)
                            for key in ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD",
                                        "CAIRN_COORDINATION_DISABLED", "CAIRN_WAKE_CONTEXT"):
                                environment.pop(key, None)
                            if scenario == "disabled":
                                environment["CAIRN_LIFECYCLE_DISABLED"] = "1"
                            elif scenario == "invalid-identity":
                                event.update(session_id="", conversationId="")
                            else:
                                event["hook_event_name"] = "Unknown"
                            args = [sys.executable, str(SOURCE / (role + ".py"))]
                            if role == "coordination":
                                args.append("hook")
                            args += ["--config", str(config_path)]
                            child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                     stderr=subprocess.PIPE, text=True, env=environment,
                                                     start_new_session=True)
                            self.addCleanup(stop_owned_child, child)
                            stdout, stderr = child.communicate(json.dumps(event), timeout=3)
                            expected = (0, "{}\n", "")
                            if scenario == "invalid-identity":
                                if role == "memory":
                                    expected = (1, "", "Cairn lifecycle diagnostic: phase=handle error_class=HookError\n"
                                                "Cairn lifecycle: missing or invalid host session identity; "
                                                "use native tools or an explicit handoff.\n")
                                else:
                                    expected = (1, "", "Cairn coordination: INVALID_HOST: native conversation ID required; "
                                                "session presence unavailable\n")
                            elif scenario == "unsupported-event" and role == "coordination":
                                expected = (1, "", "Cairn coordination: INVALID_HOST: unsupported coordination hook event; "
                                            "session presence unavailable\n")
                            self.assertEqual((child.returncode, stdout, stderr), expected)
                            self.assertFalse(called.exists())
                            self.assertFalse((root / "state").exists())
                            self.assertFalse((root / "never-open-token").exists())
                            observe("public-hook", [role, harness, scenario], event=event,
                                    config=config, code=child.returncode, stdout=stdout, stderr=stderr,
                                    api_called=False, child_pid=child.pid)


PEER_CHILD = r'''
import importlib.util,json,sys
from pathlib import Path
source,endpoint,output,parent=sys.argv[1:]
spec=importlib.util.spec_from_file_location("child_bundle",Path(source)/"lifecycle_bundle.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
bundle=module.Bundle(source);peer=bundle.load("unix_peer_transport");coord=bundle.load("coordination")
parent=json.loads(parent)
ancestry=[coord.caller_descends_from(parent),
          coord.caller_descends_from(dict(parent,start=parent["start"]+1)),
          coord.caller_descends_from(dict(parent,boot="synthetic-other-boot"))]
server=peer.bind_private_socket(endpoint,accept_timeout=3)
print(json.dumps(dict(ancestry=ancestry)),flush=True)
received=[]
for _ in range(6):
    connection,_=server.accept()
    with connection:
        connection.settimeout(3)
        raw=connection.recv(128);received.append(raw.hex())
        if raw:connection.sendall(b'{"ok":true}\n')
Path(output).write_text(json.dumps(received))
server.close()
'''


class PeerCharacterizationTests(unittest.TestCase):
    def test_dead_target_between_connect_and_identity_maps_to_endpoint_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint = root / "peer.sock"
            child = subprocess.Popen(
                [sys.executable, "-c", PEER_CHILD, str(SOURCE), str(endpoint),
                 str(root / "unused.json"), json.dumps(peer.process_reference(os.getpid()))],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            self.addCleanup(stop_owned_child, child)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(3))
            json.loads(child.stdout.readline())
            reference = peer.process_reference(child.pid)
            def measure_after_death(pid):
                self.assertEqual(pid, child.pid)
                stop_owned_child(child)
                return peer.process_identity_tuple(pid)
            with self.assertRaises(RuntimeError) as caught:
                peer.open_verified_socket(str(endpoint), reference, identity_reader=measure_after_death)
            self.assertEqual(str(caught.exception), "native queue endpoint is unavailable")
            self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
            observe("dead-target", "after-connect-before-measurement", target=reference,
                    error_class="RuntimeError", error=str(caught.exception),
                    cause=type(caught.exception.__cause__).__name__, actual_pid_reuse=False)

    def test_real_child_peer_modes_ancestry_and_prewrite_refusals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint, output = root / "peer.sock", root / "received.json"
            parent = peer.process_reference(os.getpid())
            child = subprocess.Popen([sys.executable, "-c", PEER_CHILD, str(SOURCE),
                                      str(endpoint), str(output), json.dumps(parent)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     start_new_session=True)
            self.addCleanup(stop_owned_child, child)
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(3), "owned peer did not become ready")
            ready = json.loads(child.stdout.readline())
            self.assertEqual(ready["ancestry"], [True, False, False])
            reference = peer.process_reference(child.pid)
            transport, identity = peer.open_verified_socket(str(endpoint), reference, return_identity=True)
            with transport:
                self.assertEqual(identity, {k: reference[k] for k in ("pid", "start", "boot")})
                self.assertEqual(peer.exchange_json_line(transport, {"case": "process"}, timeout=1), {"ok": True})
            for changed, message in ((dict(reference, pid=os.getpid()), "native socket belongs to a different process"),
                                     (dict(reference, start=reference["start"] + 1), "native process identity has changed"),
                                     (dict(reference, boot="synthetic-other-boot"), "native process identity has changed")):
                with self.subTest(reference=changed), self.assertRaises(RuntimeError) as caught:
                    peer.open_verified_socket(str(endpoint), changed, timeout=1)
                self.assertEqual(str(caught.exception), message)
            # Same-user ownership permits a peer PID different from the measured target.
            transport, identity = peer.open_verified_socket(str(endpoint), parent, owner="uid", return_identity=True)
            with transport:
                self.assertEqual(identity["pid"], child.pid)
                self.assertEqual(peer.exchange_json_line(transport, {"case": "uid"}, timeout=1), {"ok": True})
            with socket.socket(socket.AF_UNIX) as transport:
                transport.connect(str(endpoint))
                actual_credentials = peer.peer_credentials(transport)
                with self.assertRaises(RuntimeError) as caught:
                    peer.verify_peer_credentials(transport, reference, expected_uid=os.geteuid() + 1)
                self.assertEqual(str(caught.exception), "native socket belongs to another user")
            stdout, stderr = child.communicate(timeout=3)
            self.assertEqual((child.returncode, stdout, stderr), (0, "", ""))
            self.assertEqual(json.loads(output.read_text()),
                             [b'{"case": "process"}\n'.hex(), "", "", "", b'{"case": "uid"}\n'.hex(), ""])
            observe("real-child-peer", "process-and-uid-modes", parent=parent, child=reference,
                    credentials=actual_credentials, ancestry=ready["ancestry"],
                    received_hex=json.loads(output.read_text()),
                    synthetic_mismatches=["claimed pid", "start", "boot", "expected uid"],
                    actual_cross_user_or_pid_reuse=False)

    def test_real_zombie_is_not_alive_and_reaped_child_stays_unavailable(self):
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 start_new_session=True)
        self.addCleanup(stop_owned_child, child)
        reference = peer.process_reference(child.pid)
        child.stdin.write(b"x")
        child.stdin.close()
        child.stdin = None
        deadline = time.monotonic() + 3
        zombie = None
        # Do not poll/wait before measuring Z: either would reap the owned child.
        while time.monotonic() < deadline:
            current = peer.process_reference(child.pid)
            if current["state"] == "Z":
                zombie = current
                break
            time.sleep(0.002)
        self.assertIsNotNone(zombie, "owned child did not reach observable zombie state")
        self.assertEqual([zombie[k] for k in ("pid", "start", "boot")],
                         [reference[k] for k in ("pid", "start", "boot")])
        self.assertFalse(peer.process_alive(reference))
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertFalse(peer.process_alive(reference))
        observe("real-child-process", "zombie-then-reaped", live=reference, zombie=zombie,
                zombie_alive=False, reaped_alive=False, actual_pid_reuse=False)

    def test_frame_eof_encoding_extra_bytes_and_timeout_policies(self):
        cases = [
            ("empty", b"", True, 64, EOFError, "connection closed before replying", None),
            ("missing-newline", b"{}", True, 64, EOFError, "connection closed before replying", None),
            ("allowed-eof", b"{}", False, 64, None, None, {}),
            ("invalid-utf8", b"\xff\n", True, 64, UnicodeDecodeError, None, None),
            ("invalid-json", b"{\n", True, 64, json.JSONDecodeError, None, None),
            ("extra-json-before-newline", b"{}{}\n", True, 64, json.JSONDecodeError, None, None),
            ("second-line-ignored", b"{}\n[]\n", True, 64, None, None, {}),
            ("newline-counts-in-limit", b"{}\n", True, 2, ValueError, "reply exceeds the line limit", None),
            ("exact-limit", b"{}\n", True, 3, None, None, {}),
            ("trailing-chunk-counts", b"{}\nxxxx", True, 3, ValueError, "reply exceeds the line limit", None),
        ]
        for name, raw, newline, limit, error, message, expected in cases:
            left, right = socket.socketpair()
            with self.subTest(case=name), left, right:
                right.sendall(raw)
                right.shutdown(socket.SHUT_WR)
                if error:
                    with self.assertRaises(error) as caught:
                        peer.recv_json_line(left, timeout=1, max_bytes=limit, require_newline=newline)
                    if message:
                        self.assertEqual(str(caught.exception), message)
                    result = dict(error_class=type(caught.exception).__name__, error=str(caught.exception))
                else:
                    value = peer.recv_json_line(left, timeout=1, max_bytes=limit, require_newline=newline)
                    self.assertEqual(value, expected)
                    result = dict(value=value)
                observe("framing", name, input_hex=raw.hex(), require_newline=newline,
                        max_bytes=limit, **result)
        self.assertEqual(peer.format_json_line({}, max_bytes=2), b"{}\n")
        left, right = socket.socketpair()
        with left, right:
            right.sendall(b"{")
            with self.assertRaises(socket.timeout) as caught:
                peer.recv_json_line(left, timeout=0.03)
            self.assertEqual(str(caught.exception), "reply timed out")
            observe("framing", "partial-response-timeout", input_hex=b"{".hex(),
                    error_class=type(caught.exception).__name__, error=str(caught.exception))


if __name__ == "__main__":
    unittest.main()
