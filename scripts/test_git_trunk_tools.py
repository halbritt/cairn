#!/usr/bin/env python3
"""Unit tests for scripts/git_trunk_tools.py."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from git_trunk_tools import (
    _compare_evidence,
    _run_git,
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


def _status(cwd: Path) -> list[str]:
    """Porcelain status lines, keeping the leading column that ``_git`` strips."""
    res = subprocess.run(
        ["git", "-C", str(cwd), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.splitlines()


class GitTrunkToolsTests(unittest.TestCase):
    maxDiff = None

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

    def _landing_worktree(self, name: str, files: dict[str, str]) -> Path:
        """Create a worktree whose branch holds one commit that writes ``files``."""
        wt = git_wt_create(self.repo, name, base_dir=self.wt_base)
        for rel, text in files.items():
            (wt / rel).write_text(text)
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", f"{name}: landed work")
        return wt

    def _checkout_state(self, checkout: Path) -> dict[str, object]:
        """What a refused trunk update must leave alone: HEAD, ORIG_HEAD, index and files."""
        orig_head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet", "ORIG_HEAD"],
                                   capture_output=True, text=True)
        return {
            "head": _git(checkout, "rev-parse", "HEAD"),
            "branch": _git(checkout, "symbolic-ref", "--short", "HEAD"),
            "orig_head": orig_head.stdout.strip(),
            "index": _git(checkout, "ls-files", "--stage"),
            "status": _status(checkout),
            "diff": _git(checkout, "diff", "HEAD"),
        }

    def _origin_trunk(self) -> str:
        return _git(self.repo, "ls-remote", "origin", "refs/heads/main").split()[0]

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

    def test_landing_fast_forwards_dirty_trunk_checkout_and_keeps_non_overlapping_edits(self) -> None:
        (self.repo / "notes.txt").write_text("notes\n")
        (self.repo / "obsolete.txt").write_text("obsolete\n")
        _git(self.repo, "add", "notes.txt", "obsolete.txt")
        _git(self.repo, "commit", "-m", "notes")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("non-overlap", {"README.md": "initial\nlanded line\n", "landed.txt": "landed\n"})
        # Another agent's uncommitted work in the checkout that holds trunk.
        (self.repo / "notes.txt").write_text("notes\nlocal edit\n")
        (self.repo / "obsolete.txt").unlink()
        (self.repo / "staged.txt").write_text("staged\n")
        _git(self.repo, "add", "staged.txt")
        (self.repo / "scratch.txt").write_text("scratch\n")
        orig_head = self._checkout_state(self.repo)["orig_head"]

        res = git_land(wt)

        self.assertEqual(_git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), res["head"])
        self.assertEqual(self._checkout_state(self.repo)["orig_head"], orig_head)
        self.assertCountEqual(_status(self.repo),
                              [" M notes.txt", " D obsolete.txt", "A  staged.txt", "?? scratch.txt"])
        self.assertEqual((self.repo / "README.md").read_text(), "initial\nlanded line\n")
        self.assertEqual((self.repo / "landed.txt").read_text(), "landed\n")
        self.assertEqual((self.repo / "notes.txt").read_text(), "notes\nlocal edit\n")
        self.assertEqual(res["trunk_status"], "fast-forwarded")
        self.assertEqual(res["trunk_worktree"], str(self.repo))
        self.assertTrue(res["worktree_removed"])
        self.assertFalse(wt.exists())

    def test_landing_leaves_trunk_checkout_untouched_when_local_edits_overlap(self) -> None:
        wt = self._landing_worktree("overlap", {"README.md": "initial\nlanded line\n", "landed.txt": "landed\n"})
        # Another agent's uncommitted edit to a file the landed commit also changes.
        (self.repo / "README.md").write_text("initial, edited by another agent\n")
        before = self._checkout_state(self.repo)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            res = git_land(wt)

        self.assertTrue(res["pushed"])
        self.assertEqual(self._origin_trunk(), res["head"])
        # HEAD, index and files are exactly as the other agent left them.
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), before["head"])
        self.assertEqual((self.repo / "README.md").read_text(), "initial, edited by another agent\n")
        self.assertFalse((self.repo / "landed.txt").exists())
        # Its diff is that agent's own edit: no landed line reversed, no landed file deleted.
        self.assertEqual(_status(self.repo), [" M README.md"])
        self.assertNotIn("landed", _git(self.repo, "diff", "HEAD"))
        # The outcome is reported in words the landing agent can act on.
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertEqual(res["trunk_worktree"], str(self.repo))
        self.assertIn("README.md", res["trunk_detail"])
        for expected in ("NOT advanced", str(self.repo), "git pull --rebase origin main"):
            self.assertIn(expected, stderr.getvalue())
        # origin holds the commit, so the landed branch and worktree are still cleaned up.
        self.assertTrue(res["worktree_removed"])
        self.assertFalse(wt.exists())
        self.assertEqual(_git(self.repo, "branch", "--format=%(refname:short)").splitlines(), ["main"])

    def test_commit_after_overlapping_landing_cannot_silently_revert_landed_work(self) -> None:
        wt = self._landing_worktree("revert", {"README.md": "initial\nlanded line\n", "landed.txt": "landed\n"})
        (self.repo / "README.md").write_text("initial, edited by another agent\n")
        res = git_land(wt)

        # The other agent's ordinary next step in the checkout that holds trunk.
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "another agent's edit")
        push = subprocess.run(["git", "-C", str(self.repo), "push", "origin", "main"], capture_output=True, text=True)

        self.assertNotEqual(push.returncode, 0, "the push fast-forwarded origin over the landed commit")
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_git(self.origin, "show", "main:landed.txt"), "landed")
        self.assertEqual(_git(self.origin, "show", "main:README.md"), "initial\nlanded line")
        # Git surfaces the overlap as a real merge for that agent to resolve.
        pull = subprocess.run(["git", "-C", str(self.repo), "pull", "--rebase", "origin", "main"], capture_output=True, text=True)
        self.assertNotEqual(pull.returncode, 0)
        self.assertIn("UU README.md", _status(self.repo))

    def test_refused_fast_forward_without_origin_keeps_landed_branch_and_worktree(self) -> None:
        _git(self.repo, "remote", "remove", "origin")
        wt = self._landing_worktree("no-origin", {"README.md": "initial\nlanded line\n"})
        landed = _git(wt, "rev-parse", "HEAD")
        self.assertTrue(any("unmerged" in issue for issue in inspect_hygiene(wt)["issues"]))
        (self.repo / "README.md").write_text("initial, edited by another agent\n")
        before = self._checkout_state(self.repo)

        with self.assertRaisesRegex(RuntimeError, "did not land.*README.md"):
            git_land(wt)

        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/wt/no-origin"), landed)
        self.assertEqual(_git(wt, "rev-parse", "HEAD"), landed)
        self.assertEqual(_status(wt), [])

    def test_no_push_with_diverged_local_trunk_keeps_landed_commit_reachable(self) -> None:
        wt = self._landing_worktree("no-push", {"landed.txt": "landed\n"})
        landed = _git(wt, "rev-parse", "HEAD")
        origin_before = self._origin_trunk()
        # Local trunk gains a commit that origin, and so the landed commit, does not have.
        (self.repo / "local.txt").write_text("local\n")
        _git(self.repo, "add", "local.txt")
        _git(self.repo, "commit", "-m", "unpushed trunk commit")
        unpushed = _git(self.repo, "rev-parse", "HEAD")

        with self.assertRaisesRegex(RuntimeError, "did not land.*must be pushed first"):
            git_land(wt, no_push=True)

        self.assertEqual(_git(self.repo, "for-each-ref", "--contains", landed, "--format=%(refname)"),
                         "refs/heads/wt/no-push")
        self.assertEqual(_git(wt, "rev-parse", "HEAD"), landed)
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), unpushed)
        self.assertEqual(self._origin_trunk(), origin_before)

    def test_trunk_held_by_secondary_worktree_is_fast_forwarded_there(self) -> None:
        # The primary is parked on another branch; a secondary worktree holds trunk.
        _git(self.repo, "checkout", "-b", "parked")
        trunk_checkout = self.root / "trunk-checkout"
        _git(self.repo, "worktree", "add", str(trunk_checkout), "main")
        (trunk_checkout / "scratch.txt").write_text("scratch\n")
        parked = self._checkout_state(self.repo)
        wt = self._landing_worktree("to-secondary", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(_status(trunk_checkout), ["?? scratch.txt"])
        self.assertEqual(_git(trunk_checkout, "rev-parse", "HEAD"), res["head"])
        self.assertEqual((trunk_checkout / "landed.txt").read_text(), "landed\n")
        self.assertEqual(self._checkout_state(self.repo), parked)
        self.assertEqual(res["trunk_status"], "fast-forwarded")
        self.assertEqual(res["trunk_worktree"], str(trunk_checkout))
        self.assertTrue(res["worktree_removed"])

    def test_trunk_held_by_secondary_worktree_is_left_alone_when_untracked_file_overlaps(self) -> None:
        _git(self.repo, "checkout", "-b", "parked")
        trunk_checkout = self.root / "trunk-checkout"
        _git(self.repo, "worktree", "add", str(trunk_checkout), "main")
        # Another agent's untracked draft at a path the landed commit adds.
        (trunk_checkout / "landed.txt").write_text("another agent's draft\n")
        before = self._checkout_state(trunk_checkout)
        wt = self._landing_worktree("secondary-overlap", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual((trunk_checkout / "landed.txt").read_text(), "another agent's draft\n")
        self.assertEqual(self._checkout_state(trunk_checkout), before)
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), before["head"])
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertEqual(res["trunk_worktree"], str(trunk_checkout))
        self.assertIn("landed.txt", res["trunk_detail"])

    def test_trunk_registered_in_a_missing_worktree_is_not_moved(self) -> None:
        _git(self.repo, "checkout", "-b", "parked")
        trunk_checkout = self.root / "trunk-checkout"
        _git(self.repo, "worktree", "add", str(trunk_checkout), "main")
        # The directory went away without `git worktree remove`; git still counts trunk as checked out there.
        shutil.rmtree(trunk_checkout)
        trunk_before = _git(self.repo, "rev-parse", "refs/heads/main")
        wt = self._landing_worktree("stale-holder", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), trunk_before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertEqual(res["trunk_worktree"], str(trunk_checkout))
        self.assertIn("could not inspect that checkout", res["trunk_detail"])

    def test_trunk_checked_out_nowhere_is_moved_as_a_branch(self) -> None:
        _git(self.repo, "checkout", "-b", "parked")
        parked = self._checkout_state(self.repo)
        wt = self._landing_worktree("no-holder", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), res["head"])
        self.assertEqual(self._checkout_state(self.repo), parked)
        self.assertEqual(res["trunk_status"], "ref-updated")
        self.assertIsNone(res["trunk_worktree"])

    def test_unpushed_trunk_commits_are_not_dropped_when_trunk_is_checked_out_nowhere(self) -> None:
        (self.repo / "local.txt").write_text("local\n")
        _git(self.repo, "add", "local.txt")
        _git(self.repo, "commit", "-m", "unpushed trunk commit")
        unpushed = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", "-b", "parked", "origin/main")
        wt = self._landing_worktree("diverged", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), unpushed)
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("1 commit", res["trunk_detail"])

    def test_trunk_being_rebased_in_another_checkout_is_not_moved(self) -> None:
        (self.repo / "second.txt").write_text("second\n")
        _git(self.repo, "add", "second.txt")
        _git(self.repo, "commit", "-m", "second")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("mid-rebase", {"landed.txt": "landed\n"})
        # Another agent's rebase of trunk has stopped; git lists that checkout as detached.
        stopped = subprocess.run(["git", "-C", str(self.repo), "rebase", "--exec", "false", "HEAD~1"],
                                 capture_output=True, text=True)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("detached", _git(self.repo, "worktree", "list", "--porcelain").split("\n\n")[0])
        trunk_before = _git(self.repo, "rev-parse", "refs/heads/main")

        res = git_land(wt)

        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), trunk_before)
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")
        # The other agent can still finish the rebase it started.
        _git(self.repo, "rebase", "--continue")
        self.assertEqual(_git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")

    def test_ignored_file_in_trunk_checkout_is_not_overwritten_by_landing(self) -> None:
        (self.repo / ".gitignore").write_text("*.out\n")
        _git(self.repo, "add", ".gitignore")
        _git(self.repo, "commit", "-m", "ignore outputs")
        _git(self.repo, "push", "origin", "main")
        wt = git_wt_create(self.repo, "ignored", base_dir=self.wt_base)
        (wt / "result.out").write_text("landed result\n")
        _git(wt, "add", "--force", "result.out")
        _git(wt, "commit", "-m", "track one result")
        # Another agent's ignored output at the path the landed commit starts tracking.
        (self.repo / "result.out").write_text("another agent's result\n")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertEqual((self.repo / "result.out").read_text(), "another agent's result\n")
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("result.out", res["trunk_detail"])

    def test_uncommitted_deletion_in_trunk_checkout_is_not_undone_by_landing(self) -> None:
        (self.repo / "module.py").write_text("v1\n")
        _git(self.repo, "add", "module.py")
        _git(self.repo, "commit", "-m", "module")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("deleted", {"module.py": "v2 landed\n"})
        # Another agent renamed the file without committing; git alone would write module.py back.
        (self.repo / "module.py").rename(self.repo / "renamed.py")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertFalse((self.repo / "module.py").exists())
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("module.py", res["trunk_detail"])

    def test_autostash_setting_cannot_rewrite_overlapping_local_edits(self) -> None:
        _git(self.repo, "config", "merge.autoStash", "true")
        wt = self._landing_worktree("autostash", {"README.md": "landed rewrite\n"})
        (self.repo / "README.md").write_text("another agent's rewrite\n")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertEqual((self.repo / "README.md").read_text(), "another agent's rewrite\n")
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(_git(self.repo, "stash", "list"), "")
        self.assertEqual(res["trunk_status"], "left-behind")

    def test_branch_merge_options_cannot_turn_the_fast_forward_into_a_squash(self) -> None:
        _git(self.repo, "config", "branch.main.mergeoptions", "--squash")
        wt = self._landing_worktree("squash-option", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), res["head"])
        self.assertEqual(_status(self.repo), [])
        self.assertEqual(res["trunk_status"], "fast-forwarded")

    def test_sparse_trunk_checkout_is_not_fast_forwarded(self) -> None:
        (self.repo / "src").mkdir()
        (self.repo / "src" / "a.txt").write_text("a\n")
        _git(self.repo, "add", "src")
        _git(self.repo, "commit", "-m", "src")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("sparse", {"notes.txt": "landed notes\n"})
        _git(self.repo, "sparse-checkout", "set", "--cone", "src")
        # In a sparse checkout git only warns about this untracked file and then overwrites it.
        (self.repo / "notes.txt").write_text("another agent's notes\n")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertEqual((self.repo / "notes.txt").read_text(), "another agent's notes\n")
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("sparse-checkout", res["trunk_detail"])

    def test_unfinished_revert_in_trunk_checkout_keeps_its_prepared_message(self) -> None:
        (self.repo / "change.txt").write_text("change\n")
        _git(self.repo, "add", "change.txt")
        _git(self.repo, "commit", "-m", "change")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("unfinished-revert", {"landed.txt": "landed\n"})
        _git(self.repo, "revert", "--no-commit", "HEAD")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("REVERT_HEAD", res["trunk_detail"])
        # The other agent can still finish the revert with the message git prepared.
        subprocess.run(["git", "-C", str(self.repo), "revert", "--continue"], check=True, capture_output=True,
                       env={**os.environ, "GIT_EDITOR": "true"})
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s"), 'Revert "change"')

    def test_directory_rename_behind_a_symlink_is_not_undone_by_landing(self) -> None:
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "a.txt").write_text("a\n")
        _git(self.repo, "add", "pkg")
        _git(self.repo, "commit", "-m", "pkg")
        _git(self.repo, "push", "origin", "main")
        wt = self._landing_worktree("dir-rename", {"pkg/a.txt": "a landed\n"})
        # Another agent moved the directory and left a symlink; the old paths still open.
        (self.repo / "pkg").rename(self.repo / "lib")
        (self.repo / "pkg").symlink_to("lib")
        before = self._checkout_state(self.repo)

        res = git_land(wt)

        self.assertTrue((self.repo / "pkg").is_symlink())
        self.assertEqual((self.repo / "lib" / "a.txt").read_text(), "a\n")
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("pkg/a.txt", res["trunk_detail"])

    def test_trunk_checkout_without_a_commit_is_not_fast_forwarded(self) -> None:
        fresh = self.root / "fresh"
        subprocess.run(["git", "init", "-b", "main", str(fresh)], check=True, capture_output=True)
        _git(fresh, "config", "user.name", "Test Agent")
        _git(fresh, "config", "user.email", "agent@example.invalid")
        _git(fresh, "remote", "add", "origin", str(self.origin))
        _git(fresh, "fetch", "origin")
        (fresh / "notes.txt").write_text("another agent's notes\n")
        wt = git_wt_create(fresh, "unborn", base_dir=self.wt_base)
        (wt / "landed.txt").write_text("landed\n")
        _git(wt, "add", "landed.txt")
        _git(wt, "commit", "-m", "landed")

        res = git_land(wt)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertEqual(sorted(p.name for p in fresh.iterdir()), [".git", "notes.txt"])
        self.assertNotEqual(subprocess.run(["git", "-C", str(fresh), "rev-parse", "--verify", "--quiet", "refs/heads/main"],
                                           capture_output=True).returncode, 0)

    def test_stale_nested_trunk_worktree_does_not_move_the_enclosing_checkout(self) -> None:
        _git(self.repo, "checkout", "-b", "parked")
        nested = self.repo / ".worktrees" / "trunk"
        _git(self.repo, "worktree", "add", str(nested), "main")
        # Without its .git file the directory belongs to the enclosing checkout, which is on another branch.
        (nested / ".git").unlink()
        parked = _git(self.repo, "rev-parse", "refs/heads/parked")
        trunk_before = _git(self.repo, "rev-parse", "refs/heads/main")
        wt = self._landing_worktree("nested", {"landed.txt": "landed\n"})

        res = git_land(wt)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/parked"), parked)
        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), trunk_before)
        self.assertFalse((self.repo / "landed.txt").exists())
        self.assertEqual(res["trunk_status"], "left-behind")

    def test_trunk_checked_out_twice_is_not_moved_under_the_second_checkout(self) -> None:
        second = self.root / "second"
        _git(self.repo, "worktree", "add", "--force", str(second), "main")
        wt = self._landing_worktree("twice", {"landed.txt": "landed\n"})
        first_before, second_before = self._checkout_state(self.repo), self._checkout_state(second)

        res = git_land(wt)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(self._checkout_state(self.repo), first_before)
        self.assertEqual(self._checkout_state(second), second_before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn(str(second), res["trunk_detail"])

    def test_refused_fast_forward_restores_orig_head_in_trunk_checkout(self) -> None:
        wt = self._landing_worktree("orig-head", {"README.md": "initial\nlanded line\n"})
        (self.repo / "dropped.txt").write_text("dropped\n")
        _git(self.repo, "add", "dropped.txt")
        _git(self.repo, "commit", "-m", "dropped")
        dropped = _git(self.repo, "rev-parse", "HEAD")
        # After the reset ORIG_HEAD is the other agent's handle on the commit it dropped.
        _git(self.repo, "reset", "--hard", "HEAD~1")
        (self.repo / "README.md").write_text("initial, edited by another agent\n")

        res = git_land(wt)

        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertEqual(_git(self.repo, "rev-parse", "ORIG_HEAD"), dropped)

    def test_failed_fast_forward_that_changed_the_checkout_is_reported_as_changed(self) -> None:
        wt = self._landing_worktree("stale-ref-lock", {"landed.txt": "landed\n"})
        # A stale lock lets git update the index and files and then fail to move the branch.
        (self.repo / ".git" / "refs" / "heads" / "main.lock").write_text("")
        trunk_before = _git(self.repo, "rev-parse", "refs/heads/main")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            res = git_land(wt)

        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), trunk_before)
        self.assertEqual(_status(self.repo), ["A  landed.txt"])
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("WAS CHANGED", res["trunk_detail"])
        self.assertNotIn("left exactly as it was", stderr.getvalue())

    def test_trunk_status_is_read_back_from_the_repository_not_from_the_exit_code(self) -> None:
        wt = self._landing_worktree("exit-code", {"landed.txt": "landed\n"})
        before = self._checkout_state(self.repo)

        def merge_that_exits_zero_and_does_nothing(cwd: Path, *args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if "merge" in args:
                return subprocess.CompletedProcess(list(args), 0, "", "")
            return _run_git(cwd, *args, **kwargs)

        with patch("git_trunk_tools._run_git", side_effect=merge_that_exits_zero_and_does_nothing):
            res = git_land(wt)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(self._checkout_state(self.repo), before)
        self.assertEqual(res["trunk_status"], "left-behind")
        self.assertIn("did not reach the landed commit", res["trunk_detail"])
        self.assertIn("left exactly as it was", res["trunk_detail"])

    def test_left_behind_trunk_does_not_make_landed_work_look_unmerged(self) -> None:
        wt = self._landing_worktree("lagging", {"README.md": "initial\nlanded line\n"})
        (self.repo / "README.md").write_text("initial, edited by another agent\n")
        self.assertEqual(git_land(wt)["trunk_status"], "left-behind")

        # A worktree cut from origin's trunk has no work of its own while local trunk lags behind.
        idle = git_wt_create(self.repo, "idle", base_dir=self.wt_base)
        old = time.time() - 24 * 60 * 60
        os.utime(idle, (old, old))
        hygiene = inspect_hygiene(idle)
        self.assertEqual(hygiene["ahead_local_trunk"], 1)
        self.assertFalse(any("unmerged" in issue for issue in hygiene["issues"]))
        # Work that origin does not have is still reported.
        busy = self._landing_worktree("busy", {"wip.txt": "wip\n"})
        self.assertTrue(any("unmerged" in issue for issue in inspect_hygiene(busy)["issues"]))

        gc_res = git_wt_gc([self.repo], active_workspaces={str(busy)}, busy_workspaces=set())
        self.assertIn(str(idle), gc_res["removed_worktrees"])
        self.assertFalse(idle.exists())
        self.assertTrue(busy.is_dir())

    def test_landing_a_primary_branch_switches_the_checkout_back_to_trunk(self) -> None:
        _git(self.repo, "checkout", "-b", "feature")
        (self.repo / "feature.txt").write_text("feature\n")
        _git(self.repo, "add", "feature.txt")
        _git(self.repo, "commit", "-m", "feature")

        res = git_land(self.repo)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_git(self.repo, "symbolic-ref", "--short", "HEAD"), "main")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), res["head"])
        self.assertEqual(_status(self.repo), [])
        self.assertEqual(_git(self.repo, "branch", "--format=%(refname:short)").splitlines(), ["main"])
        self.assertFalse(res["worktree_removed"])
        self.assertEqual(res["trunk_status"], "ref-updated")

    def test_landing_a_primary_branch_does_not_reset_unpushed_trunk_commits(self) -> None:
        (self.repo / "local.txt").write_text("local\n")
        _git(self.repo, "add", "local.txt")
        _git(self.repo, "commit", "-m", "unpushed trunk commit")
        unpushed = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", "-b", "feature", "origin/main")
        (self.repo / "feature.txt").write_text("feature\n")
        _git(self.repo, "add", "feature.txt")
        _git(self.repo, "commit", "-m", "feature")

        res = git_land(self.repo)

        self.assertEqual(_git(self.repo, "rev-parse", "refs/heads/main"), unpushed)
        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_git(self.repo, "symbolic-ref", "--short", "HEAD"), "feature")
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), res["head"])
        self.assertEqual(res["trunk_status"], "left-behind")

    def test_landing_a_primary_branch_fast_forwards_trunk_held_by_secondary_worktree(self) -> None:
        _git(self.repo, "checkout", "-b", "feature")
        trunk_checkout = self.root / "trunk-checkout"
        _git(self.repo, "worktree", "add", str(trunk_checkout), "main")
        (self.repo / "feature.txt").write_text("feature\n")
        _git(self.repo, "add", "feature.txt")
        _git(self.repo, "commit", "-m", "feature")

        res = git_land(self.repo)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(_status(trunk_checkout), [])
        self.assertEqual(_git(trunk_checkout, "rev-parse", "HEAD"), res["head"])
        self.assertEqual((trunk_checkout / "feature.txt").read_text(), "feature\n")
        self.assertEqual(_git(self.repo, "symbolic-ref", "--short", "HEAD"), "feature")
        self.assertEqual(res["trunk_status"], "fast-forwarded")
        self.assertEqual(res["trunk_worktree"], str(trunk_checkout))

    def test_landing_from_the_trunk_checkout_reports_trunk_current(self) -> None:
        (self.repo / "direct.txt").write_text("direct\n")
        _git(self.repo, "add", "direct.txt")
        _git(self.repo, "commit", "-m", "direct")

        res = git_land(self.repo)

        self.assertEqual(self._origin_trunk(), res["head"])
        self.assertEqual(res["trunk_status"], "current")
        self.assertEqual(res["trunk_worktree"], str(self.repo))
        self.assertFalse(res["worktree_removed"])

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
