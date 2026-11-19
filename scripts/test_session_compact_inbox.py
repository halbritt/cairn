"""CAIRN-472: per-session compact inbox and notice batches on a shared live binding."""
import hashlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import test_f04_activation as f04

INSTALLER = ROOT / "scripts/install-agent-coordination.py"
installer_spec = importlib.util.spec_from_file_location("coord_installer_472", INSTALLER)
installer = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer)


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _claude_wake_prompt(native_id, agent_id, execution_id, delivery_id):
    body = installer.engine.wake_message(dict(
        session=dict(agent_id=agent_id, execution_id=execution_id),
        delivery_id=delivery_id,
    ))
    return (
        f'<channel source="cairn-events" native_session_id="{native_id}" '
        f'agent_id="{agent_id}" execution_id="{execution_id}" delivery_id="{delivery_id}">\n'
        f'{body}\n</channel>'
    )


class SharedBindingLiveSessionTests(unittest.TestCase):
    """Acceptance for CAIRN-472 on a shared live binding with enabled inbox recall."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.base.chmod(0o700)
        self.home = self.base / "home"
        self.bin_dir = self.home / ".local/bin"
        self.bin_dir.mkdir(parents=True, mode=0o700)
        self.claude_home = self.home / ".claude-harm"
        self.claude_home.mkdir(parents=True, mode=0o700)
        self.work = self.base / "work"
        (self.work / ".git").mkdir(parents=True)
        self.coord_root = self.base / "coordination"
        (self.coord_root / "bindings").mkdir(parents=True, mode=0o700)
        self.state_dir = self.coord_root / "state" / "claude-two"
        self.state_dir.mkdir(parents=True, mode=0o700)
        self.mem_state = self.base / "memory-state"
        self.mem_state.mkdir(mode=0o700)
        self.token = self.base / "hosted-agent.token"
        self.token.write_text("fixture-token")
        self.token.chmod(0o600)
        self.store_state = self.base / "store.json"
        self.api = self.base / "fake-cairn"
        self._write_fake_api()
        bundle_spec = importlib.util.spec_from_file_location(
            "bundle_472", ROOT / "integrations/lifecycle/lifecycle_bundle.py")
        self.bundle_mod = importlib.util.module_from_spec(bundle_spec)
        bundle_spec.loader.exec_module(self.bundle_mod)
        self.bundle_mod.stage_runtime(ROOT / "integrations/lifecycle", self.coord_root)
        self._stage_pinned_shared_binding()

    def _write_fake_api(self):
        script = '''#!/usr/bin/env python3
import hashlib, json, pathlib, sys
state_file = pathlib.Path(__file__).with_name("store.json")
st = json.loads(state_file.read_text())
args = sys.argv[1:]
if args[0] == "agent":
    idx = 1
    while args[idx].startswith("--"):
        idx += 2
    op = args[idx]
    req = json.loads(sys.stdin.read() or "{}")
    if op == "agent-register":
        info = st["agents"][req["native_session_id"]]
        info["metadata"] = req["metadata"]
        state_file.write_text(json.dumps(st))
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": info}))
    elif op in ("agent-heartbeat", "agent-context"):
        aid = (req.get("session") or req)["agent_id"]
        info = next(v for v in st["agents"].values() if v["agent_id"] == aid)
        if op == "agent-context":
            info["metadata"] = req["metadata"]
            info["context_revision"] += 1
            state_file.write_text(json.dumps(st))
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": info}))
    elif op == "search":
        data = {"status": "READY", "destination": {"name": "hosted", "allow_local": False},
                "selected": [{"mandatory": True, "record": {"record_id": st["required_id"], "version": 1, "kind": "instruction", "body": st["required_body"]}}],
                "index": [], "omitted": {}}
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": data}))
    elif op == "history":
        body = st["records"][req["record_id"]]
        raw = body.encode("utf-8")
        ver = {"version": req["version"], "payload_available": True, "body": body,
               "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest(), "span": None}
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK",
                          "data": {"record_id": req["record_id"], "historical": True, "versions": [ver]}}))
    elif op == "event-renew":
        d = st["deliveries"][req["delivery_id"]]
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": d}))
    elif op == "event-next":
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": {"delivery": None}}))
    elif op == "session-inbox-claim":
        did = req.get("delivery_id")
        att = ({"attempt_id": req["request_id"], "session": req["session"],
                "native_turn_id": req.get("native_turn_id"), "delivery": st["deliveries"][did]}
               if did else None)
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK", "data": {"attempt": att}}))
    elif op == "session-inbox-reconcile":
        print(json.dumps({"schema": "cairn.response/1", "ok": False, "status": "DELIVERY_ACTIVE", "message": "leased"}))
        raise SystemExit(4)
    elif op == "session-inbox-advance":
        nxt = st.get("advance_next")
        if nxt is not None:
            nxt = dict(nxt, attempt_id=req["request_id"], session=req["session"])
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK",
                          "data": {"attempt": nxt, "remainder": st.get("advance_remainder")}}))
    elif op in ("recall-observation", "session-inbox-pending"):
        print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK",
                          "data": {"requests": 0, "notices": 0, "responses": 0}}))
    else:
        raise SystemExit(f"unexpected agent op: {op}")
elif args[0] == "ack":
    did = args[-1]
    st["deliveries"][did]["state"] = "handled"
    st["deliveries"][did]["attempts"] = 1
    state_file.write_text(json.dumps(st))
    print(json.dumps({"schema": "cairn.response/1", "ok": True, "status": "OK",
                      "data": {"state": "handled", "attempts": 1}}))
else:
    raise SystemExit(f"unexpected cli cmd: {args}")
'''
        self.api.write_text(script)
        self.api.chmod(0o700)

    def _stage_pinned_shared_binding(self):
        self.coord_id = str(uuid.uuid4())
        self.worker_id = str(uuid.uuid4())
        self.coord_agent = str(uuid.uuid4())
        self.coord_exec = str(uuid.uuid4())
        self.worker_agent = str(uuid.uuid4())
        self.worker_exec = str(uuid.uuid4())
        self.required_id = str(uuid.uuid4())
        self.required_body = "Mandatory recall rule preserved across shared binding."
        self.rec_coord_1 = str(uuid.uuid4())
        self.rec_coord_2 = str(uuid.uuid4())
        self.rec_worker = str(uuid.uuid4())
        self.oversized_body = "Coordinator notice source straddling 4500B budget. " * 75
        self.second_body = "Second batched coordinator notice inline."
        self.worker_body = "Worker standard notice body."
        self.deliv_c1 = str(uuid.uuid4())
        self.deliv_c2 = str(uuid.uuid4())
        self.deliv_w1 = str(uuid.uuid4())
        self.ev_c1 = str(uuid.uuid4())
        self.ev_c2 = str(uuid.uuid4())
        self.ev_w1 = str(uuid.uuid4())
        deliveries = {
            self.deliv_c1: dict(delivery_id=self.deliv_c1, lease_id=str(uuid.uuid4()), state="leased",
                event=dict(event_id=self.ev_c1, kind="notice", ref=dict(record_id=self.rec_coord_1, version=1),
                           **{"from": "agent/lane-1"})),
            self.deliv_c2: dict(delivery_id=self.deliv_c2, lease_id=str(uuid.uuid4()), state="leased",
                event=dict(event_id=self.ev_c2, kind="notice", ref=dict(record_id=self.rec_coord_2, version=1),
                           **{"from": "agent/lane-2"})),
            self.deliv_w1: dict(delivery_id=self.deliv_w1, lease_id=str(uuid.uuid4()), state="leased",
                event=dict(event_id=self.ev_w1, kind="notice", ref=dict(record_id=self.rec_worker, version=1),
                           **{"from": "agent/lead"})),
        }
        self.store_state.write_text(json.dumps(dict(
            required_id=self.required_id, required_body=self.required_body,
            records={self.rec_coord_1: self.oversized_body, self.rec_coord_2: self.second_body,
                     self.rec_worker: self.worker_body},
            deliveries=deliveries,
            agents={
                self.coord_id: dict(agent_id=self.coord_agent, execution_id=self.coord_exec,
                    native_session_id=self.coord_id, display_name="agent-coord",
                    inbox="agent/coord", context_revision=1, metadata={}),
                self.worker_id: dict(agent_id=self.worker_agent, execution_id=self.worker_exec,
                    native_session_id=self.worker_id, display_name="agent-worker",
                    inbox="agent/worker", context_revision=1, metadata={}),
            },
            advance_next=None, advance_remainder="batch_empty",
        )))
        self.recall_path = self.base / "recall-binding.json"
        self.coord_cfg_path = self.coord_root / "bindings" / "claude-two.json"
        self.mem_cfg_path = self.base / "memory.json"
        common = dict(cairn=str(self.api), socket=str(self.base / "api.sock"),
                      token_file=str(self.token), repo="fixture:cairn-472",
                      harness="claude", inbox_recall_binding=str(self.recall_path))
        coord_cfg = dict(common, binding="claude-two", state_dir=str(self.state_dir),
                         config_home=str(self.claude_home), native_delivery=True,
                         process_names=["python3", "python"])
        mem_cfg = dict(common, state_dir=str(self.mem_state), config_home=str(self.claude_home),
                       recall_mode="agent_tools", context_bytes=4500)
        for path, doc in ((self.coord_cfg_path, coord_cfg), (self.mem_cfg_path, mem_cfg)):
            path.write_text(json.dumps(doc, sort_keys=True))
            path.chmod(0o600)
        desc = self.bundle_mod.bundle_descriptor(self.coord_root)
        pin = lambda p: dict(path=str(p), sha256=_sha256(p.read_bytes()))
        manifest = dict(
            schema="cairn.inbox-recall-binding/2", enabled=True,
            bridge=dict(pin(self.coord_root / "inbox_recall.py"), bundle=desc),
            engine=dict(pin(self.coord_root / "memory.py"), bundle=desc),
            memory_config=pin(self.mem_cfg_path),
            coordination_configs=[pin(self.coord_cfg_path)],
        )
        self.recall_path.write_text(json.dumps(manifest, sort_keys=True))
        self.recall_path.chmod(0o600)

    def _env(self):
        env = dict(os.environ, HOME=str(self.home), CLAUDE_CONFIG_DIR=str(self.claude_home),
                   PATH=str(self.bin_dir) + os.pathsep + os.environ["PATH"])
        for k in ("CAIRN_LIFECYCLE_DISABLED", "CAIRN_COORDINATION_DISABLED",
                  "CAIRN_MEMORY_DISABLED", "CAIRN_INBOX_RECALL_DISABLED", "CAIRN_WAKE_CONTEXT"):
            env.pop(k, None)
        return env

    def _run_hook(self, native_id, event_name, *, prompt="", prompt_id="p-1"):
        payload = dict(hook_event_name=event_name, session_id=native_id, cwd=str(self.work),
                       prompt=prompt, prompt_id=prompt_id, host_pid=os.getpid())
        res = subprocess.run(
            [sys.executable, "-B", str(self.coord_root / "coordination.py"),
             "hook", "--config", str(self.coord_cfg_path)],
            input=json.dumps(payload), text=True, capture_output=True, env=self._env(), timeout=20,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def _seed_bound_wake(self, native_id, agent_id, exec_id, delivery_id, prompt_id):
        cfg = json.loads(self.coord_cfg_path.read_text())
        s_path = installer.engine.state_path(cfg, native_id)
        st = json.loads(s_path.read_text())
        st["idle_wake"] = dict(
            transport="claude-channel", status="submitted", native_id=native_id,
            session=dict(agent_id=agent_id, execution_id=exec_id),
            delivery_id=delivery_id, parent=st["process"],
        )
        installer.engine.write_state(s_path, st)
        return _claude_wake_prompt(native_id, agent_id, exec_id, delivery_id)

    def test_one_coordinator_gets_compact_and_batches_while_worker_keeps_standard_recall_without_watcher_restart(self):
        before_cfg = self.coord_cfg_path.read_bytes()
        before_manifest = self.recall_path.read_bytes()

        # Register both live sessions on the shared claude-two binding.
        self._run_hook(self.coord_id, "SessionStart")
        self._run_hook(self.worker_id, "SessionStart")

        # 1. Binding-wide --compact-inbox still refuses while the recall manifest is enabled.
        refused = subprocess.run(
            [sys.executable, "-B", str(INSTALLER), "--harness", "claude", "--binding", "claude-two",
             "--settings", str(self.claude_home / "settings.json"), "--root", str(self.coord_root),
             "--cairn", str(self.api), "--socket", str(self.base / "api.sock"),
             "--token-file", str(self.token), "--no-service", "--native-delivery", "--compact-inbox"],
            capture_output=True, text=True, env=self._env(), timeout=10,
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("manifest disabled", refused.stderr)
        self.assertEqual(self.coord_cfg_path.read_bytes(), before_cfg)

        # 2. Per-session enablement via --agent-id succeeds without touching pinned config or restarting service.
        argv = ["install-agent-coordination.py", "--binding", "claude-two", "--root", str(self.coord_root),
                "--agent-id", self.coord_agent, "--compact-inbox", "--notice-batch"]
        out_buf = io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), \
                mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "stdout", out_buf), \
                mock.patch.object(installer, "install_service", side_effect=AssertionError("watcher restarted")):
            installer.main()
        configured = json.loads(out_buf.getvalue())
        self.assertEqual(configured["status"], "configured")
        self.assertEqual(configured["native_session_id"], self.coord_id)
        self.assertEqual((configured["compact_inbox_version"], configured["notice_batch_version"]), (1, 1))
        self.assertEqual(self.coord_cfg_path.read_bytes(), before_cfg)
        self.assertEqual(self.recall_path.read_bytes(), before_manifest)
        self.assertTrue((self.bin_dir / "cairn-inbox").is_file())

        # 3. Coordinator wakes on oversized notice: gets bound recall + compact fallback + advance batching!
        wake_c = self._seed_bound_wake(self.coord_id, self.coord_agent, self.coord_exec, self.deliv_c1, "p-coord-1")
        out_c = self._run_hook(self.coord_id, "UserPromptSubmit", prompt=wake_c, prompt_id="p-coord-1")
        text_c = out_c["hookSpecificOutput"]["additionalContext"]
        self.assertIn("cairn.inbox-recall/1", text_c)
        self.assertIn(self.required_body, text_c)
        self.assertNotIn(self.oversized_body, text_c)
        self.assertLessEqual(len(json.dumps(out_c, ensure_ascii=False, separators=(",", ":")).encode()), 4500)

        coord_ctxs = [json.loads(p.read_text()) for p in (self.state_dir / "inbox").glob("*.json")
                      if json.loads(p.read_text()).get("event_id") == self.ev_c1]
        self.assertEqual(len(coord_ctxs), 1)
        handle_1 = coord_ctxs[0]["compact_handle"]
        self.assertIn(f"cairn-inbox read {handle_1}", text_c)
        self.assertIn(f"cairn-inbox settle {handle_1} handled", text_c)
        self.assertIn(f"cairn-inbox advance {handle_1}", text_c)

        env_coord = dict(self._env(), CLAUDE_SESSION_ID=self.coord_id)
        read_res = json.loads(subprocess.run(["cairn-inbox", "read", handle_1],
                                             capture_output=True, text=True, env=env_coord, check=True).stdout)
        self.assertEqual(read_res["body"], self.oversized_body)
        settle_res = json.loads(subprocess.run(["cairn-inbox", "settle", handle_1, "handled"],
                                               capture_output=True, text=True, env=env_coord, check=True).stdout)
        self.assertEqual(settle_res["status"], "handled")

        st_doc = json.loads(self.store_state.read_text())
        st_doc["advance_next"] = dict(
            batch_root=coord_ctxs[0]["attempt_id"], batch_index=2, batch_limit=3,
            native_turn_id="claude-channel:p-coord-1",
            delivery=st_doc["deliveries"][self.deliv_c2],
        )
        self.store_state.write_text(json.dumps(st_doc))
        adv_res = json.loads(subprocess.run(["cairn-inbox", "advance", handle_1],
                                            capture_output=True, text=True, env=env_coord, check=True).stdout)
        self.assertEqual((adv_res["ok"], adv_res["batch_index"], adv_res["batch_limit"]), (True, 2, 3))
        self.assertIn(self.second_body, adv_res["next"])

        # 4. Worker on the SAME shared binding gets standard non-compact bound recall!
        wake_w = self._seed_bound_wake(self.worker_id, self.worker_agent, self.worker_exec, self.deliv_w1, "p-worker-1")
        out_w = self._run_hook(self.worker_id, "UserPromptSubmit", prompt=wake_w, prompt_id="p-worker-1")
        text_w = out_w["hookSpecificOutput"]["additionalContext"]
        self.assertIn("cairn.inbox-recall/1", text_w)
        self.assertIn(self.required_body, text_w)
        self.assertIn("Read the structured inbox context at", text_w)
        self.assertNotIn("cairn-inbox", text_w)
        worker_ctxs = [json.loads(p.read_text()) for p in (self.state_dir / "inbox").glob("*.json")
                       if json.loads(p.read_text()).get("event_id") == self.ev_w1]
        self.assertEqual(len(worker_ctxs), 1)
        self.assertNotIn("compact_handle", worker_ctxs[0])

        # 5. --clear-session-options removes override cleanly without touching pinned config.
        clr_argv = ["install-agent-coordination.py", "--binding", "claude-two", "--root", str(self.coord_root),
                    "--session", self.coord_id, "--clear-session-options"]
        clr_out = io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), \
                mock.patch.object(sys, "argv", clr_argv), \
                mock.patch.object(sys, "stdout", clr_out):
            installer.main()
        self.assertEqual(json.loads(clr_out.getvalue())["status"], "cleared")
        self.assertEqual(self.coord_cfg_path.read_bytes(), before_cfg)

    def test_excluded_harnesses_and_tampered_options_fail_closed(self):
        self._run_hook(self.coord_id, "SessionStart")
        # Excluded harness (agy) refuses per-session compact opt-in.
        agy_cfg = dict(json.loads(self.coord_cfg_path.read_text()), harness="agy", binding="agy-one",
                       state_dir=str(self.coord_root / "state" / "agy-one"))
        agy_cfg.pop("inbox_recall_binding", None)
        agy_path = self.coord_root / "bindings" / "agy-one.json"
        agy_path.write_text(json.dumps(agy_cfg))
        agy_path.chmod(0o600)
        res = subprocess.run(
            [sys.executable, "-B", str(INSTALLER), "--binding", "agy-one", "--root", str(self.coord_root),
             "--session", self.coord_id, "--compact-inbox"],
            capture_output=True, text=True, env=self._env(), timeout=10,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("Codex or Claude", res.stderr)

        # Group-writable session-options file refuses hook admission and inbox_recall with command_context/compact_fallback.
        cfg = json.loads(self.coord_cfg_path.read_text())
        installer.engine.configure_session_inbox(cfg, native_session_id=self.coord_id, compact_inbox=True)
        opt_path = installer.engine.session_options_path(cfg, self.coord_id)
        opt_path.chmod(0o660)
        bridge = self.bundle_mod.Bundle(self.coord_root).load("inbox_recall")
        with self.assertRaises(ValueError) as caught:
            bridge.session_compact_inbox_enabled(cfg, self.coord_id)
        self.assertEqual(caught.exception.cairn_inbox_refusal,
                         dict(refusal_stage="command_context", refusal_reason="compact_fallback"))

        # Overflow integer updated_at (10**310), duplicate JSON keys, oversized (>65536B), and symlink all fail closed.
        for bad_raw in (
            json.dumps(dict(schema="cairn.session-inbox-options/1", binding="claude-two",
                            native_session_id=self.coord_id, compact_inbox_version=1, updated_at=10**310)),
            '{"schema":"cairn.session-inbox-options/1","schema":"cairn.session-inbox-options/1","binding":"claude-two","native_session_id":"' + self.coord_id + '","compact_inbox_version":1}',
            "{" + " " * 66000 + "}",
        ):
            opt_path.write_text(bad_raw)
            opt_path.chmod(0o600)
            with self.assertRaises((installer.engine.CoordinationError, ValueError)):
                installer.engine.effective_session_inbox_options(cfg, self.coord_id)
            with self.assertRaises(ValueError) as caught_b:
                bridge.session_compact_inbox_enabled(cfg, self.coord_id)
            self.assertEqual(caught_b.exception.cairn_inbox_refusal["refusal_reason"], "compact_fallback")

        opt_path.unlink()
        target_other = self.base / "foreign-opt.json"
        target_other.write_text("{}")
        target_other.chmod(0o600)
        opt_path.symlink_to(target_other)
        with self.assertRaises(installer.engine.CoordinationError) as sym_err:
            installer.engine.effective_session_inbox_options(cfg, self.coord_id)
        self.assertEqual(sym_err.exception.code, "INVALID_HOST")
        with self.assertRaises(ValueError) as sym_b:
            bridge.session_compact_inbox_enabled(cfg, self.coord_id)
        self.assertEqual(sym_b.exception.cairn_inbox_refusal["refusal_reason"], "compact_fallback")
        opt_path.unlink()

        # Unknown --agent-id refuses BEFORE writing ~/.local/bin/cairn-inbox.
        self.assertFalse((self.bin_dir / "cairn-inbox").exists())
        bad_agent = subprocess.run(
            [sys.executable, "-B", str(INSTALLER), "--binding", "claude-two", "--root", str(self.coord_root),
             "--agent-id", str(uuid.uuid4()), "--compact-inbox"],
            capture_output=True, text=True, env=self._env(), timeout=10,
        )
        self.assertEqual(bad_agent.returncode, 2)
        self.assertFalse((self.bin_dir / "cairn-inbox").exists())


class PublicGoV3SessionCompactTests(unittest.TestCase):
    """End-to-end /3 public Go cairn-lifecycle + Python CLI differential parity for CAIRN-472."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.lifecycle_bin = Path(cls._tmp.name) / "cairn-lifecycle"
        subprocess.run(["go", "build", "-o", str(cls.lifecycle_bin), "./cmd/cairn-lifecycle"],
                       cwd=str(ROOT), check=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_v3_go_coordination_and_inbox_recall_ipc_on_shared_binding_without_repinning(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        fx = f04.Fixture(root / "v3", self.lifecycle_bin, harness="codex", compact=True, binary_name="cairn-lifecycle")
        fx.coord.pop("compact_inbox_version", None)
        fx.coord.pop("notice_batch_version", None)
        fx.coord["process_names"] = ["python3", "python"]
        fx.memory["context_bytes"] = 4500

        token = fx.root / "recall-token"
        sock = fx.root / "recall.sock"
        f04.write(token, b"f04-synthetic-token", 0o600)
        for cfg in (fx.memory, fx.coord):
            cfg.update(socket=str(sock), token_file=str(token))
        fx.publish_configuration()

        before_coord = fx.coord_path.read_bytes()
        before_binding = fx.binding.read_bytes()
        before_receipt = fx.receipt_path.read_bytes()

        oversized = "V3 compact notice source straddling 4500B budget. " * 76
        second_notice = "Second v3 batched notice."
        rec_1 = "77777777-7777-4777-8777-777777777777"
        rec_2 = "88888888-8888-4888-8888-888888888888"
        ev_1 = "99999999-9999-4999-8999-999999999999"
        ev_2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        deliv_id_1 = "55555555-5555-4555-8555-555555555555"
        deliv_1 = dict(delivery_id=deliv_id_1, lease_id="lease-1", state="leased",
                       event=dict(event_id=ev_1, kind="notice", ref=dict(record_id=rec_1, version=1),
                                  **{"from": "agent/lane-1"}))
        deliv_2 = dict(delivery_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", lease_id="lease-2", state="leased",
                       event=dict(event_id=ev_2, kind="notice", ref=dict(record_id=rec_2, version=1),
                                  **{"from": "agent/lane-2"}))

        def hist_entry(body):
            raw = body.encode("utf-8")
            return dict(version=1, payload_available=True, body=body,
                        body_bytes=len(raw), body_sha256=_sha256(raw), span=None)

        fx.replies({
            "history": [
                dict(ok=True, status="OK", data=dict(record_id=rec_1, historical=True, versions=[hist_entry(oversized)])),
                dict(ok=True, status="OK", data=dict(record_id=rec_1, historical=True, versions=[hist_entry(oversized)])),
                dict(ok=True, status="OK", data=dict(record_id=rec_2, historical=True, versions=[hist_entry(second_notice)])),
            ],
            "event-renew": [dict(ok=True, status="OK", data=deliv_1),
                            dict(ok=True, status="OK", data=deliv_1),
                            dict(ok=True, status="OK", data=deliv_2),
                            dict(ok=True, status="OK", data=deliv_1)],
            "session-inbox-reconcile": [dict(ok=False, status="DELIVERY_ACTIVE", exit=4)],
            "ack": [dict(ok=True, status="OK", data=dict(state="handled", attempts=1))],
            "session-inbox-advance": [dict(
                ok=True, status="OK", advance_identity=True,
                data=dict(remainder="none", attempt=dict(
                    batch_root="att-v3-1", batch_index=2, batch_limit=3,
                    native_turn_id=f04.TURN, delivery=deliv_2)),
            )],
        })

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(inner):
                req = json.loads(inner.rfile.read(int(inner.headers["Content-Length"])))
                op = inner.path.removeprefix("/v1/")
                if op == "version":
                    data = dict(preview_capabilities=dict(schema="cairn.preview-capabilities/1", entities_omitted=True))
                elif op == "index":
                    data = dict(package=dict(receipt_id=f04.SESSION, nonce="", seal="blake3:" + "a" * 64,
                        semantic=dict(schema="cairn.semantic/17", mode="index", status="READY",
                            scope=req["scope"], purpose="context",
                            destination=dict(name="hosted", allow_local=False),
                            query="sha256:q", available_tokens=32000,
                            selected=[dict(mandatory=True, record=dict(body="V3 mandatory rule."))],
                            omitted={}, index=[])),
                        handles=[], expires_at="2030-01-01T00:00:00Z", credits_remaining=4, bytes_remaining=24000)
                elif op == "event-renew":
                    data = deliv_1
                elif op == "recall-observation":
                    data = {}
                else:
                    inner.send_error(400); return
                inner.send_response(200); inner.end_headers()
                inner.wfile.write(f04.encoded(dict(schema="cairn.response/1", ok=True, status="OK", data=data)))

        class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
        srv = UnixServer(str(sock), Handler)
        thr = threading.Thread(target=srv.serve_forever, daemon=True); thr.start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), thr.join()))

        # Stage the verified /3 compatibility launcher in fx.launch_root, then run Go cairn-lifecycle --receipt!
        v3_launcher = fx.launcher("coordination")
        go_cmd = [str(fx.binary), "coordination", "--receipt", str(fx.receipt_path),
                  "--receipt-sha256", fx.receipt_sha, "--role", "coordination",
                  "--entrypoint", "coordination-hook.py", "--config", str(fx.coord_path),
                  "--hook-timeout", "15", "--launch-root", str(fx.launch_root), "--shell", "/bin/sh", "--"]

        # Register coordinator on Go /3 hook.
        start_ev = dict(hook_event_name="SessionStart", session_id=f04.SESSION, turn_id="t-0",
                        cwd=str(fx.root), prompt="", host_pid=os.getpid())
        reg = subprocess.run([*go_cmd, "hook", "--config", str(fx.coord_path)], input=f04.encoded(start_ev),
                             capture_output=True, cwd=fx.root, env=fx.env(), timeout=15)
        self.assertEqual((reg.returncode, reg.stderr), (0, b""), reg.stdout)

        # Enable compact + batch on coordinator via Go cairn-lifecycle CLI!
        cfg_cmd = subprocess.run(
            [str(fx.binary), "coordination", "configure-session-inbox", "--config", str(fx.coord_path),
             "--native-session-id", f04.SESSION, "--compact-inbox", "--notice-batch"],
            capture_output=True, text=True, cwd=fx.root, env=fx.env(), timeout=10,
        )
        self.assertEqual((cfg_cmd.returncode, cfg_cmd.stderr), (0, ""))
        self.assertEqual(fx.coord_path.read_bytes(), before_coord)
        self.assertEqual(fx.binding.read_bytes(), before_binding)
        self.assertEqual(fx.receipt_path.read_bytes(), before_receipt)

        # Seed admitted notice attempt on coordinator and fire bound wake through Go /3 hook + inboxipc!
        s_path = installer.engine.state_path(fx.coord, f04.SESSION)
        st = json.loads(s_path.read_text())
        sess_ref = dict(agent_id=st["agent"]["agent_id"], execution_id=st["agent"]["execution_id"])
        st["inbox_intent"] = dict(request_id="att-v3-1", session=sess_ref, native_turn_id=f04.TURN)
        st["inbox_attempt"] = dict(attempt_id="att-v3-1", session=sess_ref, native_turn_id=f04.TURN, delivery=deliv_1)
        installer.engine.write_state(s_path, st)

        wake_ev = dict(fx.event, host_pid=os.getpid())
        wake_res = subprocess.run([*go_cmd, "hook", "--config", str(fx.coord_path)], input=f04.encoded(wake_ev),
                                  capture_output=True, cwd=fx.root, env=fx.env(), timeout=15)
        self.assertEqual((wake_res.returncode, wake_res.stderr), (0, b""), wake_res.stdout)
        hook_out = json.loads(wake_res.stdout.decode())
        text = hook_out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("V3 mandatory rule.", text)
        self.assertNotIn(oversized, text)
        self.assertLessEqual(len(wake_res.stdout), 4500)

        ctx_doc = json.loads((Path(fx.coord["state_dir"]) / "inbox" / "att-v3-1.json").read_text())
        handle = ctx_doc["compact_handle"]
        self.assertIn(f"{v3_launcher} inbox compact read {handle}", text)
        self.assertIn(f"{v3_launcher} inbox compact advance {handle}", text)

        env_v3 = dict(fx.env(), CODEX_THREAD_ID=f04.SESSION, CAIRN_INBOX_ROOT=str(fx.root))
        settled = json.loads(subprocess.run(
            [*go_cmd, "inbox", "compact", "settle", handle, "handled"],
            capture_output=True, text=True, cwd=fx.root, env=env_v3, check=True).stdout)
        self.assertEqual(settled["status"], "handled")
        advanced = json.loads(subprocess.run(
            [*go_cmd, "inbox", "compact", "advance", handle],
            capture_output=True, text=True, cwd=fx.root, env=env_v3, check=True).stdout)
        self.assertEqual((advanced["ok"], advanced["batch_index"]), (True, 2))
        self.assertIn(second_notice, advanced["next"])

        # Worker session on the SAME /3 receipt receives standard non-compact bound recall!
        worker_sess_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        worker_turn_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
        w_start = dict(hook_event_name="SessionStart", session_id=worker_sess_id, turn_id="t-w0",
                       cwd=str(fx.root), prompt="", host_pid=os.getpid())
        w_reg = subprocess.run([*go_cmd, "hook", "--config", str(fx.coord_path)], input=f04.encoded(w_start),
                               capture_output=True, cwd=fx.root, env=fx.env(), timeout=15)
        self.assertEqual((w_reg.returncode, w_reg.stderr), (0, b""))
        w_path = installer.engine.state_path(fx.coord, worker_sess_id)
        w_st = json.loads(w_path.read_text())
        w_ref = dict(agent_id=w_st["agent"]["agent_id"], execution_id=w_st["agent"]["execution_id"])
        w_st["inbox_intent"] = dict(request_id="att-v3-w1", session=w_ref, native_turn_id=worker_turn_id)
        w_st["inbox_attempt"] = dict(attempt_id="att-v3-w1", session=w_ref, native_turn_id=worker_turn_id, delivery=deliv_1)
        installer.engine.write_state(w_path, w_st)
        w_wake = dict(hook_event_name="UserPromptSubmit", session_id=worker_sess_id, turn_id=worker_turn_id,
                      prompt_id="p-w1", cwd=str(fx.root), prompt=fx.wake(), host_pid=os.getpid())
        w_res = subprocess.run([*go_cmd, "hook", "--config", str(fx.coord_path)], input=f04.encoded(w_wake),
                               capture_output=True, cwd=fx.root, env=fx.env(), timeout=15)
        self.assertEqual((w_res.returncode, w_res.stderr), (0, b""), w_res.stdout)
        w_text = json.loads(w_res.stdout.decode())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("V3 mandatory rule.", w_text)
        self.assertIn("Read context", w_text)
        self.assertNotIn("inbox compact", w_text)
        w_ctx = json.loads((Path(fx.coord["state_dir"]) / "inbox" / "att-v3-w1.json").read_text())
        self.assertNotIn("compact_handle", w_ctx)

        # /3 VerifyTail refuses configure-session-inbox under --receipt preflight!
        preflight_refused = subprocess.run(
            [*go_cmd, "configure-session-inbox", "--config", str(fx.coord_path),
             "--native-session-id", f04.SESSION, "--compact-inbox"],
            capture_output=True, text=True, cwd=fx.root, env=fx.env(), timeout=10,
        )
        self.assertEqual(preflight_refused.returncode, 1)
        self.assertEqual(preflight_refused.stdout, "")
        self.assertIn("phase=compatibility_preflight error_class=CompatibilityPreflightError", preflight_refused.stderr)

    def test_python_and_go_configure_session_inbox_cli_differential_parity(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        py_state = root / "py-state"
        go_state = root / "go-state"
        py_state.mkdir(mode=0o700)
        go_state.mkdir(mode=0o700)
        py_cfg = root / "py.json"
        go_cfg = root / "go.json"
        base_cfg = dict(cairn="/bin/true", socket=str(root / "api.sock"), token_file=str(root / "tok"),
                        repo="fixture", binding="claude-two", harness="claude", native_delivery=True)
        py_cfg.write_text(json.dumps(dict(base_cfg, state_dir=str(py_state))))
        go_cfg.write_text(json.dumps(dict(base_cfg, state_dir=str(go_state))))
        py_cfg.chmod(0o600)
        go_cfg.chmod(0o600)

        cases = [
            (["configure-session-inbox"], 2),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "s1"], 2),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "s1", "--notice-batch"], 2),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "s1", "--compact-inbox", "--clear-session-options"], 2),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "", "--compact-inbox"], 1),
            (["configure-session-inbox", "--config", "<CFG>", "--agent-id", "33333333-3333-4333-8333-333333333333", "--compact-inbox"], 1),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "s1", "--compact-inbox", "--notice-batch"], 0),
            (["configure-session-inbox", "--config", "<CFG>", "--native-session-id", "s1", "--clear-session-options"], 0),
        ]
        for raw_args, expected_code in cases:
            py_args = [str(py_cfg) if a == "<CFG>" else a for a in raw_args]
            go_args = [str(go_cfg) if a == "<CFG>" else a for a in raw_args]
            py_res = subprocess.run([sys.executable, "-B", str(ROOT / "integrations/lifecycle/coordination.py"), *py_args],
                                    capture_output=True, text=True, timeout=10)
            go_res = subprocess.run([str(self.lifecycle_bin), "coordination", *go_args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(py_res.returncode, expected_code, py_res.stderr)
            self.assertEqual(go_res.returncode, expected_code, go_res.stderr)
            self.assertEqual(py_res.stderr, go_res.stderr)
            if py_res.stdout:
                py_doc = json.loads(py_res.stdout.replace(str(py_state), "<STATE>"))
                go_doc = json.loads(go_res.stdout.replace(str(go_state), "<STATE>"))
                self.assertEqual(py_doc, go_doc)


if __name__ == "__main__":
    unittest.main()
