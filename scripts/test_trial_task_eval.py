import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

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
                      ("printf 'Propose removal of items that are not visible in the photo.\\n' >> binkeeper/nightly_prompt.md", [], "Now proposes removals.")),
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
    def test_fixtures_validate(self):
        self.assertEqual(te.validate(te.load_cases(), te.load_corpus()), [])

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
        if (te.TRIAL / "FROZEN.json").exists():
            te.verify_frozen()


class InjectionTest(unittest.TestCase):
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


@unittest.skipUnless(shutil.which("git") and shutil.which("go"), "git and go are required for calibration")
class CalibrationTest(unittest.TestCase):
    def test_checks_separate_correct_from_mistake(self):
        for case in te.load_cases():
            good, bad = SCRIPTS[case["id"]]
            with self.subTest(case=case["id"], script="correct"):
                self.assertEqual(scripted(case, good)["outcome"], "correct")
            if bad is not None:
                with self.subTest(case=case["id"], script="mistake"):
                    self.assertEqual(scripted(case, bad)["outcome"], "mistake")


if __name__ == "__main__":
    unittest.main()
