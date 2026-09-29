#!/usr/bin/env python3
"""Paired real-task evaluation of Cairn memory (stdlib only).

Subcommands:
  validate   check cases, corpus and workspace fixtures
  freeze     record hashes of the pre-registered labels before any candidate run
  verify     refuse when labels changed after freezing
  retrieval  measure the retrieval funnel for every case wording at growing collection sizes
  agent      run sandboxed Claude Code on the cases under no-memory, direct-context and memory arms
  report     summarise retrieval and agent result directories

The store commands need CAIRN_TASK_EVAL_PG, the socket directory of a disposable
PostgreSQL cluster created by scripts/trial-task-eval.sh. Nothing here reads or
writes an existing Cairn store.
"""
import argparse
import concurrent.futures
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

ROOT = Path(__file__).resolve().parents[1]
TRIAL = ROOT / "trials" / "task-eval"
TRIAL_REPO = "trial:task-eval"
OTHER_REPO = "trial:other-collection"
CLAUDE_CONTEXT_BYTES = 9500  # scripts/install-claude-hooks.py
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


def load_corpus(path=TRIAL / "corpus.json"):
    return load_json(path)["notes"]


CHECK_TYPES = {"command", "answer", "file", "exists", "added", "unchanged", "shell", "commit_files", "staged", "all", "any"}


def validate(cases, corpus, workspaces=TRIAL / "workspaces"):
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
        if set(case["wordings"]) != {"task", "paraphrase", "direct"}:
            problems.append(cid + ": wordings must be task, paraphrase and direct")
        if not (workspaces / case["workspace"]).is_dir():
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

    def seed_corpus(self, corpus):
        self.seed(corpus)
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
    source = TRIAL / "workspaces" / case["workspace"]
    target = Path(root) / case["copy_to"]
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("setup.sh", "__pycache__"))
    env = dict(os.environ, GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
               GIT_AUTHOR_DATE="2026-09-01T00:00:00Z", GIT_COMMITTER_DATE="2026-09-01T00:00:00Z")
    run(["bash", "-e", source / "setup.sh"], cwd=target, env=env)
    cwd = Path(root) / case["cwd"]
    run(["git", "tag", "eval-base"], cwd=cwd, env=env)
    snapshot = {}
    for path in cwd.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(cwd).parts:
            snapshot[path.relative_to(cwd).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (Path(root) / ".eval-snapshot.json").write_text(json.dumps(snapshot))
    return cwd


def added_lines(cwd):
    """Lines added relative to eval-base, including commits and untracked files (without touching the index)."""
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        env = dict(os.environ, GIT_INDEX_FILE=str(index))
        run(["git", "read-tree", "eval-base"], cwd=cwd, env=env)
        run(["git", "add", "-A", "."], cwd=cwd, env=env)
        diff = run(["git", "diff", "--cached", "--no-color", "--unified=0", "eval-base"], cwd=cwd, env=env).stdout.decode(errors="replace")
    return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


def evaluate(check, ctx):
    """Evaluate one check against a finished run. ctx: cwd, commands, answer, snapshot."""
    kind, cwd = check["type"], ctx["cwd"]
    if kind == "all":
        return all(evaluate(c, ctx) for c in check["checks"])
    if kind == "any":
        return any(evaluate(c, ctx) for c in check["checks"])
    expect = check.get("expect", True)
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
        result = subprocess.run(["bash", "-c", check["run"]], cwd=cwd, capture_output=True, timeout=120,
                                env=dict(os.environ, GOTOOLCHAIN="local", GOFLAGS="-mod=mod"))
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


def grade(case, ctx):
    correct = [evaluate(c, ctx) for c in case.get("correct", [])]
    mistakes = [evaluate(c, ctx) for c in case.get("mistake", [])]
    if any(mistakes):
        outcome = "mistake"
    elif all(correct):
        outcome = "correct"
    else:
        outcome = "incomplete"
    return dict(outcome=outcome, correct=correct, mistake=mistakes)


# ---------------------------------------------------------------- agent runs

def parse_stream(text):
    """Summarise Claude Code stream-json output: commands, file writes, memory tool calls, answer, usage."""
    commands, writes, memory, answer, result = [], [], [], "", {}
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for part in (event.get("message") or {}).get("content", []):
                if part.get("type") == "tool_use":
                    name, data = part.get("name", ""), part.get("input") or {}
                    if name == "Bash":
                        commands.append(data.get("command", ""))
                    elif name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        writes.append(data.get("file_path", ""))
                    elif name.startswith("mcp__cairn__"):
                        memory.append(dict(tool=name, input=data))
                elif part.get("type") == "text":
                    answer = part.get("text", "")
        elif event.get("type") == "result":
            result = event
            answer = event.get("result") or answer
    usage = result.get("usage") or {}
    return dict(commands=commands, writes=writes, memory_calls=memory, answer=answer,
                turns=result.get("num_turns"), duration_ms=result.get("duration_ms"), cost_usd=result.get("total_cost_usd"),
                is_error=result.get("is_error"), subtype=result.get("subtype"),
                input_tokens=usage.get("input_tokens"), cache_read_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_tokens=usage.get("cache_creation_input_tokens"), output_tokens=usage.get("output_tokens"))


def sandbox_command(root, cwd_name, extra_binds, env, claude_args):
    """bwrap: read-only host, private /tmp and HOME, only the claude install, credentials and trial paths bound."""
    home = Path.home()
    command = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", str(home),
               "--ro-bind", str(home / ".local/share/claude"), str(home / ".local/share/claude"),
               "--ro-bind", str(home / ".local/bin"), str(home / ".local/bin"),
               "--ro-bind", str(home / ".local/go"), str(home / ".local/go"),
               "--dir", str(home / ".claude"),
               "--bind", str(home / ".claude/.credentials.json"), str(home / ".claude/.credentials.json"),
               "--bind", str(root), str(root), "--chdir", str(Path(root) / cwd_name),  # same path: worktrees record absolute gitdirs
               "--unshare-pid", "--die-with-parent", "--clearenv"]
    for source, target, mode in extra_binds:
        command += ["--ro-bind" if mode == "ro" else "--bind", str(source), str(target)]
    for key, value in env.items():
        command += ["--setenv", key, value]
    return command + claude_args


