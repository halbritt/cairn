#!/usr/bin/env python3
"""Mechanical trunk integration, worktree management, and Stop-hook hygiene tools."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

WORKTREE_GC_GRACE_SECONDS = 2 * 60 * 60


def _run_git(cwd: Path | str, *args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def find_git_root(path: Path | str) -> Path | None:
    p = Path(path).resolve()
    if not p.is_dir():
        return None
    res = _run_git(p, "rev-parse", "--show-toplevel", check=False, timeout=5)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return Path(res.stdout.strip()).resolve()


def detect_trunk_branch(repo_root: Path) -> str:
    for candidate in ("main", "master"):
        if _run_git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}", check=False).returncode == 0:
            return candidate
        if _run_git(repo_root, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{candidate}", check=False).returncode == 0:
            return candidate
    return "main"


def common_git_dir(repo_root: Path) -> Path:
    res = _run_git(repo_root, "rev-parse", "--git-common-dir", check=True, timeout=5)
    raw = Path(res.stdout.strip())
    return (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()


def primary_worktree(repo_root: Path) -> Path:
    res = _run_git(repo_root, "worktree", "list", "--porcelain", check=True, timeout=5)
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line.split(" ", 1)[1].strip()).resolve()
    return repo_root


def inspect_hygiene(cwd: Path | str) -> dict[str, object]:
    root = find_git_root(cwd)
    if root is None:
        return {"in_git": False, "clean": True, "issues": []}
    trunk = detect_trunk_branch(root)
    primary = primary_worktree(root)
    is_secondary = root != primary
    status_out = _run_git(root, "status", "--porcelain", check=False, timeout=10).stdout
    dirty_lines = [line for line in status_out.splitlines() if line.strip()]
    branch = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False, timeout=5).stdout.strip()
    head = _run_git(root, "rev-parse", "HEAD", check=False, timeout=5).stdout.strip()
    has_origin = _run_git(root, "remote", "get-url", "origin", check=False, timeout=5).returncode == 0
    origin_trunk_exists = (
        has_origin
        and _run_git(root, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{trunk}", check=False, timeout=5).returncode == 0
    )
    ahead_origin = 0
    if origin_trunk_exists:
        cnt = _run_git(root, "rev-list", "--count", f"origin/{trunk}..HEAD", check=False, timeout=5).stdout.strip()
        ahead_origin = int(cnt) if cnt.isdigit() else 0
    ahead_local_trunk = 0
    if branch != trunk and _run_git(root, "show-ref", "--verify", "--quiet", f"refs/heads/{trunk}", check=False, timeout=5).returncode == 0:
        cnt = _run_git(root, "rev-list", "--count", f"{trunk}..HEAD", check=False, timeout=5).stdout.strip()
        ahead_local_trunk = int(cnt) if cnt.isdigit() else 0

    issues: list[str] = []
    if dirty_lines:
        issues.append(f"{len(dirty_lines)} uncommitted/untracked file(s) in {root}")
    if is_secondary:
        issues.append(f"currently in secondary worktree {root} (primary is {primary})")
    if branch != trunk:
        label = branch or "detached HEAD"
        if ahead_local_trunk > 0 or ahead_origin > 0:
            issues.append(f"on {label} with unmerged commits ahead of {trunk}")
    elif ahead_origin > 0:
        issues.append(f"local {trunk} is {ahead_origin} commit(s) ahead of origin/{trunk} (unpushed)")

    return {
        "in_git": True,
        "root": str(root),
        "primary": str(primary),
        "trunk": trunk,
        "branch": branch or "HEAD",
        "head": head,
        "is_secondary": is_secondary,
        "dirty_count": len(dirty_lines),
        "ahead_origin": ahead_origin,
        "ahead_local_trunk": ahead_local_trunk,
        "clean": len(issues) == 0,
        "issues": issues,
    }


def git_land(
    cwd: Path | str,
    *,
    check_cmd: str | None = None,
    keep_worktree: bool = False,
    no_push: bool = False,
    lock_timeout: int = 300,
) -> dict[str, object]:
    root = find_git_root(cwd)
    if root is None:
        raise RuntimeError(f"{cwd} is not inside a git repository")
    status_out = _run_git(root, "status", "--porcelain", check=True, timeout=10).stdout
    if status_out.strip():
        raise RuntimeError(f"working tree {root} has uncommitted/untracked changes; commit or discard before git-land")

    trunk = detect_trunk_branch(root)
    primary = primary_worktree(root)
    is_secondary = root != primary
    orig_branch = _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False, timeout=5).stdout.strip()
    has_origin = _run_git(root, "remote", "get-url", "origin", check=False, timeout=5).returncode == 0

    target_ref = f"origin/{trunk}" if has_origin else trunk
    if has_origin:
        _run_git(root, "fetch", "origin", trunk, timeout=60)
    if _run_git(root, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{trunk}" if has_origin else f"refs/heads/{trunk}", check=False).returncode == 0:
        rb = _run_git(root, "rebase", target_ref, check=False, timeout=60)
        if rb.returncode != 0:
            _run_git(root, "rebase", "--abort", check=False, timeout=15)
            raise RuntimeError(f"rebase onto {target_ref} failed: {rb.stderr.strip() or rb.stdout.strip()}")

    checked_head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    if check_cmd:
        subprocess.run(check_cmd, shell=True, cwd=root, check=True)

    lock_file = common_git_dir(root) / "git-land.lock"
    deadline = time.monotonic() + lock_timeout
    with lock_file.open("a+") as lf:
        while True:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"timed out waiting for {lock_file}") from exc
                time.sleep(0.1)

        if has_origin:
            _run_git(root, "fetch", "origin", trunk, timeout=60)
        if _run_git(root, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{trunk}" if has_origin else f"refs/heads/{trunk}", check=False).returncode == 0:
            rb = _run_git(root, "rebase", target_ref, check=False, timeout=60)
            if rb.returncode != 0:
                _run_git(root, "rebase", "--abort", check=False, timeout=15)
                raise RuntimeError(f"rebase onto {target_ref} failed: {rb.stderr.strip() or rb.stdout.strip()}")

        landed_head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
        if check_cmd and landed_head != checked_head:
            subprocess.run(check_cmd, shell=True, cwd=root, check=True)

        if has_origin and not no_push:
            _run_git(root, "push", "origin", f"HEAD:refs/heads/{trunk}", timeout=120)

        if is_secondary:
            primary_branch = _run_git(primary, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()
            primary_dirty = _run_git(primary, "status", "--porcelain", check=False).stdout.strip()
            if primary_branch == trunk and not primary_dirty:
                _run_git(primary, "merge", "--ff-only", landed_head, check=False)
            else:
                _run_git(primary, "update-ref", f"refs/heads/{trunk}", landed_head, check=False)
            if not keep_worktree:
                _run_git(primary, "worktree", "remove", "--force", str(root), check=False)
                if root.exists():
                    shutil.rmtree(root, ignore_errors=True)
                _run_git(primary, "worktree", "prune", check=False)
                if orig_branch and orig_branch != trunk:
                    _run_git(primary, "branch", "-D", orig_branch, check=False)
        elif orig_branch != trunk:
            _run_git(root, "checkout", "-B", trunk, landed_head)
            if orig_branch:
                _run_git(root, "branch", "-D", orig_branch, check=False)

    return {
        "ok": True,
        "primary": str(primary),
        "trunk": trunk,
        "head": landed_head,
        "pushed": bool(has_origin and not no_push),
        "worktree_removed": bool(is_secondary and not keep_worktree),
    }


def git_wt_create(
    cwd: Path | str,
    name: str,
    *,
    base_dir: Path | None = None,
) -> Path:
    root = find_git_root(cwd)
    if root is None:
        raise RuntimeError(f"{cwd} is not inside a git repository")
    primary = primary_worktree(root)
    trunk = detect_trunk_branch(primary)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    if not slug:
        raise ValueError("worktree name must be non-empty")
    wt_root = (base_dir or (Path.home() / ".cache" / "agent-worktrees")) / primary.name
    wt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = wt_root / slug
    branch = f"wt/{slug}"
    start_ref = f"origin/{trunk}" if _run_git(primary, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{trunk}", check=False).returncode == 0 else trunk
    _run_git(primary, "worktree", "add", "-B", branch, str(dest), start_ref, check=True)
    return dest.resolve()


def _active_agent_workspaces() -> tuple[set[str], set[str]] | None:
    token = Path.home() / ".local" / "share" / "cairn" / "hosted-agent.token"
    cairn_bin = shutil.which("cairn")
    if not cairn_bin or not token.is_file():
        return None
    try:
        res = subprocess.run(
            [cairn_bin, "agent", "--token-file", str(token), "agent-directory"],
            input='{"limit":200}\n',
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode != 0:
            return None
        envelope = json.loads(res.stdout)
        if not isinstance(envelope, dict) or envelope.get("ok") is not True:
            return None
        data = envelope.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("agents"), list):
            return None
        active = set()
        busy = set()
        for entry in data["agents"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("online"), bool):
                return None
            if not isinstance(entry.get("stopped", False), bool):
                return None
            if entry["online"] and not entry.get("stopped", False):
                metadata = entry.get("metadata") or {}
                if not isinstance(metadata, dict):
                    return None
                ws = metadata.get("workspace")
                if isinstance(ws, str) and ws:
                    if metadata.get("state") not in {"busy", "idle"}:
                        return None
                    resolved = str(Path(ws).resolve())
                    active.add(resolved)
                    if metadata.get("state") == "busy":
                        busy.add(resolved)
        return active, busy
    except Exception:
        return None


def _active_process_cwds() -> set[Path] | None:
    """Return current-user process working directories, or None if unknown."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set()
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return None

    cwds: set[Path] = set()
    uid = os.getuid()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            raw = os.readlink(entry / "cwd")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError:
            # Same-user service processes can still be hidden by ptrace policy.
            # Session workspaces and the creation grace cover those processes.
            continue
        if raw.endswith(" (deleted)"):
            continue
        cwds.add(Path(raw).resolve())
    return cwds


