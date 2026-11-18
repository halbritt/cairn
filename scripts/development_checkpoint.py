#!/usr/bin/env python3
"""Pack selected development references and inspect them without resuming work."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid


SCHEMA = "development-checkpoint/1"
OUTCOMES = {"confirmed-success", "confirmed-failure", "unknown"}
STATES = {"current", "stale", "missing", "unknown"}


class CheckpointError(ValueError):
    """The declaration or an external source cannot establish a checkpoint."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointError(f"{field} must be a nonempty string")
    return value


def object_keys(value: object, required: set[str], optional: set[str], field: str) -> dict:
    if not isinstance(value, dict):
        raise CheckpointError(f"{field} must be an object")
    if required - value.keys() or value.keys() - required - optional:
        raise CheckpointError(f"{field} has missing or unsupported fields")
    return value


def string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise CheckpointError(f"{field} must be an array")
    for item in value:
        text(item, field)
    return value


def timestamp(value: object, field: str) -> str:
    raw = text(value, field)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CheckpointError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CheckpointError(f"{field} must include a timezone")
    return raw


def digest(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CheckpointError(f"{field} must be a lowercase SHA-256 digest")
    return value


def read_snapshot(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
        result = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise CheckpointError(f"{path} must contain a JSON object")
    return result, hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> dict:
    return read_snapshot(path)[0]


def file_digest(path: Path) -> str:
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise CheckpointError(f"selected reference is not a regular file: {path}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise CheckpointError(f"reference changed while reading: {path}")
    return sha.hexdigest()


def validate(checkpoint: dict, *, sealed: bool) -> None:
    required = {"schema", "project", "goal", "owner", "workspace", "next_action", "references"}
    optional = {"entrypoint", "questions", "effects", "findings", "declared_at"}
    if sealed:
        required.add("packed_at")
    object_keys(checkpoint, required, optional, "checkpoint")
    if checkpoint["schema"] != SCHEMA:
        raise CheckpointError(f"unsupported schema: {checkpoint['schema']!r}")
    for key in ("project", "goal", "workspace", "next_action"):
        text(checkpoint[key], key)
    if sealed and not Path(checkpoint["workspace"]).is_absolute():
        raise CheckpointError("sealed workspace must be absolute")
    owner = object_keys(checkpoint["owner"], {"name", "provenance"}, set(), "owner")
    text(owner["name"], "owner.name")
    text(owner["provenance"], "owner.provenance")
    for key in ("declared_at", "packed_at"):
        if key in checkpoint:
            timestamp(checkpoint[key], key)
    if "questions" in checkpoint:
        string_list(checkpoint["questions"], "questions")
    if "entrypoint" in checkpoint:
        entry = object_keys(checkpoint["entrypoint"], {"argv", "cwd", "versions"}, set(), "entrypoint")
        if not string_list(entry["argv"], "entrypoint.argv"):
            raise CheckpointError("entrypoint.argv must not be empty")
        text(entry["cwd"], "entrypoint.cwd")
        if sealed and not Path(entry["cwd"]).is_absolute():
            raise CheckpointError("sealed entrypoint.cwd must be absolute")
        if not isinstance(entry["versions"], dict):
            raise CheckpointError("entrypoint.versions must be an object")
        for name, version in entry["versions"].items():
            text(name, "version name")
            text(version, "version")
    references = checkpoint["references"]
    if not isinstance(references, list) or not references:
        raise CheckpointError("references must be a nonempty array")
    names: set[str] = set()
    for reference in references:
        validate_reference(reference, sealed=sealed)
        if reference["name"] in names:
            raise CheckpointError(f"duplicate reference name: {reference['name']}")
        names.add(reference["name"])
    effects = checkpoint.get("effects", [])
    if not isinstance(effects, list):
        raise CheckpointError("effects must be an array")
    for effect in effects:
        object_keys(effect, {"operation_id", "outcome", "reconciliation", "source"}, set(), "effect")
        for field in ("operation_id", "reconciliation", "source"):
            text(effect[field], f"effect.{field}")
        if text(effect["outcome"], "effect.outcome") not in OUTCOMES:
            raise CheckpointError("unsupported effect outcome")
        if effect["source"] not in names:
            raise CheckpointError("effect.source must name a selected reference")
    findings = checkpoint.get("findings", [])
    if not isinstance(findings, list):
        raise CheckpointError("findings must be an array")
    for finding in findings:
        object_keys(finding, {"id", "summary", "disposition", "source"}, set(), "finding")
        for field in ("id", "summary", "disposition", "source"):
            text(finding[field], f"finding.{field}")
        if finding["source"] not in names:
            raise CheckpointError("finding.source must name a selected reference")


def validate_reference(reference: object, *, sealed: bool) -> None:
    if not isinstance(reference, dict):
        raise CheckpointError("reference must be an object")
    kind = text(reference.get("kind"), "reference.kind")
    common = {"name", "kind"}
    required = common | ({"record_id", "version"} if kind == "cairn" else {"path"})
    optional = {"role"} if kind == "evidence" else set()
    if sealed:
        required.add("sha256")
    else:
        optional.add("sha256")
    object_keys(reference, required, optional, "reference")
    text(reference["name"], "reference.name")
    if kind not in {"file", "cairn", "evidence"}:
        raise CheckpointError(f"unsupported reference kind: {kind!r}")
    if "sha256" in reference:
        digest(reference["sha256"], "reference.sha256")
    if kind == "cairn":
        try:
            uuid.UUID(text(reference["record_id"], "record_id"))
        except ValueError as exc:
            raise CheckpointError("record_id must be a UUID") from exc
        if type(reference["version"]) is not int or reference["version"] < 1:
            raise CheckpointError("version must be a positive integer")
    else:
        text(reference["path"], "reference.path")
        if sealed and not Path(reference["path"]).is_absolute():
            raise CheckpointError("sealed reference.path must be absolute")
    if kind == "evidence" and text(reference.get("role"), "reference.role") not in {"tested", "reviewed"}:
        raise CheckpointError("evidence.role must be tested or reviewed")


@dataclass(frozen=True)
class Clients:
    cairn: str = "cairn"
    token_file: str | None = None
    socket: str | None = None
    evidence_tool: str = "development-evidence"

    def history(self, reference: dict) -> dict:
        if not self.token_file:
            raise CheckpointError("select an existing --token-file for Cairn references")
        argv = [self.cairn, "agent", "--token-file", self.token_file]
        if self.socket:
            argv.extend(["--socket", self.socket])
        return command_json(argv + ["history"], {
            "record_id": reference["record_id"], "version": reference["version"]
        })


def command_json(argv: list[str], request: dict | None = None, *, codes: tuple[int, ...] = (0,)) -> dict:
    try:
        result = subprocess.run(argv, input=json.dumps(request) if request is not None else None,
                                text=True, capture_output=True, timeout=20, check=False)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise CheckpointError(f"source command unavailable: {exc}") from exc
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"source command returned invalid JSON (exit {result.returncode})") from exc
    if not isinstance(response, dict):
        raise CheckpointError("source command returned a non-object response")
    if result.returncode not in codes and response.get("ok") is not False:
        raise CheckpointError(f"source command failed (exit {result.returncode})")
    if response.get("schema") == "development-evidence-comparison/1":
        expected = {"current": 0, "stale": 1, "unknown": 2, "missing": 3}
        freshness = response.get("freshness")
        if not isinstance(freshness, str) or expected.get(freshness) != result.returncode:
            raise CheckpointError("evidence freshness and exit code disagree")
    return response


def evidence_observation(reference: dict, workspace: str, base: str | None, clients: Clients) -> dict:
    inspected = command_json([clients.evidence_tool, "inspect", reference["path"], "--json"], codes=(0, 1, 2, 3))
    if inspected.get("schema") != "development-evidence-inspection/1":
        raise CheckpointError("unsupported evidence inspection response")
    if inspected.get("valid") is not True:
        return {"state": "unknown", "reason": "evidence receipt is invalid or unavailable",
                "evidence_diagnostics": inspected.get("diagnostics", [])}
    record = inspected.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("subject"), dict):
        raise CheckpointError("evidence inspector omitted the validated subject")
    argv = [clients.evidence_tool, "compare", reference["path"], "--repo", workspace, "--json"]
    if base is not None:
        argv.extend(["--base", base])
    compared = command_json(argv, codes=(0, 1, 2, 3))
    freshness = compared.get("freshness")
    if (compared.get("schema") != "development-evidence-comparison/1"
            or not isinstance(freshness, str) or freshness not in STATES):
        raise CheckpointError("unsupported evidence comparison response")
    if any(report.get("receipt_sha256") != reference["sha256"] for report in (inspected, compared)):
        raise CheckpointError("evidence receipt changed during inspection")
    return {"state": compared["freshness"], "reason": "evidence source and artifacts compared",
            "evidence": {"recorded_at": record.get("recorded_at"), "subject": record["subject"],
                         "outcome": record.get("outcome"), "observations": record.get("observations", []),
                         "diagnostics": inspected.get("diagnostics", []), "reasons": compared.get("reasons", []),
                         "limits": compared.get("limits", []), "current_subject": compared.get("current_subject")}}


def record_observation(reference: dict, clients: Clients) -> dict:
    response = clients.history(reference)
    if response.get("ok") is not True:
        state = "missing" if response.get("status") == "NOT_FOUND" else "unknown"
        return {"state": state, "reason": f"Cairn history: {response.get('status', 'invalid response')}"}
    data = response.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("versions"), list):
        raise CheckpointError("Cairn returned malformed history data")
    versions = data["versions"]
    if data.get("record_id") != reference["record_id"] or len(versions) != 1:
        raise CheckpointError("Cairn returned a different or ambiguous record")
    version = versions[0]
    if not isinstance(version, dict):
        raise CheckpointError("Cairn returned malformed version data")
    if version.get("version") != reference["version"]:
        raise CheckpointError("Cairn returned a different version")
    if version.get("payload_available") is not True or not isinstance(version.get("body"), str):
        return {"state": "unknown", "reason": "Cairn exact source payload unavailable"}
    sha = hashlib.sha256(version["body"].encode("utf-8")).hexdigest()
    if version.get("body_sha256") != sha:
        raise CheckpointError("Cairn source body does not match its reported digest")
    current = data.get("current_version")
    if type(current) is not int or current < reference["version"]:
        raise CheckpointError("Cairn current version unavailable")
    current_source = current == reference["version"] and data.get("current_lifecycle") == "active"
    return {"state": "current" if current_source else "stale", "sha256": sha,
            "current_version": current, "source_written_at": version.get("written_at"),
            "source_writer": version.get("observed_writer"),
            "reason": "exact source available" if current_source else "source superseded or inactive"}