def run_agent(case, arm, seed, order, args, stores, out):
    run_id = f"{case['id']}.{arm}.s{seed}"
    base = out / "runs" / run_id
    base.mkdir(parents=True)
    root = base / "work"
    root.mkdir()
    prepare_workspace(case, root)
    cwd = root / case["cwd"]
    corpus = {n["id"]: n for n in load_corpus()}
    prompt = case["wordings"][args.wording]
    home = Path.home()
    env = dict(HOME=str(home), PATH=f"{home}/.local/bin:{home}/.local/go/bin:/usr/local/bin:/usr/bin:/bin",
               LANG="C.UTF-8", GOTOOLCHAIN="local", GOFLAGS="-mod=mod", TERM="dumb",
               GIT_AUTHOR_NAME="trial agent", GIT_AUTHOR_EMAIL="agent@example.invalid",
               GIT_COMMITTER_NAME="trial agent", GIT_COMMITTER_EMAIL="agent@example.invalid")
    claude = ["claude", "-p", "--model", args.model, "--output-format", "stream-json", "--verbose",
              "--permission-mode", "bypassPermissions", "--no-session-persistence", "--strict-mcp-config",
              "--max-turns", str(args.max_turns)]
    binds = []
    if arm == "direct":
        notes = [corpus[n]["body"] for n in case["expected"]]
        if notes:
            prompt = "Relevant saved notes (fallible; check against the workspace):\n\n" + "\n\n---\n\n".join(notes) + "\n\n---\n\n" + prompt
    elif arm != "none":
        store = stores[arm]
        session_state = base / "hook-state"
        session_state.mkdir()
        trial = Path("/tmp/trial/store")
        config = dict(cairn=str(trial / "cairn"), socket=str(trial / "sock/api.sock"), token_file=str(trial / "agent.token"),
                      repo=TRIAL_REPO, state_dir="/tmp/trial/hookstate", context_bytes=CLAUDE_CONTEXT_BYTES, harness="claude")
        (base / "hook-config.json").write_text(json.dumps(config))
        hook_cmd = "bash -o pipefail -c 'python3 -B /tmp/trial/hook/memory.py --config /tmp/trial/hookconfig.json | tee -a /tmp/trial/hookstate/calls.jsonl'"
        settings = dict(hooks={event: [dict(hooks=[dict(type="command", command=hook_cmd, timeout=13)])] for event in HOOK_EVENTS})
        (base / "settings.json").write_text(json.dumps(settings))
        mcp = dict(mcpServers=dict(cairn=dict(type="stdio", command=str(trial / "cairn"), args=[
            "mcp", "--socket", str(trial / "sock/api.sock"), "--token-file", str(trial / "agent.token"), "--repo", TRIAL_REPO,
            "--task", "task-eval/" + run_id, "--run", run_id])))
        (base / "mcp.json").write_text(json.dumps(mcp))
        binds += [(store["store"].binary, trial / "cairn", "ro"), (store["store"].sockdir, trial / "sock", "rw"),
                  (store["store"].token_file, trial / "agent.token", "ro"), (Path(store["hook"]).parent, "/tmp/trial/hook", "ro"),
                  (base / "hook-config.json", "/tmp/trial/hookconfig.json", "ro"), (session_state, "/tmp/trial/hookstate", "rw"),
                  (base / "settings.json", "/tmp/trial/settings.json", "ro"), (base / "mcp.json", "/tmp/trial/mcp.json", "ro")]
        claude += ["--settings", "/tmp/trial/settings.json", "--mcp-config", "/tmp/trial/mcp.json", "--append-system-prompt", MEMORY_INSTRUCTION]
    claude += ["--", prompt]
    command = sandbox_command(root, case["cwd"], binds, env, claude)
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, timeout=args.timeout)
        stdout, stderr, code = result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"), result.returncode
    except subprocess.TimeoutExpired as expired:
        stdout = (expired.stdout or b"").decode(errors="replace")
        stderr, code = "timeout", 124
    elapsed = time.monotonic() - started
    (base / "stream.jsonl").write_text(stdout)
    (base / "stderr.txt").write_text(stderr[-20000:])
    trace = parse_stream(stdout)
    if trace["turns"] is None and not trace["commands"] and not trace["answer"]:
        record = dict(run_id=run_id, case=case["id"], arm=arm, seed=seed, order=order, outcome="harness_error",
                      exit=code, error=stderr[-600:])
        (base / "result.json").write_text(json.dumps(record, indent=2))
        return record
    ctx = dict(cwd=cwd, commands=trace["commands"], answer=trace["answer"],
               snapshot=json.loads((root / ".eval-snapshot.json").read_text()))
    graded = grade(case, ctx)
    memory = hook_observations(base / "hook-state", stores.get(arm, {}).get("store"))
    record = dict(run_id=run_id, case=case["id"], category=case["category"], provenance=case["provenance"]["type"], arm=arm,
                  seed=seed, order=order, model=args.model, wording=args.wording, exit=code, seconds=round(elapsed, 2), prompt_bytes=len(prompt.encode()),
                  **graded, trace={k: v for k, v in trace.items() if k not in ("answer", "commands")},
                  commands=len(trace["commands"]), answer_chars=len(trace["answer"] or ""), memory=memory)
    record["memory_tool_pulls"] = [names_for(stores.get(arm, {}).get("store"), call) for call in trace["memory_calls"]]
    if memory:
        expected = set(case["expected"])
        memory["delivered_expected"] = sorted(expected & set(memory["delivered"]))
        memory["forbidden"] = sorted((set(case.get("must_not_deliver", [])) | set(case.get("over_applied", []))) & set(memory["delivered"]))
        memory["irrelevant"] = sorted(set(memory["delivered"]) - expected - set(case.get("acceptable", [])))
    (base / "result.json").write_text(json.dumps(record, indent=2))
    return record


