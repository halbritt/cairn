"""Regression checks for the opt-in CAIRN-220 private queue-stage trace."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent
OBSERVER = SCRIPTS / "fixtures" / "cairn220_queue_trace"
LIFECYCLE = SCRIPTS.parent / "integrations" / "lifecycle"


class CodexQueueStageTraceTests(unittest.TestCase):
    def test_trace_is_private_fixed_code_and_excludes_refusal_text(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.jsonl"
            env = dict(os.environ)
            env.update(
                CAIRN220_QUEUE_TRACE=str(trace),
                PYTHONPATH=os.pathsep.join((str(OBSERVER), str(LIFECYCLE))),
            )
            code = r"""
import json, time
import codex_queue

class Peer:
    def settimeout(self, _value): pass
    def send(self, _value): pass
    def recv(self):
        return json.dumps({"id": 1, "error": {"code": -32600, "message": "PRIVATE WAKE TEXT"}})

rpc = codex_queue._Rpc(Peer(), time.monotonic() + 5)
try:
    rpc("thread/queue/add", {})
except codex_queue.QueueRefused:
    pass
else:
    raise AssertionError("the synthetic native refusal was not preserved")
"""
            completed = subprocess.run(
                [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
            rows = [json.loads(line) for line in trace.read_text().splitlines()]

        self.assertEqual(
            [(row["stage"], row["outcome"]) for row in rows],
            [("trace", "started"), ("queue_add", "before"), ("queue_add", "refused")],
        )
        self.assertEqual(rows[-1]["error_class"], "QueueRefused")
        self.assertEqual(rows[-1]["native_code"], -32600)
        self.assertNotIn("PRIVATE WAKE TEXT", json.dumps(rows))
        self.assertFalse(any("message" in row for row in rows))


if __name__ == "__main__":
    unittest.main()
