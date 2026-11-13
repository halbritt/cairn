#!/usr/bin/env python3
"""Rehearse documented recovery against two newly owned PostgreSQL clusters.

Invoke through mise host:exec. Importing this module starts no process or store.
This is synthetic software evidence, not a production backup or rollout gate.
"""

import argparse
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from disposable_host import DisposableHost, HostRefusal, OwnedCluster, ProvidedDatabase
from disposable_host import decode_json, pairs_unique, private_dir, require, write_private


SOURCE = Path(__file__).resolve().parents[1]
PG_ROUTING = {"PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
              "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGAPPNAME",
              "PGCLIENTENCODING", "PGTARGETSESSIONATTRS", "PGCONNECT_TIMEOUT",
              "PGSSLMODE", "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT", "PGSSLPASSWORD"}
LIMITS = [
    "Synthetic pg_dump/pg_restore is not a production pgBackRest chain, WAL or content qualification.",
    "Checkpoint headers bind emitted audit metadata, not all bodies, truth or unknown newer commits.",
    "Admission does not globally fence old processes or direct-pool helpers; external isolation is required.",
    "Retained /2 lifecycle and S-04R semantic offline reverse closures are not exercised here.",
    "Deletion covers stored payloads and a retained derived-from edge, not post-backup external file custody.",
    "No R06/R07 acceptance, production admission or observation clock follows this rehearsal.",
]


def uid():
    return str(uuid.uuid4())


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    write_private(path, json.dumps(value, indent=2) + "\n")


def fingerprint(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_mode & 0o111, "executable image required")
    return {"path": str(path), "sha256": digest(path), "bytes": info.st_size,
            "device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid, "mode": stat.S_IMODE(info.st_mode)}


def require_image(pin):
    require(fingerprint(pin["path"]) == pin, "pinned executable changed before use")


def resolve_tool(name):
    path = shutil.which(name)
    require(path is not None, f"required tool {name} is unavailable")
    return Path(path).resolve(strict=True)


@contextmanager
def fixture_routing(pg_bin):
    # Clear application routing only; preserve the agent's HOME/tools/sign-ins.
    inherited = {k: v for k, v in os.environ.items() if k.startswith("CAIRN_") or k in PG_ROUTING}
    for key in inherited:
        del os.environ[key]
    os.environ["CAIRN_PG_BIN"] = str(pg_bin)
    os.environ["PGPASSFILE"] = "/dev/null"
    try:
        yield
    finally:
        for key in list(os.environ):
            if key.startswith("CAIRN_") or key in PG_ROUTING:
                del os.environ[key]
        os.environ.update(inherited)


def checkpoint_expectation(header):
    require(header.get("schema") == "cairn.audit-checkpoint-header/1"
            and header.get("digest_schema") == "cairn.audit-checkpoint/1"
            and "members" not in header, "bounded checkpoint header required")
    uuid.UUID(header["checkpoint_id"])
    require(re.fullmatch(r"[0-9a-f]{64}", header["sha256"] or "") is not None
            and bool(header["export_id"]), "checkpoint expectation is incomplete")
    return {"checkpoint_id": header["checkpoint_id"], "expected_sha256": header["sha256"],
            "expected_export_id": header["export_id"]}


def recovery_parts(directory, expected_manifest):
    """Bind the CLI-produced immutable bundle; the CLI validates its semantics."""
    directory = Path(directory)
    private_dir(directory)
    manifest = directory / "manifest.jsonl"
    info = manifest.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
            "recovery manifest must be a private owned regular file")
    lines = manifest.read_bytes().splitlines(keepends=True)
    require(len(lines) >= 3 and all(line.endswith(b"\n") for line in lines), "incomplete recovery manifest")
    header, footer = decode_json(lines[0]), decode_json(lines[-1])
    require(header.get("schema") == "cairn.recovery-bundle/1", "unsupported recovery bundle")
    checksum = hashlib.sha256(b"".join(lines[:-1])).hexdigest()
    require(checksum == expected_manifest == footer.get("sha256"), "external manifest expectation changed")
    count = header.get("count")
    require(type(count) is int and 1 <= count <= 100000 and len(lines) == count + 2,
            "missing recovery bundle positions")
    uuid.UUID(header["root_grant_id"])
    parts = []
    for index, line in enumerate(lines[1:-1], 1):
        part = decode_json(line)
        name = f"part-{index:09d}.json"
        require(part.get("index") == index and part.get("file") == name, "recovery part path or position differs")
        path = directory / name
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                "recovery part must be a private owned regular file")
        require(digest(path) == part["sha256"], "recovery part bytes changed")
        record = decode_json(path.read_bytes())
        require(record.get("root_grant_id") == header["root_grant_id"]
                and record.get("sha256") == part["record_sha256"], "recovery record expectation differs")
        parts.append({"path": path, "record": record, "record_sha256": part["record_sha256"]})
    return parts


