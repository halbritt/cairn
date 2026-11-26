"""Keep the native OpenCode cairn_edit arguments equal to the MCP cairn_edit arguments.

The static tests need no host. The behavior test drives the installed adapter
through `opencode debug agent` with a scripted CLI: no model, API or database.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_SOURCE = REPO_ROOT / "mcpapi/server.go"
ADAPTER = REPO_ROOT / "integrations/opencode/cairn.ts"
IDENTITY = {"request_id", "record_id", "expected_version"}

FAKE_CLI = r'''#!/usr/bin/python3
import json, pathlib, sys

argv = sys.argv[1:]
idx = 1
while idx < len(argv) and argv[idx].startswith("--"):
    idx += 2
request = json.loads(sys.stdin.read())
pathlib.Path(sys.argv[0]).with_name("call.json").write_text(
    json.dumps({"op": argv[idx], "request": request}), encoding="utf-8")
print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK",
                  "data": {"record_id": request["record_id"], "version": request["expected_version"] + 1}}))
'''


def mcp_edit_arguments():
    """JSON names of the MCP editArgs struct fields."""
    struct = re.search(r"^type editArgs struct \{\n(.*?)^\}", MCP_SOURCE.read_text(encoding="utf-8"), re.S | re.M)
    assert struct, "mcpapi/server.go no longer declares editArgs"
    return set(re.findall(r'`json:"([a-z_]+)', struct.group(1)))


def mcp_exclusive_message():
    source = MCP_SOURCE.read_text(encoding="utf-8")
    start = source.index("func (t memoryTools) edit(")
    message = re.search(r'Message: "(supply exactly one of [^"]+)"', source[start:])
    assert message, "MCP edit no longer states its exactly-one-of rule"
    return message.group(1)


def opencode_edit_source():
    source = ADAPTER.read_text(encoding="utf-8")
    start = source.index("export const edit = validatedTool({")
    return source[start:source.index("\n})\n", start)]


def opencode_edit_arguments():
    """Top-level keys of the adapter's edit `args` object literal."""
    source = opencode_edit_source()
    text = source[source.index("args: {") + len("args: "):]
    keys, depth, i, expect_key = set(), 0, 0, False
    while i < len(text):
        char = text[i]
        if char in "\"'`":
            i += 1
            while text[i] != char:
                i += 2 if text[i] == "\\" else 1
        elif char in "{([":
            depth += 1
            expect_key = depth == 1
        elif char in "})]":
            depth -= 1
            if depth == 0:
                return keys
        elif char == "," and depth == 1:
            expect_key = True
        elif expect_key and not char.isspace():
            key = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*:", text[i:])
            assert key, "unrecognized edit argument near: " + text[i:i + 40]
            keys.add(key.group(1))
            i += key.end() - 1
            expect_key = False
        i += 1
    raise AssertionError("edit args object literal is not closed")


class OpenCodeEditParityTests(unittest.TestCase):
    def test_argument_names_match_mcp(self):
        mcp = mcp_edit_arguments()
        self.assertTrue(IDENTITY < mcp, mcp)
        self.assertIn("set_sections", mcp)
        self.assertEqual(opencode_edit_arguments(), mcp)

    def test_exactly_one_rule_matches_mcp(self):
        message = mcp_exclusive_message()
        for mode in mcp_edit_arguments() - IDENTITY:
            self.assertIn(mode, message)
        source = opencode_edit_source()
        self.assertIn('"INVALID_REQUEST: ' + message + '"', source)
        # Both description profiles name every mode.
        full, compact = re.search(r'description: profiledDescription\("((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\)', source).groups()
        for mode in mcp_edit_arguments() - IDENTITY:
            self.assertIn(mode, full)
            self.assertIn(mode, compact)


class OpenCodeEditBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opencode = shutil.which("opencode")
        if not cls.opencode:
            raise unittest.SkipTest("opencode binary is not installed on PATH")
        cls._tmp = tempfile.TemporaryDirectory(prefix="cairn-opencode-edit-")
        cls.root = Path(cls._tmp.name)
        cls.cairn_bin = cls.root / "cairn"
        subprocess.run(["go", "build", "-o", str(cls.cairn_bin), "./cmd/cairn"],
                       cwd=REPO_ROOT, check=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def test_every_mcp_edit_mode_reaches_its_api_operation(self):
        work = self.root / "work"
        work.mkdir()
        subprocess.run(
            [str(self.cairn_bin), "opencode-install", "--project", str(work),
             "--socket", str(self.root / "api.sock"), "--token-file", str(self.root / "agent.token"),
             "--repo", "fixture:opencode-edit"],
            check=True, capture_output=True, text=True, timeout=15)
        settings_path = work / ".opencode/cairn.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        fake_cli = self.root / "fake-cairn"
        fake_cli.write_text(FAKE_CLI, encoding="utf-8")
        fake_cli.chmod(0o700)
        recorded = self.root / "call.json"
        settings["executable"] = str(fake_cli)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        config_path = work / "opencode.json"
        config_path.write_text(json.dumps({
            "model": "fixture/probe",
            "provider": {"fixture": {
                "npm": "@ai-sdk/openai-compatible", "name": "Edit fixture",
                "options": {"baseURL": "http://127.0.0.1:1/v1", "apiKey": "unused"},
                "models": {"probe": {"name": "Probe"}}}},
            "permission": {"*": "deny", "cairn_edit": "allow"},
        }), encoding="utf-8")
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        for key, folder in [("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                            ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")]:
            directory = work / folder
            directory.mkdir()
            env[key] = str(directory)
        env.update(OPENCODE_CONFIG=str(config_path), OPENCODE_DISABLE_AUTOUPDATE="true",
                   OPENCODE_DISABLE_MODELS_FETCH="true", OPENCODE_DISABLE_DEFAULT_PLUGINS="true")

        def invoke(args, expect_error=None):
            recorded.unlink(missing_ok=True)
            proc = subprocess.run(
                [self.opencode, "debug", "agent", "build", "--pure", "--tool", "cairn_edit",
                 "--params", json.dumps(args)],
                cwd=work, env=env, capture_output=True, text=True, timeout=60)
            if expect_error is not None:
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(expect_error, proc.stderr + proc.stdout)
                self.assertFalse(recorded.exists(), "a refused edit reached the CLI: " + json.dumps(args))
                return None
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(json.loads(proc.stdout)["result"]["output"])

        record_id = str(uuid.uuid4())
        base = lambda: {"request_id": str(uuid.uuid4()), "record_id": record_id, "expected_version": 3}
        sections = {"Status": "Section edits reach the API.\n", "Next steps": "- none\n"}
        draft = {"kind": "note", "body": "replacement", "claim_type": "self", "sensitivity": "local",
                 "scope": {"repo": settings["repo"], "task_id": "*", "run_id": "*"}}
        citations = [{"evidence_id": str(uuid.uuid4()), "expected_sha256": "a" * 64}]
        # mode -> (argument, API operation, operation-specific request fields)
        modes = {
            "body": ("new body", "revise", {"body": "new body"}),
            "append": ("\nsuffix", "append", {"body": "\nsuffix"}),
            "replace": ({"old_text": "old", "new_text": ""}, "replace", {"old_text": "old", "new_text": ""}),
            "set_sections": (sections, "set-sections", {"sections": sections}),
            "evidence_citations": (citations, "cite", {"evidence_citations": citations}),
            "draft": (draft, "edit", None),
        }
        # A mode added to the MCP tool must be added to the adapter and here.
        self.assertEqual(set(modes), mcp_edit_arguments() - IDENTITY)
        for mode, (value, operation, fields) in modes.items():
            with self.subTest(mode=mode):
                args = dict(base(), **{mode: value})
                result = invoke(args)
                self.assertEqual(result, {"record_id": record_id, "version": 4, "request_id": args["request_id"]})
                call = json.loads(recorded.read_text(encoding="utf-8"))
                self.assertEqual(call["op"], operation)
                want = args if fields is None else dict(
                    request_id=args["request_id"], record_id=record_id, expected_version=3,
                    repo=settings["repo"], **fields)
                self.assertEqual(call["request"], want)

        exclusive = "INVALID_REQUEST: " + mcp_exclusive_message()
        invoke(base(), exclusive)
        for other in ("body", "draft"):
            invoke(dict(base(), set_sections=sections, **{other: modes[other][0]}), exclusive)
        invoke(dict(base(), set_sections={}), "INVALID_REQUEST: set_sections requires 1-16 section updates")
        invoke(dict(base(), set_sections={f"Section {i}": "text" for i in range(17)}),
               "INVALID_REQUEST: set_sections requires 1-16 section updates")
        sixteen = {f"Section {i}": "text" for i in range(16)}
        self.assertEqual(invoke(dict(base(), set_sections=sixteen))["version"], 4)
        for invalid in ({"Status": ""}, {"": "text"}, {"Status": 7}, {"S" * 129: "text"},
                        {"Status": "b" * 65537}, "Status"):
            invoke(dict(base(), set_sections=invalid), "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
