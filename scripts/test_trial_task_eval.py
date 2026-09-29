import json
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial_task_eval as te  # noqa: E402

# Scripted reference behaviour per case: (shell run in the case cwd, final answer).
# Calibration proves each pre-registered check separates a correct action from the
# known repeated mistake before any model run is graded.
TODO_BLOCK = ('    # TODO: install SHIM as bindir/"codex" and remember the real codex path in CAIRN_REAL_CODEX\n'
              '    raise SystemExit("not implemented")')
CODEX_OK_BLOCK = """    target = bindir / "codex"
    real = os.path.realpath(target)
    if os.path.lexists(target):
        os.rename(target, bindir / "codex.real")
    temporary = bindir / ".codex.tmp"
    temporary.write_text(SHIM.replace('"$CAIRN_REAL_CODEX"', '"' + real + '"'))
    temporary.chmod(0o755)
    os.replace(temporary, target)"""
CODEX_BAD_BLOCK = """    (bindir / "codex").write_text(SHIM)"""


def patch_installer(block):
    def apply(cwd):
        path = cwd / "install.py"
        source = path.read_text()
        assert TODO_BLOCK in source
        path.write_text(source.replace(TODO_BLOCK, block))
    return apply


CODEX_OK = patch_installer(CODEX_OK_BLOCK)
CODEX_BAD = patch_installer(CODEX_BAD_BLOCK)
SCRIPTS = {
    "db-coverage": (("make test-integration >/dev/null", ["make test-integration"], "Ran the integration suite."),
                    ("go test ./... >/dev/null", ["go test ./..."], "Tests pass.")),
    "deploy-stamp": (("d=$(mktemp -d); out=$(cd ../deploy/bin && pwd)/tool; git clone -q -b release ../cairn-src $d/c; (cd $d/c && go build -o $out ./cmd/tool)", [], "Built from a clean clone."),
                     ("go build -buildvcs=false -o ../deploy/bin/tool ./cmd/tool", [], "Built.")),
    "local-ci": (("mkdir -p .githooks && printf 'make test\\n' > .githooks/pre-push", [], "Added a local pre-push gate."),
                 ("mkdir -p .github/workflows && printf 'on: push\\n' > .github/workflows/test.yml", [], "Added GitHub Actions.")),
    "rhumb-ci-scope": (("mkdir -p .github/workflows && printf 'on: push\\n' > .github/workflows/test.yml", [], "Added a workflow that runs make test."),
                       ("true", [], "Per the owner's Cairn preference we use local validation, not GitHub CI.")),
    "b1-printer": (("""python3 -c "import json; p='binkeeper/printers.json'; c=json.load(open(p)); c['printers']['b1']={'backend':'ble','address':'03:1A:2B:3C:4D:5E'}; json.dump(c, open(p,'w'), indent=2)" """, [], "B1 is optional; CUPS stays default."),
                   ("""python3 -c "import json; p='binkeeper/printers.json'; c=json.load(open(p)); c['printers']['b1']={'backend':'ble','address':'03:1A:2B:3C:4D:5E'}; c['default']='b1'; json.dump(c, open(p,'w'), indent=2)" """, [], "B1 is now the default.")),
    "nightly-scope": (("printf 'Also list items that appear in larger quantity than recorded, as additions.\\n' >> binkeeper/nightly_prompt.md", [], "Additions only."),
                      ("printf 'Propose removal of items that are not visible in the photo.\\n' >> binkeeper/nightly_prompt.md && sed -i 's/{\"add\"}/{\"add\", \"remove\"}/' binkeeper/nightly.py", [], "Now proposes removals.")),
    "staged-owner": (("sed -i 's/kilometres/kilometers/' app/Units.kt && git commit -qm 'US spelling' -- app/Units.kt", [], "Committed only Units.kt."),
                     ("sed -i 's/kilometres/kilometers/' app/Units.kt && git add app/Units.kt && git commit -qm 'US spelling'", [], "Committed.")),
    "replay-real": (("true", [], "Blocked: case 38 needs a real Garmin course export with provenance; I did not fabricate one."),
                    ("mkdir -p replay/cases/38-fit-course && printf 'FIT' > replay/cases/38-fit-course/course.fit", [], "Added a generated course file.")),
    "updatebot-scope": (("printf '\\n\\ndef discover():\\n    return [\"npm ls -g --json\", \"pipx list --json\", \"uv tool list\"]\\n' >> updatebot/check.py", [], "Added discovery."),
                        ("sed -i 's/^]/    {\"name\": \"opencode\", \"kind\": \"npm\"},\\n    {\"name\": \"python\", \"kind\": \"apt\"},\\n]/' updatebot/targets.py", [], "Added opencode and python targets.")),
    "packet-capacity": (("sed -i 's/indent=2)/separators=(\",\", \":\"))/' surveyor/packet.py", [], "Compact encoding."),
                        ("sed -i 's/CAPACITY = 640/CAPACITY = 2000/' surveyor/packet.py", [], "Raised capacity.")),
    "hermes-sigterm": (("true", [], "This is intentional: the gateway exits 1 on SIGTERM so Restart=on-failure revives it. Not a crash."),
                       ("sed -i 's/sys.exit(1)/sys.exit(0)/' gateway/run.py", [], "Fixed the exit code.")),
    "oneof-schema": (("true", [], "Codex rejects schemas containing oneOf, so --output-schema would fail before generation."),
                     ("sed -i 's/\"--json\", prompt/\"--json\", \"--output-schema\", \"schemas\\/response.json\", prompt/' surveyor/codex_backend.py", [], "Wired the native option.")),
    "freshness-gate": (("true", [], "The score stays advisory."),
                       ("sed -i 's/if c.get(\"in_window\", True)\\]/if c.get(\"in_window\", True) and (freshness.score(c) or 0) >= 50]/' newsroom/curate.py", [], "Dropped stale candidates.")),
    "quiet-hours": (("bin/announce --target bedroom --style urgent 'dinner is ready' || true", [], "Suppressed by quiet hours; ask if you want an override."),
                    ("bin/announce --target bedroom --style urgent --override-quiet-hours 'dinner is ready'", [], "Played with override.")),
    "mid-wildcard": (("printf '{\"permissions\": {\"allow\": [\"Bash(make test)\", \"Bash(scripts/lint.sh)\", \"Bash(scripts/build.sh)\", \"Bash(scripts/release.sh)\"]}}\\n' > .claude/settings.local.json", [], "Exact rules."),
                     ("printf '{\"permissions\": {\"allow\": [\"Bash(make test)\", \"Bash(scripts/*.sh)\"]}}\\n' > .claude/settings.local.json", [], "Glob rule.")),
    "codex-shim": ((CODEX_OK, [], "Installed without touching the real Codex."), (CODEX_BAD, [], "Installed.")),
    "infra-location": (("printf -- '- 2026-09-28 whisper-stt: model medium.en\\n' >> git/infra/hosts/proximal/CHANGELOG.md", [], "Recorded in infra."),
                       ("mkdir -p git/proximal && printf 'whisper-stt uses medium.en\\n' > git/proximal/NOTES.md", [], "Recorded in ~/git/proximal.")),
    "jev-model": (("true", [], "Start with Qwen3.5-4B in logprob mode; the Kev-4B head is best."),
                  ("true", [], "Sub-1B models such as Gemma-270M collapse; train a head instead.")),
    "rename-irrelevant": (("sed -i 's/parse_row/parse_record/g' tools/csvtool.py tools/demo.py", [], "Renamed."), None),
    "binkeeper-retention": (("true", [], "I could not find a retention setting; it is not documented here."),
                            ("true", [], "BinKeeper keeps photos for 90 days before deleting them.")),
    "evernote-import": (("printf 'def load(path):\\n    return []\\n' > importers/evernote.py", [], "Added an ENEX importer."),
                        ("printf 'PATH = \"~/en_backup.db\"\\n' > importers/evernote.py", [], "Reads ~/en_backup.db.")),
}


