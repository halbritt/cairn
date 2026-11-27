"""Keep the native OpenCode tool arguments equal to the MCP tool arguments.

Every tool the adapter shares with the MCP server is compared by argument name:
the JSON names of the Go struct the MCP handler decodes against the keys of the
adapter tool's strict `args` object. An argument the MCP tool gains and the
adapter lacks is refused by the adapter as INVALID_REQUEST (set_sections on
cairn_edit, force on cairn_pull), so every difference must be listed below with
its reason. Only top-level names are compared, not nested shapes or bounds.

The static tests need no host. The behavior tests drive the installed adapter
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
MCP_DIR = REPO_ROOT / "mcpapi"
CORE_DIR = REPO_ROOT / "core"
MCP_SOURCE = MCP_DIR / "server.go"
ADAPTER_DIR = REPO_ROOT / "integrations/opencode"
ADAPTER_FILES = ("cairn.ts", "messaging.ts")
ADAPTER = ADAPTER_DIR / "cairn.ts"
IDENTITY = {"request_id", "record_id", "expected_version"}

# MCP tools the adapter intentionally does not provide.
MCP_ONLY_TOOLS = {
    # Reports the running MCP facade build, its profile and API capabilities. The
    # adapter has no facade process: OpenCode loads the installed tool file itself.
    "cairn_client_info",
}
# Adapter tools with no MCP counterpart.
ADAPTER_ONLY_TOOLS = set()
# tool -> MCP arguments the adapter intentionally does not accept.
MCP_ONLY_ARGUMENTS = {
    # Needs a design decision, not a pass-through. The adapter sends search through
    # `cairn agent search` flags; the distinct semantic input has no flag and goes
    # through --semantic-query-file/--semantic-query-stdin, with a joint 4096-byte
    # bound with query and a capability check before submission. The adapter would
    # also have to verify that the response kept the distinct input (the MCP tool
    # refuses a silently downgraded response) before it can offer the argument.
    "cairn_search": {"semantic_query"},
}
# tool -> adapter arguments the MCP tool does not have. An argument the adapter
# derives from the OpenCode session (scope, session identity) is not a tool
# argument on either side, so nothing is listed today.
ADAPTER_ONLY_ARGUMENTS = {}

FAKE_CLI = r'''#!/usr/bin/python3
import json, pathlib, sys

argv = sys.argv[1:]
idx = 1
while idx < len(argv) and argv[idx].startswith("--"):
    idx += 2
request = json.loads(sys.stdin.read())
pathlib.Path(sys.argv[0]).with_name("call.json").write_text(
    json.dumps({"op": argv[idx], "request": request}), encoding="utf-8")
if argv[idx] == "expand" and request.get("force") is True:
    data = {"selection": {"record": {"record_id": "forced", "body": "whole body"}}}
elif argv[idx] == "expand":
    # What `cairn agent expand` answers for a pull its session already delivered.
    data = {"already_delivered": True, "record_id": "delivered", "version": 2,
            "message": "already delivered in this session; pass --force to re-read"}
else:
    data = {"record_id": request["record_id"], "version": request["expected_version"] + 1}
print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": data}))
'''


def _go_sources(directory):
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(directory.glob("*.go")) if not path.name.endswith("_test.go")}


def go_struct_arguments(type_name, packages, package="mcpapi", _seen=()):
    """JSON names a Go struct accepts, following embedded structs."""
    if "." in type_name:
        package, type_name = type_name.split(".", 1)
    assert (package, type_name) not in _seen, "recursive embedding: " + type_name
    bodies = [m.group(1) for source in packages[package].values()
              for m in re.finditer(r"^type " + re.escape(type_name) + r" struct \{\n(.*?)^\}", source, re.S | re.M)]
    if not bodies and re.search(r"^type " + re.escape(type_name) + r" struct\s*\{\s*\}", "\n".join(packages[package].values()), re.M):
        return set()
    assert len(bodies) == 1, f"{package}.{type_name}: expected one struct declaration, found {len(bodies)}"
    names = set()
    for line in bodies[0].splitlines():
        line = line.split("//", 1)[0].strip() if "`" not in line else line.strip()
        if not line or line.startswith("//"):
            continue
        tag = re.search(r'`[^`]*json:"([^",]*)', line)
        if tag:
            if tag.group(1) != "-":
                assert tag.group(1), "untagged JSON name in " + type_name + ": " + line
                names.add(tag.group(1))
            continue
        embedded = re.fullmatch(r"\*?((?:[a-z]+\.)?[A-Za-z_][A-Za-z0-9_]*)", line)
        assert embedded, f"{package}.{type_name}: field without a json tag: {line}"
        names |= go_struct_arguments(embedded.group(1), packages, package, (*_seen, (package, type_name)))
    return names


def mcp_tools():
    """Map each registered MCP tool name to the JSON argument names of its handler."""
    packages = {"mcpapi": _go_sources(MCP_DIR), "core": _go_sources(CORE_DIR)}
    source = "\n".join(packages["mcpapi"].values())
    tools = {}
    for registration in re.finditer(r"^\s*mcp\.AddTool\(server, (.*), tools\.(\w+)\)$", source, re.M):
        definition, method = registration.groups()
        if re.fullmatch(r"\w+", definition):
            declared = re.findall(r"^\s*" + definition + r" := &mcp\.Tool\{(.*)$", source, re.M)
            assert len(declared) == 1, "cannot find the mcp.Tool named by variable " + definition
            definition = declared[0]
        names = re.findall(r'\bName: "(cairn_[a-z_]+)"', definition)
        assert len(names) == 1, "cannot read the tool name of handler " + method
        handlers = re.findall(
            r"^func \(\w+ \*?\w+\) " + method + r"\(\w+ context\.Context, \w+ \*mcp\.CallToolRequest, \w+ ([\w.]+|struct\{\})\)",
            source, re.M)
        assert len(handlers) == 1, "cannot find the handler " + method
        assert names[0] not in tools, "tool registered twice: " + names[0]
        tools[names[0]] = set() if handlers[0] == "struct{}" else go_struct_arguments(handlers[0], packages)
    assert source.count("mcp.AddTool(") == len(tools), "an mcp.AddTool call has a form this test does not read"
    return tools


def _object_literal_keys(text, constants, origin):
    """Top-level keys of the object literal that starts at text[0] == '{'."""
    assert text[0] == "{", origin
    keys, depth, i, expect_key = set(), 0, 0, False
    while i < len(text):
        char = text[i]
        if char in "\"'`":
            i += 1
            while text[i] != char:
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.index("\n", i)
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
            spread = re.match(r"\.\.\.([A-Za-z_][A-Za-z0-9_]*)\s*(?=[,}])", text[i:])
            key = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*(:|(?=[,}]))", text[i:])
            if spread:
                keys |= ts_constant_keys(spread.group(1), constants)
                i += spread.end() - 1
            else:
                assert key, origin + ": unrecognized argument near: " + text[i:i + 60]
                keys.add(key.group(1))
                i += key.end() - 1
            expect_key = False
        i += 1
    raise AssertionError(origin + ": args object literal is not closed")


def ts_constant_keys(name, constants):
    assert name in constants, "args spread an object this test cannot read: " + name
    return _object_literal_keys(constants[name], constants, name)


def adapter_tools():
    """Map each native OpenCode tool (as cairn_<export>) to its argument keys."""
    tools = {}
    for filename in ADAPTER_FILES:
        source = (ADAPTER_DIR / filename).read_text(encoding="utf-8")
        constants = {m.group(1): source[m.end() - 1:]
                     for m in re.finditer(r"^(?:export )?const ([A-Za-z_][A-Za-z0-9_]*)(?:: [^=\n]+)? = \{", source, re.M)}
        for export in re.finditer(r"^export const ([a-z_]+) = validatedTool\(\{\n", source, re.M):
            body = source[export.end():source.index("\n})\n", export.end())]
            args = re.search(r"^  args: ", body, re.M)
            assert args, filename + ": tool without args: " + export.group(1)
            rest = body[args.end():]
            named = re.match(r"([A-Za-z_][A-Za-z0-9_]*),\n", rest)
            name = "cairn_" + export.group(1)
            assert name not in tools, "adapter tool defined twice: " + name
            tools[name] = (ts_constant_keys(named.group(1), constants) if named
                           else _object_literal_keys(rest, constants, name))
        assert len(re.findall(r"validatedTool\(\{", source)) == len(
            re.findall(r"^export const [a-z_]+ = validatedTool\(\{\n", source, re.M)), filename
    return tools


def mcp_edit_arguments():
    """JSON names of the MCP cairn_edit arguments."""
    return mcp_tools()["cairn_edit"]


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
    return adapter_tools()["cairn_edit"]


class OpenCodeToolParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mcp = mcp_tools()
        cls.adapter = adapter_tools()

    def test_readers_find_the_known_tools_and_arguments(self):
        # Guard the readers themselves: a reader that silently finds nothing would pass.
        self.assertGreaterEqual(len(self.mcp), 17)
        self.assertGreaterEqual(len(self.adapter), 16)
        self.assertEqual(self.mcp["cairn_pull"], {"request_id", "receipt_id", "handle", "span", "opening_bytes", "force"})
        self.assertEqual(self.mcp["cairn_client_info"], set())
        self.assertTrue({"query", "semantic", "context", "entities"} <= self.adapter["cairn_search"])
        self.assertTrue({"to", "body", "kind", "agent_id"} <= self.adapter["cairn_send"], self.adapter["cairn_send"])
        self.assertTrue(all(self.mcp[name] for name in self.mcp if name != "cairn_client_info"))

    def test_shared_tools_are_the_same_set(self):
        self.assertEqual(set(self.mcp) - set(self.adapter), MCP_ONLY_TOOLS)
        self.assertEqual(set(self.adapter) - set(self.mcp), ADAPTER_ONLY_TOOLS)

    def test_every_shared_tool_has_the_mcp_argument_names(self):
        for name in sorted(set(self.mcp) & set(self.adapter)):
            with self.subTest(tool=name):
                self.assertEqual(self.mcp[name] - self.adapter[name], MCP_ONLY_ARGUMENTS.get(name, set()),
                                 "MCP arguments the adapter refuses")
                self.assertEqual(self.adapter[name] - self.mcp[name], ADAPTER_ONLY_ARGUMENTS.get(name, set()),
                                 "adapter arguments the MCP tool lacks")

    def test_allowlists_name_only_existing_differences(self):
        self.assertTrue(MCP_ONLY_TOOLS <= set(self.mcp))
        self.assertTrue(ADAPTER_ONLY_TOOLS <= set(self.adapter))
        for allowed, present in ((MCP_ONLY_ARGUMENTS, self.mcp), (ADAPTER_ONLY_ARGUMENTS, self.adapter)):
            for name, arguments in allowed.items():
                self.assertTrue(arguments and arguments <= present[name], name)

    def test_pull_names_the_force_argument_as_the_mcp_tool_does(self):
        # The CLI's already-delivered refusal names its flag; the MCP tool and the
        # adapter name the argument.
        mcp_message = "already delivered in this session; pass force=true to re-read"
        self.assertIn('"' + mcp_message + '"', (MCP_DIR / "pull_dedup.go").read_text(encoding="utf-8"))
        self.assertIn('"already delivered in this session; pass --force to re-read"',
                      (REPO_ROOT / "cmd/cairn/pull_dedup.go").read_text(encoding="utf-8"))
        source = ADAPTER.read_text(encoding="utf-8")
        pull = source[source.index("export const pull = validatedTool({"):]
        pull = pull[:pull.index("\n})\n")]
        self.assertIn("force: z.boolean().optional()", pull)
        self.assertIn('message: "' + mcp_message + '"', pull)


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


class OpenCodeToolBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opencode = shutil.which("opencode")
        if not cls.opencode:
            raise unittest.SkipTest("opencode binary is not installed on PATH")
        cls._tmp = tempfile.TemporaryDirectory(prefix="cairn-opencode-parity-")
        cls.root = Path(cls._tmp.name)
        cls.cairn_bin = cls.root / "cairn"
        subprocess.run(["go", "build", "-o", str(cls.cairn_bin), "./cmd/cairn"],
                       cwd=REPO_ROOT, check=True, timeout=300, stdin=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def install(self, name, tool):
        """Install the adapter in a new project whose CLI is the recording fake."""
        work = self.root / name
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
            "permission": {"*": "deny", tool: "allow"},
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
                [self.opencode, "debug", "agent", "build", "--pure", "--tool", tool,
                 "--params", json.dumps(args)],
                cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            if expect_error is not None:
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertIn(expect_error, proc.stderr + proc.stdout)
                self.assertFalse(recorded.exists(), "a refused call reached the CLI: " + json.dumps(args))
                return None
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(json.loads(proc.stdout)["result"]["output"])

        return invoke, recorded, settings

    def test_pull_forwards_force_and_names_the_argument_when_deduplicated(self):
        invoke, recorded, _ = self.install("pull", "cairn_pull")
        handle = lambda: {"request_id": str(uuid.uuid4()), "receipt_id": str(uuid.uuid4()), "handle": str(uuid.uuid4())}
        args = dict(handle(), force=True)
        self.assertEqual(invoke(args), {"selection": {"record": {"record_id": "forced", "body": "whole body"}}})
        # The request reaches the same API field the MCP tool sets.
        self.assertEqual(json.loads(recorded.read_text(encoding="utf-8")), {"op": "expand", "request": args})
        args = dict(handle(), force=False, span={"offset": 0, "length": 64})
        self.assertEqual(invoke(args), {
            "already_delivered": True, "record_id": "delivered", "version": 2,
            "message": "already delivered in this session; pass force=true to re-read"})
        self.assertEqual(json.loads(recorded.read_text(encoding="utf-8")), {"op": "expand", "request": args})
        args = handle()
        self.assertTrue(invoke(args)["already_delivered"])
        self.assertNotIn("force", json.loads(recorded.read_text(encoding="utf-8"))["request"])
        for invalid in ("true", 1, None):
            invoke(dict(handle(), force=invalid), "INVALID_REQUEST")

    def test_every_mcp_edit_mode_reaches_its_api_operation(self):
        invoke, recorded, settings = self.install("work", "cairn_edit")
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
