#!/usr/bin/env python3
"""Paired real-task evaluation of Cairn memory (stdlib only).

Subcommands:
  validate   check cases, corpus and workspace fixtures
  freeze     record hashes of the pre-registered labels before any candidate run
  verify     refuse when labels changed after freezing
  retrieval  measure the retrieval funnel for every case wording at growing collection sizes
  agent      run sandboxed Claude Code or Codex under no-memory, direct-context and memory arms
  report     summarise retrieval and agent result directories

The store commands need CAIRN_TASK_EVAL_PG, the socket directory of a disposable
PostgreSQL cluster created by scripts/trial-task-eval.sh. Nothing here reads or
writes an existing Cairn store.
"""
import argparse
import concurrent.futures
from contextlib import ExitStack
import fnmatch
import glob
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid

from trial_native_observation import capture, measure
from trial_source_delivery import bind_origins
from trial_corpus_fingerprint import corpus_stamp, same_corpus
from trial_task_arms import copy_arms, checked_file, bind_ordinary_claude
from trial_task_input import freeze_input, load_input
from trial_task_permissions import settings_permissions, command_metadata, error_metadata, denial_metadata, background_result

ROOT = Path(__file__).resolve().parents[1]
TRIAL = ROOT / "trials" / "task-eval"
TRIAL_REPO = "trial:task-eval"
OTHER_REPO = "trial:other-collection"
CLAUDE_CONTEXT_BYTES = 9500  # scripts/install-claude-hooks.py
CODEX_CONTEXT_BYTES = 12000  # scripts/install-codex-hooks.py
RANK_DEPTH = 50
MEMORY_INSTRUCTION = """# Shared memory

Use the `cairn` MCP tools. Search for relevant prior decisions, preferences and
lessons at the start of substantive work; pull relevant notes using the returned
`pull_arguments` and check them against current source. Saved notes are fallible."""
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure")


# ---------------------------------------------------------------- fixtures

def load_json(path):
    return json.loads(Path(path).read_text())


def revision_files(version=None):
    """Labelled revision overlays (revisions/vN.json), in order, up to version."""
    files = sorted((TRIAL / "revisions").glob("v*.json"), key=lambda p: int(p.stem[1:]))
    return [p for p in files if version is None or int(p.stem[1:]) <= version]


def latest_version():
    files = revision_files()
    return int(files[-1].stem[1:]) if files else 1


def load_cases(version=None, path=TRIAL / "cases.json"):
    """Cases with every revision overlay up to version applied (default: latest)."""
    cases = load_json(path)["cases"]
    for revision in revision_files(version):
        changes = load_json(revision)["cases"]
        for case in cases:
            case.update(changes.get(case["id"], {}))
    return cases


def load_corpus(version=None, path=TRIAL / "corpus.json"):
    """Corpus with revision `corpus` overlays up to version (default: latest) applied by note id."""
    notes = load_json(path)["notes"]
    for revision in revision_files(version):
        changes = load_json(revision).get("corpus", {})
        for note in notes:
            note.update(changes.get(note["id"], {}))
    return notes


def workspace_dir(case):
    """Fixture directory: frozen workspaces/NAME, or revisions/vN/... for a later labelled version."""
    if "_prospective_workspace" in case:
        return Path(case["_prospective_workspace"])
    name = case["workspace"]
    return TRIAL / name if name.startswith("revisions/") else TRIAL / "workspaces" / name


CHECK_TYPES = {"command", "tool_output", "answer", "file", "exists", "added", "unchanged", "shell", "commit_files", "staged", "all", "any"}


def validate(cases, corpus, workspaces=TRIAL / "workspaces", *, prospective=False):
    """Return a list of problems; empty means the fixtures are consistent."""
    problems = []
    ids = [n["id"] for n in corpus]
    if len(ids) != len(set(ids)):
        problems.append("duplicate corpus ids")
    known = set(ids)
    for note in corpus:
        if note.get("supersede_with") and note["supersede_with"] not in known:
            problems.append(note["id"] + ": unknown supersede_with")
    seen = set()
    for case in cases:
        cid = case["id"]
        if cid in seen:
            problems.append(cid + ": duplicate case id")
        seen.add(cid)
        for field in ("expected", "acceptable", "must_not_deliver", "over_applied"):
            for ref in case.get(field, []):
                if ref not in known:
                    problems.append(f"{cid}: unknown {field} note {ref}")
        if set(case.get("expected", [])) & set(case.get("must_not_deliver", [])):
            problems.append(cid + ": expected and forbidden overlap")
        if set(case["wordings"]) != ({"task"} if prospective else {"task", "paraphrase", "direct"}):
            problems.append(cid + ": wordings must be task, paraphrase and direct")
        if not workspace_dir(case).is_dir():
            problems.append(cid + ": missing workspace")
        if case["provenance"]["type"] not in ("actual", "adapted", "synthetic"):
            problems.append(cid + ": provenance type")
        for check in iter_checks(case.get("correct", []) + case.get("mistake", [])):
            if check["type"] not in CHECK_TYPES:
                problems.append(f"{cid}: unknown check {check['type']}")
    return problems


def iter_checks(checks):
    for check in checks:
        yield check
        if check["type"] in ("all", "any"):
            yield from iter_checks(check["checks"])