def git_wt_gc(
    repo_roots: list[Path],
    *,
    active_workspaces: set[str] | None = None,
    busy_workspaces: set[str] | None = None,
) -> dict[str, object]:
    if active_workspaces is None:
        discovery = _active_agent_workspaces()
        discovery_unavailable = discovery is None
        active, discovered_busy = discovery if discovery is not None else (set(), set())
    else:
        discovery_unavailable = False
        active, discovered_busy = active_workspaces, set()
    busy = busy_workspaces if busy_workspaces is not None else discovered_busy
    process_cwds = _active_process_cwds()
    removed_worktrees: list[str] = []
    removed_branches: list[str] = []
    dirty_orphans: list[str] = []
    protected_worktrees: list[str] = []

    for repo in repo_roots:
        primary = find_git_root(repo)
        if primary is None or primary != primary_worktree(primary):
            continue
        primary_busy = any(
            Path(workspace) == primary or primary in Path(workspace).parents
            for workspace in busy
        )
        if not discovery_unavailable:
            _run_git(primary, "worktree", "prune", check=False)
        trunk = detect_trunk_branch(primary)
        wt_out = _run_git(primary, "worktree", "list", "--porcelain", check=False).stdout
        for block in wt_out.strip().split("\n\n"):
            lines = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            wt_path_str = lines.get("worktree", "").strip()
            if not wt_path_str:
                continue
            wt_path = Path(wt_path_str).resolve()
            if wt_path == primary:
                continue
            if any(wt_path == Path(a) or Path(a) in wt_path.parents or wt_path in Path(a).parents for a in active):
                protected_worktrees.append(str(wt_path))
                continue
            is_merged = _run_git(primary, "merge-base", "--is-ancestor", lines.get("HEAD", "HEAD"), trunk, check=False).returncode == 0
            status_lines = _run_git(wt_path, "status", "--porcelain", check=False).stdout.splitlines() if wt_path.is_dir() else []
            non_deleted_dirty = [ln for ln in status_lines if not ln.startswith(" D ")]
            if not is_merged or non_deleted_dirty:
                if status_lines or not is_merged:
                    dirty_orphans.append(str(wt_path))
                continue
            if discovery_unavailable:
                protected_worktrees.append(str(wt_path))
                continue
            if primary_busy:
                protected_worktrees.append(str(wt_path))
                continue
            if process_cwds is None or any(wt_path == cwd or wt_path in cwd.parents for cwd in process_cwds):
                protected_worktrees.append(str(wt_path))
                continue
            try:
                recent = wt_path.is_dir() and time.time() - wt_path.stat().st_mtime < WORKTREE_GC_GRACE_SECONDS
            except OSError:
                recent = True
            if recent:
                protected_worktrees.append(str(wt_path))
                continue
            _run_git(primary, "worktree", "remove", "--force", str(wt_path), check=False)
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
            removed_worktrees.append(str(wt_path))

        if discovery_unavailable:
            continue
        _run_git(primary, "worktree", "prune", check=False)
        merged_refs = _run_git(primary, "for-each-ref", "--merged", trunk, "--format=%(refname:short)", "refs/heads/", check=False).stdout.splitlines()
        checked_out = {
            line.split(" ", 1)[1].removeprefix("refs/heads/").strip()
            for line in _run_git(primary, "worktree", "list", "--porcelain", check=False).stdout.splitlines()
            if line.startswith("branch ")
        }
        for br in merged_refs:
            if br and br not in (trunk, "main", "master") and br not in checked_out:
                if _run_git(primary, "branch", "-d", br, check=False).returncode == 0:
                    removed_branches.append(f"{primary.name}:{br}")

    return {
        "removed_worktrees": removed_worktrees,
        "removed_branches": removed_branches,
        "dirty_orphans": dirty_orphans,
        "protected_worktrees": protected_worktrees,
        "discovery_unavailable": discovery_unavailable,
    }