def selected_path(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else base / path


def pack(spec_path: Path, output: Path, clients: Clients) -> dict:
    checkpoint = read_json(spec_path)
    validate(checkpoint, sealed=False)
    base = spec_path.absolute().parent
    checkpoint["workspace"] = str(selected_path(base, checkpoint["workspace"]).absolute())
    if "entrypoint" in checkpoint:
        checkpoint["entrypoint"]["cwd"] = str(selected_path(base, checkpoint["entrypoint"]["cwd"]).absolute())
    for reference in checkpoint["references"]:
        if reference["kind"] == "cairn":
            observed = record_observation(reference, clients)
            if "sha256" not in observed:
                raise CheckpointError(f"{reference['name']}: {observed['reason']}")
            sha = observed["sha256"]
        else:
            path = selected_path(base, reference["path"]).absolute()
            sha = file_digest(path)
            reference["path"] = str(path)
        if reference.get("sha256", sha) != sha:
            raise CheckpointError(f"{reference['name']}: supplied digest does not match selected source")
        reference["sha256"] = sha
    checkpoint["packed_at"] = now()
    validate(checkpoint, sealed=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(checkpoint, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    return {"schema": "development-checkpoint-pack/1", "path": str(output.absolute()),
            "sha256": file_digest(output), "reference_count": len(checkpoint["references"])}


def inspect_reference(reference: dict, clients: Clients, workspace: str, base: str | None) -> dict:
    try:
        if reference["kind"] == "cairn":
            observed = record_observation(reference, clients)
        else:
            sha = file_digest(Path(reference["path"]))
            observed = {"state": "current", "sha256": sha, "reason": "selected file available"}
        if "sha256" in observed and observed["sha256"] != reference["sha256"]:
            observed.update(state="stale", reason="selected source digest changed")
        if "sha256" in observed:
            observed["observed_sha256"] = observed.pop("sha256")
        if reference["kind"] == "evidence" and observed["state"] == "current":
            observed.update(evidence_observation(reference, workspace, base, clients))
        return {**reference, **observed, "observed_at": now()}
    except FileNotFoundError:
        return {**reference, "state": "missing", "reason": "selected file missing", "observed_at": now()}
    except (OSError, CheckpointError) as exc:
        return {**reference, "state": "unknown", "reason": str(exc), "observed_at": now()}


def inspect_checkpoint(path: Path, clients: Clients, base: str | None = None) -> dict:
    checkpoint, checkpoint_sha = read_snapshot(path)
    validate(checkpoint, sealed=True)
    references = [inspect_reference(reference, clients, checkpoint["workspace"], base)
                  for reference in checkpoint["references"]]
    states = {reference["state"] for reference in references}
    state = next((state for state in ("unknown", "missing", "stale") if state in states), "current")
    if file_digest(path) != checkpoint_sha:
        raise CheckpointError("checkpoint changed during inspection")
    return {"schema": "development-checkpoint-inspection/1", "observed_at": now(),
            "checkpoint": str(path.absolute()), "checkpoint_sha256": checkpoint_sha,
            "reference_status": state, "references": references,
            "declared": {key: checkpoint[key] for key in (
                "project", "goal", "owner", "workspace", "next_action", "packed_at", "declared_at",
                "entrypoint", "questions", "effects", "findings"
            ) if key in checkpoint}}


def status_view(inspection: dict) -> dict:
    states = {reference["name"]: reference["state"] for reference in inspection["references"]}
    declared = inspection["declared"]
    effects = [{**effect, "source_status": states[effect["source"]],
                "observed_outcome": "unknown"} for effect in declared.get("effects", [])]
    findings = [{**finding, "source_status": states[finding["source"]],
                 "observed_disposition": "unverified"} for finding in declared.get("findings", [])]
    candidates = {role: [] for role in ("tested", "reviewed")}
    current_subjects = []
    for reference in inspection["references"]:
        if reference["kind"] != "evidence":
            continue
        evidence = reference.get("evidence", {})
        candidates[reference["role"]].append({
            "source": reference["name"], "source_status": reference["state"],
            "receipt_sha256": reference["sha256"], "recorded_at": evidence.get("recorded_at"),
            "candidate": evidence.get("subject", {}).get("candidate"),
            "snapshot": evidence.get("subject", {}).get("snapshot"),
            "outcome": evidence.get("outcome"), "observed_at": reference["observed_at"]})
        if isinstance(evidence.get("current_subject"), dict):
            current_subjects.append(evidence["current_subject"])
    current_subject = current_subjects[0] if current_subjects else None
    if any(subject != current_subject for subject in current_subjects):
        current_subject = None
    return {"schema": "development-checkpoint-status/1", "observed_at": inspection["observed_at"],
            "reference_status": inspection["reference_status"], "declared": declared,
            "effects": effects, "findings": findings, "candidates": candidates,
            "current_subject": current_subject, "references": inspection["references"],
            "task_outcome": "unverified", "limitations": [
                "Declarations are selected context, not live task state or authorization.",
                "This tool checks references; it does not reconcile effects or execute the entry point.",
                "Matching digests establish identity, not adequate coverage or correctness."
            ]}


def render_status(view: dict) -> str:
    declared = view["declared"]
    lines = [f"{declared['project']}: {declared['goal']}",
             f"Owner (declared): {declared['owner']['name']}",
             f"References: {view['reference_status']} as of {view['observed_at']}",
             f"Task outcome: {view['task_outcome']}",
             f"Next action (declared): {declared['next_action']}"]
    subject = view["current_subject"]
    lines.append(f"Current candidate: {subject.get('candidate') or 'dirty/unknown'}" if subject else
                 "Current candidate: unknown (no consistent live evidence comparison)")
    for role, candidates in view["candidates"].items():
        if not candidates:
            lines.append(f"{role.title()} candidate: unknown (no selected evidence)")
        for candidate in candidates:
            lines.append(f"{role.title()} candidate: {candidate['candidate'] or candidate['snapshot'] or 'unknown'}; "
                         f"{candidate['source_status']}; recorded outcome {candidate['outcome'] or 'unknown'}")
    lines.extend(f"  {reference['name']}: {reference['state']} ({reference['reason']})"
                 for reference in view["references"])
    lines.extend(f"  Effect {effect['operation_id']}: declared {effect['outcome']}; "
                 f"source {effect['source_status']}; live outcome unknown" for effect in view["effects"])
    lines.extend(f"  Finding {finding['id']}: declared {finding['disposition']}; source {finding['source_status']}"
                 for finding in view["findings"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pack", "inspect", "status"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
        if name == "pack":
            command.add_argument("--output", required=True, type=Path)
        if name == "status":
            command.add_argument("--text", action="store_true")
        if name != "pack":
            command.add_argument("--base", help="current established review base, when evidence records one")
        command.add_argument("--token-file")
        command.add_argument("--socket")
        command.add_argument("--cairn", default="cairn", help="installed Cairn executable")
        command.add_argument("--evidence-tool", default="development-evidence")
    args = parser.parse_args(argv)
    clients = Clients(args.cairn, args.token_file, args.socket, args.evidence_tool)
    try:
        if args.command == "pack":
            result = pack(args.path, args.output, clients)
        else:
            result = inspect_checkpoint(args.path, clients, args.base)
            if args.command == "status":
                result = status_view(result)
        if args.command == "status" and args.text:
            print(render_status(result))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("reference_status", "current") == "current" else 1
    except (OSError, CheckpointError) as exc:
        print(json.dumps({"schema": "development-checkpoint-error/1", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
