"""Verify opt-in OpenCode session messaging tools (`opencode-install --messaging`)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
MESSAGING_TOOLS = (
    "agents", "send", "inbox", "ack", "complete", "renew", "event_status",
)
MEMORY_TOOLS = (
    "search", "prepare_note", "pull", "pull_evidence", "remember", "edit",
    "history", "assess", "assessments",
)


def uuidv5_sha1(namespace_uuid: str, purpose: str) -> str:
    return str(uuid.uuid5(uuid.UUID(namespace_uuid), purpose))


FAKE_CAIRN_CLI = r'''#!/usr/bin/python3
import json, pathlib, sys, uuid

log_path = pathlib.Path(sys.argv[0]).with_name("calls.jsonl")
state_path = pathlib.Path(sys.argv[0]).with_name("state.json")
state = json.loads(state_path.read_text())

argv = sys.argv[1:]
assert argv and argv[0] == "agent", argv
idx = 1
agent_id = ""
execution_id = ""
while idx < len(argv) and argv[idx].startswith("--"):
    flag = argv[idx]
    val = argv[idx + 1]
    if flag == "--agent-id":
        agent_id = val
    elif flag == "--execution-id":
        execution_id = val
    idx += 2
op = argv[idx]
raw_in = sys.stdin.read()
body = json.loads(raw_in) if raw_in.strip() else {}
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"op": op, "agent_id": agent_id, "execution_id": execution_id, "body": body}) + "\n")

def ok(data):
    print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": data}))
    sys.exit(0)

def fail(code, message):
    print(json.dumps({"schema": "cairn.response/1", "ok": False, "status": code, "message": f"{code}: {message}"}))
    sys.exit(1)

if op == "agent-directory":
    agents = list(state["agents"])
    if state.get("capture_session_as_self") and not any(a.get("native_session_id", "").startswith("ses_") for a in agents):
        # Dynamically bind self when requested by the test.
        pass
    after = int(body.get("after", 0))
    limit = int(body.get("limit", 25))
    include_offline = bool(body.get("include_offline", False))
    only_id = body.get("agent_id", "")
    project = body.get("project", "")
    out = []
    more = False
    for a in agents:
        if a["ordinal"] <= after:
            continue
        if not include_offline and (not a.get("online") or a.get("stopped")):
            continue
        if only_id and a["agent_id"] != only_id:
            continue
        if project and a.get("metadata", {}).get("project") != project:
            continue
        if len(out) == limit:
            more = True
            break
        out.append(a)
    res = {"agents": out, "more": more}
    if out:
        res["next_after"] = out[-1]["ordinal"]
    ok(res)
elif op == "create":
    draft = body["draft"]
    run_id = draft.get("scope", {}).get("run_id", "")
    if run_id.startswith("ses_") and state.get("register_first_session"):
        state["agents"][0]["native_session_id"] = run_id
        state_path.write_text(json.dumps(state))
    ok({"record_id": state["source_record"], "version": 1, "body": draft["body"]})
elif op == "event-publish":
    if body["kind"] in state.get("fail_publish", []):
        fail("ADMISSION_REFUSED", "synthetic refusal")
    event_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, body["request_id"]))
    ok({
        "event_id": event_id,
        "from": "agent/" + agent_id,
        "kind": body["kind"],
        "summary": body.get("summary", ""),
        "ref": body["ref"],
        "destination": body["destination"],
        "causation_id": body.get("causation_id", ""),
    })
elif op == "session-inbox-pending":
    ok({"requests": 1, "notices": 2})
elif op == "event-next":
    if state.get("empty_inbox"):
        ok({})
    ok({"delivery": state["delivery"]})
elif op == "history":
    if state.get("fail_history"):
        fail("NOT_FOUND", "synthetic missing source")
    ok({"record_id": body["record_id"], "versions": [{"version": body["version"], "body": state["source_body"]}]})
elif op in ("event-complete", "event-renew"):
    d = dict(state["delivery"], state="completed")
    if body.get("draft"):
        d["result"] = {"record_id": state["result_record"], "version": 1}
    else:
        d["event"] = dict(d["event"], kind="notice")
    ok(d)
elif op == "event-inspect":
    ok({
        "event": {
            "event_id": body["event_id"], "kind": "request", "from": "agent/" + agent_id,
            "created_at": "2026-11-18T00:00:00Z", "ref": {"record_id": state["source_record"], "version": 1},
            "destination": {"type": "agent", "name": "agent/" + state["peer_agent"]},
        },
        "deliveries": [{
            "delivery_id": state["delivery"]["delivery_id"],
            "consumer": "agent/" + state["peer_agent"],
            "state": "completed",
            "result": {"record_id": state["result_record"], "version": 1},
        }],
        "more": False,
    })
elif op == "index":
    # Capture OpenCode's generated ses_... session ID from search --run!
    ok({"schema": "cairn.agent-search/1", "index": [], "bytes_remaining": 0})
fail("INVALID_REQUEST", f"unexpected op {op}")
'''


class OpenCodeMessagingToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.opencode = shutil.which("opencode")
        if not cls.opencode:
            raise unittest.SkipTest("opencode binary is not installed on PATH")
        cls._tmp = tempfile.TemporaryDirectory(prefix="cairn-opencode-msg-test-")
        cls.root = Path(cls._tmp.name)
        cls.cairn_bin = cls.root / "cairn"
        subprocess.run(
            ["go", "build", "-o", str(cls.cairn_bin), "./cmd/cairn"],
            cwd=REPO_ROOT, check=True, timeout=60,
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "_tmp"):
            cls._tmp.cleanup()

    def test_messaging_tools_end_to_end_and_compact_profile(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        work = self.root / "work"
        work.mkdir()
        installed = subprocess.run(
            [str(self.cairn_bin), "opencode-install", "--project", str(work),
             "--socket", str(self.root / "api.sock"),
             "--token-file", str(self.root / "agent.token"),
             "--repo", "fixture:opencode-messaging", "--messaging"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertTrue(json.loads(installed.stdout)["ok"])
        fake_cli = self.root / "fake-cairn"
        fake_cli.write_text(FAKE_CAIRN_CLI, encoding="utf-8")
        fake_cli.chmod(0o700)
        calls_path = self.root / "calls.jsonl"
        state_path = self.root / "state.json"

        settings_path = work / ".opencode/cairn.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertIs(settings.get("messaging"), True)
        settings["executable"] = str(fake_cli)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        self_agent = "11111111-1111-4111-8111-111111111111"
        self_exec = "22222222-2222-4222-8222-222222222222"
        peer_agent = "33333333-3333-4333-8333-333333333333"
        peer_exec = "44444444-4444-4444-8444-444444444444"
        stopped_agent = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        source_record = "55555555-5555-4555-8555-555555555555"
        result_record = "66666666-6666-4666-8666-666666666666"
        delivery_id = "77777777-7777-4777-8777-777777777777"
        lease_id = "88888888-8888-4888-8888-888888888888"
        request_event = "99999999-9999-4999-8999-999999999999"

        base_state = {
            "register_first_session": True,
            "peer_agent": peer_agent,
            "source_record": source_record,
            "result_record": result_record,
            "source_body": "Please review the OpenCode messaging adapter.",
            "fail_publish": [],
            "delivery": {
                "delivery_id": delivery_id, "lease_id": lease_id, "state": "leased", "attempts": 1,
                "event": {
                    "event_id": request_event, "kind": "request", "from": "agent/" + peer_agent,
                    "summary": "Review OpenCode messaging adapter",
                    "created_at": "2026-11-18T00:00:00Z", "ref": {"record_id": source_record, "version": 3},
                    "destination": {"type": "agent", "name": "agent/" + self_agent},
                },
            },
            "agents": [
                {"agent_id": self_agent, "ordinal": 7, "display_name": "agent-7", "native_session_id": "unbound",
                 "inbox": "agent/" + self_agent, "execution_id": self_exec, "online": True, "stopped": False,
                 "last_seen": "2026-11-18T00:00:00Z", "metadata": {"harness": "opencode", "project": "cairn", "state": "busy"}},
                {"agent_id": peer_agent, "ordinal": 9, "display_name": "agent-9", "native_session_id": "ses_peer",
                 "inbox": "agent/" + peer_agent, "execution_id": peer_exec, "online": True, "stopped": False,
                 "last_seen": "2026-11-18T00:00:00Z", "metadata": {"harness": "claude", "project": "cairn", "state": "idle"}},
                {"agent_id": stopped_agent, "ordinal": 12, "display_name": "agent-12", "native_session_id": "ses_stopped",
                 "inbox": "agent/" + stopped_agent, "execution_id": str(uuid.uuid4()), "online": False, "stopped": True,
                 "last_seen": "2026-11-18T00:00:00Z", "metadata": {"harness": "codex", "project": "cairn"}},
            ],
        }
        state_path.write_text(json.dumps(base_state), encoding="utf-8")

        send_req = str(uuid.uuid4())
        comp_req = str(uuid.uuid4())
        ack_req = str(uuid.uuid4())
        scripted_calls = [
            ("unknown-self", "cairn_agents", {"agent": "self"}),
            ("bind-session", "cairn_remember", {"request_id": str(uuid.uuid4()), "body": "bind session", "scope": "run"}),
            ("self-lookup", "cairn_agents", {"agent": "self"}),
            ("stopped-send", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-12", "action": "review", "body": "Hi"}),
            ("notice-no-action", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "body": "Missing action"}),
            ("cd1-act-mismatch", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "action": "none", "body": "cd1 cairn T1 open act=work\nobs ok"}),
            ("cd1-action-none-blk", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "action": "none", "body": "cd1 cairn T1 blocked\nblk T1 wait"}),
            ("cd1-unknown-version", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "action": "review", "body": "cd2 cairn T1 open\nobs ok"}),
            ("long-no-summary", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "action": "review", "body": "x" * 281}),
            ("long-with-summary", "cairn_send", {"request_id": str(uuid.uuid4()), "to": "agent-9", "action": "review", "summary": "Long OpenCode report", "body": "x" * 281}),
            ("valid-send", "cairn_send", {"request_id": send_req, "to": "agent-9", "action": "review", "body": "Synthetic notice.", "urgency": "urgent"}),
            ("inbox-peek", "cairn_inbox", {"peek": True}),
            ("inbox-claim", "cairn_inbox", {"lease_seconds": 600}),
            ("renew-lease", "cairn_renew", {"delivery_id": delivery_id, "lease_id": lease_id, "lease_seconds": 900}),
            ("complete-req", "cairn_complete", {"request_id": comp_req, "delivery_id": delivery_id, "lease_id": lease_id, "body": "Done."}),
            ("ack-notice", "cairn_ack", {"request_id": ack_req, "delivery_id": delivery_id, "lease_id": lease_id}),
            ("inspect-ev", "cairn_event_status", {"event_id": "cairn:" + request_event}),
        ]
        captured_requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(size))
                captured_requests.append(req)
                tool_reqs = [r for r in captured_requests if r.get("tools")]
                if not req.get("tools"):
                    delta, finish = {"role": "assistant", "content": "Messaging check"}, "stop"
                elif len(tool_reqs) <= len(scripted_calls):
                    cid, tname, targs = scripted_calls[len(tool_reqs) - 1]
                    delta = {"role": "assistant", "tool_calls": [{
                        "index": 0, "id": cid, "type": "function",
                        "function": {"name": tname, "arguments": json.dumps(targs)},
                    }]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"role": "assistant", "content": "Done."}, "stop"
                chunks = [
                    {"id": "f", "object": "chat.completion.chunk", "created": 1, "model": "probe",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {"id": "f", "object": "chat.completion.chunk", "created": 1, "model": "probe",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                ]
                payload = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        config = {
            "model": "fixture/probe",
            "provider": {"fixture": {
                "npm": "@ai-sdk/openai-compatible", "name": "Scripted fixture",
                "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1", "apiKey": "unused"},
                "models": {"probe": {"name": "Probe", "limit": {"context": 32000, "output": 1000}}},
            }},
            "permission": {
                "*": "deny",
                **{f"cairn_{name}": "allow" for name in (*MEMORY_TOOLS, *MESSAGING_TOOLS)},
            },
        }
        config_path = work / "opencode.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        for key, folder in [("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                            ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state")]:
            d = work / folder
            d.mkdir()
            env[key] = str(d)
        env.update(
            OPENCODE_CONFIG=str(config_path),
            OPENCODE_DISABLE_AUTOUPDATE="true",
            OPENCODE_DISABLE_MODELS_FETCH="true",
            OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
            CAIRN_OPENCODE_TOOL_PROFILE="compact/1",
        )
        thread.start()
        try:
            proc = subprocess.run(
                [self.opencode, "run", "--format", "json", "--model", "fixture/probe", "Run scripted check."],
                cwd=work, env=env, capture_output=True, text=True, timeout=45,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        tool_reqs = [r for r in captured_requests if r.get("tools")]
        declared_names = {t["function"]["name"] for t in tool_reqs[0]["tools"] if t["function"]["name"].startswith("cairn_")}
        self.assertEqual(declared_names, {f"cairn_{n}" for n in (*MEMORY_TOOLS, *MESSAGING_TOOLS)})

        final_messages = tool_reqs[-1]["messages"]
        tool_outputs = {m["tool_call_id"]: m["content"] for m in final_messages if m.get("role") == "tool"}
        self.assertIn("IDENTITY_UNKNOWN", str(tool_outputs["unknown-self"]))
        self.assertIn("RECIPIENT_STOPPED", str(tool_outputs["stopped-send"]))
        self.assertIn("none, review, or decide", str(tool_outputs["notice-no-action"]))
        self.assertIn("disagreeing with envelope action=none", str(tool_outputs["cd1-act-mismatch"]))
        self.assertIn("forbids cd1 opcode blk", str(tool_outputs["cd1-action-none-blk"]))
        self.assertIn("unsupported cd1 protocol version cd2", str(tool_outputs["cd1-unknown-version"]))
        self.assertIn("is required when body exceeds 280 bytes", str(tool_outputs["long-no-summary"]))
        self.assertEqual(json.loads(tool_outputs["long-with-summary"])["summary"], "Long OpenCode report")

        me = json.loads(tool_outputs["self-lookup"])
        self.assertEqual(me["agents"][0]["agent_id"], self_agent)
        self.assertEqual(me["agents"][0]["execution_id"], self_exec)

        sent = json.loads(tool_outputs["valid-send"])
        self.assertEqual(sent["from"], "agent/" + self_agent)
        self.assertEqual(json.loads(tool_outputs["inbox-peek"]), {"requests": 1, "notices": 2})
        claimed = json.loads(tool_outputs["inbox-claim"])
        self.assertEqual(claimed["body"], base_state["source_body"])
        self.assertEqual(claimed["header"], {"summary": "Review OpenCode messaging adapter", "action_needed": "complete"})
        self.assertEqual(json.loads(tool_outputs["renew-lease"])["delivery_id"], delivery_id)
        done = json.loads(tool_outputs["complete-req"])
        self.assertTrue(done.get("reply_event_id"))
        self.assertIsNone(done.get("reply_pending"))
        self.assertEqual(json.loads(tool_outputs["ack-notice"])["state"], "completed")
        self.assertEqual(json.loads(tool_outputs["inspect-ev"])["deliveries"][0]["consumer"], "agent/" + peer_agent)

        cli_calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        send_creates = [c for c in cli_calls if c["op"] == "create" and c["body"]["draft"]["body"] == "Synthetic notice."]
        self.assertEqual(len(send_creates), 1)
        self.assertEqual(send_creates[0]["agent_id"], self_agent)
        self.assertEqual(send_creates[0]["execution_id"], self_exec)
        self.assertEqual(send_creates[0]["body"]["request_id"], uuidv5_sha1(send_req, "cairn_send/source"))
        reply_publishes = [c for c in cli_calls if c["op"] == "event-publish" and c["body"]["kind"] == "response"]
        self.assertEqual(len(reply_publishes), 1)
        self.assertEqual(reply_publishes[0]["body"]["request_id"], uuidv5_sha1(comp_req, "cairn_complete/response"))



if __name__ == "__main__":
    unittest.main()
