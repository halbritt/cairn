import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().with_name("render_kanban_board.py")


class RenderKanbanBoardTests(unittest.TestCase):
    def test_deterministic_cairn301_kanban_table_from_board_and_plane(self):
        board_envelope = {
            "schema": "cairn.response/1",
            "ok": True,
            "status": "OK",
            "data": {
                "repo": "/home/halbritt/git/cairn",
                "max_stream_bytes": 2048,
                "streams": [
                    {
                        "stream": "foundation",
                        "state": "active",
                        "current": "CAIRN-304 Remaining F01 qualification",
                        "last_landed": "73865776 Go process/framing helpers",
                        "next_checkpoint": "2026-11-08T19:30:00Z",
                    },
                    {
                        "stream": "semantic",
                        "state": "blocked",
                        "current": "CAIRN-309 Rust ORT session fixture",
                        "last_landed": "ead41ab9 Model-free path",
                        "blocker": "Waiting on GPU slot lease",
                    },
                    {
                        "stream": "release",
                        "state": "waiting",
                        "current": "CAIRN-308 Ownership repair review",
                        "decision_needed": "Approve helper patch rollout order",
                    },
                    {
                        "stream": "documentation",
                        "state": "done",
                        "last_landed": "Audit and inventories pushed",
                    },
                ],
            },
        }
        plane_envelope = {
            "ok": True,
            "meta": {"pagination": {"drained": True}},
            "data": [
                {"id": "camp-301", "sequence_id": 301, "parent": None, "state": {"group": "started"}},
                {"id": "grp-1", "sequence_id": 302, "parent": "camp-301", "state": {"group": "started"}},
                {"id": "leaf-1", "sequence_id": 303, "parent": "grp-1", "state": {"group": "completed"}},
                {"id": "leaf-2", "sequence_id": 304, "parent": "grp-1", "state": {"group": "completed"}},
                {"id": "leaf-3", "sequence_id": 305, "parent": "camp-301", "state": {"group": "started"}},
                {"id": "leaf-4", "sequence_id": 306, "parent": "camp-301", "state": {"group": "backlog"}},
                {"id": "leaf-cancel", "sequence_id": 307, "parent": "camp-301", "state": {"group": "cancelled"}},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            board_path = Path(td) / "board.json"
            plane_path = Path(td) / "plane.json"
            board_path.write_text(json.dumps(board_envelope), encoding="utf-8")
            plane_path.write_text(json.dumps(plane_envelope), encoding="utf-8")
            res = subprocess.run(
                [
                    sys.executable, "-B", str(SCRIPT),
                    "--board-json", str(board_path),
                    "--plane-json", str(plane_path),
                    "--campaign", "CAIRN-301",
                    "--now", "2026-11-08T19:16:00+00:00",
                    "--timezone", "America/Los_Angeles",
                ],
                text=True, capture_output=True, check=True,
            )
            out = res.stdout
            self.assertIn("**CAIRN-301 — 11:16 PST: estimated completion 50% (2/4 work items Done).**", out)
            self.assertIn("| Foundation | active | 73865776 Go process/framing helpers | CAIRN-304 Remaining F01 qualification; Next checkpoint: 2026-11-08T19:30:00Z |", out)
            self.assertIn("| Semantic | blocked | ead41ab9 Model-free path | CAIRN-309 Rust ORT session fixture |", out)
            self.assertIn("| Documentation | done | Audit and inventories pushed | None |", out)
            self.assertIn("| 24-hour burn-in | unstarted | Not started | Outstanding |", out)
            self.assertIn("### Blockers\n- **semantic**: Waiting on GPU slot lease", out)
            self.assertIn("### Decisions needed\n- **release**: Approve helper patch rollout order", out)
            # Deterministic: identical second invocation produces identical bytes.
            res2 = subprocess.run(
                [
                    sys.executable, "-B", str(SCRIPT),
                    "--board-json", str(board_path),
                    "--plane-json", str(plane_path),
                    "--campaign", "CAIRN-301",
                    "--now", "2026-11-08T19:16:00+00:00",
                    "--timezone", "America/Los_Angeles",
                ],
                text=True, capture_output=True, check=True,
            )
            self.assertEqual(out, res2.stdout)

    def test_pagination_cycle_cancelled_child_and_escaping_guards(self):
        if str(SCRIPT.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPT.parent))
        import render_kanban_board as rkb

        # Open parent whose only child is cancelled remains an eligible leaf (0/1 Done, not dropped).
        items = [
            {"id": "c1", "sequence_id": 10, "parent": None, "state": {"group": "started"}},
            {"id": "p1", "sequence_id": 11, "parent": "c1", "state": {"group": "started"}},
            {"id": "ch1", "sequence_id": 12, "parent": "p1", "state": {"group": "cancelled"}},
        ]
        m = rkb.compute_leaf_completion(items, "OTHER-10", project="OTHER")
        self.assertEqual((m["campaign"], m["done"], m["total"]), ("OTHER-10", 0, 1))

        # Cycle in parent links raises ValueError.
        cyc = [
            {"id": "c1", "sequence_id": 1, "parent": "a1", "state": {"group": "started"}},
            {"id": "a1", "sequence_id": 2, "parent": "c1", "state": {"group": "started"}},
        ]
        with self.assertRaises(ValueError):
            rkb.compute_leaf_completion(cyc, "CAIRN-1")

        # Naive --now without UTC offset raises ValueError.
        from datetime import datetime
        with self.assertRaises(ValueError):
            rkb.render_kanban_markdown({"streams": []}, None, now=datetime(2026, 11, 8, 12, 0, 0))

        # Escaping `\|` preserves backslash and escapes pipe.
        self.assertEqual(rkb._escape_cell(r"foo\|bar"), r"foo\\\|bar")


if __name__ == "__main__":
    unittest.main()
