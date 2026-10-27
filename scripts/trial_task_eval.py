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
import stat
import subprocess
import sys
import tempfile
import time
import uuid

import disposable_host

from trial_account_selection import credentials_source
import trial_codex_account_selection as codex_account_selection
import trial_legacy_limits as legacy_limits
import trial_codex_weekly_limits as codex_weekly_limits
import trial_weekly_accounts as weekly_accounts
import trial_native_copy as native_copy
import trial_original_arms as original_arm_controls
import trial_embedding_identity as identity_declaration
import trial_writer_release as writer_release
import trial_legacy_pregrade as legacy_pregrade
from trial_native_observation import capture, measure
from trial_process_custody import read_custody
from trial_source_delivery import bind_origins
from trial_corpus_fingerprint import corpus_stamp, same_corpus
from trial_receipt_explanation import export_receipts, retain_hook_explanations, receipt_export_metadata
from trial_task_arms import copy_arms, checked_file, bind_ordinary_claude
from trial_task_input import corpus_entities, freeze_input, load_input
from trial_task_permissions import settings_permissions, command_metadata, error_metadata, denial_metadata, background_result
from trial_attempt_custody import AttemptBaseline, BaselineError
import trial_attempt
# One attempt lives in trial_attempt; these names stay importable from the harness.
from trial_attempt import (  # noqa: F401
    BINDING_CHECK, CLAUDE_CONTEXT_BYTES, CODEX_BINDING_CHECK, CODEX_CONTEXT_BYTES, HOOK_EVENTS,
    MEMORY_INSTRUCTION, PROSPECTIVE_MEMORY_INSTRUCTION, TRIAL_REPO, InertProvider,
    add_delivery_evidence, binding_check_command, classify_relevance, codex_process_admission_failure,
    codex_weekly_command, execution_failure, failed_selected_input, hook_observations, names_for,
    paired_json_pin, parse_injection, prerequisite_command, retain_observation_error,
    selected_observation_error, trial_hook_observer_bindings)

ROOT = Path(__file__).resolve().parents[1]
TRIAL = ROOT / "trials" / "task-eval"
OTHER_REPO = "trial:other-collection"
RANK_DEPTH = 50
CASE_MATERIALS_SCHEMA = 'cairn.legacy-v12-case-material-manifest/2'
DB_COVERAGE_ACTION_PROJECTION = 'cairn.legacy-db-coverage-action-trace/1'
CASE_MATERIALS_LIMIT = 2 * 1024 * 1024
# Pinned exporters inspect this literal without importing the selected harness.
EXECUTION_PROFILE_SCHEMA = "cairn.task-eval.execution-profile/1"


# ---------------------------------------------------------------- fixtures

def load_json(path):
    return json.loads(Path(path).read_text())


def revision_files(version=None, *, root=TRIAL):
    """Labelled revision overlays (revisions/vN.json), in order, up to version."""
    files = sorted((Path(root) / "revisions").glob("v*.json"), key=lambda p: int(p.stem[1:]))
    return [p for p in files if version is None or int(p.stem[1:]) <= version]


def latest_version(*, root=TRIAL):
    files = revision_files(root=root)
    return int(files[-1].stem[1:]) if files else 1


def load_cases(version=None, path=None, *, root=TRIAL):
    """Cases with every revision overlay up to version applied (default: latest)."""
    cases = load_json(path if path is not None else Path(root) / "cases.json")["cases"]
    for revision in revision_files(version, root=root):
        changes = load_json(revision)["cases"]
        for case in cases:
            case.update(changes.get(case["id"], {}))
    return cases


def load_corpus(version=None, path=None, *, root=TRIAL):
    """Corpus with revision `corpus` overlays up to version (default: latest) applied by note id."""
    notes = load_json(path if path is not None else Path(root) / "corpus.json")["notes"]
    for revision in revision_files(version, root=root):
        changes = load_json(revision).get("corpus", {})
        for note in notes:
            note.update(changes.get(note["id"], {}))
    return notes


def workspace_dir(case, *, root=TRIAL):
    """Fixture directory: frozen workspaces/NAME, or revisions/vN/... for a later labelled version."""
    if "_prospective_workspace" in case:
        return Path(case["_prospective_workspace"])
    name = case["workspace"]
    return Path(root) / name if name.startswith("revisions/") else Path(root) / "workspaces" / name


CHECK_TYPES = {"command", "tool_output", "answer", "file", "exists", "added", "unchanged", "shell", "commit_files", "staged", "all", "any"}


def validate(cases, corpus, workspaces=TRIAL / "workspaces", *, prospective=False, root=TRIAL):
    """Return a list of problems; empty means the fixtures are consistent."""
    problems = []
    ids = [n["id"] for n in corpus]
    if len(ids) != len(set(ids)):
        problems.append("duplicate corpus ids")
    known = set(ids)
    for note in corpus:
        if note.get("supersede_with") and note["supersede_with"] not in known:
            problems.append(note["id"] + ": unknown supersede_with")
        try:
            corpus_entities(note)
        except ValueError as error:
            problems.append(note["id"] + ": " + str(error))
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
        if not workspace_dir(case, root=root).is_dir():
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


def tree_digest(paths, *, root=TRIAL):
    digest = hashlib.sha256()
    for path in sorted(paths):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def frozen_path(version, *, root=TRIAL):
    return Path(root) / ("FROZEN.json" if version == 1 else f"FROZEN-v{version}.json")