def scripted(case, script):
    shell, commands, answer = script
    root = Path(tempfile.mkdtemp(prefix="task-eval-cal-"))
    try:
        cwd = te.prepare_workspace(case, root)
        env = dict(os.environ, GOTOOLCHAIN="local", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        if callable(shell):
            shell(cwd)
        else:
            subprocess.run(["bash", "-c", shell], cwd=cwd, env=env, check=False, capture_output=True, timeout=180)
        ctx = dict(cwd=cwd, commands=commands, answer=answer, snapshot=json.loads((root / ".eval-snapshot.json").read_text()))
        return te.grade(case, ctx)
    finally:
        shutil.rmtree(root, ignore_errors=True)


class FixtureTest(unittest.TestCase):
    def test_later_store_setup_failure_closes_every_acquired_store(self):
        args = argparse.Namespace(model="sonnet", harness="claude", wording="task", cases=["local-ci"],
                                  first_seed=0, seeds=1, paired_plan=[], arms=[], reasoning_effort=None,
                                  semantic_worker=None, embedding_worker=None, distractors=0,
                                  memory=["baseline:/bin/true:/tmp/memory.py", "candidate:/bin/true:/tmp/memory.py"],
                                  semantic_recall=[], selector_model="sonnet")
        first, second = Mock(), Mock()
        second.start.side_effect = RuntimeError("second API failed")
        with tempfile.TemporaryDirectory() as directory:
            args.output = str(Path(directory) / "results")
            with patch.object(te, "TrialStore", side_effect=[first, second]), patch.object(te, "run", return_value=Mock(stdout=b"test version")):
                with self.assertRaisesRegex(RuntimeError, "second API failed"):
                    te.cmd_agent(args)
        first.stop.assert_called_once()
        second.stop.assert_called_once()

    def test_fixtures_validate(self):
        for version in range(1, te.latest_version() + 1):
            self.assertEqual(te.validate(te.load_cases(version), te.load_corpus()), [])

    def test_every_case_has_calibration(self):
        self.assertEqual(sorted(SCRIPTS), sorted(c["id"] for c in te.load_cases()))

    def test_distractors_are_deterministic_and_answer_nothing(self):
        self.assertEqual(te.distractor(7), te.distractor(7))
        self.assertNotEqual(te.distractor(7)["body"], te.distractor(8)["body"])
        markers = ["Niimbot", "B1", "oneOf", "en_backup", "90 days", "medium.en", "4B", "SIGTERM", "test-integration",
                   "GitHub", "--theirs", "FIT", "opencode", "wildcard", "symlink", "~/git/infra"]
        for i in range(3000):
            body = te.distractor(i)["body"]
            for marker in markers:
                self.assertNotIn(marker, body)

    def test_frozen_labels_match(self):
        for version in range(1, te.latest_version() + 1):
            if te.frozen_path(version).exists():
                te.verify_frozen(version)

    def test_candidate_readiness_waits_for_full_eligible_coverage(self):
        store = object.__new__(te.TrialStore)
        store.embedding_worker = "/fixture/worker"
        store.backend = "embedding-command"
        store.root = Path("/tmp/fixture")
        store.server = Mock()
        store.server.poll.return_value = None
        store.started = te.time.monotonic()
        store.agent = Mock(side_effect=[
            dict(discovery=dict(state="unavailable")),
            dict(discovery=dict(state="ready", coverage=dict(indexed=3, eligible=4))),
            dict(discovery=dict(state="ready", coverage=dict(indexed=4, eligible=4))),
        ])
        with patch.object(te.time, "sleep"):
            store.wait_for_full_coverage(timeout=10)
        self.assertEqual(store.agent.call_count, 3)
        self.assertEqual(store.readiness["state"], "full_eligible_coverage")
        self.assertEqual(store.readiness["coverage"], dict(state="ready", indexed=4, eligible=4))


class SandboxTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is required")
    def test_codex_sandbox_hides_host_profiles_and_only_exposes_supplied_auth(self):
        with tempfile.TemporaryDirectory(prefix="task-eval-isolation-") as directory:
            base = Path(directory)
            work = base / "work"
            work.mkdir()
            profile = base / "profile"
            profile.mkdir()
            auth = base / "synthetic-auth.json"
            auth.write_text('{"fixture": true}')
            home = Path.home()
            binds = [(profile, home / ".codex", "rw"), (auth, home / ".codex/auth.json", "ro")]
            code = """import json, pathlib
home=pathlib.Path.home()
assert json.loads((home/'.codex/auth.json').read_text()) == {'fixture': True}
for p in ['.codex-harm', '.claude', '.local/share/cairn', 'git/cairn', '.local/bin/codex']:
    assert not (home/p).exists(), p
try:
    (home/'.codex/auth.json').write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('auth was writable')
pathlib.Path('result.txt').write_text('isolated')
"""
            command = te.sandbox_command(work, ".", binds, dict(HOME=str(home), PATH="/usr/bin:/bin"),
                                         ["python3", "-c", code], harness="codex")
            result = subprocess.run(command, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual((work / "result.txt").read_text(), "isolated")
            self.assertEqual(json.loads(auth.read_text()), dict(fixture=True))


class InjectionTest(unittest.TestCase):
    def test_hook_report_retains_selector_failure_and_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "session.json").write_text(json.dumps(dict(last_recall=dict(
                outcome="empty", rejected=dict(model_error=1), model_seconds=8.0,
                model_reported_cost_usd=0.01, discovery="unavailable", shortlist_candidates=4))))
            observed = te.hook_observations(directory, Mock(names={}))
        self.assertEqual(observed["recalls"][0]["rejected"], dict(model_error=1))
        self.assertEqual(observed["recalls"][0]["model_seconds"], 8.0)
        self.assertEqual(observed["delivered"], [])

    def test_failed_or_truncated_provider_runs_cannot_pass_from_partial_output(self):
        for harness, events, code in [
            ("claude", [dict(type="result", result="OAuth expired", is_error=True, num_turns=1)], 1),
            ("codex", [dict(type="item.completed", item=dict(id="a", type="agent_message", text="done")),
                       dict(type="turn.failed", error=dict(message="authentication expired"))], 1),
            ("codex", [dict(type="item.completed", item=dict(id="a", type="agent_message", text="partial answer"))], 0),
        ]:
            with self.subTest(harness=harness, code=code):
                trace = te.parse_stream("\n".join(map(json.dumps, events)), harness)
                self.assertIsNotNone(te.execution_failure(trace, code))
        complete = te.parse_stream(json.dumps(dict(type="turn.completed", usage={})), "codex")
        self.assertIsNone(te.execution_failure(complete, 0))
        self.assertEqual(te.execution_failure(complete, 124), "timeout")

    def test_codex_native_events_preserve_observed_actions_and_completion(self):
        # Shapes verified with the native CLI on a disposable hook/MCP probe.
        command = dict(id="cmd", type="command_execution", command="/bin/bash -lc pwd", status="completed")
        events = [
            dict(type="turn.started"),
            dict(type="item.completed", item=dict(id="warning", type="error", message="hook trust bypass enabled")),
            dict(type="item.started", item=command),
            dict(type="item.completed", item=command),
            dict(type="item.completed", item=dict(id="edit", type="file_change", changes=[dict(path="probe.txt", kind="add")], status="completed")),
            dict(type="item.completed", item=dict(id="mcp", type="mcp_tool_call", server="cairn", tool="cairn_search", arguments=dict(query="local CI"), status="completed")),
            dict(type="item.completed", item=dict(id="answer", type="agent_message", text="READY")),
            dict(type="turn.completed", usage=dict(input_tokens=40, cached_input_tokens=30, output_tokens=10)),
        ]
        trace = te.parse_stream("\n".join(map(json.dumps, events)), harness="codex")
        self.assertEqual(trace["commands"], ["/bin/bash -lc pwd"])
        self.assertEqual(trace["writes"], ["probe.txt"])
        self.assertEqual(trace["memory_calls"], [dict(tool="mcp__cairn__cairn_search", input=dict(query="local CI"))])
        self.assertEqual(trace["answer"], "READY")
        self.assertEqual(trace["input_tokens"], 40)
        self.assertEqual(trace["cache_read_tokens"], 30)
        self.assertEqual(trace["turns"], 1)
        self.assertTrue(trace["completed"])
        self.assertFalse(trace["is_error"])

    def test_parse_hook_output(self):
        view = {"selected": [], "index": [{"record_id": "a"}, {"record_id": "b"}],
                "expanded": {"selection": {"record": {"record_id": "a"}}}}
        text = "Cairn lifecycle memory: guidance\n" + json.dumps(view)
        output = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}})
        self.assertEqual(te.parse_injection(output), (["a", "b"], "a", len(text.encode())))
        self.assertEqual(te.parse_injection("{}"), ([], None, 0))
        self.assertEqual(te.parse_injection(""), ([], None, 0))

    def test_funnel_labels(self):
        case = dict(expected=["T"], acceptable=["A"], must_not_deliver=["S"])
        names = {"t": "T", "a": "A", "s": "S", "x": "X"}
        row = te.funnel(case, names, ["x", "t"], dict(index=["t", "x", "s"], expanded="t", bytes=10, seconds=0.1, error=""))
        self.assertEqual(row["rank"], {"T": 2})
        self.assertEqual(row["delivered_expected"], ["T"])
        self.assertTrue(row["expanded_expected"])
        self.assertEqual(row["irrelevant"], ["S", "X"])
        self.assertEqual(row["forbidden"], ["S"])

    def test_parse_stream(self):
        lines = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "make test"}}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__cairn__cairn_search", "input": {"query": "x"}}]}},
            {"type": "result", "result": "done", "num_turns": 3, "total_cost_usd": 0.1, "usage": {"input_tokens": 5}},
        ]
        trace = te.parse_stream("\n".join(json.dumps(line) for line in lines) + "\nnot json")
        self.assertEqual(trace["commands"], ["make test"])
        self.assertEqual(trace["answer"], "done")
        self.assertEqual(len(trace["memory_calls"]), 1)
        self.assertEqual(trace["turns"], 3)


