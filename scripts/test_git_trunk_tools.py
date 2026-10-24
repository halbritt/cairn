#!/usr/bin/env python3
"""Unit tests for scripts/git_trunk_tools.py."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from git_trunk_tools import (
    git_land,
    git_wt_create,
    git_wt_gc,
    inspect_hygiene,
    stop_hygiene_hook,
)


def _git(cwd: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


class GitTrunkToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        self.wt_base = self.root / "worktrees"
        self.state_dir = self.root / "hook-state"

        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origin)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.origin), str(self.repo)], check=True, capture_output=True)
        _git(self.repo, "config", "user.name", "Test Agent")
        _git(self.repo, "config", "user.email", "agent@example.invalid")
        (self.repo / "README.md").write_text("initial\n")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-m", "initial")
        _git(self.repo, "push", "-u", "origin", "main")

    def test_inspect_hygiene_detects_clean_dirty_and_unpushed(self) -> None:
        h0 = inspect_hygiene(self.repo)
        self.assertTrue(h0["clean"])

        (self.repo / "untracked.txt").write_text("dirty\n")
        h1 = inspect_hygiene(self.repo)
        self.assertFalse(h1["clean"])
        self.assertEqual(h1["dirty_count"], 1)

        _git(self.repo, "add", "untracked.txt")
        _git(self.repo, "commit", "-m", "unpushed commit")
        h2 = inspect_hygiene(self.repo)
        self.assertFalse(h2["clean"])
        self.assertEqual(h2["ahead_origin"], 1)

        _git(self.repo, "push", "origin", "main")
        h3 = inspect_hygiene(self.repo)
        self.assertTrue(h3["clean"])

    def test_parallel_worktrees_land_rebase_push_and_cleanup(self) -> None:
        wt_a = git_wt_create(self.repo, "agent-a", base_dir=self.wt_base)
        wt_b = git_wt_create(self.repo, "agent-b", base_dir=self.wt_base)

        (wt_a / "a.txt").write_text("from A\n")
        _git(wt_a, "add", "a.txt")
        _git(wt_a, "commit", "-m", "add a.txt")

        (wt_b / "b.txt").write_text("from B\n")
        _git(wt_b, "add", "b.txt")
        _git(wt_b, "commit", "-m", "add b.txt")

        res_a = git_land(wt_a, check_cmd="test -f a.txt")
        self.assertTrue(res_a["ok"])
        self.assertTrue(res_a["pushed"])
        self.assertTrue(res_a["worktree_removed"])
        self.assertFalse(wt_a.exists())

        res_b = git_land(wt_b, check_cmd="test -f a.txt && test -f b.txt")
        self.assertTrue(res_b["ok"])
        self.assertTrue(res_b["pushed"])
        self.assertTrue(res_b["worktree_removed"])
        self.assertFalse(wt_b.exists())

        self.assertTrue((self.repo / "a.txt").is_file())
        self.assertTrue((self.repo / "b.txt").is_file())
        self.assertTrue(inspect_hygiene(self.repo)["clean"])
        branches = _git(self.repo, "branch").splitlines()
        self.assertEqual([b.strip().lstrip("* ") for b in branches], ["main"])

    def test_gc_removes_merged_inactive_and_preserves_active(self) -> None:
        wt_merged = git_wt_create(self.repo, "merged-wt", base_dir=self.wt_base)
        os.utime(wt_merged, (time.time() - 24 * 60 * 60, time.time() - 24 * 60 * 60))
        wt_active = git_wt_create(self.repo, "active-wt", base_dir=self.wt_base)
        wt_unmerged = git_wt_create(self.repo, "unmerged-wt", base_dir=self.wt_base)
        (wt_unmerged / "wip.txt").write_text("wip\n")
        _git(wt_unmerged, "add", "wip.txt")
        _git(wt_unmerged, "commit", "-m", "wip")

        gc_res = git_wt_gc([self.repo], active_workspaces={str(wt_active)})
        self.assertIn(str(wt_merged), gc_res["removed_worktrees"])
        self.assertFalse(wt_merged.exists())
        self.assertTrue(wt_active.exists())
        self.assertIn(str(wt_unmerged), gc_res["dirty_orphans"])

        git_land(wt_active)
        git_land(wt_unmerged)

    def test_gc_preserves_live_process_worktree_then_reclaims_stale_worktree(self) -> None:
        wt = git_wt_create(self.repo, "live-test-wt", base_dir=self.wt_base)
        # Make it old enough that only the live process can protect it.
        old = time.time() - 24 * 60 * 60
        os.utime(wt, (old, old))
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=wt,
        )
        try:
            active_result = git_wt_gc([self.repo], active_workspaces=set())
            self.assertNotIn(str(wt), active_result["removed_worktrees"])
            self.assertTrue(wt.is_dir())
        finally:
            child.terminate()
            child.wait(timeout=10)

        stale_result = git_wt_gc([self.repo], active_workspaces=set())
        self.assertIn(str(wt), stale_result["removed_worktrees"])
        self.assertFalse(wt.exists())

    def test_gc_defers_merged_worktree_for_active_primary_session(self) -> None:
        wt = git_wt_create(self.repo, "root-session-wt", base_dir=self.wt_base)
        old = time.time() - 24 * 60 * 60
        os.utime(wt, (old, old))

        active_result = git_wt_gc(
            [self.repo],
            active_workspaces={str(self.repo)},
            busy_workspaces={str(self.repo)},
        )
        self.assertNotIn(str(wt), active_result["removed_worktrees"])
        self.assertTrue(wt.is_dir())

        stale_result = git_wt_gc([self.repo], active_workspaces=set())
        self.assertIn(str(wt), stale_result["removed_worktrees"])
        self.assertFalse(wt.exists())

        idle_wt = git_wt_create(self.repo, "idle-root-session-wt", base_dir=self.wt_base)
        os.utime(idle_wt, (old, old))
        idle_result = git_wt_gc(
            [self.repo],
            active_workspaces={str(self.repo)},
            busy_workspaces=set(),
        )
        self.assertIn(str(idle_wt), idle_result["removed_worktrees"])
        self.assertFalse(idle_wt.exists())

    def test_gc_fails_closed_when_active_session_discovery_is_unavailable(self) -> None:
        wt = git_wt_create(self.repo, "discovery-outage-wt", base_dir=self.wt_base)
        old = time.time() - 24 * 60 * 60
        os.utime(wt, (old, old))
        _git(self.repo, "branch", "stale-merged")

        with patch("git_trunk_tools.shutil.which", return_value=None):
            unavailable_result = git_wt_gc([self.repo])

        self.assertTrue(unavailable_result["discovery_unavailable"])
        self.assertEqual(unavailable_result["removed_worktrees"], [])
        self.assertEqual(unavailable_result["removed_branches"], [])
        self.assertIn(str(wt), unavailable_result["protected_worktrees"])
        self.assertTrue(wt.is_dir())
        self.assertEqual(_git(self.repo, "show-ref", "--verify", "--quiet", "refs/heads/stale-merged"), "")

        empty_result = git_wt_gc([self.repo], active_workspaces=set(), busy_workspaces=set())
        self.assertFalse(empty_result["discovery_unavailable"])
        self.assertIn(str(wt), empty_result["removed_worktrees"])
        self.assertIn("repo:stale-merged", empty_result["removed_branches"])
        self.assertFalse(wt.exists())

    def test_stop_hygiene_hook_blocks_once_then_circuit_breaks(self) -> None:
        self.assertEqual(
            stop_hygiene_hook({"session_id": "s1", "cwd": str(self.repo)}, state_dir=self.state_dir),
            {},
        )
        (self.repo / "dirty.txt").write_text("x\n")
        out1 = stop_hygiene_hook({"session_id": "s1", "cwd": str(self.repo)}, state_dir=self.state_dir)
        self.assertEqual(out1.get("decision"), "block")
        self.assertIn("uncommitted/untracked", str(out1.get("reason")))

        out2 = stop_hygiene_hook({"session_id": "s1", "cwd": str(self.repo)}, state_dir=self.state_dir)
        self.assertEqual(out2, {})

        agy_out = stop_hygiene_hook(
            {"conversationId": "c1", "workspacePaths": [str(self.repo)], "fullyIdle": True},
            state_dir=self.state_dir,
        )
        self.assertEqual(agy_out.get("decision"), "continue")


if __name__ == "__main__":
    unittest.main()
