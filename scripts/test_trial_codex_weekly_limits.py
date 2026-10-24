"""Codex weekly limit measurement from the retained --json stream; synthetic only."""
import hashlib
import json
import unittest

import trial_codex_weekly_limits as limits
import trial_paired_request as paired


def line(event):
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def stream(result=None, answer="ok"):
    item = dict(id="m1", type="mcp_tool_call", server="cairn", tool="cairn_search",
                arguments={"query": "x"}, status="completed",
                result=result or dict(content=[dict(type="text", text="ok")]))
    return "\n".join((line(dict(type="item.completed", item=item)),
                     line(dict(type="item.completed", item=dict(id="a1", type="agent_message", text=answer))),
                     line(dict(type="turn.completed", usage={})))) + "\n"


def timings(text, received=1.0):
    rows=[]
    for index, row in enumerate(text.splitlines(), 1):
        raw=(row+"\n").encode()
        rows.append(dict(line=index, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                         received_seconds=received))
    return rows


def hooks():
    return [dict(event=event, stdout_bytes=1, stderr_bytes=0, process_seconds=1.0, exit_code=0,
                 memory_call_attempts=dict(search=0, pull=0, **{"pull-evidence":0,"history":0,"other":0}))
            for event in ("SessionStart", "UserPromptSubmit")]


class CodexWeeklyLimitsTests(unittest.TestCase):
    def test_codex_events_measure_calls_results_hooks_and_completion(self):
        text=stream()
        result=limits.assess(text, hooks(), input_bytes=100, timings=timings(text))
        self.assertEqual(result["status"], "within_codex_weekly_limits")
        self.assertEqual(result["native_searches"], 1)
        self.assertGreater(result["native_memory_result_bytes"], 2)

    def test_missing_codex_mcp_result_payload_is_unknown_not_a_pass(self):
        text=stream(result=None)
        item=json.loads(text.splitlines()[0]); item["item"].pop("result")
        text="\n".join((line(item), *text.splitlines()[1:]))+"\n"
        result=limits.assess(text, hooks(), input_bytes=100, timings=timings(text))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("mcp_result_payload_unobserved", result["unknown"])

    def test_incomplete_codex_stream_timing_and_hook_failure_do_not_pass(self):
        text=stream()
        self.assertEqual(limits.assess(text, hooks(), input_bytes=100, timings=None)["status"], "unknown")
        bad=hooks(); bad[0]["process_seconds"]=5.01
        self.assertEqual(limits.assess(text, bad, input_bytes=100, timings=timings(text))["status"], "failed")


if __name__ == "__main__":
    unittest.main()