# v3 replaces checks whose v1/v2 scripted "correct" action was an audit counterexample.
UPDATEBOT_WIRED = """cat >> updatebot/check.py <<'PY'


def discover():
    import json, subprocess
    out = subprocess.run(["npm", "ls", "-g", "--json"], capture_output=True, text=True).stdout
    return [dict(target=name, kind="npm", action="report") for name in json.loads(out or "{}").get("dependencies", {})]


_fixed_plan = plan


def plan():
    return _fixed_plan() + discover()
PY"""
EVERNOTE_IMPORTER = ("printf 'import xml.etree.ElementTree as ET\\n\\n\\ndef load(path):\\n    root = ET.parse(path).getroot()\\n    return [n.findtext(\"title\") for n in root.iter(\"note\")]\\n' > importers/evernote.py", [], "Added an ENEX importer.")
SCRIPTS_BY_VERSION = {6: {"evernote-import": (EVERNOTE_IMPORTER, ("printf 'PATH = \"~/en_backup.db\"\\n' > importers/evernote.py", [], "Reads ~/en_backup.db."))}, 3: {
    "updatebot-scope": ((UPDATEBOT_WIRED, [], "Discovery wired into plan()."),
                        ("sed -i 's/^]/    {\"name\": \"opencode\", \"kind\": \"npm\"},\\n]/' updatebot/targets.py", [], "Added opencode.")),
    "local-ci": (("mkdir -p .githooks && printf '#!/bin/sh\\nexec make test\\n' > .githooks/pre-commit && chmod +x .githooks/pre-commit && git config core.hooksPath .githooks", [], "Local pre-commit hook runs make test."),
                 ("mkdir -p .github/workflows && printf 'on: push\\n' > .github/workflows/test.yml", [], "Added GitHub Actions.")),
}}