def checked_envelope(stdout, stderr, exit_code, expected):
    require(len(stdout) <= 1_000_000 and len(stderr) <= 100_000, "CLI response exceeds fixture bounds")
    reply = decode_json(stdout)
    require(isinstance(reply, dict) and reply.get("schema") == "cairn.response/1"
            and reply.get("ok") is (reply.get("status") == "OK")
            and (exit_code == 0) is (reply.get("status") == "OK"), "CLI response/exit disagree")
    require(reply.get("status") == expected,
            f"unexpected CLI status {reply.get('status')}; expected {expected}")
    return reply.get("data") if expected == "OK" else reply


class Receipts:
    """Retain exact command evidence; lifecycle itself belongs to disposable_host."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700)
        self.calls = []
        self.forbidden = []
        self.pins = {}

    def command(self, label, argv, *, env=None, body=None, expected_exit=0, timeout=120):
        require(re.fullmatch(r"[a-z0-9-]+", label) is not None, "invalid receipt label")
        executable = str(Path(argv[0]).resolve(strict=True))
        require(executable in self.pins, "executable was not pinned before use")
        require_image(self.pins[executable])
        stem = self.directory / f"{len(self.calls) + 1:04d}-{label}"
        command = {"argv": [str(a) for a in argv], "started_at": time.time(), "timeout": timeout}
        save_json(str(stem) + ".command.json", command)
        stdin = None if body is None else json.dumps(body)
        if stdin is not None:
            write_private(str(stem) + ".stdin.json", stdin + "\n")
        try:
            input_args = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
            result = subprocess.run(command["argv"], **input_args, capture_output=True,
                                    text=True, env=env, cwd=SOURCE, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            require(not any(secret in stdout or secret in stderr for secret in self.forbidden),
                    "fixture token appeared in timed-out output; raw output withheld")
            write_private(str(stem) + ".stdout", stdout)
            write_private(str(stem) + ".stderr", stderr)
            outcome = {**command, "ended_at": time.time(), "exit": None, "timed_out": True}
            save_json(str(stem) + ".result.json", outcome)
            self.calls.append(outcome)
            raise HostRefusal(f"{label} timed out; partial raw receipt retained") from error
        require(not any(secret in result.stdout or secret in result.stderr for secret in self.forbidden),
                "fixture token appeared in command output; raw output withheld")
        write_private(str(stem) + ".stdout", result.stdout)
        write_private(str(stem) + ".stderr", result.stderr)
        outcome = {**command, "ended_at": time.time(), "exit": result.returncode,
                   "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                   "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest()}
        save_json(str(stem) + ".result.json", outcome)
        self.calls.append(outcome)
        if expected_exit is not None:
            require(result.returncode == expected_exit, f"{label} exit {result.returncode}; retained {stem.name}")
        return result

    def pin(self, path):
        pin = fingerprint(path)
        if pin["path"] in self.pins:
            require(pin == self.pins[pin["path"]], "executable changed since its first pin")
            return pin
        self.pins[pin["path"]] = pin
        save_json(self.directory / f"pin-{len(self.pins):04d}.json", pin)
        return pin

    def operator(self, host, operation, body=None, *, args=(), expected="OK"):
        result = self.command(operation, [host.binary, operation, *args], env=host.env,
                              body=body, expected_exit=None)
        return checked_envelope(result.stdout, result.stderr, result.returncode, expected)

    def search(self, host, repo, *, expected="OK"):
        result = self.command("api-search", [host.binary, "agent", *host.connection("consumer"),
            "search", "--repo", repo, "--task", "control", "--run", "one", "--tokens", "16384",
            "R07 retained control"], env=host.env, expected_exit=None)
        return checked_envelope(result.stdout, result.stderr, result.returncode, expected)

    def sql(self, host, statement):
        result = self.command("owned-sql", [host.pg_bin / "psql", "-XAt", "-v", "ON_ERROR_STOP=1",
                              "--dbname", host.dsn, "-c", statement], env=host.env)
        return result.stdout.strip()


def source_pins(trace):
    git = trace.pin(resolve_tool("git"))
    trace.command("git-version", [git["path"], "--version"])
    def git_text(*args):
        return trace.command("source-git", [git["path"], *args]).stdout.strip()
    require(not git_text("status", "--porcelain"), "rehearsal requires a clean frozen checkout")
    return {"commit": git_text("rev-parse", "HEAD"), "tree": git_text("rev-parse", "HEAD^{tree}"),
            "files": {name: digest(SOURCE / name) for name in [
                "go.mod", "go.sum", "scripts/disposable_host.py",
                "scripts/check_r07_recovery_rehearsal.py", "scripts/test_r07_recovery_rehearsal.py"]},
            "python": fingerprint(sys.executable), "python_version": sys.version}


SOURCE_FIELDS = ("GoFiles", "CgoFiles", "CFiles", "CXXFiles", "MFiles", "HFiles",
                 "FFiles", "SFiles", "SwigFiles", "SwigCXXFiles", "SysoFiles", "EmbedFiles")


def parse_dependencies(text):
    """Accept the complete JSON stream for exactly the selected build target."""
    decoder = json.JSONDecoder(object_pairs_hook=pairs_unique)
    packages, position = {}, 0
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position == len(text):
            break
        try:
            package, position = decoder.raw_decode(text, position)
        except ValueError as error:
            raise HostRefusal("selected dependency stream is malformed or truncated") from error
        require(isinstance(package, dict), "selected dependency entry is not an object")
        name = package.get("ImportPath")
        require(isinstance(name, str) and bool(name) and name not in packages,
                "selected dependency identity is missing or duplicated")
        require(package.get("Error") is None and not package.get("DepsErrors")
                and package.get("Incomplete", False) is False and not package.get("InvalidGoFiles"),
                "selected dependency has load errors: " + name)
        require(type(package.get("Standard", False)) is bool, "malformed standard-package flag")
        require(isinstance(package.get("Name"), str) and bool(package["Name"])
                and isinstance(package.get("Dir"), str) and Path(package["Dir"]).is_absolute(),
                "selected dependency lacks its source directory: " + name)
        for field in ("Imports", "Deps", "Match", *SOURCE_FIELDS):
            require(isinstance(package.get(field, []), list)
                    and all(isinstance(item, str) and item for item in package.get(field, [])),
                    "malformed selected dependency field: " + field)
        packages[name] = package
    targets = [p for p in packages.values() if "./cmd/cairn" in p.get("Match", [])]
    require(len(targets) == 1 and targets[0]["Name"] == "main", "selected ./cmd/cairn target is missing or ambiguous")
    target = targets[0]
    require(set(target.get("Deps", [])) | {target["ImportPath"]} == set(packages),
            "selected dependency closure is partial or contains unrelated packages")
    for package in packages.values():
        mapping = package.get("ImportMap", {})
        require(isinstance(mapping, dict) and all(isinstance(k, str) and isinstance(v, str)
                for k, v in mapping.items()), "malformed import mapping")
        for imported in package.get("Imports", []):
            require(imported == "C" or mapping.get(imported, imported) in packages,
                    "selected dependency import is missing: " + imported)
    return target, packages


def source_file(path):
    path = Path(path).resolve(strict=True)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), "selected source input is not a regular file")
    return {"path": str(path), "sha256": digest(path), "bytes": info.st_size}


def dependency_inventory(text, toolchain, source=SOURCE):
    target, packages = parse_dependencies(text)
    require(Path(target["Dir"]).resolve() == source / "cmd" / "cairn", "dependency target directory differs")
    files, modules = {}, {}
    def retain(path):
        pin = source_file(path)
        files[pin["path"]] = pin
    def module_pin(module):
        require(isinstance(module, dict) and isinstance(module.get("Path"), str)
                and bool(module["Path"]) and module.get("Error") is None, "selected module metadata is incomplete or failed")
        result = {key: module[key] for key in ("Path", "Version", "Sum", "Main", "Dir", "GoMod", "GoVersion") if key in module}
        require(isinstance(result.get("Version", ""), str), "module version is malformed")
        if "Replace" in module:
            result["Replace"] = module_pin(module["Replace"])
        effective = result.get("Replace", result)
        require(isinstance(effective.get("Dir"), str) and Path(effective["Dir"]).is_absolute()
                and isinstance(effective.get("GoMod"), str) and Path(effective["GoMod"]).is_absolute(),
                "selected module lacks source or go.mod")
        retain(effective["GoMod"])
        module_file = Path(effective["Dir"]) / "go.mod"
        if module_file.exists():
            retain(module_file)
        return result
    for package in packages.values():
        for field in SOURCE_FIELDS:
            for filename in package.get(field, []):
                retain(Path(package["Dir"]) / filename)
        if not package.get("Standard"):
            module = module_pin(package.get("Module"))
            effective = module
            while "Replace" in effective:
                effective = effective["Replace"]
            require(Path(package["Dir"]).resolve().is_relative_to(Path(effective["Dir"]).resolve()),
                    "package source is outside its selected module or replacement")
            key = module["Path"]
            require(key not in modules or modules[key] == module, "selected module metadata disagrees between packages")
            modules[key] = module
    main = target.get("Module", {})
    require(main.get("Main") is True and target["ImportPath"] == main.get("Path", "") + "/cmd/cairn",
            "selected main-module identity differs")
    for path in [source / "go.mod", source / "go.sum", Path(toolchain["GOROOT"]) / "VERSION"]:
        retain(path)
    for path in (Path(toolchain["GOROOT"]) / "pkg" / "include").iterdir():
        if path.is_file():
            retain(path)
    if toolchain.get("GOENV") and Path(toolchain["GOENV"]).is_file():
        retain(toolchain["GOENV"])
    if toolchain.get("GOWORK"):
        work = Path(toolchain["GOWORK"])
        retain(work)
        if Path(str(work) + ".sum").exists():
            retain(Path(str(work) + ".sum"))
    flags = shlex.split(toolchain.get("GOFLAGS", ""))
    require(not any(flag.split("=", 1)[0] in {"-overlay", "-toolexec"} for flag in flags),
            "selected source overlay or tool wrapper is not inventoried; refuse before compilation")
    return {"schema": "cairn.r07-selected-dependencies/1", "target": target["ImportPath"],
            "main_module": main["Path"], "package_count": len(packages), "modules": modules,
            "files": files, "toolchain": toolchain,
            "package_inputs": {name: {key: p[key] for key in (*SOURCE_FIELDS, "CgoCFLAGS", "CgoCPPFLAGS",
                "CgoCXXFLAGS", "CgoLDFLAGS", "CgoPkgConfig") if key in p} for name, p in packages.items()}}


def verify_dependency_sources(inventory):
    for path, pin in inventory["files"].items():
        require(source_file(path) == pin, "selected dependency source changed before/after compilation")


def verify_buildinfo(text, inventory, commit, *, source=SOURCE):
    lines = text.splitlines()
    require(bool(lines) and lines[0].endswith(": " + inventory["toolchain"]["GOVERSION"]),
            "built image Go version differs or build metadata is missing")
    modules, current, target, settings = {}, None, None, {}
    for line in lines[1:]:
        fields = line.strip().split("\t")
        if fields[0] == "path" and len(fields) == 2:
            require(target is None, "duplicate built target identity")
            target = fields[1]
        elif fields[0] in {"mod", "dep", "=>"}:
            require(3 <= len(fields) <= 4, "malformed built module metadata")
            module = {"Path": fields[1], "Version": fields[2], "Sum": fields[3] if len(fields) == 4 else ""}
            if fields[0] == "=>":
                require(current is not None and "Replace" not in current, "unbound built replacement")
                current["Replace"] = module
            else:
                require(module["Path"] not in modules, "duplicate built module")
                modules[module["Path"]] = current = module
        elif fields[0] == "build" and len(fields) == 2:
            key, separator, value = fields[1].partition("=")
            require(bool(separator) and key not in settings, "malformed built settings")
            settings[key] = value
        else:
            raise HostRefusal("unknown built module metadata line")
    require(target == inventory["target"] and set(modules) == set(inventory["modules"]),
            "built target/module closure differs from selected inventory")
    def correspondence(expected, actual):
        require(actual["Path"] == expected["Path"]
                and actual["Version"] == (expected.get("Version") or "(devel)"), "built module version differs")
        if expected.get("Sum") and "Replace" not in expected:
            require(actual["Sum"] == expected["Sum"], "built module checksum differs")
        require(("Replace" in expected) == ("Replace" in actual), "built replacement differs")
        if "Replace" in expected:
            correspondence(expected["Replace"], actual["Replace"])
    for name, expected in inventory["modules"].items():
        correspondence(expected, modules[name])
    vcs_stamped = "vcs.revision" in settings or "vcs.modified" in settings
    if vcs_stamped:
        require(settings.get("vcs.revision") == commit and settings.get("vcs.modified") == "false",
                "built image does not correspond to the clean frozen source")
    else:
        marker = source / ".git"
        require(marker.is_file() and not marker.is_symlink(),
                "unstamped image requires a linked Git worktree")
    return {"vcs_stamped": vcs_stamped}


def prepare_go_inventory(trace, directory):
    go = trace.pin(resolve_tool("go"))
    trace.command("go-version", [go["path"], "version"])
    fields = ["GOTOOLDIR", "GOROOT", "GOVERSION", "CGO_ENABLED", "CC", "GOOS", "GOARCH",
              "GOPROXY", "GOSUMDB", "GOFLAGS", "GOENV", "GOWORK", "GOMODCACHE"]
    toolchain = decode_json(trace.command("go-toolchain", [go["path"], "env", "-json", *fields]).stdout)
    require(toolchain.get("GOPROXY") == "off", "selected inventory requires the unchanged offline Go policy")
    trace.pin(Path(toolchain["GOROOT"]) / "bin" / "go")
    for name in ["compile", "link", "asm", "cgo"]:
        trace.pin(Path(toolchain["GOTOOLDIR"]) / name)
    if toolchain["CGO_ENABLED"] == "1":
        compiler = shlex.split(toolchain["CC"])
        require(bool(compiler), "CGO has no selected C compiler")
        trace.pin(resolve_tool(compiler[0]))
    before = {name: digest(SOURCE / name) for name in ("go.mod", "go.sum")}
    save_json(directory / "selected-module-inputs-before.json", before)
    selected = trace.command("go-selected-deps", [go["path"], "list", "-deps", "-json", "./cmd/cairn"])
    require(before == {name: digest(SOURCE / name) for name in before}, "Go inventory changed a readonly module input")
    inventory = dependency_inventory(selected.stdout, toolchain)
    save_json(directory / "selected-dependencies.json", inventory)
    return go, inventory


def prepare_tools(trace, directory, source):
    go, inventory = prepare_go_inventory(trace, directory)
    pg_config = trace.pin(resolve_tool("pg_config"))
    pg_bin = Path(trace.command("pg-bindir", [pg_config["path"], "--bindir"]).stdout.strip()).resolve(strict=True)
    for name in ["initdb", "pg_ctl", "postgres", "createdb", "psql", "pg_dump", "pg_restore"]:
        pin = trace.pin(pg_bin / name)
        trace.command("pg-version", [pin["path"], "--version"])
    binary = directory / "cairn"
    verify_dependency_sources(inventory)
    trace.command("build-cairn", [go["path"], "build", "-o", binary, "./cmd/cairn"], timeout=180)
    verify_dependency_sources(inventory)
    trace.pin(binary)
    metadata = trace.command("cairn-build-metadata", [go["path"], "version", "-m", binary])
    save_json(directory / "cairn-build-verification.json",
              verify_buildinfo(metadata.stdout, inventory, source["commit"], source=SOURCE))
    return binary, pg_bin


def create_note(trace, host, scope, text):
    return trace.operator(host, "create", {"request_id": uid(), "draft": {
        "kind": "note", "body": text, "scope": scope, "claim_type": "self", "sensitivity": "shareable"}})


def prepare_source(trace, host, repo, directory, report):
    root = trace.operator(host, "bootstrap", {"request_id": uid(), "reason": "Bootstrap only this owned R07 rehearsal"})
    withdrawal_scope = {"repo": repo, "task_id": "withdrawal", "run_id": "one"}
    record = create_note(trace, host, withdrawal_scope, "Synthetic R07 record that will be forgotten.")
    dependent = trace.operator(host, "create", {"request_id": uid(), "draft": {
        "kind": "note", "body": "Synthetic R07 derived record.", "scope": withdrawal_scope,
        "claim_type": "self", "sensitivity": "shareable", "relations": [{"record_id": record["record_id"],
        "version": record["version"], "relation": "derived_from"}]}})
    grant = trace.operator(host, "grant", {"request_id": uid(), "parent_id": root["grant_id"],
        "principal": "r07-withdrawn-fixture", "repo": repo, "capabilities": ["issue"],
        "reason": "Synthetic grant revoked after the archive"})
    instruction = trace.operator(host, "issue", {"request_id": uid(), "draft": {
        "kind": "instruction", "body": "Synthetic R07 withdrawn instruction", "scope": withdrawal_scope,
        "claim_type": "self"}, "grant_id": root["grant_id"], "policy_key": "r07-fixture",
        "reason": "Synthetic instruction retracted after the archive"})
    control_scope = {"repo": repo, "task_id": "control", "run_id": "one"}
    control = create_note(trace, host, control_scope, "R07 retained control remains available.")
    compiled = trace.operator(host, "compile", {"request_id": uid(), "scope": control_scope,
        "query": "", "purpose": "context", "available_tokens": 64000})
    api = trace.search(host, repo)
    require(bool(api.get("receipt_id")) and any(row.get("record_id") == control["record_id"]
            for row in api.get("index", [])), "real source API did not select the synthetic control")
    checkpoint = trace.operator(host, "checkpoint", {"request_id": uid(), "export_id": "r07-before-withdrawals.dump"}, args=["--header"])
    expectation = checkpoint_expectation(checkpoint)
    save_json(directory / "checkpoint-expectation.json", expectation)
    archive = directory / "r07-before-withdrawals.dump"
    trace.command("pg-dump", [host.pg_bin / "pg_dump", "--format=custom", "--file", archive,
                              "--dbname", host.dsn], env=host.env)
    catalog = {"schema": "cairn.r07-rehearsal-catalog/1", "archive": archive.name,
               "archive_sha256": digest(archive), "checkpoint_expectation": expectation}
    save_json(directory / "backup-catalog.json", catalog)
    trace.operator(host, "revoke-grant", {"request_id": uid(), "grant_id": grant["grant_id"],
        "authority_id": root["grant_id"], "expected_version": grant["version"], "reason": "Synthetic post-archive withdrawal"})
    preview = trace.operator(host, "preview-retract", args=[instruction["record_id"]])
    trace.operator(host, "retract", {"request_id": uid(), "record_id": instruction["record_id"],
        "expected_version": instruction["version"], "grant_id": root["grant_id"],
        "preview_id": preview["preview_id"], "reason": "Synthetic post-archive retraction"})
    preview = trace.operator(host, "preview-delete", args=[record["record_id"]])
    trace.operator(host, "forget", {"request_id": uid(), "record_id": record["record_id"],
        "expected_version": record["version"], "grant_id": root["grant_id"], "preview_id": preview["preview_id"]})
    bundle = directory / "post-archive-recovery"
    exported = trace.operator(host, "recovery-export", args=["--directory", bundle])
    parts = recovery_parts(bundle, exported["manifest_sha256"])
    report["backup"] = {**catalog, "checkpoint_header": checkpoint,
        "recovery_manifest_sha256": exported["manifest_sha256"],
        "recovery_manifest_file_sha256": digest(bundle / "manifest.jsonl"), "recovery_parts": len(parts)}
    save_json(directory / "external-expectations.json", report["backup"])
    return {"record": record, "dependent": dependent, "grant": grant, "instruction": instruction, "compiled": compiled,
            "archive": archive, "catalog": catalog, "bundle": bundle, "parts": parts}


def verify_restrictions(trace, host, state):
    record, grant = state["record"]["record_id"], state["grant"]["grant_id"]
    uuid.UUID(record)
    uuid.UUID(grant)
    result = trace.sql(host, f"SELECT (SELECT revoked::text FROM cairn.authority_grant WHERE grant_id='{grant}') "
        f"|| '|' || (SELECT lifecycle FROM cairn.memory_record WHERE record_id='{record}')")
    require(result == "true|tombstoned", "revocation/deletion projections were not restored")


def verify_dependency(trace, host, state):
    source, dependent = state["record"]["record_id"], state["dependent"]["record_id"]
    uuid.UUID(source)
    uuid.UUID(dependent)
    version = state["dependent"]["version"]
    require(type(version) is int and version > 0, "invalid retained dependent version")
    count = trace.sql(host, "SELECT count(*) FROM cairn.deletion_dependency d "
        "JOIN cairn.deletion_request r USING(deletion_id) "
        f"WHERE d.record_id='{dependent}' AND d.version={version} AND r.record_id='{source}'")
    require(count == "1", "exact derived-version deletion exclusion was not rebuilt")


def reconcile_restore(trace, host, state, repo, report):
    bundle, parts = state["bundle"], state["parts"]
    # This is an isolated destination: no API/ordinary consumer has connected yet.
    trace.command("restored-migrate", [host.binary, "migrate"], env=host.env)
    begin = {"request_id": uid(), "target": state["archive"].name, "reason": "Reconcile an owned synthetic older restore"}
    session = trace.operator(host, "begin-restore", begin)
    require(trace.operator(host, "begin-restore", begin) == session, "begin retry changed session")
    host.start_server()
    report["api_processes"].append({"destination": "restored", "pid": host.process.pid,
        "image_sha256": digest(f"/proc/{host.process.pid}/exe")})
    require(host.process.pid > 1 and report["api_processes"][-1]["image_sha256"] == digest(host.binary),
            "actual restored API image differs")
    trace.search(host, repo, expected="RESTORE_PAUSED")
    before = trace.operator(host, "recovery-inspect", args=["--directory", bundle], expected="INTEGRITY_FAILURE")["data"]["inspection"]
    reasons = {g["reason"] for g in before["gaps"]}
    require({"GRANT_REVIVED", "RECORD_REVIVED", "AUDIT_MISSING"} <= reasons,
            "older archive did not expose the known newer restrictions")
    actions = []
    for part in parts:
        args = ["--request-id", uid(), "--expected-sha256", part["record_sha256"],
                "--reason", "Reapply original synthetic R07 restrictions", part["path"]]
        applied = trace.operator(host, "recovery-reapply", args=args)
        require(trace.operator(host, "recovery-reapply", args=args) == applied, "reapply retry changed identity")
        actions.extend(applied["actions"])
    for subject in [state[key][field] for key, field in [
            ("record", "record_id"), ("instruction", "record_id"), ("grant", "grant_id")]]:
        require(any(a["subject_id"] == subject and a["outcome"] == "reapplied"
                    and a["current_event_id"] != a["event_id"] for a in actions),
                "a known withdrawal was not reapplied as a new event")
    verify_restrictions(trace, host, state)
    pending = trace.sql(host, "SELECT DISTINCT deletion_id FROM cairn.deletion_effect "
        "WHERE status IN ('pending','running','failed') ORDER BY deletion_id")
    for deletion in pending.splitlines():
        uuid.UUID(deletion)
        trace.operator(host, "purge-deletion", args=[deletion])
    after = None
    while after is None or not after.get("complete"):
        request = {"request_id": uid(), "session_id": session["session_id"], "limit": 1000}
        if after is not None:
            require(bool(after.get("next_after")), "incomplete rebuild has no continuation")
            request["after"] = after["next_after"]
        after = trace.operator(host, "rebuild-restore", request)
    verify_dependency(trace, host, state)
    inspection = trace.operator(host, "recovery-inspect", args=["--directory", bundle], expected="INTEGRITY_FAILURE")["data"]["inspection"]
    require({g["reason"] for g in inspection["gaps"]} == {"AUDIT_MISSING"},
            "reapplication erased audit gaps or left restrictive state unreconciled")
    verification = {"session_id": session["session_id"], "checkpoint": state["catalog"]["checkpoint_expectation"],
        "recovery": parts[0]["record"], "fixtures": [{"receipt_id": state["compiled"]["receipt_id"], "query": ""}]}
    for problem in ["changed-expectation", "missing-checkpoint", "omitted-expectation"]:
        bad = copy.deepcopy(verification)
        expected = "CHECKPOINT_MISMATCH"
        if problem == "changed-expectation":
            bad["checkpoint"]["expected_export_id"] += ".changed"
        elif problem == "missing-checkpoint":
            bad["checkpoint"]["checkpoint_id"] = uid()
        else:
            del bad["checkpoint"]
            expected = "INVALID_REQUEST"
        trace.operator(host, "verify-restore", bad, expected=expected)
    resume = {"request_id": uid(), "verification": verification, "policy": "unsupported-r07-policy",
              "reason": "Exercise an unsupported policy only in this owned destination"}
    trace.operator(host, "resume-restore", resume, expected="INVALID_REQUEST")
    trace.search(host, repo, expected="RESTORE_PAUSED")
    ready = trace.operator(host, "verify-restore", verification)
    require(ready["ready"] and bool(ready["reapplied_missing_events"]), "restore verification was not complete")
    require(trace.operator(host, "restore-status")["paused"], "verification silently resumed service")
    trace.search(host, repo, expected="RESTORE_PAUSED")
    resume.update(request_id=uid(), policy="local-restore/1", reason="Explicitly admit only this verified synthetic restore")
    admitted = trace.operator(host, "resume-restore", resume)
    require(trace.operator(host, "resume-restore", resume) == admitted, "resume retry changed result")
    require(not trace.operator(host, "restore-status")["paused"], "explicit resume did not admit the owned restore")
    require(bool(trace.search(host, repo).get("receipt_id")), "ordinary API did not work after explicit admission")
    verify_restrictions(trace, host, state)
    verify_dependency(trace, host, state)
    report["verification"] = ready
    report["admission"] = admitted
    report["known_missing_original_audit"] = inspection


def run_rehearsal(directory):
    directory = Path(directory)
    require(directory.is_absolute() and directory == directory.resolve(), "explicit canonical evidence path required")
    private_dir(directory.parent)
    directory.mkdir(mode=0o700)
    private_dir(directory)
    trace = Receipts(directory / "commands")
    report = {"schema": "cairn.r07-recovery-rehearsal/1", "limits": LIMITS,
              "api_processes": [], "cleanup": [], "success": False}
    runtime = None
    clusters, hosts = [], []
    try:
        report["source"] = source_pins(trace)
        binary, pg_bin = prepare_tools(trace, directory, report["source"])
        # Short owned paths avoid Unix socket truncation without replacing HOME.
        runtime = Path(tempfile.mkdtemp(prefix="cairn-r07-", dir=Path.home() / ".cache"))
        private_dir(runtime)
        report["owned_runtime"] = str(runtime)
        with fixture_routing(pg_bin):
            repo = "fixture:r07-recovery:" + uid()
            principal = "agent/r07-consumer-" + uid()
            source_cluster = OwnedCluster(runtime / "s", pg_bin)
            clusters.append(source_cluster)
            for pin in trace.pins.values():
                require_image(pin)
            OwnedCluster.start(source_cluster.root, pg_bin)
            source = DisposableHost.start(source_cluster, repo=repo, binary=binary,
                database="r07_source", home=runtime / "source-home", roles=("consumer",),
                principals={"consumer": principal}, serve=False, log=directory / "source-api.log")
            hosts.append(source)
            trace.forbidden.append(source.token("consumer").read_text())
            source.start_server()
            report["api_processes"].append({"destination": "source", "pid": source.process.pid,
                "image_sha256": digest(f"/proc/{source.process.pid}/exe")})
            require(source.process.pid > 1 and report["api_processes"][-1]["image_sha256"] == digest(binary),
                    "actual source API image differs")
            report["source_database"] = trace.sql(source,
                "SELECT current_database(),system_identifier FROM pg_control_system()")
            state = prepare_source(trace, source, repo, directory, report)
            report["source_api_exit_before_restore"] = source.stop_server()
            target_cluster = OwnedCluster(runtime / "d", pg_bin)
            clusters.append(target_cluster)
            for pin in trace.pins.values():
                require_image(pin)
            OwnedCluster.start(target_cluster.root, pg_bin)
            target_dsn = target_cluster.create_database("r07_restored")
            require(digest(state["archive"]) == state["catalog"]["archive_sha256"], "archive checksum changed before restore")
            trace.command("pg-restore", [pg_bin / "pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                "--dbname", target_dsn, state["archive"]], env=source.env)
            restored = DisposableHost.start(ProvidedDatabase(target_dsn), repo=repo, binary=binary,
                home=runtime / "restored-home", roles=("consumer",), principals={"consumer": principal},
                serve=False, log=directory / "restored-api.log")
            hosts.append(restored)
            trace.forbidden.append(restored.token("consumer").read_text())
            report["restored_database"] = trace.sql(restored,
                "SELECT current_database(),system_identifier FROM pg_control_system()")
            require(report["source_database"].split("|")[1] != report["restored_database"].split("|")[1],
                    "rehearsal destinations did not use distinct newly owned clusters")
            reconcile_restore(trace, restored, state, repo, report)
            require(source_pins(trace)["commit"] == report["source"]["commit"], "source moved during rehearsal")
            report["success"] = True
    except Exception as error:
        # Preserve a failed run and its receipts; never convert it into a pass.
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for host in reversed(hosts):
            process = host.process
            try:
                require(process is None or process.pid > 1, "refuse invalid owned API PID")
                report["cleanup"].append({"api_home": str(host.home), "pid": None if process is None else process.pid,
                    "exit": host.close(), "joined": process is None or process.poll() is not None})
                require(report["cleanup"][-1]["joined"], "owned API did not join")
            except Exception as error:
                report["cleanup"].append({"api_home": str(host.home), "error": str(error)})
                report["success"] = False
        for cluster in reversed(clusters):
            try:
                require_image(trace.pins[str((cluster.pg_bin / "pg_ctl").resolve())])
                pid_file = cluster.root / "data" / "postmaster.pid"
                pid = int(pid_file.read_text().splitlines()[0]) if pid_file.exists() else None
                require(pid is None or pid > 1, "refuse invalid owned PostgreSQL PID")
                stopped = cluster.stop()
                report["cleanup"].append({"cluster": str(cluster.root), "pid": pid, "stopped": stopped})
                require(stopped, "owned PostgreSQL did not stop; runtime retained")
                log = cluster.root / "postgres.log"
                if log.exists():
                    shutil.copyfile(log, directory / (cluster.root.name + "-postgres.log"))
            except Exception as error:
                report["cleanup"].append({"cluster": str(cluster.root), "error": str(error)})
                report["success"] = False
        if runtime is not None and report["success"]:
            shutil.rmtree(runtime)
            report["runtime_removed"] = not runtime.exists()
        report["command_count"] = len(trace.calls)
        save_json(directory / "result.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path, help="new evidence child of an owned0700 directory")
    args = parser.parse_args()
    report = run_rehearsal(args.directory)
    print(json.dumps({"success": report["success"], "result": str(args.directory / "result.json"), "limits": LIMITS}))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