def names_for(store, call):
    return dict(tool=call["tool"].replace("mcp__cairn__", ""), query=(call["input"] or {}).get("query"))


def hook_observations(state_dir, store):
    """Delivered records per hook call, from the lifecycle state files (last_recall is overwritten per call)."""
    if store is None or not Path(state_dir).is_dir():
        return {}
    delivered, size, outcomes = [], 0, []
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
        delivered += [store.names.get(r["record_id"], "?") for r in recall.get("records", [])]
        delivered += [store.names.get(r, "?") for r in state.get("seen", {})]
    return dict(delivered=sorted(set(delivered)), injected_bytes=size, injections=injections, outcomes=outcomes)


def cmd_agent(args):
    frozen = verify_frozen()
    cases = [c for c in load_cases() if not args.cases or c["id"] in args.cases]
    expected_pairs = {(c["id"], seed) for c in cases for seed in range(args.first_seed, args.first_seed + args.seeds)}
    paired_plans, baseline_pairs = [], set()
    for path in args.paired_plan:
        baseline = load_json(path)
        if (baseline["model"] != args.model or baseline["wording"] != args.wording
                or baseline["distractors"] != args.distractors
                or baseline["frozen"]["cases_sha256"] != frozen["cases_sha256"]
                or baseline["frozen"]["corpus_sha256"] != frozen["corpus_sha256"]):
            raise SystemExit("paired baseline plan differs in model, wording, distractors or base fixtures: " + path)
        pairs = {(case, seed) for case, arm, seed, _ in baseline["runs"] if arm == "baseline"}
        baseline_pairs |= pairs
        paired_plans.append(dict(path=str(Path(path).resolve()), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                 frozen_labels_sha256=baseline["frozen"]["labels_sha256"],
                                 label_version=baseline["frozen"].get("label_version", 1),
                                 baseline_pairs=len(pairs)))
    if paired_plans and baseline_pairs != expected_pairs:
        raise SystemExit("paired baseline plans do not cover exactly the candidate case/seed pairs")
    out = Path(args.output)
    out.mkdir(mode=0o700)
    stores = {}
    corpus = load_corpus()
    arms = list(args.arms)
    for spec in args.memory:
        label, binary, hook = spec.split(":", 2)
        store = TrialStore(out / "stores" / label, binary, label, worker=args.semantic_worker,
                           embedding_worker=args.embedding_worker)
        store.seed_corpus(corpus)
        store.grow(args.distractors, 0)
        store.start()
        try:
            store.wait_for_full_coverage()
        except Exception:
            store.stop()
            raise
        stores[label] = dict(store=store, hook=str(Path(hook).resolve()))
        arms.append(label)
    plan = []
    for seed in range(args.first_seed, args.first_seed + args.seeds):
        for case in cases:
            order = list(arms)
            random.Random(f"{seed}:{case['id']}").shuffle(order)
            plan += [(case, arm, seed, position) for position, arm in enumerate(order)]
    (out / "plan.json").write_text(json.dumps(dict(frozen=frozen, arms=arms, model=args.model, wording=args.wording,
                                                   distractors=args.distractors, paired_plans=paired_plans,
                                                   memory_backends={k: dict(backend=v["store"].backend,
                                                                            readiness=v["store"].readiness)
                                                                    for k, v in stores.items()},
                                                   limits=dict(max_turns=args.max_turns, timeout=args.timeout,
                                                               parallel=args.parallel),
                                                   runs=[(c["id"], a, s, p) for c, a, s, p in plan]), indent=2))
    records = []
    try:
        with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
            futures = {pool.submit(run_agent, c, a, s, p, args, stores, out): (c["id"], a, s) for c, a, s, p in plan}
            for future in concurrent.futures.as_completed(futures):
                try:
                    record = future.result()
                except Exception as error:  # keep failed runs in the record
                    cid, arm, seed = futures[future]
                    record = dict(run_id=f"{cid}.{arm}.s{seed}", case=cid, arm=arm, seed=seed, outcome="harness_error", error=str(error)[-600:])
                records.append(record)
                print(json.dumps({k: record.get(k) for k in ("run_id", "outcome", "seconds")}), flush=True)
    finally:
        for store in stores.values():
            store["store"].stop()
    report = dict(schema="cairn.task-eval.agent/1", frozen=frozen, arms=arms, model=args.model, wording=args.wording,
                  distractors=args.distractors, memory={k: dict(cairn=str(v["store"].binary), hook=v["hook"],
                  hook_sha256=hashlib.sha256(Path(v["hook"]).read_bytes()).hexdigest(),
                  backend=v["store"].backend, readiness=v["store"].readiness,
                  cairn_version=cairn_json(v["store"].binary, ["version"], v["store"].env)) for k, v in stores.items()},
                  paired_plans=paired_plans,
                  records=sorted(records, key=lambda r: r["run_id"]))
    report["summary"] = summarise_agent(report["records"], arms)
    (out / "agent.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))


def summarise_agent(records, arms):
    summary = {}
    for arm in arms:
        rows = [r for r in records if r.get("arm") == arm]
        outcomes = [r.get("outcome") for r in rows]
        summary[arm] = dict(runs=len(rows), **{k: outcomes.count(k) for k in ("correct", "mistake", "incomplete", "harness_error")},
                            memory_delivered_expected=sum(bool((r.get("memory") or {}).get("delivered_expected")) for r in rows),
                            forbidden_delivered=sum(bool((r.get("memory") or {}).get("forbidden")) for r in rows),
                            median_seconds=statistics.median([r["seconds"] for r in rows if r.get("seconds")]) if any(r.get("seconds") for r in rows) else None,
                            median_turns=statistics.median([r["trace"]["turns"] for r in rows if (r.get("trace") or {}).get("turns")]) if any((r.get("trace") or {}).get("turns") for r in rows) else None)
    return summary


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
            trace = parse_stream((base / "stream.jsonl").read_text())
            ctx = dict(cwd=base / "work" / case["cwd"], commands=trace["commands"], answer=trace["answer"],
                       snapshot=json.loads((base / "work" / ".eval-snapshot.json").read_text()))
            record["graded_v1"] = {k: record.get(k) for k in ("outcome", "correct", "mistake")}
            record.update(grade(case, ctx))
        records.append(record)
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
    sub.add_parser("validate")
    f = sub.add_parser("freeze")
    f.add_argument("--version", type=int, default=None, help="label version (default: latest revision)")
    sub.add_parser("verify")
    g = sub.add_parser("regrade")
    g.add_argument("result_dir", help="agent output directory with runs/*/work and stream.jsonl")
    g.add_argument("--version", type=int, default=None)
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
    a.add_argument("--arms", nargs="*", default=["none", "direct"], choices=["none", "direct"])
    a.add_argument("--memory", action="append", default=[], help="LABEL:CAIRN_BINARY:MEMORY_PY")
    a.add_argument("--cases", nargs="*")
    a.add_argument("--seeds", type=int, default=1)
    a.add_argument("--first-seed", type=int, default=0)
    a.add_argument("--model", default="sonnet")
    a.add_argument("--wording", default="task", choices=["task", "paraphrase", "direct"])
    a.add_argument("--distractors", type=int, default=1900)
    a.add_argument("--semantic-worker")
    a.add_argument("--embedding-worker", help="persistent passage backend; waits for full eligible coverage")
    a.add_argument("--paired-plan", action="append", default=[], help="captured baseline plan for case/seed matching")
    a.add_argument("--max-turns", type=int, default=40)
    a.add_argument("--timeout", type=int, default=900)
    a.add_argument("--parallel", type=int, default=3)
    p = sub.add_parser("report")
    p.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
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
        cmd_agent(args)
    elif args.command == "regrade":
        cmd_regrade(args)
    else:
        cmd_report(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