def stop_hygiene_hook(event: dict[str, object], *, state_dir: Path | None = None) -> dict[str, object]:
    if os.environ.get("GIT_HYGIENE_SKIP") == "1":
        return {}
    if event.get("stop_hook_active") is True:
        return {}
    is_agy = "conversationId" in event or "workspacePaths" in event
    if is_agy and event.get("fullyIdle") is not True:
        return {}

    cwd = None
    if is_agy:
        roots = event.get("workspacePaths")
        if isinstance(roots, list) and roots:
            cwd = str(roots[0])
        session_id = str(event.get("conversationId") or "default")
    else:
        cwd = str(event.get("cwd") or "")
        session_id = str(event.get("session_id") or "default")

    if not cwd or not Path(cwd).is_dir():
        return {}
    if (Path(cwd) / ".git-hygiene-ignore").exists():
        return {}

    report = inspect_hygiene(cwd)
    if not report.get("in_git") or report.get("clean"):
        return {}

    cache_dir = state_dir or (Path.home() / ".cache" / "git-hygiene-hook")
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "root": report.get("root"),
                "head": report.get("head"),
                "issues": report.get("issues"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    marker = cache_dir / f"{session_hash}.json"
    now = time.time()
    if marker.is_file():
        try:
            prev = json.loads(marker.read_text())
            if prev.get("fingerprint") == fingerprint and now - float(prev.get("ts", 0)) < 900:
                return {}
        except Exception:
            pass
    marker.write_text(json.dumps({"fingerprint": fingerprint, "ts": now}))

    issues_str = "; ".join(str(x) for x in (report.get("issues") or []))
    reason = (
        f"Git trunk-hygiene check failed ({issues_str}). "
        "Before finishing, commit or discard dirty files, land your branch/worktree onto trunk "
        "(`git-land` or `git push origin main`), and remove any temporary worktree."
    )
    return {"decision": "continue" if is_agy else "block", "reason": reason}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="git-trunk-tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_land = sub.add_parser("land", help="Rebase onto origin/main, run optional check, push to origin/main, and clean up worktree")
    p_land.add_argument("--cwd", default=".")
    p_land.add_argument("--check", dest="check_cmd", default=None)
    p_land.add_argument("--keep-worktree", action="store_true")
    p_land.add_argument("--no-push", action="store_true")
    p_land.add_argument("--timeout", type=int, default=300)

    p_wt = sub.add_parser("wt", help="Manage isolated agent worktrees outside /tmp")
    wt_sub = p_wt.add_subparsers(dest="wt_cmd", required=True)
    p_wt_create = wt_sub.add_parser("create")
    p_wt_create.add_argument("name")
    p_wt_create.add_argument("--cwd", default=".")
    p_wt_gc = wt_sub.add_parser("gc")
    p_wt_gc.add_argument("--git-root", default=str(Path.home() / "git"))

    sub.add_parser("stop-hook", help="Stop hook reading JSON from stdin")

    args = parser.parse_args(argv)
    if args.cmd == "land":
        res = git_land(
            args.cwd,
            check_cmd=args.check_cmd,
            keep_worktree=args.keep_worktree,
            no_push=args.no_push,
            lock_timeout=args.timeout,
        )
        print(json.dumps(res))
        return 0
    if args.cmd == "wt":
        if args.wt_cmd == "create":
            path = git_wt_create(args.cwd, args.name)
            print(str(path))
            return 0
        if args.wt_cmd == "gc":
            base = Path(args.git_root).resolve()
            repos = [p for p in sorted(base.iterdir()) if p.is_dir() and (p / ".git").exists()] if base.is_dir() else []
            print(json.dumps(git_wt_gc(repos)))
            return 0
    if args.cmd == "stop-hook":
        raw = sys.stdin.read().strip()
        event = json.loads(raw) if raw else {}
        out = stop_hygiene_hook(event)
        if out:
            print(json.dumps(out))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
