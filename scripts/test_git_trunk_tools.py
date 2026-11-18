#!/usr/bin/env python3
"""Unit tests for scripts/git_trunk_tools.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from git_trunk_tools import (
    _compare_evidence,
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

    def test_check_cannot_push_a_dirty_candidate_or_discard_its_output(self) -> None:
        wt = git_wt_create(self.repo, "dirty-check", base_dir=self.wt_base)
        (wt / "change.txt").write_text("candidate\n")
        _git(wt, "add", "change.txt")
        _git(wt, "commit", "-m", "candidate")
        before = _git(self.repo, "rev-parse", "origin/main")
        with self.assertRaisesRegex(RuntimeError, "check.*uncommitted|uncommitted.*check"):
            git_land(wt, check_cmd="printf 'modified\\n' >> change.txt")
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0], before)
        self.assertEqual((wt / "change.txt").read_text(), "candidate\nmodified\n")

    def test_check_that_changes_head_requires_a_new_land_attempt(self) -> None:
        wt = git_wt_create(self.repo, "head-check", base_dir=self.wt_base)
        before = _git(self.repo, "rev-parse", "origin/main")
        with self.assertRaisesRegex(RuntimeError, "check.*HEAD|HEAD.*check"):
            git_land(wt, check_cmd="git -c core.hooksPath=/dev/null commit --allow-empty -m changed-by-check")
        self.assertNotEqual(_git(wt, "rev-parse", "HEAD"), before)
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0], before)
        self.assertTrue(wt.is_dir())

    def test_hidden_untracked_check_output_is_preserved(self) -> None:
        wt = git_wt_create(self.repo, "hidden-output", base_dir=self.wt_base)
        _git(wt, "config", "status.showUntrackedFiles", "no")
        before = _git(self.repo, "rev-parse", "origin/main")
        with self.assertRaisesRegex(RuntimeError, "check.*uncommitted"):
            git_land(wt, check_cmd="printf 'diagnostic\\n' > diagnostic.txt")
        self.assertEqual((wt / "diagnostic.txt").read_text(), "diagnostic\n")
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0], before)
        self.assertEqual(inspect_hygiene(wt)["dirty_count"], 1)

    def test_final_rebased_check_cannot_dirty_the_pushed_candidate(self) -> None:
        wt = git_wt_create(self.repo, "final-check", base_dir=self.wt_base)
        (wt / "candidate.txt").write_text("candidate\n")
        _git(wt, "add", "candidate.txt")
        _git(wt, "commit", "-m", "candidate")
        marker = self.root / "checked-once"
        script = self.root / "check.py"
        script.write_text(
            "from pathlib import Path\nimport subprocess\n"
            f"marker = Path({str(marker)!r})\n"
            "if marker.exists():\n"
            "    Path('candidate.txt').write_text('dirty after rebase\\n')\n"
            "else:\n"
            "    marker.touch()\n"
            f"    repo = {str(self.repo)!r}\n"
            "    subprocess.run(['git', '-C', repo, 'commit', '--allow-empty', '-m', 'concurrent'], check=True)\n"
            "    subprocess.run(['git', '-C', repo, 'push', 'origin', 'main'], check=True)\n"
        )
        with self.assertRaisesRegex(RuntimeError, "final check.*uncommitted"):
            git_land(wt, check_cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0],
                         _git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual((wt / "candidate.txt").read_text(), "dirty after rebase\n")

    def test_advisory_compares_final_candidate_under_lock_after_checks(self) -> None:
        wt = git_wt_create(self.repo, "evidence", base_dir=self.wt_base)
        (wt / "candidate.txt").write_text("candidate\n")
        _git(wt, "add", "candidate.txt")
        _git(wt, "commit", "-m", "candidate")
        original = _git(wt, "rev-parse", "HEAD")
        checks = self.root / "check-count"
        check = self.root / "check.py"
        check.write_text(
            "from pathlib import Path\nimport subprocess\n"
            f"counter = Path({str(checks)!r})\n"
            "count = int(counter.read_text()) if counter.exists() else 0\n"
            "counter.write_text(str(count + 1))\n"
            "if count == 0:\n"
            f"    repo = {str(self.repo)!r}\n"
            "    subprocess.run(['git', '-C', repo, 'commit', '--allow-empty', '-m', 'concurrent'], check=True)\n"
            "    subprocess.run(['git', '-C', repo, 'push', 'origin', 'main'], check=True)\n"
        )
        captured = self.root / "compared.json"
        comparator = self.root / "development-evidence"
        comparator.write_text(
            f"#!{sys.executable}\n"
            "import fcntl,json,sys,subprocess\nfrom pathlib import Path\n"
            f"lock = open({str(self.repo / '.git/git-land.lock')!r}, 'a+')\n"
            "locked = False\n"
            "try:\n    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n    locked = True\n"
            f"Path({str(captured)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'locked': locked, 'checks': Path({str(checks)!r}).read_text(), 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}}))\n"
            "print(json.dumps({'schema': 'development-evidence-comparison/1', 'freshness': 'stale', 'outcome': 'clear-within-scope', 'reasons': ['candidate changed'], 'limits': []}))\n"
            "sys.exit(1)\n"
        )
        comparator.chmod(0o755)
        receipt = self.root / "review.json"
        receipt.write_text('{}\n')
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            result = git_land(wt, check_cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(check))}",
                              evidence_paths=(str(receipt),))
        seen = json.loads(captured.read_text())
        self.assertTrue(seen["locked"])
        self.assertEqual(seen["checks"], "2")
        final = result["head"]
        self.assertNotEqual(final, original)
        self.assertEqual(seen["head"], final)
        self.assertEqual(seen["argv"], ["compare", str(receipt), "--repo", str(wt),
                                      "--base", _git(self.repo, "rev-parse", "HEAD^"), "--json"])
        self.assertEqual(result["evidence"][0]["freshness"], "stale")
        self.assertEqual(result["evidence"][0]["outcome"], "clear-within-scope")
        self.assertEqual(_git(self.repo, "rev-parse", "origin/main"), final)
        self.assertTrue(result["pushed"])
        self.assertFalse(wt.exists())

    def test_unavailable_or_invalid_comparator_is_unknown(self) -> None:
        with patch.dict(os.environ, {"PATH": str(self.root / "absent")}):
            result = _compare_evidence(self.repo, self.root / "review.json", "base")
        self.assertEqual(result["freshness"], "unknown")
        for output in ('{}', 'not json', '[]', '{"freshness":"approved"}',
                       '{"schema":"development-evidence-comparison/1","freshness":[]}',
                       '{"schema":"development-evidence-comparison/1","freshness":{}}',
                       '{"schema":"development-evidence-comparison/1","freshness":"current","outcome":[]}'):
            with self.subTest(output=output), patch("git_trunk_tools.subprocess.run", return_value=
                    subprocess.CompletedProcess([], 0, output, "")):
                result = _compare_evidence(self.repo, self.root / "review.json", "base")
            self.assertEqual(result["freshness"], "unknown")

    def test_invalid_comparator_encoding_does_not_block_landing(self) -> None:
        comparator = self.root / "development-evidence"
        comparator.write_text(f"#!{sys.executable}\nimport os\nos.write(1, bytes([255]))\n")
        comparator.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            result = git_land(self.repo, evidence_paths=(str(self.root / "review.json"),))
        self.assertTrue(result["pushed"])
        self.assertEqual(result["evidence"][0]["freshness"], "unknown")

    def test_comparator_failure_cannot_claim_current(self) -> None:
        with patch("git_trunk_tools.subprocess.run", return_value=
                   subprocess.CompletedProcess([], 2,
                       json.dumps({"schema": "development-evidence-comparison/1", "freshness": "current",
                                   "outcome": "findings-open", "reasons": [], "limits": [],
                                   "receipt_sha256": "a" * 64}), "failed")):
            result = _compare_evidence(self.repo, self.root / "review.json", "base")
        self.assertEqual(result["freshness"], "unknown")

    def test_comparison_cannot_change_the_candidate_before_push(self) -> None:
        wt = git_wt_create(self.repo, "mutating-comparator", base_dir=self.wt_base)
        before = _git(self.repo, "rev-parse", "origin/main")
        comparator = self.root / "development-evidence"
        comparator.write_text(
            f"#!{sys.executable}\nfrom pathlib import Path\nimport json\n"
            "Path('README.md').write_text('modified by comparator\\n')\n"
            "print(json.dumps({'schema': 'development-evidence-comparison/1', 'freshness': 'current', "
            "'outcome': 'findings-open', 'reasons': [], 'limits': [], 'receipt_sha256': 'a' * 64}))\n"
        )
        comparator.chmod(0o755)
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(RuntimeError, "evidence comparison.*uncommitted"):
                git_land(wt, evidence_paths=(str(self.root / "review.json"),))
        self.assertEqual((wt / "README.md").read_text(), "modified by comparator\n")
        self.assertEqual(_git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0], before)

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
