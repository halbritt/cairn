import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_loopback_fixture as fixture


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    text = raw.decode("utf-8")
    blocks = [block for block in text.split("\n\n") if block.strip()]
    parsed = []
    for block in blocks:
        lines = block.splitlines()
        event_line = next(line for line in lines if line.startswith("event: "))
        data_line = next(line for line in lines if line.startswith("data: "))
        parsed.append((event_line[len("event: ") :], json.loads(data_line[len("data: ") :])))
    return parsed


class ResponseStreamAndItemTest(unittest.TestCase):
    def test_message_response_stream_frames_text_deltas_and_usage(self):
        item = fixture.message_item("msg_1", "Hello from loopback.", status="completed")
        raw = fixture.response_stream("resp_1", item, model="gpt-6-test", input_tokens=42, output_tokens=3)
        events = _parse_sse(raw)
        self.assertEqual(
            [name for name, _ in events],
            [
                "response.created",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        for index, (_, payload) in enumerate(events):
            self.assertEqual(payload["sequence_number"], index)
        self.assertEqual(events[3][1]["delta"], "Hello from loopback.")
        completed = events[-1][1]["response"]
        self.assertEqual(completed["id"], "resp_1")
        self.assertEqual(completed["model"], "gpt-6-test")
        self.assertEqual(
            completed["usage"],
            {"input_tokens": 42, "output_tokens": 3, "total_tokens": 45},
        )

    def test_function_call_response_stream_frames_arguments_done(self):
        item = fixture.function_call_item("call_1", "exec_command", {"cmd": "true"}, item_id="fc_1")
        events = _parse_sse(fixture.response_stream("resp_fc", item))
        self.assertEqual(
            [name for name, _ in events],
            [
                "response.created",
                "response.output_item.added",
                "response.function_call_arguments.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        self.assertEqual(json.loads(events[2][1]["arguments"]), {"cmd": "true"})

    def test_custom_tool_call_response_stream_omits_text_and_argument_deltas(self):
        item = fixture.exec_tool_item("read_1", "echo hi", max_output_tokens=1500)
        self.assertIn("1500", item["input"])
        events = _parse_sse(fixture.response_stream("resp_ctc", item))
        self.assertEqual(
            [name for name, _ in events],
            [
                "response.created",
                "response.output_item.added",
                "response.output_item.done",
                "response.completed",
            ],
        )

    def test_unsupported_response_stream_item_type_raises_value_error(self):
        with self.assertRaises(ValueError):
            fixture.response_stream("resp_bad", {"type": "unknown_item", "id": "bad"})

    def test_custom_tool_outputs_and_exec_declaration_check(self):
        request = {
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [
                        {
                            "name": "functions",
                            "tools": [
                                {
                                    "name": "exec",
                                    "description": "Run via tools.exec_command in JS.",
                                }
                            ],
                        }
                    ],
                },
                {"type": "message", "content": "ignored"},
                {"type": "custom_tool_call_output", "call_id": "c1", "output": "ok-1"},
                {"type": "custom_tool_call_output", "call_id": "c2", "output": "ok-2"},
            ],
        }
        fixture.assert_exec_tool_declared(request)
        outputs = fixture.custom_tool_outputs(request)
        self.assertEqual(set(outputs), {"c1", "c2"})
        self.assertEqual(outputs["c1"]["output"], "ok-1")
        with self.assertRaises((AssertionError, StopIteration)):
            fixture.assert_exec_tool_declared({"input": []})


class LoopbackResponsesServerTest(unittest.TestCase):
    def test_server_handles_responses_models_and_early_http_status(self):
        after_calls = []

        def handle(request, results):
            if request.get("stop_early"):
                return fixture.LoopbackHTTPResponse(status=503, message="stopped early")
            return fixture.message_item("msg_" + str(len(server.requests)), "ok:" + str(len(results)))

        with fixture.LoopbackResponsesServer(
            handle,
            model="gpt-6-loopback",
            enable_models_endpoint=True,
            max_requests=3,
            after_response=after_calls.append,
        ) as server:
            with urllib.request.urlopen(f"{server.base_url}/models") as resp:
                models = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(models["data"][0]["id"], "gpt-6-loopback")

            req_body = json.dumps(
                {
                    "tag": "first",
                    "input": [{"type": "custom_tool_call_output", "call_id": "c1", "output": "out"}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{server.base_url}/responses",
                data=req_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream")
                sse = _parse_sse(resp.read())
            self.assertEqual(sse[-1][0], "response.completed")
            self.assertEqual(len(server.requests), 1)
            self.assertIn("c1", server.results)
            self.assertEqual(len(after_calls), 1)

            stop_req = urllib.request.Request(
                f"{server.base_url}/responses",
                data=json.dumps({"stop_early": True, "input": []}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(stop_req)
            self.assertEqual(ctx.exception.code, 503)
            ctx.exception.close()
            self.assertEqual(server.failures, [])

    def test_server_records_handler_failures_and_budget_limits(self):
        error_hook_seen = []

        def handle(request, _results):
            if request.get("boom"):
                raise AssertionError("synthetic handler assertion")
            return fixture.message_item("msg_ok", "ok")

        with fixture.LoopbackResponsesServer(
            handle,
            max_requests=1,
            failure_format="type_and_message",
            on_error=error_hook_seen.append,
        ) as server:
            bad_req = urllib.request.Request(
                f"{server.base_url}/responses",
                data=json.dumps({"boom": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req)
            self.assertEqual(ctx.exception.code, 500)
            ctx.exception.close()
            self.assertEqual(server.failures, ["AssertionError: synthetic handler assertion"])
            self.assertEqual(len(error_hook_seen), 1)

            budget_req = urllib.request.Request(
                f"{server.base_url}/responses",
                data=json.dumps({"boom": False}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(budget_req)
            self.assertEqual(ctx.exception.code, 500)
            ctx.exception.close()
            self.assertIn("AssertionError: local Responses fixture bounds exceeded", server.failures)

    def test_http_413_and_404_when_http_status_on_invalid_enabled(self):
        with fixture.LoopbackResponsesServer(
            lambda _req, _res: fixture.message_item("msg_1", "ok"),
            max_requests=1,
            enforce_bounds_via_http_status=True,
            check_size_before_path=True,
        ) as server:
            req_404 = urllib.request.Request(
                f"{server.base_url}/unknown",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_404)
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()

            req_ok = urllib.request.Request(
                f"{server.base_url}/responses",
                data=b'{"input":[]}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req_ok) as resp:
                resp.read()

            req_413 = urllib.request.Request(
                f"{server.base_url}/responses",
                data=b'{"input":[]}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_413)
            self.assertEqual(ctx.exception.code, 413)
            ctx.exception.close()


class ProfileAndHookStagingTest(unittest.TestCase):
    def test_write_config_toml_and_prepare_synthetic_auth_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "codex-home"
            home.mkdir(mode=0o700)
            auth_path = fixture.prepare_synthetic_auth_fixture(home, api_key="sk-test-fixture")
            self.assertEqual(stat.S_IMODE(auth_path.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(auth_path.read_text(encoding="utf-8")),
                {"OPENAI_API_KEY": "sk-test-fixture"},
            )

            cfg_path = fixture.write_loopback_config_toml(
                home,
                45678,
                model="gpt-6-luna",
                model_context_window=1000,
                model_auto_compact_token_limit=50,
            )
            self.assertEqual(stat.S_IMODE(cfg_path.stat().st_mode), 0o600)
            cfg_text = cfg_path.read_text(encoding="utf-8")
            self.assertIn('base_url = "http://127.0.0.1:45678/v1"', cfg_text)
            self.assertIn("model_context_window = 1000", cfg_text)
            self.assertIn("model_auto_compact_token_limit = 50", cfg_text)

    def test_stage_and_wrap_hook_commands_and_trust_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir(mode=0o700)
            wrapper = fixture.stage_hook_metadata_wrapper(root)
            self.assertTrue(wrapper.exists())
            self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o600)

            metadata_path = root / "hook-metadata.jsonl"
            stop_path = root / "stop.json"
            settings_path = home / "hooks.json"
            fixture.write_private_file(
                settings_path,
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "hooks": [
                                        {"command": "python3 /old/coordination.py --config /old/c.json"},
                                        {"command": "python3 /old/memory.py --config /old/m.json"},
                                    ]
                                }
                            ]
                        }
                    }
                ),
            )
            diag_path = root / "diag.json"
            commands = fixture.wrap_hook_commands(
                settings_path,
                metadata_path=metadata_path,
                stop_complete_path=stop_path,
                wrapper_path=wrapper,
                diagnostic_path=diag_path,
                include_hook_kind=True,
                match_targets=("coordination.py", "memory.py"),
                path_replacements=(("/old", "/new"),),
            )
            self.assertIn("--diagnostic-summary", commands["coordination"])
            self.assertIn("/new/coordination.py", commands["coordination"])
            self.assertNotIn("--diagnostic-summary", commands["memory"])
            self.assertIn("/new/memory.py", commands["memory"])
            self.assertEqual(stat.S_IMODE(settings_path.stat().st_mode), 0o600)

            rpc_calls = []

            def fake_rpc(method, params):
                rpc_calls.append((method, params))
                if method == "hooks/list":
                    return {
                        "data": [
                            {
                                "hooks": [
                                    {
                                        "sourcePath": str(settings_path),
                                        "command": commands["coordination"],
                                        "key": f"k{i}",
                                        "currentHash": f"h{i}",
                                        "enabled": True,
                                        "trustStatus": "trusted",
                                    }
                                    for i in range(2)
                                ]
                            }
                        ]
                    }
                return {}

            loaded = fixture.trust_codex_hooks_via_rpc(
                fake_rpc,
                workspace=root,
                profile_dir=home,
                command=commands["coordination"],
                expected=2,
            )
            self.assertEqual(len(loaded), 2)
            self.assertEqual(sum(1 for m, _ in rpc_calls if m == "config/value/write"), 2)

    def test_wait_for_registration_hook_verifies_stop_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            stop_path = Path(tmp) / "stop.json"
            fixture.write_private_file(
                stop_path,
                json.dumps({"event": "Stop", "turn_id": "turn-123", "exit_code": 0}),
            )
            meta = fixture.wait_for_registration_hook(stop_path, "turn-123", timeout=1.0)
            self.assertEqual(meta["turn_id"], "turn-123")

    def test_stage_codex_binding_and_hooks_and_script_delegation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            profile = root / "profile"
            profile.mkdir(mode=0o700)
            cfg_path, cmd = fixture.stage_codex_binding_and_hooks(
                engine,
                profile,
                binding="codex-wake",
                config_data={"repo": "/repo"},
            )
            self.assertEqual(stat.S_IMODE(cfg_path.stat().st_mode), 0o600)
            self.assertIn("codex-wake.json", cmd)
            hooks_data = json.loads((profile / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(set(hooks_data["hooks"]), set(fixture.CODEX_HOOK_EVENTS))

        scripts_dir = Path(__file__).resolve().parent
        item = fixture.message_item("msg_del", "delegated")
        for script_name in (
            "check_codex_inbox.py",
            "check_codex_app_server_wake_join.py",
            "check_codex_compact_native.py",
            "check_codex_full_inbox_native.py",
        ):
            mod = fixture.load_script_module(scripts_dir / script_name, script_name[:-3])
            stream = mod.response_stream("resp_del", item)
            events = _parse_sse(stream)
            self.assertEqual(events[-1][0], "response.completed")


if __name__ == "__main__":
    unittest.main()