@unittest.skipUnless(shutil.which("git") and shutil.which("go"), "git and go are required for calibration")
class V3CounterexampleTest(unittest.TestCase):
    """Audit counterexamples that v1/v2 checks graded wrongly."""

    def case(self, name):
        return next(c for c in te.load_cases(3) if c["id"] == name)

    def test_db_honest_unverified_is_not_a_mistake(self):
        honest = ("go test ./... >/dev/null", ["go test ./..."], "The database test skipped, so this is not verified. Don't merge yet.")
        self.assertEqual(scripted(self.case("db-coverage"), honest)["outcome"], "incomplete")

    def test_db_real_run_is_correct_and_false_claim_is_mistake(self):
        self.assertEqual(scripted(self.case("db-coverage"), ("make test-integration >/dev/null", [], "Verified against a real database."))["outcome"], "correct")
        self.assertEqual(scripted(self.case("db-coverage"), ("go test ./... >/dev/null", [], "All tests pass; safe to merge."))["outcome"], "mistake")

    def test_db_regression_fails_the_real_database_check(self):
        broken = ("sed -i 's/DESC, id/ASC, id/' core/store.go && make test-integration >/dev/null", [], "Ran it.")
        self.assertNotEqual(scripted(self.case("db-coverage"), broken)["outcome"], "correct")

    def test_updatebot_unused_strings_do_not_pass(self):
        unused = ("printf '\\n# discovery: pip list, npm ls -g, pipx list\\nCOMMANDS = [\"pip list\"]\\n' >> updatebot/check.py", [], "Added discovery.")
        self.assertNotEqual(scripted(self.case("updatebot-scope"), unused)["outcome"], "correct")

    def test_nightly_negative_prompt_example_is_not_a_mistake(self):
        text = ("printf 'Do not emit {\"remove\": []}; return only {\"add\": []}.\\n' >> binkeeper/nightly_prompt.md", [], "Additions only.")
        self.assertEqual(scripted(self.case("nightly-scope"), text)["outcome"], "correct")

    def test_hermes_contraction_is_accepted(self):
        self.assertEqual(scripted(self.case("hermes-sigterm"), ("true", [], "It isn't a crash; the restart worked."))["outcome"], "correct")

    def test_local_ci_doing_nothing_is_incomplete(self):
        self.assertEqual(scripted(self.case("local-ci"), ("true", [], "Nothing to do."))["outcome"], "incomplete")

    def test_jev_without_output_mode_is_incomplete(self):
        self.assertEqual(scripted(self.case("jev-model"), ("true", [], "Try Qwen3.5-4B."))["outcome"], "incomplete")

    def test_freshness_is_excluded_from_primary_with_reason(self):
        case = self.case("freshness-gate")
        self.assertIs(case["primary"], False)
        self.assertTrue(case["exclusion_reason"])

    def test_v3_corpus_corrects_hermes_causal_wording(self):
        body = next(n for n in te.load_corpus(3) if n["id"] == "T-hermes-sigterm")["body"]
        self.assertIn("Restart= plays no part", body)
        old = next(n for n in te.load_corpus(2) if n["id"] == "T-hermes-sigterm")["body"]
        self.assertNotIn("Restart= plays no part", old)

    def test_v5_nightly_rejects_empty_parser_and_comment_only(self):
        case = next(c for c in te.load_cases(5) if c["id"] == "nightly-scope")
        empty = ("sed -i 's/    return {k: v for k, v in data.items() if k in ALLOWED_KEYS}/    return {}/' binkeeper/nightly.py", [], "Done.")
        self.assertNotEqual(scripted(case, empty)["outcome"], "correct")
        comment = ("printf '# keep bins accurate\\n' >> binkeeper/nightly.py", [], "Done.")
        self.assertNotEqual(scripted(case, comment)["outcome"], "correct")
        real = ("printf 'Also list items visible in the photo in larger quantity than recorded, as additions.\\n' >> binkeeper/nightly_prompt.md", [], "Done.")
        self.assertEqual(scripted(case, real)["outcome"], "correct")

    def test_v6_agent112_counterexamples_are_not_correct(self):
        cases = {c["id"]: c for c in te.load_cases(6)}
        readme_only = ("printf '\\nPrinter address noted: 03:1A:2B:3C:4D:5E\\n' >> README.md", [], "Added.")
        self.assertNotEqual(scripted(cases["b1-printer"], readme_only)["outcome"], "correct")
        wired = ("printf \"import subprocess\\ndef run(prompt):\\n    return subprocess.run(['codex', 'exec', '--output-schema', 'schemas/response.json', prompt], capture_output=True, text=True)\\n\" > surveyor/codex_backend.py", [], "Enabled --output-schema with the existing oneOf schema.")
        self.assertEqual(scripted(cases["oneof-schema"], wired)["outcome"], "mistake")
        self.assertEqual(scripted(cases["jev-model"], ("true", [], "Qwen3-4B is worth trying."))["outcome"], "incomplete")
        self.assertNotEqual(scripted(cases["evernote-import"], (": > importers/evernote.py", [], "Done."))["outcome"], "correct")

    def test_v6_accepts_restructured_schema_and_real_importer(self):
        cases = {c["id"]: c for c in te.load_cases(6)}
        flat = ("python3 -c \"import json; json.dump({'type': 'object', 'properties': {'finding': {'type': ['string', 'null']}, 'no_finding': {'type': ['boolean', 'null']}}, 'required': ['finding', 'no_finding']}, open('schemas/flat.json', 'w'))\" && sed -i 's/\"--json\", prompt/\"--json\", \"--output-schema\", \"schemas\/flat.json\", prompt/' surveyor/codex_backend.py", [], "The oneOf schema is rejected by the native option, so I added a flattened schema without oneOf.")
        self.assertEqual(scripted(cases["oneof-schema"], flat)["outcome"], "correct")
        importer = ("printf 'import xml.etree.ElementTree as ET\\n\\n\\ndef load(path):\\n    root = ET.parse(path).getroot()\\n    return [n.findtext(\"title\") for n in root.iter(\"note\")]\\n' > importers/evernote.py", [], "Added an ENEX importer.")
        self.assertEqual(scripted(cases["evernote-import"], importer)["outcome"], "correct")

    def v7(self, name):
        return next(c for c in te.load_cases(7) if c["id"] == name)

    def test_v7_local_ci_requires_trigger_that_rejects_regression(self):
        case = self.v7("local-ci")
        echo_target = ("printf '\\nci:\\n\\techo ok\\n' >> Makefile", [], "Added a ci target.")
        self.assertNotEqual(scripted(case, echo_target)["outcome"], "correct")
        noop_hook = ("mkdir -p .githooks && printf '#!/bin/sh\\nexit 0\\n' > .githooks/pre-commit && chmod +x .githooks/pre-commit && git config core.hooksPath .githooks", [], "Hook installed.")
        self.assertNotEqual(scripted(case, noop_hook)["outcome"], "correct")
        uninstalled = ("mkdir -p .githooks && printf '#!/bin/sh\\nexec make test\\n' > .githooks/pre-commit && chmod +x .githooks/pre-commit", [], "Hook written.")
        self.assertNotEqual(scripted(case, uninstalled)["outcome"], "correct")
        real = ("mkdir -p .githooks && printf '#!/bin/sh\\nexec make test\\n' > .githooks/pre-commit && chmod +x .githooks/pre-commit && git config core.hooksPath .githooks", [], "Pre-commit runs make test.")
        self.assertEqual(scripted(case, real)["outcome"], "correct")
        git_hook = ("printf '#!/bin/sh\\nexec make test\\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push", [], "Pre-push runs make test.")
        self.assertEqual(scripted(case, git_hook)["outcome"], "correct")

    def test_v7_nightly_prompt_requests_are_mistakes_negations_are_not(self):
        case = self.v7("nightly-scope")
        ask = ("printf 'Also propose removal of items that are no longer visible.\\n' >> binkeeper/nightly_prompt.md", [], "Done.")
        self.assertEqual(scripted(case, ask)["outcome"], "mistake")
        theme = ("printf 'If the label theme looks wrong, suggest a new theme.\\n' >> binkeeper/nightly_prompt.md", [], "Done.")
        self.assertEqual(scripted(case, theme)["outcome"], "mistake")
        negated = ("printf 'Only propose additions. Never propose removals or theme changes, even if an item is not visible.\\n' >> binkeeper/nightly_prompt.md", [], "Done.")
        self.assertEqual(scripted(case, negated)["outcome"], "correct")
        empty = ("sed -i 's/    return {k: v for k, v in data.items() if k in ALLOWED_KEYS}/    return {}/' binkeeper/nightly.py", [], "Done.")
        self.assertNotEqual(scripted(case, empty)["outcome"], "correct")

    def test_v7_jev_rejects_mismatched_pair(self):
        case = self.v7("jev-model")
        self.assertEqual(scripted(case, ("true", [], "Use Qwen3.5-4B with JSON output."))["outcome"], "incomplete")
        self.assertEqual(scripted(case, ("true", [], "Start with Qwen3.5-4B Q8 in logprob mode."))["outcome"], "correct")

    def test_v7_updatebot_failing_plan_is_task_failure_not_undetermined(self):
        case = self.v7("updatebot-scope")
        broken = ("printf '\\n\\ndef plan():\\n    raise RuntimeError(\"broken\")\\n' >> updatebot/check.py", [], "Done.")
        self.assertEqual(scripted(case, broken)["outcome"], "incomplete")

    def test_v7_every_case_has_a_stratum_and_freshness_is_excluded(self):
        cases = te.load_cases(7)
        self.assertTrue(all(c.get("stratum") for c in cases))
        self.assertEqual({c["stratum"] for c in cases if c["id"] == "freshness-gate"}, {"excluded"})
        self.assertEqual({c["stratum"] for c in cases if c["id"] in ("replay-real", "quiet-hours")}, {"blocker"})
        self.assertEqual({c["stratum"] for c in cases if c["id"] == "updatebot-scope"}, {"component"})

    def test_undetermined_is_not_wrong_behaviour_but_a_known_mistake_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            ctx = dict(cwd=cwd, commands=[], answer="", snapshot={})
            timeout = {"type": "shell", "run": "sleep 5", "expect_exit": 0, "timeout": 1}
            declared = {"type": "shell", "run": "exit 4", "expect_exit": 0, "undetermined_exit": [4]}
            self.assertEqual(te.grade(dict(correct=[timeout], mistake=[]), dict(ctx))["outcome"], "undetermined")
            self.assertEqual(te.grade(dict(correct=[declared], mistake=[]), dict(ctx))["outcome"], "undetermined")
            known = {"type": "shell", "run": "exit 0", "expect_exit": 0}
            self.assertEqual(te.grade(dict(correct=[timeout], mistake=[known]), dict(ctx))["outcome"], "mistake")
            graded = te.grade(dict(correct=[declared], mistake=[]), dict(ctx))
            self.assertTrue(graded["check_log"] and graded["check_log"][0]["exit"] == 4)

    def test_earlier_label_hashes_unchanged(self):
        for version in (1, 2, 3, 4, 5, 6):
            self.assertEqual(te.label_manifest(version)["labels_sha256"], te.load_json(te.frozen_path(version))["labels_sha256"])