def label_manifest(version=1, *, root=TRIAL):
    root = Path(root)
    files = [root / "cases.json", root / "corpus.json", *revision_files(version, root=root)]
    for revision in revision_files(version, root=root):  # fixtures and grader assets of later versions
        assets = revision.with_suffix("")
        files += [p for p in assets.rglob("*") if p.is_file() and "__pycache__" not in p.parts] if assets.is_dir() else []
    files += [p for p in (root / "workspaces").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return dict(schema="cairn.task-eval.frozen/1", labels_sha256=tree_digest(files, root=root), files=len(files),
                cases_sha256=hashlib.sha256((root / "cases.json").read_bytes()).hexdigest(),
                corpus_sha256=hashlib.sha256((root / "corpus.json").read_bytes()).hexdigest(),
                baseline_commit=load_json(root / "cases.json")["baseline_commit"], label_version=version)


def verify_frozen(version=None, *, root=TRIAL):
    version = version or latest_version(root=root)
    frozen = load_json(frozen_path(version, root=root))
    current = label_manifest(version, root=root)
    if frozen["labels_sha256"] != current["labels_sha256"]:
        raise SystemExit("labels changed after freezing; record a new labelled version instead of editing silently")
    return frozen


def _legacy_case_material_channels(case):
    """Derive frozen v12 channels and the one explicitly versioned safe projection."""
    check_types = set()

    def visit(value):
        if isinstance(value, dict):
            kind = value.get('type')
            if isinstance(kind, str) and kind in CHECK_TYPES:
                check_types.add(kind)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for field in ('correct', 'mistake', 'review_checks', 'descriptive'):
        visit(case.get(field))
    channels = ['task_statement', 'grading_instructions', 'final_answer']
    if check_types & {'exists', 'file', 'added', 'unchanged', 'shell', 'commit_files', 'staged'}:
        channels.append('artifact_bundle')
    if 'shell' in check_types:
        channels.append('evaluator_observation')
    projection = (DB_COVERAGE_ACTION_PROJECTION
                  if case.get('id') == 'db-coverage' and check_types == {
                      'all', 'tool_output', 'file', 'added', 'answer'} else None)
    unavailable = []
    if check_types & {'command', 'tool_output'}:
        if projection is None:
            unavailable.append(dict(name='action_trace', reason='raw_tool_output_unavailable'))
        else:
            channels.append('action_trace')
    return channels, unavailable, projection


def case_materials_pin(path, expected_sha256, cases, *, root=TRIAL):
    """Verify the runner-selected v12 materials manifest; retain its digest only."""
    if path is None and expected_sha256 is None:
        return None
    if (type(path) is not str or not path or type(expected_sha256) is not str
            or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256)):
        raise ValueError('case_materials_pin_incomplete')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or not 0 < info.st_size <= CASE_MATERIALS_LIMIT):
                raise ValueError('case_materials_file_policy')
            raw = source.read(CASE_MATERIALS_LIMIT + 1)
            if len(raw) != info.st_size:
                raise ValueError('case_materials_file_changed')
            after = os.fstat(source.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise ValueError('case_materials_file_changed')
    except (OSError, UnicodeError):
        raise ValueError('case_materials_unavailable') from None
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('case_materials_digest_mismatch')
    try:
        document = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('case_materials_invalid_json') from None
    try:
        canonical = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                               allow_nan=False).encode('utf-8') + b'\n'
    except (TypeError, ValueError):
        raise ValueError('case_materials_not_canonical') from None
    if raw != canonical:
        raise ValueError('case_materials_not_canonical')
    fields = {'schema', 'label_version', 'frozen_sha256', 'labels_sha256', 'cases_sha256',
              'evaluator_sha256', 'files_sha256', 'evaluator_observation_contract', 'cases'}
    if type(document) is not dict or set(document) != fields or document['schema'] != CASE_MATERIALS_SCHEMA:
        raise ValueError('case_materials_schema')
    root = Path(root)
    version = 12
    frozen_bytes = frozen_path(version, root=root).read_bytes()
    frozen = verify_frozen(version, root=root)
    actual = dict(label_version=version, frozen_sha256=hashlib.sha256(frozen_bytes).hexdigest(),
                  labels_sha256=frozen['labels_sha256'],
                  cases_sha256=hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest(),
                  evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  files_sha256=frozen['labels_sha256'])
    if any(document[key] != value for key, value in actual.items()):
        raise ValueError('case_materials_source_mismatch')
    if not isinstance(document['evaluator_observation_contract'], dict):
        raise ValueError('case_materials_evaluator_contract')
    case_rows = document['cases']
    if type(case_rows) is not list or len(case_rows) != len(cases):
        raise ValueError('case_materials_case_coverage')
    expected_cases = {case['id']: case for case in cases}
    ids = []
    row_fields = {'case', 'channels', 'unavailable_channels', 'action_trace_projection',
                  'contract', 'task_statement',
                  'grading_instructions', 'source_case_sha256'}
    for row in case_rows:
        if type(row) is not dict or set(row) != row_fields:
            raise ValueError('case_materials_case_shape')
        cid = row['case']
        ids.append(cid)
        if type(cid) is not str or cid not in expected_cases:
            raise ValueError('case_materials_case_identity')
        if (type(row['channels']) is not list or not row['channels']
                or any(type(channel) is not str or not channel for channel in row['channels'])):
            raise ValueError('case_materials_channels')
        if type(row['unavailable_channels']) is not list:
            raise ValueError('case_materials_unavailable_channels')
        expected_channels, expected_unavailable, expected_projection = _legacy_case_material_channels(expected_cases[cid])
        if (row['channels'] != expected_channels or row['unavailable_channels'] != expected_unavailable
                or row['action_trace_projection'] != expected_projection):
            raise ValueError('case_materials_channel_contract_mismatch')
        for field in ('contract', 'task_statement', 'grading_instructions'):
            pin = row[field]
            if (type(pin) is not dict or set(pin) != {'path', 'bytes', 'sha256'}
                    or type(pin['path']) is not str or not pin['path']
                    or type(pin['bytes']) is not int or pin['bytes'] <= 0
                    or type(pin['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', pin['sha256'])):
                raise ValueError('case_materials_material_pin')
        if (type(row['source_case_sha256']) is not str
                or row['source_case_sha256'] != hashlib.sha256(
                    json.dumps(expected_cases[cid], ensure_ascii=False, sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n').hexdigest()):
            raise ValueError('case_materials_case_source_mismatch')
    if ids != sorted(expected_cases):
        raise ValueError('case_materials_case_order')
    return dict(schema=CASE_MATERIALS_SCHEMA, sha256=expected_sha256)


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


def import_provenance(corpus, ids):
    """The frozen import map: fixture note -> imported record. Explicit entity associations, when a
    note declares any, are recorded as imported; inputs without them keep the earlier row shape."""
    rows = []
    for note in corpus:
        row = dict(input_id=note["id"], imported_record_id=ids[note["id"]], imported_version=1,
                   body_sha256=hashlib.sha256(note["body"].encode()).hexdigest(), provenance=note.get("provenance"))
        entities = corpus_entities(note)
        if entities:
            row["entities"] = [dict(kind=kind, name=name) for kind, name in entities]
        rows.append(row)
    return rows


class TrialStore:
    """One disposable database, one API server and one hosted agent token."""

    def __init__(self, root, binary, label, worker=None, embedding_worker=None, *, cluster=None):
        if worker and embedding_worker:
            raise ValueError("select one semantic backend")
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True)
        self.binary = Path(binary).resolve()
        self.worker = worker
        self.embedding_worker = embedding_worker
        self.backend = "embedding-command" if embedding_worker else "semantic-stream-command" if worker else "lexical"
        self.readiness = None
        self.cluster = cluster
        self.ids = {}  # fixture id -> record id
        self.names = {}  # record id -> fixture id
        self.import_results = []
        self.server = None
        self._launch_attempted = False
        if cluster is not None:
            pg, pg_bin = cluster.routing()
            self.pg_bin = Path(pg_bin)
        else:
            pg = disposable_pg()
            self.pg_bin = Path(os.environ["CAIRN_TASK_EVAL_PG_BIN"])
        database = "task_eval_" + re.sub(r"[^a-z0-9]", "_", label.lower())
        run([self.pg_bin / "createdb", "-h", pg, database])
        self.pg_socket, self.database = pg, database
        self.dsn = f"host={pg} dbname={database} sslmode=disable"
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        # Unix socket paths are limited to 108 bytes; keep the socket directory short.
        self.sockdir = Path(tempfile.mkdtemp(prefix="cte-sock-", dir="/tmp"))
        self._constructor_socket_cleanup = None
        self.token_file = self.root / "agent.token"
        # The caller registers stop() only after construction returns, so a
        # partial construction releases what it acquired here. The database
        # stays inside the wrapper-owned disposable cluster.
        try:
            (self.root / "sock").symlink_to(self.sockdir)
            self.socket = self.sockdir / "api.sock"
            base = {k: v for k, v in os.environ.items() if not k.startswith("CAIRN_")}
            self.env = dict(base, CAIRN_HOME=str(self.home), CAIRN_DATABASE_URL=self.dsn)
            run([self.binary, "migrate"], env=self.env, timeout=300)
            disposable_host.stage_identities(self.home, ("agent",), TRIAL_REPO,
                                             principals={"agent": "agent:task-eval-" + label},
                                             token_dir=self.root, token_name=lambda role: "agent.token")
        except BaseException:
            self.token_file.unlink(missing_ok=True)
            (self.home / "identities.json").unlink(missing_ok=True)
            self._constructor_socket_cleanup = 'unknown'
            try:
                shutil.rmtree(self.sockdir)
            except OSError:
                pass  # Preserve the construction error; the retained owner keeps unknown cleanup.
            else:
                self._constructor_socket_cleanup = 'removed'
            raise
        self.principal = "agent:task-eval-" + label

    def _pg_socket(self):
        if getattr(self, "cluster", None) is not None:
            return self.cluster.routing()[0]
        return disposable_pg()

    def receipt_explanations(self, observed):
        try:
            socket = self._pg_socket()
        except (SystemExit, ValueError) as error:
            raise ValueError("owned database routing unavailable") from error
        if socket != self.pg_socket:
            raise ValueError("owned database routing changed")
        return export_receipts(self.pg_bin / "psql", self.pg_socket, self.database, self.env, run,
                               self.principal, TRIAL_REPO, observed)

    def fingerprint(self):
        # This instance created its database in the wrapper-owned cluster.
        if self._pg_socket() != self.pg_socket:
            raise ValueError("owned database routing changed")
        return corpus_stamp(self.pg_bin / "psql", self.pg_socket, self.database, self.env, run)

    def remember(self, note):
        args = ["remember", "--repo", note.get("repo", TRIAL_REPO), "--kind", note["kind"], "--stdin",
                "--request-id", str(uuid.uuid5(uuid.NAMESPACE_URL, "task-eval:" + note["id"]))]
        if note.get("shareable", True):
            args.insert(1, "--shareable")
        # Explicit associations travel through the CLI's own flags, so core.NormalizeEntities
        # validates them; `=` keeps a name that begins with "-" a value, not a flag.
        entities = corpus_entities(note)
        args += [f"--entity-{kind}={name}" for kind, name in entities]
        result = run([self.binary, *args], env=self.env, input=note["body"].encode(), timeout=60)
        record = json.loads(result.stdout)["data"]
        self.import_results.append(dict(input_id=note["id"], result=record))
        self.ids[note["id"]] = record["record_id"]
        self.names[record["record_id"]] = note["id"]
        if entities:
            self.verify_entities(note["id"], record["record_id"], entities)
        return record

    def verify_entities(self, input_id, record_id, entities):
        """Compare what the store kept with what the input declared; a drop or change refuses."""
        stored = cairn_json(self.binary, ["get", record_id], self.env)
        kept = {(e["kind"], e["name"]) for e in stored.get("entities") or []}
        if kept != set(entities):
            raise RuntimeError(f"{input_id}: imported entity associations differ from the input "
                               f"({len(kept)} stored, {len(entities)} declared)")

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
        extra = []
        if self.worker:
            extra += ["--semantic-stream-command", str(Path(self.worker).resolve())]
        if self.embedding_worker:
            extra += ["--embedding-command", str(Path(self.embedding_worker).resolve())]
        self.started = time.monotonic()
        self._launch_attempted = True
        api = disposable_host.launch_server(self.binary, self.env, socket_path=self.socket, args=extra,
                                            log=self.root / "api.log", label="candidate API")
        self.log, self.server = api.log, api.process
        api.wait_ready(timeout=30, probe="exists")  # pinned consumers use synthetic binaries

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
                    # The backend's validated identity from this same discovery response (observed).
                    identity = dict(model_sha256=discovery.get("model_sha256"), algorithm=discovery.get("algorithm"))
                    self.readiness = dict(backend=self.backend, state="full_eligible_coverage", coverage=last,
                                          cold_seconds=round(time.monotonic() - self.started, 3), polls=polls,
                                          identity=identity)
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
        disposable_host.stop_process(self.server, timeout=15, label="cairn API")
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

def prepare_workspace(case, root, *, source_root=TRIAL):
    """Copy the fixture, run its setup and tag the evaluation base. Returns the working directory."""
    source = workspace_dir(case, root=source_root)
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
            try:
                ctx["added"] = ctx['baseline'].added_lines(cwd) if 'baseline' in ctx else added_lines(cwd)
            except BaselineError as error:
                raise Undetermined(str(error)) from None
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
        try:
            names = (ctx['baseline'].commit_files(cwd) if 'baseline' in ctx else
                     run(["git", "diff", "--name-only", "eval-base", "HEAD"], cwd=cwd, check=False).stdout.decode().split())
        except BaselineError as error:
            raise Undetermined(str(error)) from None
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
    answer, turns, usage, seen, admission_failure = "", 0, {}, set(), None
    terminal_failure = False
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
            if not terminal_failure:
                # A top-level `error` may describe a transient failed turn
                # followed by a successful retry in the same exec stream.
                errors.clear()
                admission_failure = None
            for key, value in (event.get("usage") or {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
        elif kind in ("turn.failed", "error"):
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            errors.append(error or event.get("message") or kind)
            # Only structured provider metadata can classify auth vs quota.
            # Current exec JSONL often supplies only a message; stop admission
            # as unknown rather than interpreting provider or model prose.
            status = next((error.get(key) for key in ("status", "status_code", "http_status")
                           if type(error.get(key)) is int), None)
            info = error.get("codexErrorInfo") or event.get("codexErrorInfo")
            if status is None:
                status = next((event.get(key) for key in ("status", "status_code", "http_status")
                               if type(event.get(key)) is int), None)
            code = info if isinstance(info, str) else error.get("code") or event.get("code")
            if status == 401 or code in ("unauthorized", "authentication_failed"):
                admission_failure = dict(provider="codex", reason="authentication_failed", api_error_status=401)
            elif status == 429 or code in ("rate_limit_exceeded", "usage_limit_exceeded", "capacity_exhausted"):
                admission_failure = dict(provider="codex", reason="capacity_exhausted", api_error_status=429)
            else:
                admission_failure = dict(provider="codex", reason="provider_error_unknown", api_error_status=None)
            if kind == "turn.failed":
                terminal_failure = True
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
                admission_failure=admission_failure,
                subtype="turn.failed" if errors else "turn.completed" if turns else "unterminated",
                input_tokens=usage.get("input_tokens"), cache_read_tokens=usage.get("cached_input_tokens"),
                cache_creation_tokens=usage.get("cache_write_input_tokens"), output_tokens=usage.get("output_tokens"))


def codex_custody_failure(process_custody, writer_record):
    """Name missing process, writer-release, or final-artifact custody separately."""
    if (not isinstance(process_custody, dict)
            or process_custody.get("cleanup", {}).get("status") != "observed_clear"):
        return "process_custody_unknown"
    if not isinstance(writer_record, dict) or writer_record.get("state") != "released":
        return "writer_release_unknown"
    pin = writer_record.get("final_artifact_custody")
    if not (isinstance(pin, dict) and pin.get("schema") == "cairn.legacy-pregrade-snapshot-pin/1"
            and isinstance(pin.get("record_path"), str) and pin["record_path"].startswith("final-artifacts/")
            and type(pin.get("bytes")) is int and pin["bytes"] > 0
            and isinstance(pin.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", pin["sha256"])):
        return "final_artifact_custody_unknown"
    return None


def campaign_admission_open(stop, observer_stop):
    """Whether the scheduler may admit another attempt after a terminal observation."""
    return stop is None and observer_stop is None


def admission_stop(record, run_id):
    failure = (record.get("trace") or {}).get("admission_failure")
    return dict(run_id=run_id, **failure) if isinstance(failure, dict) else None


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


def runtime_sandbox(*, common_runtime=None, runtime_identity=None, writable=None,
                    include_home_go=True):
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
    if include_home_go and go.exists() and common_runtime is None:
        command += ["--ro-bind", str(go.resolve()), str(go)]
    if common_runtime is not None:
        from trial_task_runtime import runtime_mounts
        command += runtime_mounts(common_runtime, runtime_identity, writable)
    return command


def writer_launch_preflight(writer, output):
    """Prove the selected writer leaf can launch the inert sandbox before store setup.

    Weekly actors must join an owned cgroup before exec. This no-provider probe
    uses the same WriterLeaf, bwrap command builder, process capture and pass-fd
    path as an attempt, so a controller launched from the wrong source cgroup
    fails before any trial database is initialized or indexed.
    """
    if writer is None:
        return None
    try:
        leaf = writer_release.WriterLeaf(writer['delegated'], writer['controls'],
                                         reviewed=writer.get('reviewed'))
    except writer_release.WriterUnavailable:
        raise RuntimeError('original_writer_preflight_unavailable') from None
    timing_path = Path(output) / '.writer-preflight-timing.json'
    code = None
    capture_failed = False
    try:
        command = leaf.wrap(runtime_sandbox() + ['--', '/usr/bin/true'])
        _, _, code, _ = capture(command, 10, timing_path, pass_fds=leaf.pass_fds,
                                observer=leaf.observe_init)
    except Exception:
        capture_failed = True
    finally:
        observed = leaf.finish(time.monotonic() + 5)
        try:
            timing_path.unlink()
        except FileNotFoundError:
            pass
    bootstrap = observed.get('bootstrap') if isinstance(observed, dict) else None
    member = (isinstance(bootstrap, dict) and bootstrap.get('state') == 'member'
              and bootstrap.get('observed_cgroup') == '0::' + leaf.group.relative)
    if (capture_failed or code != 0 or not member or observed.get('release') != 'released'
            or observed.get('close_errors')):
        raise RuntimeError('original_writer_preflight_failed')
    return dict(schema='cairn.weekly-writer-launch-preflight/1', state='passed',
                sandbox_action='true', bootstrap='member', release='released', exit_code=0)


def cmd_writer_preflight(args):
    """Run the same no-provider writer launch check for a paused manual unit gate."""
    try:
        controls = paired_json_pin(args.writer_controls, args.writer_controls_sha256)
        if (not isinstance(controls, dict)
                or set(controls) != {'schema', 'controls', 'reviewed_identity'}
                or controls['schema'] != 'cairn.writer-controls/1'):
            raise ValueError('writer_controls_invalid')
        writer = dict(delegated=args.writer_parent,
                      controls=writer_release.check_controls(controls['controls']),
                      reviewed=writer_release.check_reviewed(controls['reviewed_identity']))
        if writer['reviewed'] is None:
            raise ValueError('writer_review_missing')
        with tempfile.TemporaryDirectory(prefix='cairn-writer-preflight-') as output:
            result = writer_launch_preflight(writer, output)
    except (OSError, TypeError, ValueError, KeyError, RuntimeError, writer_release.WriterUnavailable):
        print(json.dumps(dict(schema='cairn.weekly-writer-launch-preflight/1',
                              state='refused', code='writer_preflight_failed'), sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def sandbox_command(root, cwd_name, extra_binds, env, provider_args, harness="claude", *,
                    native_executable=None, common_runtime=None, runtime_identity=None,
                    credentials_readonly=None, include_home_go=True):
    """Add the chosen provider and owned trial paths to the runtime filesystem."""
    home = Path.home()
    command = runtime_sandbox(common_runtime=common_runtime, runtime_identity=runtime_identity,
                              writable=root, include_home_go=include_home_go)
    if harness == "claude":
        claude = home / ".local/bin/claude"
        credentials = home / ".claude/.credentials.json"
        # An explicitly selected account replaces the ambient file; a bad selection refuses, never falls back.
        selected = credentials_source()
        command += ["--ro-bind", str(Path(native_executable).resolve(strict=True) if native_executable else claude.resolve(strict=True)), str(claude),
                    "--dir", str(home / ".claude"),
                    # Prospective native runs keep their read-only credentials; legacy runs keep the
                    # original read-write bind even with an original-execution binary.
                    "--ro-bind" if (bool(native_executable) if credentials_readonly is None else credentials_readonly)
                    else "--bind", str(selected or credentials), str(credentials)]
    elif harness != "codex":
        raise ValueError("unknown harness " + harness)
    command += ["--bind", str(root), str(root), "--chdir", str(Path(root) / cwd_name)]  # absolute gitdirs
    for source, target, mode in extra_binds:
        command += ["--ro-bind" if mode == "ro" else "--bind", str(source), str(target)]
    for key, value in env.items():
        command += ["--setenv", key, value]
    return command + provider_args


def attempt_toolkit():
    """The harness collaborators an attempt crosses, read at call time so tests can replace them."""
    return trial_attempt.Toolkit(
        prepare_workspace=prepare_workspace, load_corpus=load_corpus, grade=grade, parse_stream=parse_stream,
        parse_tool_outputs=parse_tool_outputs, sandbox=sandbox_command, capture=capture, read_custody=read_custody,
        custody_failure=codex_custody_failure, bind_ordinary_claude=bind_ordinary_claude,
        measure=measure, hook_observations=hook_observations)


def run_agent(case, arm, seed, order, args, stores, out, *, provider=None):
    """One attempt. The CLI namespace is only a compatibility adapter onto the execution profile.

    ``cmd_agent`` declares the profile once (``args._execution_profile``); direct callers
    still pass the legacy namespace, read here exactly once. ``provider`` injects an adapter
    such as ``trial_attempt.InertProvider``; it is never selected by environment.
    """
    profile = getattr(args, "_execution_profile", None) or trial_attempt.ExecutionProfile.from_args(args)
    if provider is not None:
        profile = profile.with_provider(provider)
    return trial_attempt.execute(case, arm, seed, order, profile, stores, out, attempt_toolkit())


def cmd_agent(args, provider=None):
    """Run a campaign. ``provider`` explicitly injects an adapter (tests, inert probes); never the environment."""
    account_assignment = original_native = original_writer = original_arms = None
    prospective = load_input(args.prospective_input) if getattr(args, "prospective_input", None) else None
    frozen = prospective["frozen"] if prospective else verify_frozen()
    all_cases = prospective["cases"] if prospective else load_cases()
    selected_case_materials = case_materials_pin(
        getattr(args, 'case_materials', None), getattr(args, 'case_materials_sha256', None),
        all_cases, root=TRIAL) if not prospective else None
    if prospective and (getattr(args, 'case_materials', None) or getattr(args, 'case_materials_sha256', None)):
        raise ValueError('case_materials_legacy_only')
    cases = [c for c in all_cases if not args.cases or c["id"] in args.cases]
    if not cases or (args.cases and set(args.cases) - {c["id"] for c in all_cases}):
        raise SystemExit("case selection must name existing cases")
    if getattr(args, 'codex_account_assignment', None) is not None:
        reference = dict(path=args.codex_account_assignment, sha256=args.codex_account_assignment_sha256)
        assignment, _, _ = weekly_accounts.validate(reference, dict(
            cases=[case['id'] for case in all_cases],
            arms=['CURRENT', 'CAND'],
            seeds=list(range(args.first_seed, args.first_seed + args.seeds))))
        for account in assignment['accounts']:
            codex_account_selection.verify_auth_file(account['auth_file'], account['auth_sha256'],
                                                      expected_account_id=account['account_id'])
        account_assignment = assignment
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
                     receipt_exporter_sha256=hashlib.sha256((ROOT / "scripts/trial_receipt_explanation.py").read_bytes()).hexdigest(),
                     harness_version=provider.harness_version(None, run) if provider is not None else
                     None if getattr(args, "original_native_path", None) and not prospective else
                     run([str(Path(args.codex_binary).resolve(strict=True)) if args.harness == "codex" and getattr(args,"codex_binary",None)
                          else str(Path(args.codex_install) / "codex/bin/codex.js") if args.harness == "codex"
                          else str(checked_file(prospective["document"]["native"]["binary"])) if prospective else "claude", "--version"]).stdout.decode().strip())
    if getattr(args, "execution_profile", None):
        execution["execution_profile"] = dict(path=str(Path(args.execution_profile[0]).resolve()),
                                              sha256=args.execution_profile[1])
    if getattr(args, "original_limits", False) and not prospective:
        execution["original_limits"] = dict(legacy_limits.LIMITS)
    if getattr(args, "codex_weekly_limits", False) and not prospective:
        execution["codex_weekly_limits"] = dict(schema=codex_weekly_limits.SCHEMA,
                                                limits=codex_weekly_limits.LIMITS)
        codex_binary=Path(args.codex_binary).resolve(strict=True)
        if native_copy.file_sha256(str(codex_binary)) != args.codex_binary_sha256:
            raise ValueError("codex_binary_mismatch")
        execution["original_codex"] = dict(declared=dict(path=str(codex_binary),sha256=args.codex_binary_sha256),
                                           observed=dict(sha256=args.codex_binary_sha256,
                                                         version=execution["harness_version"],
                                                         target=str(Path.home()/".codex/codex")))
    out = Path(args.output)
    out.mkdir(mode=0o700)
    if getattr(args, "original_native_path", None) and not prospective:
        # Before any store starts: retain, hash and version the declared binary; never run the ambient one.
        original_native = native_copy.retain(args.original_native_path, args.original_native_sha256, out)
        execution.update(original_native=original_native, reasoning_effort=args.reasoning_effort,
                         harness_version=original_native["observed"]["version"])
    declared_identity = None
    if getattr(args, "original_writer_cgroup", None) and not prospective:
        # Root-selected delegated parent and explicit resource policy for the owned writer leaf.
        controls = paired_json_pin(args.original_writer_controls, args.original_writer_controls_sha256)
        if not (isinstance(controls, dict) and set(controls) in ({"schema", "controls"},
                                                                 {"schema", "controls", "reviewed_identity"})
                and controls["schema"] == "cairn.writer-controls/1"):
            raise ValueError("original_writer_controls_invalid")
        original_writer = dict(delegated=args.original_writer_cgroup,
                                     controls=writer_release.check_controls(controls["controls"]),
                                     reviewed=writer_release.check_reviewed(controls.get("reviewed_identity")))
        if original_writer["reviewed"] is None:
            raise ValueError("original_writer_reviewed_identity_required")
        execution["original_writer"] = dict(delegated=args.original_writer_cgroup,
                                            controls=dict(path=args.original_writer_controls,
                                                          sha256=args.original_writer_controls_sha256),
                                            policy=original_writer["controls"],
                                            reviewed_identity=original_writer["reviewed"])
        execution['original_writer_preflight'] = writer_launch_preflight(original_writer, out)
    if selected_case_materials is not None:
        execution['case_materials'] = selected_case_materials
    if getattr(args, "original_arms", None) and not prospective:
        # Before any store starts: the literal per-arm controls and raw recall blobs.
        execution["original_arms"], original_arms = original_arm_controls.load_controls(
            args.original_arms, args.original_arms_sha256, args.memory)
    if getattr(args, "original_embedding_identity", None) and not prospective:
        execution["original_embedding_identity"], declared_identity = original_arm_controls.load_identity(
            args.original_embedding_identity, args.original_embedding_identity_sha256)
    if prospective:
        if out.resolve().is_relative_to(prospective["root"]):
            raise ValueError("output must be outside prospective input")
        shutil.copytree(prospective["root"], out / "input")
        prospective = load_input(out / "input")
        if prospective["frozen"] != frozen:
            raise ValueError("prospective input changed during snapshot")
        cases = [c for c in prospective["cases"] if not args.cases or c["id"] in args.cases]
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
    # The one reading of the execution declaration; every attempt runs under it.
    profile = trial_attempt.ExecutionProfile.from_args(
        args, prospective=prospective, original_native=original_native, original_arms=original_arms,
        original_writer=original_writer, account_assignment=account_assignment, provider_adapter=provider)
    args._execution_profile = profile
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
            arm_config = pinned_arms[label] if prospective else (original_arms[label] if original_arms else {})
            store = TrialStore(out / "stores" / label, binary, label, worker=args.semantic_worker,
                               embedding_worker=arm_config.get("embedding_worker") if prospective else args.embedding_worker)
            cleanup.callback(store.token_file.unlink, missing_ok=True)
            cleanup.callback((store.home / "identities.json").unlink, missing_ok=True)
            cleanup.callback(store.stop)
            # Legacy keeps its original parallel import; both retain the fixture-to-record map.
            store.seed_corpus(corpus, workers=1 if prospective else 8)
            (store.root / "import-provenance.json").write_text(json.dumps(import_provenance(corpus, store.ids), indent=2))
            store.grow(args.distractors, 0)
            store.start()
            store.wait_for_full_coverage(timeout=args.cold_readiness_timeout)
            if declared_identity is not None and not identity_declaration.matches(
                    (store.readiness or {}).get("identity"), declared_identity):
                # Compared before any attempt; dimensions are declared only, never observed.
                raise ValueError("original_execution_identity_mismatch")
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
        account_assignment_sha256 = None
        run_accounts = None
        if account_assignment is not None:
            account_assignment_sha256 = args.codex_account_assignment_sha256
            assignment_by_run = {row['run_id']: row['account_id']
                                 for row in account_assignment['assignments']}
            run_accounts = [dict(run_id=f'{case["id"]}.{arm}.s{seed}',
                                 account_id=assignment_by_run[f'{case["id"]}.{arm}.s{seed}'])
                            for seed in range(args.first_seed, args.first_seed + args.seeds)
                            for case in cases for arm in arms]
            execution['account_assignment_sha256'] = account_assignment_sha256
        execution["recall"] = recall
        plan_document = dict(frozen=frozen, arms=arms, model=args.model, wording=args.wording, **execution,
                             distractors=args.distractors, paired_plans=paired_plans,
                             run_accounts=run_accounts,
                             memory_backends={k: dict(backend=v["store"].backend,
                                                      readiness=v["store"].readiness)
                                              for k, v in stores.items()},
                             limits=dict(max_turns=args.max_turns, timeout=args.timeout,
                                         parallel=args.parallel),
                             runs=[(c["id"], a, s, p) for c, a, s, p in plan])
        # Keep this plan field explicit when no assignment is selected, while
        # allowing the report's execution metadata to retain its existing shape.
        plan_document['account_assignment_sha256'] = account_assignment_sha256
        (out / "plan.json").write_text(json.dumps(plan_document, indent=2))
        before = {label: value["store"].fingerprint() for label, value in stores.items()} if prospective else None
        if prospective:
            (out / "corpus-before.json").write_text(json.dumps(before, indent=2))
        records, next_run, stop, observer_stop = [], 0, None, None
        with concurrent.futures.ThreadPoolExecutor(args.parallel) as pool:
            futures = {}
            while futures or (campaign_admission_open(stop, observer_stop) and next_run < len(plan)):
                # Keep no queued backlog: after a confirmed failure only the
                # already admitted window may finish. Never cancel its evidence.
                while campaign_admission_open(stop, observer_stop) and len(futures) < args.parallel and next_run < len(plan):
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
                        account = (weekly_accounts.account_for(account_assignment, c["id"], arm, seed)
                                   if profile.codex_weekly and account_assignment is not None else None)
                        record = trial_attempt.failed_attempt_record(
                            c, arm, seed, profile, error, out / "runs" / f'{c["id"]}.{arm}.s{seed}',
                            attempt_toolkit(), account=account)
                    if prospective and arm in stores and "receipt_explanation_export" not in record:
                        record["receipt_explanation_export"] = receipt_export_metadata(out / "runs" / record["run_id"])
                    records.append(record)
                    if stop is None:
                        stop = admission_stop(record, record["run_id"])
                    if stop is not None and stop.get("run_id") == record["run_id"]:
                        print(json.dumps(dict(admission_stop=stop)), flush=True)
                    if (observer_stop is None and record.get('observer_integrity_unknown')
                            and (prospective or (args.harness == "codex" and getattr(args, "codex_weekly_limits", False)))):
                        observer_stop = dict(run_id=record['run_id'], reason=record.get(
                            'observer_failure_reason', 'observer_custody_unknown'))
                        print(json.dumps(dict(observer_admission_stop=observer_stop)), flush=True)
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
        admission = dict(state="stopped_provider_failure" if stop else ("stopped_observer_failure" if observer_stop else "complete"), stop=stop,
                         planned=len(plan), admitted=next_run,
                         not_started=[dict(run_id=f"{c['id']}.{a}.s{s}", case=c["id"], arm=a, seed=s, order=p)
                                      for c, a, s, p in plan[next_run:]])
        if prospective:
            admission['observer_stop'] = observer_stop
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
        elif getattr(args, "original_limits", False):
            # Memory-arm rows must be measured within the original limits; a missing assessment fails.
            report["measurement_failures"] = [r["run_id"] for r in records if r.get("arm") in stores
                                               and (r.get("original_limits") or {}).get("status") != "within_original_limits"]
        elif getattr(args, "codex_weekly_limits", False):
            report["measurement_failures"] = [r["run_id"] for r in records if r.get("arm") in stores
                                               and (r.get("codex_weekly_limits") or {}).get("status") != "within_codex_weekly_limits"]
        report["summary"] = summarise_agent(report["records"], arms)
        (out / "agent.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["summary"], indent=2))
        return 2 if stop or (prospective and (execution["corpus_integrity"]["unchanged"] is not True or report["measurement_failures"])) \
            or report.get("measurement_failures") else 0


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
            try:
                baseline = AttemptBaseline.load(base, case, record['arm'], record['seed'], record.get('baseline_custody'))
            except BaselineError as error:
                record.update(outcome='not_regradable', correct=None, mistake=None, regrade_reason=str(error))
                records.append(record)
                continue
            ctx = dict(cwd=base / "work" / case["cwd"], commands=trace["commands"], answer=trace["answer"],
                       tool_outputs=parse_tool_outputs(stream, record.get("harness", data.get("harness", "claude"))),
                       snapshot=baseline.snapshot, baseline=baseline)
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
    writer_probe = sub.add_parser("writer-preflight", help="inert bwrap writer-leaf launch check; no provider or store")
    writer_probe.add_argument("--writer-parent", required=True)
    writer_probe.add_argument("--writer-controls", required=True)
    writer_probe.add_argument("--writer-controls-sha256", required=True)
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
    a = agent_parser = sub.add_parser("agent")
    a.add_argument("--output", required=True)
    a.add_argument("--execution-profile", nargs=2, metavar=("PATH", "SHA256"),
                   help="pinned cairn.task-eval.execution-profile/1 declaring the agent flags; "
                        "an explicit conflicting flag refuses")
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
    a.add_argument("--codex-binary", help="exact selected Codex executable for a weekly run")
    a.add_argument("--codex-binary-sha256", help="expected SHA-256 of --codex-binary")
    a.add_argument("--codex-auth-file", default=str(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "auth.json"),
                   help="existing account auth only; read-only mount into isolated profile")
    a.add_argument("--codex-auth-sha256", help="expected digest of the selected subscription auth file")
    a.add_argument("--codex-account-assignment", help="pinned weekly case/seed to ChatGPT account assignment")
    a.add_argument("--codex-account-assignment-sha256", help="expected digest of the account assignment")
    a.add_argument("--case-materials", help="private pinned legacy v12 case-material manifest; never bound into the actor sandbox")
    a.add_argument("--case-materials-sha256", help="expected SHA-256 of --case-materials")
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
    a.add_argument("--original-native-path", help="legacy original execution: absolute declared native binary")
    a.add_argument("--original-native-sha256", help="its declared SHA-256; the run uses a verified retained copy")
    a.add_argument("--original-arms", help="cairn.original-arm-controls/1: literal per-arm recall controls and raw recall blobs")
    a.add_argument("--original-arms-sha256")
    a.add_argument("--original-embedding-identity", help="typed cairn.embedding-model-identity/1 declaration")
    a.add_argument("--original-embedding-identity-sha256")
    a.add_argument("--original-writer-cgroup", help="root-selected delegated cgroup parent for the owned writer leaf")
    a.add_argument("--original-writer-controls", help="cairn.writer-controls/1: the selected leaf resource policy")
    a.add_argument("--original-writer-controls-sha256")
    a.add_argument("--original-limits", action="store_true",
                   help="legacy Claude fixtures under trial_paired_request.ORIGINAL_LIMITS (5 s hooks, 32000 tool room, "
                        "task wire charged, captured stream timing); exceeding rows are measurement failures")
    a.add_argument("--codex-weekly-limits", action="store_true",
                   help="Codex JSONL-specific check under unchanged CAIRN-107 limits; missing evidence fails closed")
    p = sub.add_parser("report")
    p.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "writer-preflight":
        return cmd_writer_preflight(args)
    if args.command == "freeze-input":
        bundle = load_input(args.input, frozen=False)
        problems = validate(bundle["cases"], bundle["notes"], prospective=True)
        if problems:
            parser.error("; ".join(problems))
        print(json.dumps(freeze_input(args.input)["frozen"], indent=2))
        return 0
    if args.command == "agent" and args.execution_profile:
        import pinned_document
        path, digest = args.execution_profile
        try:
            fields = trial_attempt.profile_fields(pinned_document.read_json_pin(dict(path=path, sha256=digest)))
        except (pinned_document.Refusal, ValueError) as error:
            parser.error("execution profile refused: " + str(error))
        for name, value in fields.items():
            current = getattr(args, name)
            if current != agent_parser.get_default(name) and current != value:
                parser.error("--" + name.replace("_", "-") + " conflicts with the execution profile")
            setattr(args, name, value)
    if args.command == "agent":
        originals = (args.original_native_path, args.original_native_sha256, args.original_arms, args.original_arms_sha256,
                     args.original_embedding_identity, args.original_embedding_identity_sha256,
                     args.original_writer_controls, args.original_writer_controls_sha256)
        if bool(args.original_writer_cgroup) != bool(args.original_writer_controls):
            parser.error("--original-writer-cgroup and --original-writer-controls go together")
        if args.original_writer_cgroup and not (args.original_limits or args.codex_weekly_limits):
            parser.error("--original-writer-cgroup requires original or Codex weekly limits")
        if args.prospective_input and any(originals):
            parser.error("original execution is a legacy-fixtures option")
        for path, digest in zip(originals[::2], originals[1::2]):
            if (path is None) != (digest is None):
                parser.error("original execution paths and SHA-256 values go together")
        if any(originals) and not (args.original_limits or args.codex_weekly_limits):
            parser.error("original execution requires --original-limits")
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
            if (args.original_native_path is None) != (args.original_native_sha256 is None):
                parser.error("--original-native-path and --original-native-sha256 go together")
            if args.original_native_path is not None and (
                    not args.original_limits or args.reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}):
                parser.error("original native execution requires --original-limits and an explicit Claude --reasoning-effort")
            args.arms = args.arms if args.arms is not None else ["none", "direct"]
            args.reasoning_effort = args.reasoning_effort or "high"
        if args.original_limits and (args.prospective_input or args.harness != "claude" or args.wording != "task"):
            parser.error("--original-limits applies only to legacy Claude fixtures with task wording")
        if args.codex_weekly_limits and (args.prospective_input or args.harness != "codex" or args.wording != "task"):
            parser.error("--codex-weekly-limits applies only to legacy Codex fixtures with task wording")
        if args.codex_weekly_limits and args.original_native_path is not None:
            parser.error("Codex weekly execution uses --codex-binary, not --original-native-path")
        if (args.codex_binary is None) != (args.codex_binary_sha256 is None):
            parser.error("--codex-binary and --codex-binary-sha256 go together")
        if args.codex_weekly_limits and args.codex_binary is None:
            parser.error("--codex-weekly-limits requires a pinned --codex-binary")
        if args.codex_weekly_limits and args.codex_auth_sha256 is None:
            parser.error("--codex-weekly-limits requires a pinned --codex-auth-sha256")
        if (args.codex_account_assignment is None) != (args.codex_account_assignment_sha256 is None):
            parser.error("--codex-account-assignment and its SHA-256 go together")
        if args.codex_account_assignment is not None and not args.codex_weekly_limits:
            parser.error("--codex-account-assignment applies only to Codex weekly execution")
        if args.codex_auth_sha256 is not None and not args.codex_weekly_limits:
            parser.error("--codex-auth-sha256 applies only to Codex weekly execution")
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
            if (args.codex_binary is None and not (Path(args.codex_install) / "codex/bin/codex.js").is_file()) or not Path(args.codex_auth_file).is_file():
                parser.error("Codex installation and existing auth file must exist")
            if args.codex_weekly_limits:
                try:
                    codex_account_selection.verify_auth_file(args.codex_auth_file,args.codex_auth_sha256)
                except (OSError,ValueError) as error:
                    parser.error(str(error))
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