def tree_digest(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        rel = path.relative_to(TRIAL).as_posix()
        digest.update(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def frozen_path(version):
    return TRIAL / ("FROZEN.json" if version == 1 else f"FROZEN-v{version}.json")


def label_manifest(version=1):
    files = [TRIAL / "cases.json", TRIAL / "corpus.json", *revision_files(version)]
    for revision in revision_files(version):  # fixtures and grader assets of later versions
        assets = revision.with_suffix("")
        files += [p for p in assets.rglob("*") if p.is_file() and "__pycache__" not in p.parts] if assets.is_dir() else []
    files += [p for p in (TRIAL / "workspaces").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return dict(schema="cairn.task-eval.frozen/1", labels_sha256=tree_digest(files), files=len(files),
                cases_sha256=hashlib.sha256((TRIAL / "cases.json").read_bytes()).hexdigest(),
                corpus_sha256=hashlib.sha256((TRIAL / "corpus.json").read_bytes()).hexdigest(),
                baseline_commit=load_json(TRIAL / "cases.json")["baseline_commit"], label_version=version)


def verify_frozen(version=None):
    version = version or latest_version()
    frozen = load_json(frozen_path(version))
    current = label_manifest(version)
    if frozen["labels_sha256"] != current["labels_sha256"]:
        raise SystemExit("labels changed after freezing; record a new labelled version instead of editing silently")
    return frozen


# ---------------------------------------------------------------- distractors

PROJECTS = ["rhumb", "surveyor", "infra", "binkeeper", "cairn", "newsroom", "announce", "council",
            "striatum-next", "uipass", "agent-board", "pastebin", "token-dashboard", "skillpack", "jev"]
SUBJECTS = ["session store", "replay suite", "printer queue", "deploy script", "test gate", "inbox wake",
            "backup restore", "label renderer", "digest curator", "speaker routing", "GPU lease", "schema migration",
            "commit journal", "relay transport", "permission profile", "update check", "vision prompt",
            "search ranking", "worker pool", "status report", "release branch", "build cache", "hook installer"]
SENTENCES = [
    "The {s} change landed on {p} main after review by the coordinating agent.",
    "Checked the {s} against current source; the earlier note about it is still accurate.",
    "Coordination status: {p} {s} work is waiting on an owner decision before merge.",
    "Tests for the {s} passed locally with the disposable database; no deploy yet.",
    "The owner asked for a short report on the {s} with a comparison table first.",
    "Default printer and label configuration for the {s} was listed in the weekly summary.",
    "A follow-up request to review the {s} was sent through the inbox and completed.",
    "Staged changes in the {s} worktree were discussed in the review thread.",
    "The {s} failed once with a timeout, then passed after the service restarted.",
    "Recorded the {s} verification under docs/verification with the commit hash.",
    "Printer and label settings in the {s} were read back from the live page.",
    "The {s} build artifacts were uploaded after the run finished.",
    "The nightly job for the {s} completed and its summary was posted.",
    "Quiet periods for the {s} were discussed; no change was made.",
    "Update checks for the {s} ran on schedule this week.",
    "Freshness numbers for the {s} were plotted for the report.",
    "Capacity figures for the {s} were collected from the backend logs.",
]
KINDS = ["note"] * 16 + ["observation", "decision", "lesson", "procedure", "preference"]


def distractor(index, seed=20260928):
    """Deterministic synthetic note number `index`; content never answers a case."""
    rng = random.Random(seed * 1_000_003 + index)
    project, subject = rng.choice(PROJECTS), rng.choice(SUBJECTS)
    count = rng.choice([2, 3, 4, 6, 10, 18, 30])
    lines = [rng.choice(SENTENCES).format(s=rng.choice(SUBJECTS), p=rng.choice(PROJECTS)) for _ in range(count)]
    title = f"{project}: {subject} status {index:05d}"
    return dict(id=f"D-{index:05d}", kind=rng.choice(KINDS), body=title + "\n\n" + " ".join(lines))


# ---------------------------------------------------------------- store

def run(command, *, env=None, input=None, timeout=120, cwd=None, check=True):
    result = subprocess.run([str(c) for c in command], env=env, input=input, capture_output=True,
                            timeout=timeout, cwd=cwd)
    if check and result.returncode:
        raise RuntimeError(f"{command[0]} {command[1] if len(command) > 1 else ''} failed: "
                           + result.stderr.decode(errors="replace")[-800:] + result.stdout.decode(errors="replace")[-800:])
    return result


def cairn_json(binary, args, env, payload=None, timeout=60):
    result = run([binary, *args], env=env, input=None if payload is None else json.dumps(payload).encode(), timeout=timeout)
    response = json.loads(result.stdout)
    if not response.get("ok"):
        raise RuntimeError(" ".join(map(str, args[:2])) + ": " + response.get("status", "") + " " + response.get("message", ""))
    return response.get("data")


def disposable_pg():
    socket = os.environ.get("CAIRN_TASK_EVAL_PG", "")
    if not socket or not Path(socket).is_dir() or "cairn-task-eval-pg" not in socket:
        raise SystemExit("run through scripts/trial-task-eval.sh; it owns a disposable PostgreSQL cluster")
    return socket


class TrialStore:
    """One disposable database, one API server and one hosted agent token."""

    def __init__(self, root, binary, label, worker=None, embedding_worker=None):
        if worker and embedding_worker:
            raise ValueError("select one semantic backend")
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True)
        self.binary = Path(binary).resolve()
        self.worker = worker
        self.embedding_worker = embedding_worker
        self.backend = "embedding-command" if embedding_worker else "semantic-stream-command" if worker else "lexical"
        self.readiness = None
        pg = disposable_pg()
        database = "task_eval_" + re.sub(r"[^a-z0-9]", "_", label.lower())
        run([Path(os.environ["CAIRN_TASK_EVAL_PG_BIN"]) / "createdb", "-h", pg, database])
        self.pg_socket, self.database = pg, database
        self.pg_bin = Path(os.environ["CAIRN_TASK_EVAL_PG_BIN"])
        self.dsn = f"host={pg} dbname={database} sslmode=disable"
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        # Unix socket paths are limited to 108 bytes; keep the socket directory short.
        self.sockdir = Path(tempfile.mkdtemp(prefix="cte-sock-", dir="/tmp"))
        (self.root / "sock").symlink_to(self.sockdir)
        self.socket = self.sockdir / "api.sock"
        base = {k: v for k, v in os.environ.items() if not k.startswith("CAIRN_")}
        self.env = dict(base, CAIRN_HOME=str(self.home), CAIRN_DATABASE_URL=self.dsn)
        run([self.binary, "migrate"], env=self.env, timeout=300)
        token = secrets.token_urlsafe(32)
        self.token_file = self.root / "agent.token"
        self.token_file.write_text(token)
        self.token_file.chmod(0o600)
        identities = [dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(), principal="agent:task-eval-" + label,
                           repo=TRIAL_REPO, role="agent", destination="hosted")]
        (self.home / "identities.json").write_text(json.dumps(identities))
        (self.home / "identities.json").chmod(0o600)
        self.ids = {}  # fixture id -> record id
        self.names = {}  # record id -> fixture id
        self.server = None

    def fingerprint(self):
        # This instance created its database in the wrapper-owned cluster.
        if disposable_pg() != self.pg_socket:
            raise ValueError("owned database routing changed")
        return corpus_stamp(self.pg_bin / "psql", self.pg_socket, self.database, self.env, run)

    def remember(self, note):
        args = ["remember", "--repo", note.get("repo", TRIAL_REPO), "--kind", note["kind"], "--stdin",
                "--request-id", str(uuid.uuid5(uuid.NAMESPACE_URL, "task-eval:" + note["id"]))]
        if note.get("shareable", True):
            args.insert(1, "--shareable")
        result = run([self.binary, *args], env=self.env, input=note["body"].encode(), timeout=60)
        record = json.loads(result.stdout)["data"]
        self.ids[note["id"]] = record["record_id"]
        self.names[record["record_id"]] = note["id"]
        return record

    def seed(self, notes, workers=8):
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            list(pool.map(self.remember, notes))

    def supersede(self, old, new):
        preview = cairn_json(self.binary, ["preview-retract", self.ids[old]], self.env)
        cairn_json(self.binary, ["supersede"], self.env, payload=dict(
            request_id=str(uuid.uuid4()), record_id=self.ids[old], expected_version=preview["version"],
            replacement=dict(record_id=self.ids[new], version=1), preview_id=preview["preview_id"],
            reason="task-eval fixture: superseded location"))

    def seed_corpus(self, corpus, *, workers=8):
        self.seed(corpus, workers=workers)
        for note in corpus:
            if note.get("supersede_with"):
                self.supersede(note["id"], note["supersede_with"])

    def grow(self, target, start):
        """Add distractors start..target-1 (ids are stable across sizes and arms)."""
        if target > start:
            self.seed([distractor(i) for i in range(start, target)])
        return max(target, start)

    def start(self):
        command = [self.binary, "serve", "--socket", self.socket]
        if self.worker:
            command += ["--semantic-stream-command", str(Path(self.worker).resolve())]
        if self.embedding_worker:
            command += ["--embedding-command", str(Path(self.embedding_worker).resolve())]
        self.log = (self.root / "api.log").open("wb")
        self.started = time.monotonic()
        self.server = subprocess.Popen([str(c) for c in command], env=self.env, stdout=self.log, stderr=self.log)
        deadline = time.time() + 30
        while not self.socket.exists():
            if self.server.poll() is not None or time.time() > deadline:
                raise RuntimeError("API did not start; see " + str(self.root / "api.log"))
            time.sleep(0.1)

    def wait_for_full_coverage(self, timeout=1800):
        """Separate cold backfill from warm agent timing for the passage index."""
        if not self.embedding_worker:
            return
        deadline = time.monotonic() + timeout
        polls, last = 0, None
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError("candidate API exited during index backfill; see " + str(self.root / "api.log"))
            polls += 1
            try:
                data = self.agent("search", "--repo", TRIAL_REPO, "--task", "task-eval", "--run", "readiness",
                                  "--tokens", "64000", "--semantic", "--", "readiness probe")
                discovery = data.get("discovery") or {}
                coverage = discovery.get("coverage") or {}
                last = dict(state=discovery.get("state"), indexed=coverage.get("indexed"),
                            eligible=coverage.get("eligible"))
                if (last["state"] == "ready" and type(last["indexed"]) is int
                        and type(last["eligible"]) is int and last["eligible"] > 0
                        and last["indexed"] == last["eligible"]):
                    self.readiness = dict(backend=self.backend, state="full_eligible_coverage", coverage=last,
                                          cold_seconds=round(time.monotonic() - self.started, 3), polls=polls)
                    print(json.dumps(dict(index_readiness=self.readiness)), flush=True)
                    return
            except (RuntimeError, subprocess.TimeoutExpired):
                last = dict(state="search_unavailable")
            if polls == 1 or polls % 6 == 0:
                print(json.dumps(dict(index_progress=last, polls=polls,
                                      cold_seconds=round(time.monotonic() - self.started, 1))), flush=True)
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        raise RuntimeError(f"candidate index did not reach full eligible coverage in {timeout}s; last={last}")

    def stop(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(15)
            except subprocess.TimeoutExpired:
                self.server.kill()
        self.server = None
        shutil.rmtree(self.sockdir, ignore_errors=True)

    def agent(self, *args, payload=None):
        return cairn_json(self.binary, ["agent", "--socket", self.socket, "--token-file", self.token_file, *args],
                          self.env, payload=payload)


# ---------------------------------------------------------------- lifecycle hook

def load_hook(path):
    spec = importlib.util.spec_from_file_location("task_eval_hook_" + hashlib.sha256(str(path).encode()).hexdigest()[:8], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook_config(store, state_dir, context_bytes=CLAUDE_CONTEXT_BYTES):
    return dict(cairn=str(store.binary), socket=str(store.socket), token_file=str(store.token_file), repo=TRIAL_REPO,
                state_dir=str(state_dir), context_bytes=context_bytes, harness="claude")


def parse_injection(output):
    """Return (record ids in the index, expanded record id, injected bytes) from hook stdout."""
    if not output.strip():
        return [], None, 0
    data = json.loads(output)
    text = (data.get("hookSpecificOutput") or {}).get("additionalContext", "")
    if not text:
        return [], None, 0
    start = text.index("{")
    view = json.loads(text[start:])
    index = [entry["record_id"] for entry in view.get("index", [])]
    expanded = ((view.get("expanded") or {}).get("selection") or {}).get("record", {}).get("record_id")
    selected = [item.get("record", {}).get("record_id") for item in view.get("selected", []) if isinstance(item, dict)]
    return index + [s for s in selected if s], expanded, len(text.encode())


def invoke_hook(hook_path, config_path, event):
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-B", str(hook_path), "--config", str(config_path)],
                            input=json.dumps(event).encode(), capture_output=True, timeout=60)
    elapsed = time.monotonic() - started
    error = result.stderr.decode(errors="replace").strip() if result.returncode else ""
    index, expanded, size = parse_injection(result.stdout.decode()) if not result.returncode else ([], None, 0)
    return dict(index=index, expanded=expanded, bytes=size, seconds=round(elapsed, 4), error=error[-400:])


def ranked(store, query, semantic=False, depth=RANK_DEPTH):
    """Record ids in ranked order across pages, up to depth, plus discovery state."""
    order, offset, discovery, pages = [], 0, None, 0
    while len(order) < depth and offset is not None and pages < 40:
        args = ["search", "--repo", TRIAL_REPO, "--task", "task-eval", "--run", "rank-" + uuid.uuid4().hex[:8],
                "--tokens", "64000", "--offset", str(offset)]
        if semantic:
            args.append("--semantic")
        data = store.agent(*args, "--", query)
        discovery = (data.get("discovery") or {}).get("state", discovery)
        order += [e["record_id"] for e in data.get("index", [])]
        offset = (data.get("page") or {}).get("next_offset")
        pages += 1
        if not data.get("index"):
            break
    return order[:depth], discovery


def funnel(case, names, ranks, injection):
    """Classify one retrieval measurement against the pre-registered labels."""
    expected, acceptable = set(case["expected"]), set(case.get("acceptable", []))
    forbidden = set(case.get("must_not_deliver", [])) | set(case.get("over_applied", []))
    delivered = [names.get(r, "?") for r in injection["index"]]
    expanded = names.get(injection["expanded"]) if injection["expanded"] else None
    order = [names.get(r, "?") for r in ranks]
    rank = {note: order.index(note) + 1 for note in expected if note in order}
    return dict(
        expected=sorted(expected),
        rank=rank,
        in_top5=sorted(n for n in expected if rank.get(n, 99) <= 5),
        in_top50=sorted(n for n in expected if n in rank),
        delivered_expected=sorted(expected & set(delivered)),
        expanded_expected=expanded in expected,
        delivered=delivered,
        expanded=expanded,
        irrelevant=sorted(set(delivered) - expected - acceptable),
        forbidden=sorted(forbidden & (set(delivered) | {expanded})),
        bytes=injection["bytes"],
        seconds=injection["seconds"],
        error=injection["error"],
    )


def cmd_retrieval(args):
    cases, corpus = load_cases(), load_corpus()
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]
    out = Path(args.output)
    out.mkdir(mode=0o700)
    store = TrialStore(out / "store", args.cairn, args.label, worker=args.semantic_worker)
    hook_path = Path(args.hook).resolve()
    hook = load_hook(hook_path)
    store.seed_corpus(corpus)
    names = store.names  # live: distractors are added as the collection grows
    store.start()
    size, rows = 0, []
    try:
        for target in sorted(args.sizes):
            size = store.grow(target, size)
            for case in cases:
                workdir = out / "cwd" / case["id"] / case["cwd"]
                if not workdir.exists():
                    # The real fixture, so the hook sees the same project root and files as agent runs.
                    prepare_workspace(case, out / "cwd" / case["id"])
                for wording, prompt in case["wordings"].items():
                    session = str(uuid.uuid4())
                    state_dir = out / "hook-state" / session
                    config_path = out / "hook-config" / (session + ".json")
                    config_path.parent.mkdir(exist_ok=True)
                    config_path.write_text(json.dumps(hook_config(store, state_dir)))
                    event = dict(session_id=session, cwd=str(workdir), hook_event_name="UserPromptSubmit", prompt=prompt)
                    injection = invoke_hook(hook_path, config_path, event)
                    query = hook.retrieval_intent(event, {})["query"]
                    if not query.startswith(Path(case["cwd"]).name):
                        raise SystemExit(f"hook query for {case['id']} does not start with the project name: {query[:60]!r}")
                    lexical, _ = ranked(store, query)
                    row = dict(case=case["id"], category=case["category"], wording=wording, distractors=size, query=query,
                               lexical=funnel(case, names, lexical, injection))
                    if args.semantic_worker:
                        semantic, state = ranked(store, query, semantic=True)
                        row["semantic"] = dict(discovery=state, rank={n: [names.get(r) for r in semantic].index(n) + 1
                                                                      for n in case["expected"] if n in [names.get(r) for r in semantic]})
                    rows.append(row)
                    print(json.dumps(dict(case=case["id"], wording=wording, size=size,
                                          delivered=row["lexical"]["delivered_expected"], rank=row["lexical"]["rank"])), flush=True)
    finally:
        store.stop()
    report = dict(schema="cairn.task-eval.retrieval/1", label=args.label, cairn=str(store.binary), hook=str(hook_path),
                  cairn_version=cairn_json(store.binary, ["version"], store.env), hook_sha256=hashlib.sha256(hook_path.read_bytes()).hexdigest(),
                  frozen=verify_frozen(), sizes=sorted(args.sizes), corpus_notes=len(corpus), rows=rows,
                  summary=summarise_retrieval(rows))
    (out / "retrieval.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summarise_retrieval(rows):
    summary = {}
    for size in sorted({r["distractors"] for r in rows}):
        for wording in ("task", "paraphrase", "direct", "all"):
            subset = [r for r in rows if r["distractors"] == size and (wording == "all" or r["wording"] == wording)]
            answerable = [r for r in subset if r["lexical"]["expected"]]
            controls = [r for r in subset if not r["lexical"]["expected"]]
            summary[f"{size}/{wording}"] = dict(
                answerable=len(answerable),
                top50=sum(bool(r["lexical"]["in_top50"]) for r in answerable),
                top5=sum(bool(r["lexical"]["in_top5"]) for r in answerable),
                delivered=sum(bool(r["lexical"]["delivered_expected"]) for r in answerable),
                expanded=sum(r["lexical"]["expanded_expected"] for r in answerable),
                controls=len(controls),
                controls_injected=sum(bool(r["lexical"]["delivered"]) for r in controls),
                forbidden_delivered=sum(bool(r["lexical"]["forbidden"]) for r in subset),
                irrelevant_entries=sum(len(r["lexical"]["irrelevant"]) for r in subset),
                bytes_p50=percentile([r["lexical"]["bytes"] for r in subset], 0.5),
                seconds_p50=percentile([r["lexical"]["seconds"] for r in subset], 0.5),
                seconds_p95=percentile([r["lexical"]["seconds"] for r in subset], 0.95),
                hook_errors=sum(bool(r["lexical"]["error"]) for r in subset),
                semantic_states=sorted({str((r.get("semantic") or {}).get("discovery")) for r in subset}),
            )
    return summary


# ---------------------------------------------------------------- workspace and grading

def prepare_workspace(case, root):
    """Copy the fixture, run its setup and tag the evaluation base. Returns the working directory."""
    source = workspace_dir(case)
    target = Path(root) / case["copy_to"]
    def ignored(directory, names):
        return {name for name in names if name == "__pycache__"
                or (name == "setup.sh" and Path(directory) == source)}
    shutil.copytree(source, target, ignore=ignored)
    env = dict(os.environ, GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2026-09-01T00:00:00Z", GIT_COMMITTER_DATE="2026-09-01T00:00:00Z")
    setup_command = ["bash", "-e", source / "setup.sh"]
    setup = run(setup_command, cwd=target, env=env, check=False)
    if setup.returncode:
        raise subprocess.CalledProcessError(setup.returncode, setup_command, output=setup.stdout, stderr=setup.stderr)
    cwd = Path(root) / case["cwd"]
    if case.get("workspace_commit"):
        actual = run(["git", "rev-parse", "HEAD"], cwd=cwd, env=env).stdout.decode().strip()
        if actual != case["workspace_commit"]:
            raise ValueError("prepared workspace revision differs from frozen input")
    run(["git", "tag", "eval-base"], cwd=cwd, env=env)
    snapshot = {}
    for path in cwd.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(cwd).parts:
            snapshot[path.relative_to(cwd).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (Path(root) / ".eval-snapshot.json").write_text(json.dumps(snapshot))
    return cwd


def preflight_workspaces(cases, out):
    """Check trusted workspace preparation before allocating any comparison stores."""
    rows = []
    for case in cases:
        row = dict(case=case["id"], stage="workspace_prepare")
        try:
            with tempfile.TemporaryDirectory(prefix="workspace-preflight-", dir=out) as scratch:
                prepare_workspace(case, Path(scratch))
                row.update(status="passed", exit_code=0,
                           snapshot_sha256=hashlib.sha256((Path(scratch) / ".eval-snapshot.json").read_bytes()).hexdigest())
        except Exception as exc:
            row.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:800],
                       exit_code=getattr(exc, "returncode", None))
            rows.append(row)
            (out / "workspace-preflight.json").write_text(json.dumps(rows, indent=2) + "\n")
            raise
        rows.append(row)
        (out / "workspace-preflight.json").write_text(json.dumps(rows, indent=2) + "\n")


def added_lines(cwd):
    """Lines added relative to eval-base, including commits and untracked files (without touching the index)."""
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        run(["git", "read-tree", "eval-base"], cwd=cwd, env=env)
        run(["git", "add", "-A", "."], cwd=cwd, env=env)
        diff = run(["git", "diff", "--cached", "--no-color", "--unified=0", "eval-base"], cwd=cwd, env=env).stdout.decode(errors="replace")
    return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


class Undetermined(Exception):
    """A check could not reach a verdict (grader timeout, sandbox failure, declared undetermined exit)."""


def evaluate(check, ctx):
    """Evaluate one check against a finished run. ctx: cwd, commands, answer, snapshot."""
    kind, cwd = check["type"], ctx["cwd"]
    if kind == "all":
        return all(evaluate(c, ctx) for c in check["checks"])
    if kind == "any":
        return any(evaluate(c, ctx) for c in check["checks"])
    expect = check.get("expect", True)
    if kind == "tool_output":
        # Evidence from the harness's own record of a command and its output, which the model cannot write.
        matched = [t for t in ctx.get("tool_outputs", [])
                   if re.search(check["command"], t["command"]) and not t.get("is_error")
                   and all(re.search(pattern, t["output"]) for pattern in check.get("output", []))
                   and not any(re.search(pattern, t["output"]) for pattern in check.get("output_forbid", []))]
        return bool(matched) == expect
    if kind == "command":
        return any(re.search(check["pattern"], c) for c in ctx["commands"]) == expect
    if kind == "answer":
        return bool(re.search(check["pattern"], ctx["answer"] or "")) == expect
    if kind == "exists":
        matches = [p for p in glob.glob(str(cwd / check["path"]), recursive=True) if "/.git/" not in p + "/"]
        return bool(matches) == expect
    if kind == "file":
        paths = [Path(p) for p in glob.glob(str(cwd / check["path"]), recursive=True) if Path(p).is_file()]
        found = any(re.search(check["pattern"], p.read_text(errors="replace")) for p in paths)
        return found == expect
    if kind == "added":
        if "added" not in ctx:
            ctx["added"] = added_lines(cwd)
        return any(re.search(check["pattern"], line) for line in ctx["added"]) == expect
    if kind == "unchanged":
        path = cwd / check["path"]
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == ctx["snapshot"].get(check["path"])
    if kind == "shell":
        # Checks execute agent-written code with only runtime and fixture paths.
        workdir = Path(cwd).resolve()
        common_runtime=ctx.get('common_runtime')
        from trial_task_runtime import runtime_environment
        try:
            sandbox = runtime_sandbox(common_runtime=common_runtime, runtime_identity=ctx.get('runtime_identity'), writable=workdir.parent)
        except (ValueError,OSError,TypeError) as error:
            raise Undetermined('common runtime setup unavailable: '+type(error).__name__) from error
        sandbox += [
            "--bind", str(workdir.parent), str(workdir.parent), "--chdir", str(workdir),
            "--ro-bind", str(TRIAL / "revisions"), str(TRIAL / "revisions"), "--unshare-net"]
        env = dict(HOME=str(Path.home()), PATH=f"{Path.home()}/.local/go/bin:/usr/bin:/bin",
                   LANG="C.UTF-8", GOTOOLCHAIN="local", GOFLAGS="-mod=mod", GOCACHE="/tmp/go-cache", GOPATH="/tmp/go",
                   TASK_EVAL_REVISIONS=str(TRIAL / "revisions"))
        env.update(runtime_environment(common_runtime))
        for key, value in env.items():
            sandbox += ["--setenv", key, value]
        started = time.monotonic()
        log = ctx.setdefault("check_log", [])
        try:
            result = subprocess.run([*sandbox, "bash", "-c", check["run"]], capture_output=True,
                                    timeout=check.get("timeout", 120), env=env)
        except subprocess.TimeoutExpired:
            log.append(dict(run=check["run"][:200], outcome="timeout", seconds=round(time.monotonic() - started, 2)))
            raise Undetermined("grader timeout after %ss" % check.get("timeout", 120))
        output = (result.stdout + result.stderr).decode(errors="replace")[-600:]
        log.append(dict(run=check["run"][:200], exit=result.returncode, seconds=round(time.monotonic() - started, 2), output=output))
        if result.returncode in check.get("undetermined_exit", []) or output.startswith("bwrap:"):
            raise Undetermined("grader exit %d: %s" % (result.returncode, output[-200:]))
        return result.returncode == check.get("expect_exit", 0)
    if kind == "commit_files":
        names = run(["git", "diff", "--name-only", "eval-base", "HEAD"], cwd=cwd, check=False).stdout.decode().split()
        if "equals" in check:
            return sorted(names) == sorted(check["equals"])
        return bool(set(check["includes"]) & set(names))
    if kind == "staged":
        staged = run(["git", "diff", "--cached", "--name-only"], cwd=cwd, check=False).stdout.decode().split()
        return all(p in staged for p in check["paths"]) == expect
    raise ValueError("unknown check " + kind)


def verdict(check, ctx):
    try:
        return evaluate(check, ctx)
    except Undetermined as error:
        return "undetermined: " + str(error)


def grade(case, ctx):
    """correct/mistake/incomplete, or undetermined when a needed check could not reach a verdict.
    A determined mistake still wins; grader failures are never counted as wrong behaviour."""
    correct = [verdict(c, ctx) for c in case.get("correct", [])]
    mistakes = [verdict(c, ctx) for c in case.get("mistake", [])]
    unknown = [v for v in correct + mistakes if isinstance(v, str)]
    if any(v is True for v in mistakes):
        outcome = "mistake"
    elif unknown:
        outcome = "undetermined"
    elif all(correct):
        outcome = "correct"
    else:
        outcome = "incomplete"
    # Wording heuristics flag runs for human review; they never decide an outcome on their own.
    review_flags = {name: verdict(check, ctx) for name, check in case.get("review_checks", {}).items()}
    raised = sorted(name for name, value in review_flags.items() if value is not False)
    if raised and outcome != "mistake":
        outcome = "needs_review"
    result = dict(outcome=outcome, correct=correct, mistake=mistakes, stratum=case.get("stratum", "completion"))
    if review_flags:
        result["review_flags"] = review_flags
    if case.get("descriptive"):
        result["descriptive"] = {name: verdict(check, ctx) for name, check in case["descriptive"].items()}
    if ctx.get("check_log"):
        result["check_log"] = ctx["check_log"]
    return result


# ---------------------------------------------------------------- agent runs

def parse_stream(text, harness="claude"):
    if harness == "codex":
        return parse_codex_stream(text)
    if harness != "claude":
        raise ValueError("unknown harness " + harness)
    return parse_claude_stream(text)


SHELL_WRAPPER = re.compile(r"""^\s*(?:/usr)?(?:/bin/)?(?:ba|z)?sh\s+-l?c\s+(['"])(.*)\1\s*$""", re.S)


def unwrap_shell(command):
    """Codex records `/bin/bash -lc '<command>'`; checks should see the command the agent ran."""
    match = SHELL_WRAPPER.match(command)
    if not match:
        return command
    inner = match.group(2)
    return inner.replace("'\\''", "'") if match.group(1) == "'" else inner.replace('\\"', '"')


def parse_tool_outputs(text, harness="claude", *, require_completed=False):
    """Commands with the output and error status the harness recorded for them (not model-written text)."""
    outputs, pending = [], {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if harness == "codex":
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "command_execution":
                outputs.append(dict(command=unwrap_shell(item.get("command", "")), output=item.get("aggregated_output") or "",
                                    is_error=item.get("exit_code") not in (0, None)))
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use" and part.get("name") == "Bash":
                pending[part.get("id")] = ((part.get("input") or {}).get("command", ""), (part.get("input") or {}).get("run_in_background") is True)
            elif part.get("type") == "tool_result" and part.get("tool_use_id") in pending:
                body = part.get("content")
                body = body if isinstance(body, str) else "".join(x.get("text", "") for x in body or [] if isinstance(x, dict))
                command, background = pending[part["tool_use_id"]]
                if require_completed and (background or background_result(part) or background_result(event.get("tool_use_result"))):
                    continue
                pending.pop(part["tool_use_id"])
                outputs.append(dict(command=command, output=body, is_error=bool(part.get("is_error"))))
    return outputs


def parse_codex_stream(text):
    """Read completed native exec items; item-level warnings are not turn failures."""
    commands, writes, memory, errors = [], [], [], []
    answer, turns, usage, seen = "", 0, {}, set()
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn.completed":
            turns += 1
            for key, value in (event.get("usage") or {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
        elif kind in ("turn.failed", "error"):
            errors.append(event.get("error") or event.get("message") or kind)
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("id") in seen:
                continue
            if item.get("id") is not None:
                seen.add(item["id"])
            item_type = item.get("type")
            if item_type == "command_execution":
                commands.append(item.get("command", ""))
            elif item_type == "file_change" and item.get("status") == "completed":
                writes.extend(change["path"] for change in item.get("changes", []))
            elif item_type == "mcp_tool_call" and item.get("server") == "cairn":
                memory.append(dict(tool="mcp__cairn__" + item["tool"], input=item.get("arguments") or {}))
            elif item_type == "agent_message":
                answer = item.get("text", "")
    return dict(commands=commands, writes=writes, memory_calls=memory, answer=answer,
                turns=turns, turn_unit="native_exec_turn", duration_ms=None, cost_usd=None,
                is_error=bool(errors), errors=errors, completed=bool(turns) and not errors,
                subtype="turn.failed" if errors else "turn.completed" if turns else "unterminated",
                input_tokens=usage.get("input_tokens"), cache_read_tokens=usage.get("cached_input_tokens"),
                cache_creation_tokens=usage.get("cache_write_input_tokens"), output_tokens=usage.get("output_tokens"))


def claude_admission_failure(result, assistant_error):
    """Only terminal provider metadata can stop admission, never tool/answer text."""
    if result.get("is_error") is not True or result.get("terminal_reason") != "api_error":
        return None
    status = result.get("api_error_status")
    if type(status) is not int:
        return None
    if status == 401:
        return dict(provider="claude", reason="authentication_failed", api_error_status=status)
    error, message = assistant_error
    if (status == 429 and error == "rate_limit" and result.get("result") == message
            and re.fullmatch(r"You've hit your weekly limit · resets [^\r\n]+", message)):
        return dict(provider="claude", reason="capacity_exhausted", api_error_status=status)
    return None


def parse_claude_stream(text):
    """Summarise Claude Code stream-json output: commands, file writes, memory tool calls, answer, usage."""
    commands, writes, memory, answer, result = [], [], [], "", {}
    permission_calls, denials, tool_errors, backgrounds = {}, [], [], []
    denials_reported = False
    assistant_error = (None, "")
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            content = [part for part in content if isinstance(part, dict)] if isinstance(content, list) else []
            assistant_error = (event.get("error"), "".join(
                part["text"] for part in content if part.get("type") == "text" and isinstance(part.get("text"), str)))
            for part in content:
                if part.get("type") == "tool_use":
                    name, data = part.get("name", ""), part.get("input") or {}
                    if name == "Bash":
                        commands.append(data.get("command", ""))
                        ident = part.get("id")
                        if isinstance(ident, str) and re.fullmatch(r"toolu_[A-Za-z0-9_-]{1,128}", ident):
                            permission_calls[ident] = command_metadata(data.get("command"))
                            if data.get("run_in_background") is True:
                                backgrounds.append(ident)
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        writes.append(data.get("file_path", ""))
                    elif name.startswith("mcp__cairn__"):
                        memory.append(dict(tool=name, input=data))
                elif part.get("type") == "text" and isinstance(part.get("text"), str):
                    answer = part["text"]
        elif event.get("type") == "user":
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                ident = part.get("tool_use_id")
                if isinstance(ident, str) and ident in permission_calls:
                    if part.get("is_error"):
                        row = dict(tool_use_id=ident, **permission_calls[ident], **error_metadata(part.get("content")))
                        tool_errors.append(row)
                    if background_result(part) or background_result(event.get("tool_use_result")):
                        backgrounds.append(ident)
        elif event.get("type") == "result":
            result = event
            denials_reported = isinstance(event.get("permission_denials"), list)
            denials = [denial_metadata(value, permission_calls) for value in event["permission_denials"]] if denials_reported else []
            answer = event.get("result") or answer
    usage = result.get("usage") or {}
    return dict(commands=commands, writes=writes, memory_calls=memory, answer=answer,
                turns=result.get("num_turns"), duration_ms=result.get("duration_ms"), cost_usd=result.get("total_cost_usd"),
                is_error=result.get("is_error"), subtype=result.get("subtype"), completed=bool(result),
                admission_failure=claude_admission_failure(result, assistant_error),
                permission_observation=dict(denials_reported=denials_reported, denial_count=len(denials) if denials_reported else None, denials=denials, tool_errors=tool_errors,
                                            background_calls=sorted(set(backgrounds))),
                turn_unit="claude_num_turns",
                input_tokens=usage.get("input_tokens"), cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_tokens=usage.get("cache_creation_input_tokens"), output_tokens=usage.get("output_tokens"))


def execution_failure(trace, code):
    if code == 124:
        return "timeout"
    if code:
        return "nonzero_exit"
    if trace.get("is_error"):
        return "provider_error"
    if not trace.get("completed"):
        return "unterminated_stream"
    return None


def runtime_sandbox(*, common_runtime=None, runtime_identity=None, writable=None):
    """Mount system runtimes, not the host root or its application/service data.

    Network isolation is selected by the caller. Provider processes still need
    host networking; this filesystem/IPC boundary is not an egress policy.
    """
    home = Path.home()
    command = ["bwrap", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
               "--die-with-parent", "--cap-drop", "ALL", "--clearenv",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--dir", str(home)]
    for name in ("/usr/bin", "/usr/sbin", "/usr/lib", "/usr/lib64",
                 "/usr/share/git-core", "/usr/share/ca-certificates", "/etc/ssl/certs",
                 "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ld.so.cache"):
        path = Path(name)
        if path.exists():
            command += ["--ro-bind", str(path.resolve()), name]
    for name in ("/bin", "/sbin", "/lib", "/lib64"):
        path = Path(name)
        if path.is_symlink():
            command += ["--symlink", os.readlink(path), name]
        elif path.exists():
            command += ["--ro-bind", name, name]
    go = home / ".local/go"
    if go.exists() and common_runtime is None:
        command += ["--ro-bind", str(go.resolve()), str(go)]
    if common_runtime is not None:
        from trial_task_runtime import runtime_mounts
        command += runtime_mounts(common_runtime, runtime_identity, writable)
    return command


def sandbox_command(root, cwd_name, extra_binds, env, provider_args, harness="claude", *, native_executable=None, common_runtime=None, runtime_identity=None):
    """Add the chosen provider and owned trial paths to the runtime filesystem."""
    home = Path.home()
    command = runtime_sandbox(common_runtime=common_runtime, runtime_identity=runtime_identity, writable=root)
    if harness == "claude":
        claude = home / ".local/bin/claude"
        command += ["--ro-bind", str(Path(native_executable).resolve(strict=True) if native_executable else claude.resolve(strict=True)), str(claude),
                    "--dir", str(home / ".claude"),
                    "--ro-bind" if native_executable else "--bind", str(home / ".claude/.credentials.json"), str(home / ".claude/.credentials.json")]
    elif harness != "codex":
        raise ValueError("unknown harness " + harness)
    command += ["--bind", str(root), str(root), "--chdir", str(Path(root) / cwd_name)]  # absolute gitdirs
    for source, target, mode in extra_binds:
        command += ["--ro-bind" if mode == "ro" else "--bind", str(source), str(target)]
    for key, value in env.items():
        command += ["--setenv", key, value]
    return command + provider_args


def run_agent(case, arm, seed, order, args, stores, out):
    run_id = f"{case['id']}.{arm}.s{seed}"
    base = out / "runs" / run_id
    base.mkdir(parents=True)
    root = base / "work"
    root.mkdir()
    prepare_workspace(case, root)
    cwd = root / case["cwd"]
    prospective = getattr(args, "_prospective", None)
    corpus = {n["id"]: n for n in (prospective["notes"] if prospective else load_corpus())}
    prompt = case["wordings"][args.wording]
    home = Path.home()
    env = dict(HOME=str(home), PATH=f"{home}/.local/bin:{home}/.local/go/bin:/usr/local/bin:/usr/bin:/bin",
               LANG="C.UTF-8", GOTOOLCHAIN="local", GOFLAGS="-mod=mod", TERM="dumb",
               GIT_AUTHOR_NAME="trial agent", GIT_AUTHOR_EMAIL="agent@example.invalid",
               GIT_COMMITTER_NAME="trial agent", GIT_COMMITTER_EMAIL="agent@example.invalid")
    binds = []
    sandbox_options = dict(native_executable=checked_file(prospective["document"]["native"]["binary"])) if prospective else {}
    common_runtime = prospective['document'].get('runtime') if prospective else None
    runtime_identity = None
    if common_runtime is not None:
        from trial_task_runtime import validate_runtime, prepare_identity, runtime_environment
        validate_runtime(common_runtime)
        runtime_identity = prepare_identity(base/'runtime-identity')
        sandbox_options.update(common_runtime=common_runtime, runtime_identity=runtime_identity)
        env.update(runtime_environment(common_runtime))
    if args.harness == "claude":
        provider = ["claude", "-p", "--model", args.model, "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "bypassPermissions", "--no-session-persistence", "--strict-mcp-config",
                    "--max-turns", str(args.max_turns)]
        if prospective:
            provider = ["claude", "-p", "--model", args.model, "--effort", args.reasoning_effort,
                        "--output-format", "stream-json", "--verbose", "--permission-prompts", "none",
                        "--setting-sources", "", "--no-session-persistence", "--strict-mcp-config",
                        "--input-format", "stream-json", "--replay-user-messages", "--include-hook-events",
                        "--max-turns", str(args.max_turns)]
            env["ENABLE_TOOL_SEARCH"] = "false"
    else:
        # Invoke the reviewed npm entry point, not this host's coordination launcher.
        install = Path(args.codex_install).resolve()
        profile = base / "codex-home"
        profile.mkdir(mode=0o700)
        binds += [(install, install, "ro"), (profile, home / ".codex", "rw"),
                  (Path(args.codex_auth_file).resolve(), home / ".codex/auth.json", "ro")]
        provider = [str(install / "codex/bin/codex.js"), "exec", "--json", "--ephemeral", "--ignore-user-config",
                    "--ignore-rules", "--skip-git-repo-check", "--sandbox", "danger-full-access",
                    "-c", 'approval_policy="never"', "-c", "model_reasoning_effort=" + json.dumps(args.reasoning_effort),
                    "-m", args.model]
    if arm == "direct":
        notes = [corpus[n]["body"] for n in case["expected"]]
        if notes:
            prompt = "Relevant saved notes (fallible; check against the workspace):\n\n" + "\n\n---\n\n".join(notes) + "\n\n---\n\n" + prompt
    elif arm != "none":
        store = stores[arm]
        task_wire = (json.dumps(dict(type="user", message=dict(role="user", content=prompt)), ensure_ascii=False) + "\n").encode()
        context_limit = 9500 - len(task_wire) - 512 - len(MEMORY_INSTRUCTION.encode()) if prospective else (CLAUDE_CONTEXT_BYTES if args.harness == "claude" else CODEX_CONTEXT_BYTES)
        if context_limit < 1000:
            raise ValueError("task leaves insufficient hook allowance")
        session_state = base / "hook-state"
        session_state.mkdir()
        trial = Path("/tmp/trial/store")
        config = dict(cairn=str(trial / "cairn"), socket=str(trial / "sock/api.sock"), token_file=str(trial / "agent.token"),
                      repo=TRIAL_REPO, state_dir="/tmp/trial/hookstate",
                      context_bytes=context_limit,
                      harness=args.harness, semantic_fallback=arm in args.semantic_recall)
        arm_config = stores[arm].get("configuration", {}) if prospective else {}
        if prospective:
            config["semantic_fallback"] = arm_config["semantic_fallback"]
            config["recall_mode"] = arm_config["recall_mode"]
        if config["semantic_fallback"] and config.get("recall_mode") != "agent_tools":
            config.update(claude="/tmp/trial/selector/claude", model=args.selector_model)
            binds.append((Path(args.selector_binary).resolve(), config["claude"], "ro"))
            if args.harness == "codex":
                binds.append((Path(args.selector_auth_file).resolve(), home / ".claude/.credentials.json", "ro"))
        (base / "hook-config.json").write_text(json.dumps(config))
        if prospective and config["recall_mode"] == "agent_tools":
            binds += bind_ordinary_claude(base, config, arm_config["bridge"], launcher_task=True)
        hook_cmd = "bash -o pipefail -c 'python3 -B /tmp/trial/hook-observer.py --engine /tmp/trial/hook/memory.py --config /tmp/trial/hookconfig.json --observations /tmp/trial/hookstate/observations.jsonl | tee -a /tmp/trial/hookstate/calls.jsonl'"
        if prospective:
            hook_cmd = "python3 -B /tmp/trial/hook-observer.py --engine /tmp/trial/hook/memory.py --config /tmp/trial/hookconfig.json --observations /tmp/trial/hookstate/observations.jsonl"
            hook_cmd += " --frozen-prompt-sha256 " + hashlib.sha256(prompt.encode()).hexdigest()
        settings = dict(hooks={event: [dict(hooks=[dict(type="command", command=hook_cmd, timeout=13)])] for event in HOOK_EVENTS})
        (base / "settings.json").write_text(json.dumps(settings))
        mcp = dict(mcpServers=dict(cairn=dict(type="stdio", command=str(trial / "cairn"), args=[
            "mcp", "--socket", str(trial / "sock/api.sock"), "--token-file", str(trial / "agent.token"), "--repo", TRIAL_REPO,
            "--task", "task-eval/" + run_id, "--run", run_id])))
        (base / "mcp.json").write_text(json.dumps(mcp))
        binds += [(store["store"].binary, trial / "cairn", "ro"), (store["store"].sockdir, trial / "sock", "rw"),
                  (store["store"].token_file, trial / "agent.token", "ro"), (Path(store["hook"]).parent, "/tmp/trial/hook", "ro"),
                  (ROOT / "scripts/trial_hook_observer.py", "/tmp/trial/hook-observer.py", "ro"),
                  (ROOT / "scripts/trial_source_delivery.py", "/tmp/trial/trial_source_delivery.py", "ro"),
                  (base / "hook-config.json", "/tmp/trial/hookconfig.json", "ro"), (session_state, "/tmp/trial/hookstate", "rw"),
                  (base / "settings.json", "/tmp/trial/settings.json", "ro"), (base / "mcp.json", "/tmp/trial/mcp.json", "ro")]
        if args.harness == "claude":
            provider += ["--settings", "/tmp/trial/settings.json", "--mcp-config", "/tmp/trial/mcp.json", "--append-system-prompt", MEMORY_INSTRUCTION]
        else:
            # Only recall hooks; capture and production inbox hooks are absent.
            hooks = {event: [dict(hooks=[dict(type="command", command=hook_cmd, timeout=13,
                                            additionalContextLimit=CODEX_CONTEXT_BYTES)])]
                     for event in ("SessionStart", "UserPromptSubmit")}
            (profile / "hooks.json").write_text(json.dumps(dict(hooks=hooks)))
            server = mcp["mcpServers"]["cairn"]
            provider += ["--dangerously-bypass-hook-trust",
                         "-c", "mcp_servers.cairn.command=" + json.dumps(server["command"]),
                         "-c", "mcp_servers.cairn.args=" + json.dumps(server["args"]),
                         "-c", "developer_instructions=" + json.dumps(MEMORY_INSTRUCTION)]
    if prospective:
        settings_path, mcp_path = base / "settings.json", base / "mcp.json"
        settings = load_json(settings_path) if settings_path.exists() else {}
        settings.update(permissions=settings_permissions(), autoMemoryEnabled=False, claudeMdExcludes=["**"])
        settings_path.write_text(json.dumps(settings))
        if arm in ("none", "direct"):
            mcp_path.write_text(json.dumps(dict(mcpServers={})))
            binds += [(settings_path, "/tmp/trial/settings.json", "ro"), (mcp_path, "/tmp/trial/mcp.json", "ro")]
            provider += ["--settings", "/tmp/trial/settings.json", "--mcp-config", "/tmp/trial/mcp.json"]
        prerequisites = []
        binding_check = [["python3", "-c", "import json,runpy; config=json.load(open('/tmp/trial/hookconfig.json')); engine=runpy.run_path('/tmp/trial/hook/memory.py'); assert engine.get('RECALL_TASK_VERSION')==1; engine['recall_task_key'](config); runpy.run_path('/tmp/trial/hook/inbox_recall.py')['validate_binding'](config, require_enabled=True)"]] if arm not in ("none", "direct") and arm_config["recall_mode"] == "agent_tools" else []
        prerequisite_commands = binding_check + ([["go", "version"], ["go", "list", "./..."]] if (cwd / "go.mod").exists() else []) + case["preflight"]
        for argv in prerequisite_commands:
            checked = sandbox_command(root, case["cwd"], binds, env, argv, args.harness, **sandbox_options)
            try:
                done = subprocess.run(checked, capture_output=True, timeout=min(120, args.timeout))
                row = dict(argv=argv, exit=done.returncode, stdout_sha256=hashlib.sha256(done.stdout).hexdigest(),
                           stderr_sha256=hashlib.sha256(done.stderr).hexdigest())
            except subprocess.TimeoutExpired:
                row = dict(argv=argv, exit=None, error="prerequisite_timeout")
            prerequisites.append(row)
            (base / "preflight.json").write_text(json.dumps(prerequisites, indent=2))
            if row["exit"] != 0:
                raise RuntimeError("prospective prerequisite failed before provider launch")
    if prospective:
        native_input = (json.dumps(dict(type="user", message=dict(role="user", content=prompt)), ensure_ascii=False) + "\n").encode()
        input_charge = len(native_input) + 512 + (len(MEMORY_INSTRUCTION.encode()) if arm not in ("none", "direct") else 0)
        if input_charge > 8500:
            raise ValueError("task and framing leave less than1000 bytes; no provider launched")
    else:
        provider += ["--", prompt]
    command = sandbox_command(root, case["cwd"], binds, env, provider, args.harness, **sandbox_options)
    started = time.monotonic()
    try:
        if prospective:
            raw_stdout, raw_stderr, code, timings = capture(command, args.timeout, base / "stream-timing.json", input_bytes=native_input)
            stdout, stderr = raw_stdout.decode(errors="replace"), raw_stderr.decode(errors="replace")
        else:
            result = subprocess.run(command, capture_output=True, timeout=args.timeout)
            stdout, stderr, code = result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"), result.returncode
    except subprocess.TimeoutExpired as expired:
        stdout = (expired.stdout or b"").decode(errors="replace")
        stderr, code = "timeout", 124
    elapsed = time.monotonic() - started
    if not prospective:
        (base / "stream.jsonl").write_text(stdout)
        (base / "stderr.txt").write_text(stderr[-20000:])
    else:
        (base / "native-output-metadata.json").write_text(json.dumps(dict(
            stdout_bytes=len(raw_stdout), stdout_sha256=hashlib.sha256(raw_stdout).hexdigest(),
            stderr_bytes=len(raw_stderr), stderr_sha256=hashlib.sha256(raw_stderr).hexdigest(),
            raw_stream_retained=False)))
    trace = parse_stream(stdout, args.harness)
    if prospective:
        # Retain only fixed terminal admission metadata before fallible grading.
        # Prospective runs intentionally have no raw stream recovery path.
        with (base / "native-terminal.json").open("x") as retained:
            json.dump(dict(schema="cairn.native-terminal/1", admission_failure=trace["admission_failure"]), retained)
            retained.flush()
            os.fsync(retained.fileno())
    failure = execution_failure(trace, code)
    if prospective:
        try:
            observation_phase = "hook_metadata"
            observations = base / "hook-state/observations.jsonl"
            hook_rows = [json.loads(line) for line in observations.read_text().splitlines()] if observations.exists() else []
            observation_phase = "measurement"
            selected_input = measure(stdout, timings, hook_rows, input_bytes=input_charge,
                                     expects_hooks=arm not in ("none", "direct"), model=args.model, prompt=prompt)
            observation_phase = "origin_binding"
            origin_path = stores[arm]["store"].root / "import-provenance.json" if arm in stores else None
            origins = json.loads(origin_path.read_bytes()) if origin_path else []
            selected_input['origin_map_sha256'] = hashlib.sha256(origin_path.read_bytes()).hexdigest() if origin_path else None
            if arm == 'direct' and case['expected']:
                selected_input['status'] = 'unknown'
                selected_input['unknown'].append('direct_prompt_source_delivery_unobserved')
            for delivery in selected_input['source_deliveries']:
                delivery['delivery'] = bind_origins(delivery['delivery'], origins, bodies={n['id']: n['body'] for n in prospective['notes']})
                if delivery['delivery']['status'] == 'unknown':
                    selected_input['status'] = 'unknown'
                    if 'source_delivery_or_origin_unknown' not in selected_input['unknown']:
                        selected_input['unknown'].append('source_delivery_or_origin_unknown')
            # Preserve source evidence even when the independent task grader fails.
            observation_phase = "selected_input_write"
            with (base / "selected-input.json").open('x') as retained:
                json.dump(selected_input, retained)
                retained.flush()
                os.fsync(retained.fileno())
        except Exception as error:
            # Observation failure must remain visible without erasing independent
            # technical grading. Never retain exception text or raw native data.
            selected_input = failed_selected_input(input_charge, observation_phase, error)
            retain_observation_error(base, selected_input["observation_error"])
    ctx = dict(cwd=cwd, commands=trace["commands"], answer=trace["answer"],
               tool_outputs=parse_tool_outputs(stdout, args.harness, require_completed=bool(prospective)),
               snapshot=json.loads((root / ".eval-snapshot.json").read_text()),
               common_runtime=common_runtime, runtime_identity=runtime_identity)
    graded = grade(case, ctx)
    if failure:
        graded = dict(graded, check_outcome=graded["outcome"], outcome="harness_error", execution_failure=failure,
                      error="prospective_native_failure" if prospective else stderr[-600:])
    try:
        memory = hook_observations(base / "hook-state", stores.get(arm, {}).get("store"))
    except Exception as error:
        if not prospective:
            raise
        memory = dict(observation_error=selected_observation_error("hook_summary", error))
        selected_input['status'] = 'unknown'
        selected_input['unknown'].append('hook_summary_failed')
        retain_observation_error(base, memory['observation_error'], name="hook-summary-error.json")
    record = dict(run_id=run_id, case=case["id"], category=case["category"], primary=case.get("primary", True), provenance=case["provenance"]["type"], arm=arm,
                  seed=seed, order=order, harness=args.harness, model=args.model, wording=args.wording, exit=code, seconds=round(elapsed, 2), prompt_bytes=len(prompt.encode()),
                  **graded, trace={k: v for k, v in trace.items() if k not in ("answer", "commands")},
                  commands=len(trace["commands"]), answer_chars=len(trace["answer"] or ""), memory=memory)
    if prospective:
        record["selected_input"] = selected_input
    record["memory_tool_pulls"] = [names_for(stores.get(arm, {}).get("store"), call) for call in trace["memory_calls"]]
    if prospective:
        add_delivery_evidence(memory, selected_input)
    classify_relevance(memory, case, prospective=bool(prospective))
    if prospective:
        record["trace"] = {key: value for key, value in record["trace"].items() if key not in ("writes", "memory_calls")}
        record["memory_tool_pulls"] = [dict(tool=row["tool"]) for row in record["memory_tool_pulls"]]
        record["memory"]["selected_input"] = record["selected_input"]
        (base / "answer.md").write_text(trace["answer"] or "")
    (base / "result.json").write_text(json.dumps(record, indent=2))
    return record


def selected_observation_error(phase, error):
    categories = (ValueError, TypeError, AttributeError, KeyError, OSError, RuntimeError)
    category = next((kind.__name__ for kind in categories if isinstance(error,kind)), 'Exception')
    return dict(phase=phase, exception_type=category)


def retain_observation_error(base, metadata, *, name="observation-error.json"):
    try:
        with (base / name).open('x') as retained:
            json.dump(metadata, retained)
            retained.flush()
            os.fsync(retained.fileno())
    except OSError:
        # A report-write error cannot erase an independently available grade.
        # The aggregate record still exposes the fixed failure and failed save.
        metadata['persistence_failed'] = True


def failed_selected_input(input_bytes, phase, error):
    return dict(schema='cairn.task-eval.selected-input/1', status='unknown', failures=[],
                unknown=['observation_failed'], observation_error=selected_observation_error(phase, error),
                input_bytes=input_bytes, hook_wire_bytes=None, native_memory_result_bytes=None,
                total_bytes=None, last_native_memory_seconds=None, memory_attempted=None,
                memory_calls=None, native_searches=None, hook_searches=None, total_actual_pull_calls=None,
                source_deliveries=[], source_delivery_events_omitted=None, origin_map_sha256=None,
                native_prompt_events=[], native_prompt_events_omitted=None,
                limits=dict(selected_input_bytes=9500, hook_seconds=5, last_native_memory_seconds=30))


def add_delivery_evidence(memory, selected_input):
    deliveries = selected_input['source_deliveries']
    memory['source_deliveries'] = deliveries
    memory['origin_map_sha256'] = selected_input['origin_map_sha256']
    # Only actual emitted payloads establish exposure; seen/inspection do not.
    memory['delivered'] = sorted({item['input_id'] for row in deliveries for item in row['delivery']['items']
                                  if item.get('input_id') and item['extent'] != 'history_metadata'})
    memory['injected_bytes'] = selected_input['hook_wire_bytes']
    memory['injected_byte_basis'] = 'hook_stdout_wire'


def classify_relevance(memory, case, *, prospective=False):
    if not memory:
        return
    delivered = set(memory.get('delivered', []))
    memory['forbidden'] = sorted((set(case.get('must_not_deliver', [])) | set(case.get('over_applied', []))) & delivered)
    if prospective and not case.get('relevance_labels_complete', False):
        memory['relevance_status'] = 'unknown_unlabelled'
        memory.pop('irrelevant', None)
        memory.pop('delivered_expected', None)
        return
    if prospective:
        memory['relevance_status'] = 'declared_exhaustive'
    expected = set(case['expected'])
    memory['delivered_expected'] = sorted(expected & delivered)
    memory['irrelevant'] = sorted(delivered - expected - set(case.get('acceptable', [])))


def names_for(store, call):
    return dict(tool=call["tool"].replace("mcp__cairn__", ""), query=(call["input"] or {}).get("query"))


def hook_observations(state_dir, store):
    """Read delivered context and per-invocation metrics; label legacy state-only evidence."""
    if store is None or not Path(state_dir).is_dir():
        return {}
    delivered, size, outcomes, recalls = [], 0, [], []
    calls = Path(state_dir) / "calls.jsonl"
    injections = []
    if calls.exists():
        for line in calls.read_text().splitlines():
            try:
                index, expanded, size_one = parse_injection(line)
            except (ValueError, KeyError):
                continue
            injections.append(dict(index=[store.names.get(r, "?") for r in index],
                                   expanded=store.names.get(expanded) if expanded else None, bytes=size_one))
            delivered += injections[-1]["index"]
            size += size_one
    for path in Path(state_dir).glob("*.json"):
        state = json.loads(path.read_text())
        recall = state.get("last_recall") or {}
        outcomes.append(recall.get("outcome"))
        recalls.append({key: recall[key] for key in ("outcome", "discovery", "coverage", "inspected", "rejected",
                                                    "shortlist_candidates", "model_seconds", "model_reported_cost_usd",
                                                    "preview_seconds", "preview_reported_cost_usd", "pull_seconds",
                                                    "elapsed_seconds", "bytes", "preview_input_bytes", "selector_input_bytes")
                        if key in recall})
        delivered += [store.names.get(r["record_id"], "?") for r in recall.get("records", [])]
        delivered += [store.names.get(r, "?") for r in state.get("seen", {})]
    observation_path = Path(state_dir) / "observations.jsonl"
    invocations = []
    if observation_path.exists():
        for line in observation_path.read_text().splitlines():
            row = json.loads(line)
            if (not isinstance(row, dict) or row.get("schema") != "cairn.task-hook-observation/1"
                    or type(row.get("recall_attempted")) is not bool
                    or "recall" not in row or "event" not in row
                    or (row["recall"] is not None and not isinstance(row["recall"], dict))
                    or (not row["recall_attempted"] and row["recall"] is not None)):
                raise ValueError("unsupported hook observation")
            invocations.append(row)
        recalls = [dict(row["recall"] or {}, event=row["event"],
                        metrics_available=row["recall"] is not None,
                        observed_seconds=row.get("recall_seconds"))
                   for row in invocations if row["recall_attempted"]]
        outcomes = [r.get("outcome") for r in recalls]
    return dict(delivered=sorted(set(delivered)), injected_bytes=size, injections=injections, outcomes=outcomes,
                recalls=recalls, hook_invocations=invocations,
                recall_observation="per_invocation" if observation_path.exists() else "last_state_only")


def cmd_agent(args):
    prospective = load_input(args.prospective_input) if getattr(args, "prospective_input", None) else None
    frozen = prospective["frozen"] if prospective else verify_frozen()
    all_cases = prospective["cases"] if prospective else load_cases()
    cases = [c for c in all_cases if not args.cases or c["id"] in args.cases]
    if not cases or (args.cases and set(args.cases) - {c["id"] for c in all_cases}):
        raise SystemExit("case selection must name existing cases")
    expected_pairs = {(c["id"], seed) for c in cases for seed in range(args.first_seed, args.first_seed + args.seeds)}
    paired_plans, baseline_pairs = [], set()
    for path in args.paired_plan:
        baseline = load_json(path)
        if (baseline.get("harness", "claude") != args.harness
                or baseline.get("reasoning_effort") != (args.reasoning_effort if prospective or args.harness == "codex" else None)
                or baseline["model"] != args.model or baseline["wording"] != args.wording
                or baseline["distractors"] != args.distractors
                or baseline["frozen"]["cases_sha256"] != frozen["cases_sha256"]
                or baseline["frozen"]["corpus_sha256"] != frozen["corpus_sha256"]):
            raise SystemExit("paired baseline plan differs in harness, reasoning, model, wording, distractors or base fixtures: " + path)
        if baseline["frozen"]["labels_sha256"] != frozen["labels_sha256"]:
            raise SystemExit("paired baseline plan differs in frozen labels or revision assets: " + path)
        pairs = {(case, seed) for case, arm, seed, _ in baseline["runs"] if arm == "baseline"}
        baseline_pairs |= pairs
        paired_plans.append(dict(path=str(Path(path).resolve()), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                 frozen_labels_sha256=baseline["frozen"]["labels_sha256"],
                                 label_version=baseline["frozen"].get("label_version", 1),
                                 baseline_pairs=len(pairs)))
    if paired_plans and baseline_pairs != expected_pairs:
        raise SystemExit("paired baseline plans do not cover exactly the candidate case/seed pairs")
    if prospective:
        problems = validate(prospective["cases"], prospective["notes"], prospective=True)
        if problems:
            raise ValueError("invalid prospective input: " + "; ".join(problems))
        native = prospective["document"]["native"]
        if args.model != native["model"] or args.reasoning_effort != native["effort"]:
            raise ValueError("selected native model/effort must equal frozen input")
    execution = dict(cold_readiness_timeout_seconds=args.cold_readiness_timeout,
                     harness=args.harness, reasoning_effort=args.reasoning_effort if args.harness == "codex" else None,
                     evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     hook_observer_sha256=hashlib.sha256((ROOT / "scripts/trial_hook_observer.py").read_bytes()).hexdigest(),
                     harness_version=run([str(Path(args.codex_install) / "codex/bin/codex.js") if args.harness == "codex" else str(checked_file(prospective["document"]["native"]["binary"])) if prospective else "claude", "--version"]).stdout.decode().strip())
    out = Path(args.output)
    out.mkdir(mode=0o700)
    if prospective:
        if out.resolve().is_relative_to(prospective["root"]):
            raise ValueError("output must be outside prospective input")
        shutil.copytree(prospective["root"], out / "input")
        prospective = load_input(out / "input")
        if prospective["frozen"] != frozen:
            raise ValueError("prospective input changed during snapshot")
        cases = [c for c in prospective["cases"] if not args.cases or c["id"] in args.cases]
        args._prospective = prospective
        chosen_arms, pinned_arms = copy_arms(prospective["document"], out,
                                            lambda binary: cairn_json(binary, ["version"], dict(os.environ, CAIRN_DATABASE_URL="invalid-not-used")))
        args.arms = [name for name in chosen_arms if name in ("none", "direct")]
        args.memory = [label + ":" + item["binary"] + ":" + item["hook"] for label,item in pinned_arms.items()]
        execution["arm_contracts"] = prospective["document"]["memory_arms"]
        execution["native_contract"] = prospective["document"]["native"]
        execution["corpus_policy"] = prospective["corpus_policy"]
        execution["prospective_input"] = dict(schema="cairn.task-eval.input/1", path="input",
                                               frozen_sha256=hashlib.sha256((out / "input/FROZEN.json").read_bytes()).hexdigest())
        execution["reasoning_effort"] = args.reasoning_effort
        execution["permission_policy_sha256"] = hashlib.sha256((ROOT / "scripts/trial_task_permissions.py").read_bytes()).hexdigest()
        preflight_workspaces(cases, out)
    with ExitStack() as cleanup:
        stores = {}
        corpus = prospective["notes"] if prospective else load_corpus()
        corpus_bytes = (json.dumps(dict(notes=corpus), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        corpus_path = out / "observed-corpus.json"
        corpus_path.write_bytes(corpus_bytes)
        execution["observed_corpus"] = dict(path=corpus_path.name, sha256=hashlib.sha256(corpus_bytes).hexdigest())
        arms = list(args.arms)
        for spec in args.memory:
            label, binary, hook = spec.split(":", 2)
            arm_config = pinned_arms[label] if prospective else {}
            store = TrialStore(out / "stores" / label, binary, label, worker=args.semantic_worker,
                               embedding_worker=arm_config.get("embedding_worker") if prospective else args.embedding_worker)
            if prospective:
                cleanup.callback(store.token_file.unlink, missing_ok=True)
                cleanup.callback((store.home / "identities.json").unlink, missing_ok=True)
            cleanup.callback(store.stop)
            if prospective:
                store.seed_corpus(corpus, workers=1)
                (store.root / "import-provenance.json").write_text(json.dumps([dict(
                    input_id=note["id"], imported_record_id=store.ids[note["id"]], imported_version=1,
                    body_sha256=hashlib.sha256(note["body"].encode()).hexdigest(), provenance=note.get("provenance")) for note in corpus], indent=2))
            else:
                store.seed_corpus(corpus)
            store.grow(args.distractors, 0)
            store.start()
            store.wait_for_full_coverage(timeout=args.cold_readiness_timeout)
            stores[label] = dict(store=store, hook=str(Path(hook).resolve()), configuration=arm_config)
            arms.append(label)
        plan = []
        for seed in range(args.first_seed, args.first_seed + args.seeds):
            for case in cases:
                order = list(arms)
                random.Random(f"{seed}:{case['id']}").shuffle(order)
                plan += [(case, arm, seed, position) for position, arm in enumerate(order)]
        recall = {label: dict(semantic_fallback=v["configuration"]["semantic_fallback"] if prospective else label in args.semantic_recall,
                             recall_mode=v["configuration"].get("recall_mode", "ambient"),
                             selector_model=args.selector_model if label in args.semantic_recall else None)
                  for label, v in stores.items()}
        if args.semantic_recall:
            execution["selector_version"] = run([args.selector_binary, "--version"]).stdout.decode().strip()
            execution["selector_binary_sha256"] = hashlib.sha256(Path(args.selector_binary).read_bytes()).hexdigest()
        execution["recall"] = recall
        (out / "plan.json").write_text(json.dumps(dict(frozen=frozen, arms=arms, model=args.model, wording=args.wording, **execution,
                                                       distractors=args.distractors, paired_plans=paired_plans,
                                                       memory_backends={k: dict(backend=v["store"].backend,
                                                                                readiness=v["store"].readiness)
                                                                        for k, v in stores.items()},
                                                       limits=dict(max_turns=args.max_turns, timeout=args.timeout,
                                                                   parallel=args.parallel),
                                                       runs=[(c["id"], a, s, p) for c, a, s, p in plan]), indent=2))
        before = {label: value["store"].fingerprint() for label, value in stores.items()} if prospective else None
        if prospective:
            (out / "corpus-before.json").write_text(json.dumps(before, indent=2))
        records, next_run, stop = [], 0, None
        with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
            futures = {}
            while futures or (stop is None and next_run < len(plan)):
                # Keep no queued backlog: after a confirmed failure only the
                # already admitted window may finish. Never cancel its evidence.
                while stop is None and len(futures) < args.parallel and next_run < len(plan):
                    c, a, s, p = plan[next_run]
                    futures[pool.submit(run_agent, c, a, s, p, args, stores, out)] = next_run
                    next_run += 1
                done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
                # Inspect every completed result before admitting replacements.
                for future in sorted(done, key=lambda f: futures[f]):
                    c, arm, seed, _ = plan[futures.pop(future)]
                    try:
                        record = future.result()
                    except Exception as error:  # keep failed runs in the record
                        cid = c["id"]
                        record = dict(run_id=f"{cid}.{arm}.s{seed}", case=cid, arm=arm, seed=seed,
                                      outcome="harness_error", error=type(error).__name__ if prospective else str(error)[-600:],
                                      harness=args.harness, model=args.model)
                        # A later grading failure must not hide a provider stop.
                        base = out / "runs" / record["run_id"]
                        terminal = base / "native-terminal.json"
                        stream = base / "stream.jsonl"
                        if prospective and terminal.is_file():
                            try:
                                selected = json.loads(terminal.read_text())
                                failure = selected.get("admission_failure")
                                allowed = [dict(provider="claude", reason=reason, api_error_status=status)
                                           for reason, status in (("authentication_failed", 401), ("capacity_exhausted", 429))]
                                if selected.get("schema") != "cairn.native-terminal/1" or (failure is not None and failure not in allowed):
                                    raise ValueError("invalid selected terminal metadata")
                                record["trace"] = dict(admission_failure=failure)
                            except (OSError, ValueError, TypeError, AttributeError) as parse_error:
                                record["trace_parse_error"] = type(parse_error).__name__
                        elif not prospective and stream.is_file():
                            try:
                                trace = parse_stream(stream.read_text(), args.harness)
                                record["trace"] = {k: v for k, v in trace.items() if k not in ("answer", "commands")}
                            except (OSError, ValueError, TypeError, AttributeError) as parse_error:
                                record["trace_parse_error"] = str(parse_error)[-600:]
                        selected = base / "selected-input.json"
                        if prospective and selected.is_file():
                            try:
                                record['selected_input'] = json.loads(selected.read_bytes())
                                record['memory'] = {}
                                add_delivery_evidence(record['memory'], record['selected_input'])
                                classify_relevance(record['memory'], c, prospective=True)
                            except (OSError, ValueError, TypeError, KeyError):
                                record['source_delivery_error'] = 'selected_evidence_unavailable'
                    records.append(record)
                    failure = (record.get("trace") or {}).get("admission_failure")
                    if stop is None and failure:
                        stop = dict(run_id=record["run_id"], **failure)
                        print(json.dumps(dict(admission_stop=stop)), flush=True)
                    print(json.dumps({k: record.get(k) for k in ("run_id", "outcome", "seconds")}), flush=True)
        if prospective:
            after = {}
            try:
                for label, value in stores.items():
                    after[label] = value["store"].fingerprint()
                corpus_integrity = dict(observed=True, unchanged=all(same_corpus(before[k], after[k]) for k in before))
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                corpus_integrity = dict(observed=False, unchanged=None, error_type=type(error).__name__)
            (out / "corpus-after.json").write_text(json.dumps(after, indent=2))
            execution["corpus_integrity"] = corpus_integrity
        admission = dict(state="stopped_provider_failure" if stop else "complete", stop=stop,
                         planned=len(plan), admitted=next_run,
                         not_started=[dict(run_id=f"{c['id']}.{a}.s{s}", case=c["id"], arm=a, seed=s, order=p)
                                      for c, a, s, p in plan[next_run:]])
        report = dict(schema="cairn.task-eval.agent/1", frozen=frozen, arms=arms, model=args.model, wording=args.wording, **execution,
                      distractors=args.distractors, memory={k: dict(cairn=str(v["store"].binary), hook=v["hook"],
                      hook_sha256=hashlib.sha256(Path(v["hook"]).read_bytes()).hexdigest(),
                      backend=v["store"].backend, readiness=v["store"].readiness,
                      cairn_version=cairn_json(v["store"].binary, ["version"], v["store"].env)) for k, v in stores.items()},
                      paired_plans=paired_plans, admission=admission,
                      records=sorted(records, key=lambda r: r["run_id"]))
        if prospective:
            report["measurement_failures"] = [r["run_id"] for r in records
                                               if (r.get("selected_input") or {}).get("status") != "within_observed_limits"]
        report["summary"] = summarise_agent(report["records"], arms)
        (out / "agent.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["summary"], indent=2))
        return 2 if stop or (prospective and (execution["corpus_integrity"]["unchanged"] is not True or report["measurement_failures"])) else 0


def summarise_agent(records, arms):
    summary = {}
    for arm in arms:
        rows = [r for r in records if r.get("arm") == arm]
        outcomes = [r.get("outcome") for r in rows]
        primary = [r.get("outcome") for r in rows if r.get("primary", True)]
        reviewed = [(r.get("reviewed") or {}).get("outcome", r.get("outcome")) for r in rows if r.get("primary", True)]
        strata = {}
        for r in rows:
            if r.get("primary", True):
                bucket = strata.setdefault(r.get("stratum", "completion"), {})
                bucket[r.get("outcome")] = bucket.get(r.get("outcome"), 0) + 1
        summary[arm] = dict(runs=len(rows), **{k: outcomes.count(k) for k in ("correct", "mistake", "incomplete", "harness_error", "provider_error", "not_regradable")},
                            primary={k: primary.count(k) for k in ("correct", "mistake", "incomplete", "undetermined", "harness_error", "provider_error", "not_regradable")},
                            strata=strata,
                            primary_reviewed={k: reviewed.count(k) for k in ("correct", "mistake", "incomplete", "needs_review", "undetermined", "harness_error", "provider_error", "not_regradable")},
                            memory_delivered_expected=sum(bool((r.get("memory") or {}).get("delivered_expected")) for r in rows),
                            forbidden_delivered=sum(bool((r.get("memory") or {}).get("forbidden")) for r in rows),
                            median_seconds=statistics.median([r["seconds"] for r in rows if r.get("seconds")]) if any(r.get("seconds") for r in rows) else None,
                            median_turns=statistics.median([r["trace"]["turns"] for r in rows if (r.get("trace") or {}).get("turns")]) if any((r.get("trace") or {}).get("turns") for r in rows) else None)
    return summary


def apply_adjudications(records, adjudications, out):
    """Attach a reviewed outcome to each record whose run id, harness and stream hash match exactly.
    The check outcome is kept unchanged; reports show both."""
    for record in records:
        stream = out / "runs" / record["run_id"] / "stream.jsonl"
        digest = hashlib.sha256(stream.read_bytes()).hexdigest() if stream.exists() else None
        for entry in adjudications.get("entries", []):
            if entry["run_id"] == record["run_id"] and entry["stream_sha256"] == digest and \
                    entry.get("harness", "claude") == record.get("harness", "claude"):
                record["reviewed"] = {k: entry[k] for k in ("outcome", "stratum_result", "reason", "reviewer", "reviewed_at") if k in entry}


def cmd_regrade(args):
    """Re-grade stored runs under a label version; the original agent.json is left untouched."""
    version = args.version or latest_version()
    frozen = verify_frozen(version)
    out = Path(args.result_dir)
    data = load_json(out / "agent.json")
    cases = {c["id"]: c for c in load_cases(version)}
    records = []
    for record in data["records"]:
        record = dict(record)
        base = out / "runs" / record["run_id"]
        if record.get("outcome") != "harness_error" and (base / "stream.jsonl").exists():
            case = cases[record["case"]]
            record["graded_before"] = {k: record.get(k) for k in ("outcome", "correct", "mistake")}
            record["primary"] = case.get("primary", True)
            record["stratum"] = case.get("stratum", "completion")
            run_version = (data.get("frozen") or {}).get("label_version", 1)
            if case.get("regrade") is False or run_version < case.get("regrade_min_label_version", 0):
                # The fixture or task changed in this version; retained runs cannot answer the new checks.
                record.update(outcome="not_regradable", correct=None, mistake=None, regrade_reason=case.get("regrade_reason"))
                records.append(record)
                continue
            trace = parse_stream((base / "stream.jsonl").read_text(), record.get("harness", data.get("harness", "claude")))
            failure = execution_failure(trace, record.get("exit"))
            if failure:
                # Provider or process failures are not task outcomes, whatever the workspace shows.
                if re.search(r"(?i)failed to authenticate|oauth|unauthori[sz]ed|rate.?limit|quota|overloaded", trace.get("answer") or ""):
                    failure = "provider_error"
                record.update(outcome=failure if failure == "provider_error" else "harness_error", execution_failure=failure,
                              correct=None, mistake=None)
                records.append(record)
                continue
            stream = (base / "stream.jsonl").read_text()
            ctx = dict(cwd=base / "work" / case["cwd"], commands=trace["commands"], answer=trace["answer"],
                       tool_outputs=parse_tool_outputs(stream, record.get("harness", data.get("harness", "claude"))),
                       snapshot=json.loads((base / "work" / ".eval-snapshot.json").read_text()))
            record.update(grade(case, ctx))
        records.append(record)
    if getattr(args, "adjudications", None):
        apply_adjudications(records, load_json(args.adjudications), out)
    report = dict(data, frozen=frozen, label_version=version, records=records, summary=summarise_agent(records, data["arms"]))
    (out / f"agent-v{version}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))


# ---------------------------------------------------------------- report

def cmd_report(args):
    lines = []
    for path in args.results:
        data = load_json(path)
        if data["schema"] == "cairn.task-eval.retrieval/1":
            lines += [f"## Retrieval: {data['label']}", "", "| distractors/wording | answerable | top-50 | top-5 | hook delivered | body expanded | controls injected | forbidden | irrelevant entries | bytes p50 | hook s p50/p95 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
            for key, s in data["summary"].items():
                lines.append(f"| {key} | {s['answerable']} | {s['top50']} | {s['top5']} | {s['delivered']} | {s['expanded']} | {s['controls_injected']}/{s['controls']} | {s['forbidden_delivered']} | {s['irrelevant_entries']} | {s['bytes_p50']} | {s['seconds_p50']}/{s['seconds_p95']} |")
            lines.append("")
        else:
            lines += [f"## Agent runs ({data['model']}, wording {data['wording']}, {data['distractors']} distractors)", "",
                      "| case | " + " | ".join(data["arms"]) + " |", "|---" * (len(data["arms"]) + 1) + "|"]
            for case in sorted({r["case"] for r in data["records"]}):
                cells = []
                for arm in data["arms"]:
                    rows = [r for r in data["records"] if r["case"] == case and r["arm"] == arm]
                    cells.append(" ".join({"correct": "C", "mistake": "M", "incomplete": "I"}.get(r.get("outcome"), "E") for r in sorted(rows, key=lambda r: r.get("seed", 0))))
                lines.append(f"| {case} | " + " | ".join(cells) + " |")
            lines.append("")
    print("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    fresh = sub.add_parser("freeze-input", help="freeze a new explicitly reviewed prospective input")
    fresh.add_argument("--input", required=True)
    sub.add_parser("validate")
    f = sub.add_parser("freeze")
    f.add_argument("--version", type=int, default=None, help="label version (default: latest revision)")
    sub.add_parser("verify")
    g = sub.add_parser("regrade")
    g.add_argument("result_dir", help="agent output directory with runs/*/work and stream.jsonl")
    g.add_argument("--version", type=int, default=None)
    g.add_argument("--adjudications", help="reviewed adjudication JSON; applied beside, never over, check outcomes")
    r = sub.add_parser("retrieval")
    r.add_argument("--output", required=True)
    r.add_argument("--cairn", required=True)
    r.add_argument("--hook", required=True, help="integrations/lifecycle/memory.py of the arm's revision")
    r.add_argument("--label", required=True)
    r.add_argument("--sizes", type=int, nargs="+", default=[0, 1900])
    r.add_argument("--semantic-worker")
    r.add_argument("--cases", nargs="*")
    a = sub.add_parser("agent")
    a.add_argument("--output", required=True)
    source = a.add_mutually_exclusive_group(required=True)
    source.add_argument("--prospective-input", help="explicit frozen fresh input directory")
    source.add_argument("--legacy-fixtures", action="store_true", help="explicitly select historical fixtures; not authorization to replay")
    a.add_argument("--arms", nargs="*", default=None, choices=["none", "direct"])
    a.add_argument("--memory", action="append", default=[], help="LABEL:CAIRN_BINARY:MEMORY_PY")
    a.add_argument("--semantic-recall", action="append", default=[], metavar="LABEL",
                   help="enable semantic recall and the isolated applicability selector for this memory arm")
    a.add_argument("--selector-model", default="sonnet")
    a.add_argument("--selector-binary", default=shutil.which("claude"), help="reviewed Claude CLI for the hook's tool-free selector")
    a.add_argument("--selector-auth-file", default=str(Path.home() / ".claude/.credentials.json"),
                   help="existing selector account auth for Codex memory arms; read-only mount")
    a.add_argument("--cases", nargs="*")
    a.add_argument("--seeds", type=int, default=1)
    a.add_argument("--first-seed", type=int, default=0)
    a.add_argument("--harness", choices=["claude", "codex"], default="claude")
    a.add_argument("--model", help="default sonnet for Claude; required explicitly for Codex")
    a.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    a.add_argument("--codex-install", default=str(Path.home() / ".npm-global/lib/node_modules/@openai"),
                   help="npm installation containing codex/bin/codex.js and its platform packages")
    a.add_argument("--codex-auth-file", default=str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"),
                   help="existing account auth only; read-only mount into isolated profile")
    a.add_argument("--wording", default="task", choices=["task", "paraphrase", "direct"])
    a.add_argument("--distractors", type=int, default=1900)
    a.add_argument("--semantic-worker")
    a.add_argument("--embedding-worker", help="persistent passage backend; waits for full eligible coverage")
    a.add_argument("--paired-plan", action="append", default=[], help="captured baseline plan for case/seed matching")
    a.add_argument("--max-turns", type=int, help="Claude only; defaults to 40. Codex is bounded by --timeout.")
    a.add_argument("--cold-readiness-timeout", type=int, default=1800,
                   help="maximum cold passage-index readiness wait in seconds; separate from task timeout")
    a.add_argument("--timeout", type=int, default=900)
    a.add_argument("--parallel", type=int, default=3)
    p = sub.add_parser("report")
    p.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "freeze-input":
        bundle = load_input(args.input, frozen=False)
        problems = validate(bundle["cases"], bundle["notes"], prospective=True)
        if problems:
            parser.error("; ".join(problems))
        print(json.dumps(freeze_input(args.input)["frozen"], indent=2))
        return 0
    if args.command == "agent":
        if args.prospective_input:
            if args.arms is not None or args.memory or args.semantic_recall or args.semantic_worker or args.embedding_worker:
                parser.error("prospective arms and components come only from frozen input")
            if args.harness != "claude" or args.wording != "task" or not args.model or not args.reasoning_effort:
                parser.error("prospective execution requires Claude, explicit model, exact task wording")
            if args.distractors != 0:
                parser.error("prospective execution requires --distractors 0; never silently grow the corpus")
            if args.reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
                parser.error("unsupported prospective Claude effort")
        else:
            args.arms = args.arms if args.arms is not None else ["none", "direct"]
            args.reasoning_effort = args.reasoning_effort or "high"
        if args.cold_readiness_timeout <= 0 or args.timeout <= 0 or args.parallel <= 0 or args.seeds <= 0 or args.first_seed < 0:
            parser.error("invalid execution limits")
        labels = [s.split(":", 1)[0] for s in args.memory]
        if len(labels) != len(set(labels)) or set(labels) & {"none", "direct"}:
            parser.error("memory labels must be unique and must not use none/direct")
        if not set(args.semantic_recall) <= set(labels):
            parser.error("--semantic-recall must name a --memory label")
        if args.semantic_recall and (not args.selector_binary or not Path(args.selector_binary).is_file()
                                     or (args.harness == "codex" and not Path(args.selector_auth_file).is_file())):
            parser.error("semantic recall requires an existing selector binary and account auth")
        if args.harness == "codex":
            if not args.model:
                parser.error("--model is required for Codex")
            if args.max_turns is not None:
                parser.error("Codex exec has no --max-turns contract; use --timeout")
            if not (Path(args.codex_install) / "codex/bin/codex.js").is_file() or not Path(args.codex_auth_file).is_file():
                parser.error("Codex installation and existing auth file must exist")
        else:
            args.model = args.model or "sonnet"
            args.max_turns = args.max_turns if args.max_turns is not None else 40
    if args.command == "validate":
        problems = validate(load_cases(), load_corpus())
        print(json.dumps(dict(ok=not problems, problems=problems), indent=2))
        return 1 if problems else 0
    if args.command == "freeze":
        if validate(load_cases(), load_corpus()):
            raise SystemExit("fix validation problems first")
        version = args.version or latest_version()
        path = frozen_path(version)
        if path.exists():
            raise SystemExit("already frozen; labels are append-only")
        manifest = dict(label_manifest(version), frozen_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
        return 0
    if args.command == "verify":
        print(json.dumps(verify_frozen(), indent=2))
        return 0
    if args.command == "retrieval":
        cmd_retrieval(args)
    elif args.command == "agent":
        return cmd_agent(args)
    elif args.command == "regrade":
        cmd_regrade(args)
    else:
        cmd_report(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