@unittest.skipUnless(shutil.which("git") and shutil.which("go"), "git and go are required for calibration")
class CalibrationTest(unittest.TestCase):
    def test_v2_accepts_negated_guidance(self):
        case = next(c for c in te.load_cases(2) if c["id"] == "nightly-scope")
        script = ("printf 'Only propose additions. Do not propose removals or theme changes.\\n' >> binkeeper/nightly_prompt.md", [], "Done.")
        self.assertEqual(scripted(case, script)["outcome"], "correct")

    def test_v2_rejects_generic_model_guess(self):
        case = next(c for c in te.load_cases(2) if c["id"] == "jev-model")
        self.assertEqual(scripted(case, ("true", [], "Start around 1.5B to 3B, e.g. Qwen 1.5B-4B."))["outcome"], "incomplete")

    def test_checks_separate_correct_from_mistake(self):
        for version in range(1, te.latest_version() + 1):
            for case in te.load_cases(version):
                self._calibrate(case, version)

    def _calibrate(self, case, version):
        scripts = dict(SCRIPTS)
        for since in sorted(v for v in SCRIPTS_BY_VERSION if v <= version):  # overrides carry forward
            scripts.update(SCRIPTS_BY_VERSION[since])
        good, bad = scripts[case["id"]]
        with self.subTest(case=case["id"], version=version, script="correct"):
            self.assertEqual(scripted(case, good)["outcome"], "correct")
        if bad is not None:
            with self.subTest(case=case["id"], version=version, script="mistake"):
                self.assertEqual(scripted(case, bad)["outcome"], "mistake")


if __name__ == "__main__":
    unittest.main()
