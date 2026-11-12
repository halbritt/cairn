#!/usr/bin/env python3
"""Audit frozen S-01 evidence and collect fixed refusal-boundary observations.

The source-failures command uses the pinned model for missing protocol controls.
It does not regenerate the numerical oracle or authorize held experiments.
"""

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from capture_semantic_fixtures import EvidenceError, verify_sealed_bundle
from compare_semantic_workers import ComparisonFailure, verify_go_events, verify_python_events
from semantic_acceptance_isolation import IsolationError, SemanticIsolation, WORKER_ENV


PROJECT = Path(__file__).resolve().parents[1]
INVENTORY = PROJECT / "scripts/testdata/semantic-reference-inventory.json"
ACCEPTED_BASELINE_SHA256 = 'c8ad4c34e63585fbcb674673353548eab690fdd4ea70fb41e869aefebcc5e69e'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                      parse_constant=constant)


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False, sort_keys=True) + "\n")


def pinned(root, relative, expected):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts,
            "artifact path must stay below its selected root")
    path = Path(root) / relative
    require(path.is_file() and not path.is_symlink(), "missing regular artifact: " + str(path))
    require(digest(path) == expected, "artifact hash mismatch: " + str(path))
    return path


def pinned_case_files(root, folder, hashes, required):
    """Every file consumed by a case decoder must belong to its selected seal."""
    relative = Path(folder)
    require(not relative.is_absolute() and ".." not in relative.parts and
            len(relative.parts) == 1, "case folder must be one relative component")
    directory = Path(root) / relative
    files = {str(p.relative_to(root)) for p in directory.iterdir() if p.is_file()}
    require(set(hashes) == files and
            {str(relative / name) for name in required} <= files,
            "missing or unsealed case artifact")
    for name, sha in hashes.items():
        pinned(root, name, sha)
    return directory


def inventory(path=INVENTORY):
    require(digest(path) == digest(INVENTORY), "complete committed inventory required")
    value = load(path)
    require(value["schema"] == "cairn.semantic-reference-inventory/1", "inventory schema")
    old = load(PROJECT / "semantic/testdata/executable-harness/reference-inventory.json")
    for key in ("rank_ids", "query_ids", "document_ids", "expected_counts", "fixture_sha256"):
        require(value[key] == old[key], "required inventory changed: " + key)
    expected = [group + "." + method for group, methods in old["python_tests"].items()
                for method in methods]
    require(value["python_prepared"] == expected and len(expected) == 23,
            "missing required prepared selector")
    require(value["actual_tokenizer"] in expected, "actual tokenizer selector missing")
    require(value["go_process"] == old["go_process_tests"], "missing required Go selector")
    require(len(value["python_mechanics"]) == 6 and
            len(set(value["python_mechanics"])) == 6, "missing native mechanics selector")
    ids = [row["id"] for row in value["criteria"]]
    require(len(ids) == len(set(ids)) and
            set(old["required_reference_cells"]) <= set(ids), "missing required criterion")
    require(len(value["assets_sha256"]) == 5, "five model assets required")
    for relative, expected_hash in value["source_sha256"].items():
        pinned(PROJECT, relative, expected_hash)
    failure_inventory(value)
    if "failure_baseline" in value:
        accepted_baseline(value)
    return value


def accepted_baseline(value):
    spec=value['failure_baseline']
    require(spec['sha256']==ACCEPTED_BASELINE_SHA256,'accepted baseline metadata must retain its reviewed bytes')
    baseline=load(pinned(PROJECT,spec['relative'],spec['sha256']))
    require(set(baseline)=={'schema','revision','review_record','inventory_sha256','ledger_sha256',
            'ledger_sources','actors','tool_sha256','reports'} and
            baseline['schema']=='cairn.semantic-refusal-baseline/1' and
            baseline['revision']=='91a97fdb862e190ea196704b3c446dab06be2b2b' and
            baseline['review_record']=='97fee45b-2a15-4a79-9387-9884d8bad48d/v1',
            'unsupported accepted-baseline claim')
    require(set(baseline['reports'])=={'early','process','source','tests'},'missing accepted report pin')
    for relative,sha in baseline['actors'].items():
        pinned(PROJECT,relative,sha)
    return baseline


def producer_binding(value,kind,report_sha):
    """Exact accepted bytes may outlive collector/test edits; executed actors may not."""
    require(kind in ('early','process','source','tests'),'unknown refusal report kind')
    if 'failure_baseline' in value:
        baseline=accepted_baseline(value)
        if report_sha==baseline['reports'][kind]['sha256']:
            return dict(ledger=baseline['ledger_sha256'],sources=baseline['ledger_sources'],
                        tools=baseline['tool_sha256'],inventory=baseline['inventory_sha256'],
                        origin='exact accepted91a97fdb report; actors and raw tuples rechecked')
    return dict(ledger=value['failure_ledger']['sha256'],sources=failure_inventory(value)['source_sha256'],
                tools=tool_pins(),inventory=digest(INVENTORY),origin='current collector')


FAILURE_COUNTS = {"F-RANK": 19, "F-STREAM": 25, "F-EMBED": 19,
                  "F-PROCESS": 10, "F-IDENTITY": 5}
DISPOSITION_FIELDS = {"go_error_or_result", "discovery_coverage", "process_exit_kill",
                      "stdout_prefix", "cache", "restarts"}


def failure_inventory(value):
    """Read authored expectations, never promote them to observed evidence."""
    spec = value["failure_ledger"]
    ledger = load(pinned(PROJECT, spec["relative"], spec["sha256"]))
    require(set(ledger) == {"schema", "source_sha256", "retained", "cases"} and
            ledger.get("schema") == "cairn.semantic-refusal-ledger/1", "unsupported failure ledger schema/claim")
    expected = []
    for family, count in FAILURE_COUNTS.items():
        ids = value["failure_families"][family]["unbound_case_ids"]
        require(len(ids) == count and all(i.startswith(family + "/") for i in ids),
                "failure case IDs changed: " + family)
        expected.extend(ids)
    actual = [row["id"] for row in ledger["cases"]]
    require(hashlib.sha256(json.dumps(sorted(expected), separators=(",", ":")).encode()).hexdigest() ==
            "0d9287a45075db06c03e258f4f8f0d0ad53643801bc55de123ad5b71299b8291",
            "failure case IDs differ from the accepted 78-cell inventory")
    require(len(actual) == len(set(actual)) and set(actual) == set(expected),
            "missing, duplicate or changed failure case IDs")
    for relative, sha in ledger["source_sha256"].items():
        pinned(PROJECT, relative, sha)
    fixture_rows = load(PROJECT / "scripts/testdata/semantic-refusal-inputs.json")["cases"]
    require(len(fixture_rows) == len({r["id"] for r in fixture_rows}) and
            {r["id"] for r in fixture_rows} == set(expected), "failure fixture IDs changed")
    fixtures = {r["id"]: r["subcases"] for r in fixture_rows}
    subcases = set()
    for row in ledger["cases"]:
        require(set(row) == {"id", "source", "fixture", "subcases", "expected_disposition", "evidence"},
                "unsupported failure claim/fields: " + row["id"])
        require(row["source"] and row["fixture"] and row["subcases"], "missing failure binding: " + row["id"])
        require(row["subcases"] == fixtures[row["id"]], "failure input/expected binding changed: " + row["id"])
        for source in row["source"]:
            require(set(source) == {"file", "anchor"} and source["file"] in ledger["source_sha256"],
                    "unbound failure source: " + row["id"])
            require(source["anchor"] and source["anchor"] in (PROJECT / source["file"]).read_text(),
                    "failure source anchor missing: " + row["id"])
        require(set(row["expected_disposition"]) == DISPOSITION_FIELDS and
                all(isinstance(v, str) and v for v in row["expected_disposition"].values()),
                "absent expected disposition field: " + row["id"])
        for case in row["subcases"]:
            require(set(case) == {"id", "input", "expected"} and
                    case["id"].startswith(row["id"] + "/") and case["id"] not in subcases and
                    case["input"] and isinstance(case["expected"], str) and case["expected"],
                    "missing/duplicate failure subcase binding: " + row["id"])
            subcases.add(case["id"])
        for fixture in row["fixture"]:
            require(set(fixture) == {"file", "anchor"} and fixture["file"] in ledger["source_sha256"] and
                    fixture["anchor"] and fixture["anchor"] in (PROJECT / fixture["file"]).read_text(),
                    "unbound failure fixture: " + row["id"])
        require(isinstance(row["evidence"], list), "failure evidence must be a list")
    return ledger


def unobserved(reason, state="not-exercised"):
    return dict(state=state, reason=reason)


def historical_failure_observation(value, root, identifier, *, positive=False):
    expected = next((r for r in value["historical_refusals"] if r["id"] == identifier), None)
    require(expected is not None or (positive and identifier in
            {"rank-128-occurrences", "rank-request-wide-128", "embed-identity"}),
            "unbound historical case: " + identifier)
    base = Path(value["retained"]["python-characterization"]["relative"]).parent
    capture_path = pinned(root, value["retained"]["python-characterization"]["relative"],
                          value["retained"]["python-characterization"]["sha256"])
    captures = [r for r in load(capture_path)["records"] if r["name"] == identifier]
    require(len(captures) == 1 and captures[0]["status"] == "observed", "historical case missing/duplicate")
    capture = captures[0]
    if positive:
        expected = dict(expected_exit=0, **{key + "_sha256": capture[key + "_sha256"]
                                           for key in ("input", "stdout", "stderr")})
    paths = {key: pinned(root, base / relative, expected[key + "_sha256"])
             for key, relative in (("input", "inputs/" + identifier + ".stdin"),
                                   ("stdout", "raw/" + identifier + ".stdout"),
                                   ("stderr", "raw/" + identifier + ".stderr"))}
    require(capture["exit_code"] == capture["expected_exit"] == expected["expected_exit"],
            "historical case exit changed")
    return dict(id=identifier, scope="historical/direct-python-process", freshness="reused",
                input_sha256=expected["input_sha256"],
                disposition=dict(go_error_or_result=unobserved("direct Python, no Go owner"),
                  discovery_coverage=unobserved("no discovery call"),
                  process_exit_kill=dict(state="observed", exit_code=capture["exit_code"],
                                         killed=capture["exit_code"] < 0),
                  stdout_prefix=dict(state="observed", hex=paths["stdout"].read_bytes()[:64].hex(),
                                     sha256=expected["stdout_sha256"]),
                  cache=unobserved("capture did not observe cache custody", "unknown"),
                  restarts=unobserved("capture did not record restart count", "unknown")))


def accepted_route_observation(value, root, route):
    """Retain only actually recorded fields, without inferring a missing process tuple."""
    if route in ("stream-idle", "stream-cold-warm"):
        pin = value["retained"]["stream38-idle"]
        report = load(pinned(root, pin["relative"], pin["sha256"]))
        block = report["idle" if route == "stream-idle" else "stream"]
        frames = block["frames"]
        require(len(frames) == (3 if route == "stream-idle" else 38), "accepted stream case binding changed")
        disposition = {key: unobserved("not recorded per exchange", "unknown") for key in DISPOSITION_FIELDS}
        disposition["discovery_coverage"] = unobserved("no discovery call")
        disposition["go_error_or_result"] = dict(state="observed", frames=[dict(cell=f["cell"],
             result_sha256=hashlib.sha256(json.dumps(f["result"],sort_keys=True).encode()).hexdigest()) for f in frames])
        if route == "stream-idle":
            require(block["old_child_absent_before_restart"] is True and block["new_child_observed"] is True and
                    block["state"]["exact_json_state_handoff"] is True, "accepted idle disposition changed")
            disposition["cache"] = dict(state="observed", **block["state"])
            disposition["restarts"] = dict(state="observed", replacements=1,
                                             old_child=block["old_child"], new_child=block["new_child"])
            disposition["process_exit_kill"] = dict(state="partial", old_child_absent=True,
                                                      exit_code="unknown", kill="unknown")
        else:
            require(block["actual_phases"] == ["initial_cold"] + ["warm_same_child"] * 37 and
                    frames[0]["result"] == frames[1]["result"], "accepted cold/warm binding changed")
        return dict(id=route, scope="accepted/Rust-Go-supervised-model", freshness="reused", disposition=disposition)
    allowed = {"query-ascii", "document-ascii", "document-maximum-latin", "document-maximum-cjk"}
    require(route in allowed, "unsupported accepted route")
    pin = value["retained"]["package-comparison-manifest"]
    manifest_path = pinned(root, pin["relative"], pin["sha256"])
    files = load(manifest_path)["files"]
    inputs = pinned(manifest_path.parent, "attempt-02/embed-inputs.jsonl", files["attempt-02/embed-inputs.jsonl"])
    output = pinned(manifest_path.parent, "attempt-02/embed.stdout", files["attempt-02/embed.stdout"])
    requests = [json.loads(line) for line in inputs.read_bytes().splitlines()]
    replies = [json.loads(line) for line in output.read_bytes().splitlines()]
    request = [r for r in requests if r["id"] == route]
    reply = [r for r in replies if r["id"] == route]
    require(len(request) == len(reply) == 1, "accepted package case missing/duplicate")
    raw = json.dumps(reply[0],sort_keys=True,separators=(",", ":")).encode()
    disposition = {key: unobserved("not recorded for individual case in the multi-case capture", "unknown")
                   for key in DISPOSITION_FIELDS}
    disposition["go_error_or_result"] = dict(state="observed", identity=reply[0]["identity"],
         result_sha256=hashlib.sha256(raw).hexdigest(), passages=len(reply[0].get("passages", [])))
    disposition["discovery_coverage"] = unobserved("no discovery call")
    disposition["cache"] = unobserved("embedding has no rank stream cache")
    return dict(id=route, scope="accepted/packaged-Rust-Go-model", freshness="reused",
                input_text_bytes=len(request[0]["text"].encode()),
                input_sha256=hashlib.sha256(json.dumps(request[0],sort_keys=True).encode()).hexdigest(),
                disposition=disposition)


def typed_failure_observation(row):
    require(set(row) == {"id", "expected_exception", "expected_message", "scope", "inference_calls", "result"}
            and row["scope"] == "function/no-model" and type(row["inference_calls"]) is int
            and row["inference_calls"] == 0 and row["result"] is None,
            "unsupported typed failure scope/claim")
    return dict(id=row["id"], scope="function/no-model", freshness="reused",
                exception=row["expected_exception"], message=row["expected_message"], inference_calls=0,
                disposition={key: unobserved("function call has no process/Go/cache/restart observation")
                             for key in DISPOSITION_FIELDS})


def early_failure_inputs():
    """Exact missing pre-initialization process fixtures; no model can be selected."""
    note = dict(record_id="record", version=1, body="body",
                body_sha256=hashlib.sha256(b"body").hexdigest())
    good = dict(query="query", notes=[note])
    cases = []

    def add(identifier, request, error, *, stream=False, raw=None):
        if raw is None:
            raw = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
        cases.append(dict(id=identifier, mode="stream" if stream else "rank", input=raw, error=error))

    bounded = "ValueError: worker requires a bounded query and 1-64 notes"
    large_body = "x" * (1048576 + 1)
    for name, request, error in [
        ("zero-notes", dict(good, notes=[]), bounded),
        ("65-notes", dict(good, notes=[note] * 65), bounded),
        ("empty-query", dict(good, query=""), bounded),
        ("oversize-query", dict(good, query="x" * 4097), bounded),
        ("wrong-body-hash", dict(good, notes=[dict(note, body_sha256="wrong")]),
         "ValueError: note content hash mismatch"),
        ("body-byte-overflow", dict(good, notes=[dict(note, body=large_body,
          body_sha256=hashlib.sha256(large_body.encode()).hexdigest())]),
         "ValueError: note bytes exceed limit"),
    ]:
        add("F-RANK/" + name + "/boundary", request, error)
    add("F-RANK/malformed-json/boundary", None, "json.decoder.JSONDecodeError:", raw=b"{")
    add("F-RANK/one-shot-over8MiB/boundary", None,
        "ValueError: worker request exceeds limit", raw=b"x" * (8 * 1024 * 1024 + 1))
    shape = [
        ("missing-query", dict(notes=[note]), "KeyError: 'query'"),
        ("missing-notes", dict(query="query"), "KeyError: 'notes'"),
        ("query-null", dict(good, query=None), "AttributeError:"),
        ("query-list", dict(good, query=[]), "AttributeError:"),
        ("notes-null", dict(good, notes=None), "TypeError:"),
        ("notes-object", dict(good, notes={"x": note}), "TypeError:"),
        ("note-null", dict(good, notes=[None]), "TypeError:"),
        ("missing-body", dict(good, notes=[{}]), "KeyError: 'body'"),
        ("body-null", dict(good, notes=[dict(note, body=None)]), "AttributeError:"),
        ("missing-body-sha256", dict(good, notes=[{k: v for k, v in note.items() if k != "body_sha256"}]),
         "KeyError: 'body_sha256'"),
    ]
    for name, request, error in shape:
        add("F-RANK/structural-errors/" + name, request, error)
    envelope = dict(id="one", request=good)
    framing = "ValueError: worker requires a bounded JSON line"
    add("F-STREAM/incomplete-line/Python-input-without-newline", None, framing, stream=True,
        raw=json.dumps(envelope).encode())
    add("F-STREAM/oversize-line/Python-input-over8MiB", None, framing, stream=True,
        raw=b"x" * (8 * 1024 * 1024 + 2))
    for name, frame in [("missing-id", dict(request=good)), ("missing-request", dict(id="one"))]:
        add("F-STREAM/missing-envelope-key/" + name, frame,
            "ValueError: worker requires id and request", stream=True)
    add("F-STREAM/extra-envelope-key/boundary", dict(envelope, extra=True),
        "ValueError: worker requires id and request", stream=True)
    for name, identifier in [("null", None), ("boolean", False), ("integer", 1), ("array", []), ("object", {})]:
        add("F-STREAM/wrong-id-type/" + name, dict(envelope, id=identifier),
            "ValueError: invalid worker request id", stream=True)
    add("F-STREAM/empty-id/boundary", dict(envelope, id=""),
        "ValueError: invalid worker request id", stream=True)
    for name, identifier in [("ascii65", "x" * 65), ("unicode65", "漢" * 65)]:
        add("F-STREAM/over64-id/" + name, dict(envelope, id=identifier),
            "ValueError: invalid worker request id", stream=True)
    add("F-STREAM/unadvertised-cache/input-extra-cache", dict(envelope, cache={}),
        "ValueError: worker requires id and request", stream=True)
    return cases


def collect_failures(args, value):
    ledger = failure_inventory(value)
    allowed = {sub["id"] for row in ledger["cases"] for sub in row["subcases"]}
    fixtures = early_failure_inputs()
    require({row["id"] for row in fixtures} <= allowed, "early fixture outside failure ledger")
    python = pinned(args.python_home, "bin/python3.11", value["python_sha256"])
    output = args.output.absolute()
    require(not output.exists(), "failure output must be fresh")
    report = dict(schema="cairn.semantic-early-refusals/1", ledger_sha256=value["failure_ledger"]["sha256"],
                  sources=ledger["source_sha256"], tool_sha256=tool_pins(),
                  python_sha256=value["python_sha256"], cases=[], model_execution=False)
    with SemanticIsolation(read_only_paths=[PROJECT, args.python_home], output_dir=output) as isolated:
        for index, case in enumerate(fixtures):
            name = str(index).zfill(3)
            argv = [str(python), "-I", "-S", "-B", str(PROJECT / "scripts/semantic_rank.py"),
                    "--model-dir", "/nonexistent-s01b-model"]
            if case["mode"] == "stream":
                argv.append("--stream")
            (output / (name + ".stdin")).write_bytes(case["input"])
            result = isolated.run(argv, input=case["input"], timeout=10, env=dict(WORKER_ENV),
                                  role="S01b-early-source-process", mode=case["id"], stdout_limit=65536)
            (output / (name + ".stdout")).write_bytes(result.stdout)
            (output / (name + ".stderr")).write_bytes(result.stderr)
            require(result.returncode == 1 and result.stdout == b"" and
                    result.stderr.decode().splitlines()[-1].startswith(case["error"]),
                    "early source refusal changed: " + case["id"])
            report["cases"].append(dict(id=case["id"], mode=case["mode"], prefix=name,
                   exit_code=result.returncode, invocation=result.invocation,
                   files_sha256={suffix: digest(output / (name + "." + suffix))
                                 for suffix in ("stdin", "stdout", "stderr")}))
    require(report["tool_sha256"] == tool_pins(), "failure collector changed during execution")
    report["receipts_sha256"] = {name: digest(output / name)
                                  for name in ("isolation-proof.json", "cleanup.json")}
    save(output / "failure-report.json", report)
    return report


def verify_early_failures(value, path, expected_hash):
    require(expected_hash and digest(path) == expected_hash, "pinned failure report required")
    report = load(path)
    ledger = failure_inventory(value)
    require(set(report) == {"schema", "ledger_sha256", "sources", "tool_sha256", "python_sha256",
                            "cases", "model_execution", "receipts_sha256"} and
            report["schema"] == "cairn.semantic-early-refusals/1" and report["model_execution"] is False,
            "unsupported observed/accepted failure report claim")
    binding=producer_binding(value,'early',expected_hash)
    require(report["ledger_sha256"] == binding['ledger'] and
            report["sources"] == binding['sources'] and report["tool_sha256"] == binding['tools'] and
            report["python_sha256"] == value["python_sha256"], "failure source/input/collector binding changed")
    expected = {row["id"]: row for row in early_failure_inputs()}
    ids = [row["id"] for row in report["cases"]]
    require(len(ids) == len(set(ids)) and set(ids) == set(expected), "missing/duplicate early failure case IDs")
    for row in report["cases"]:
        require(set(row) == {"id", "mode", "prefix", "exit_code", "invocation", "files_sha256"} and
                set(row["files_sha256"]) == {"stdin", "stdout", "stderr"}, "absent failure disposition/binding field")
    require(set(report["receipts_sha256"]) == {"isolation-proof.json", "cleanup.json"}, "missing failure process receipts")
    receipts = {name: load(pinned(path.parent, name, sha)) for name, sha in report["receipts_sha256"].items()}
    proof, cleanup = receipts["isolation-proof.json"], receipts["cleanup.json"]
    require(proof["wrapper_sha256"] == value["source_sha256"]["scripts/semantic_acceptance_isolation.py"] and
            proof["profile"] == "default" and all(r["read_only"] for r in proof["read_only_mounts"]),
            "failure isolation/source binding changed")
    require(cleanup["all_observed_processes_exited"] is True and cleanup["backing_removed"] is True and
            cleanup["errors"] == [] and cleanup["launcher_exit_codes"] == [0] + [1] * len(expected) and
            cleanup["isolation_proof_sha256"] == report["receipts_sha256"]["isolation-proof.json"],
            "failure process cleanup/disposition changed")
    invocations = cleanup["invocations"][1:]
    require(len(invocations) == len(expected) and
            all(type(i["pid"]) is int and i["pid"] > 1 for i in invocations) and
            len({(i["pid"], i["start_ticks"]) for i in invocations}) == len(expected),
            "missing/duplicate failure process identities")
    require(len({row["prefix"] for row in report["cases"]}) == len(expected),
            "duplicate early failure file prefixes")
    observed = {}
    for row in report["cases"]:
        case = expected[row["id"]]
        raw = {suffix: pinned(path.parent, row["prefix"] + "." + suffix, sha).read_bytes()
               for suffix, sha in row["files_sha256"].items()}
        require(row["mode"] == case["mode"] and raw["stdin"] == case["input"] and
                type(row["exit_code"]) is int and row["exit_code"] == 1 and not raw["stdout"] and
                raw["stderr"].decode().splitlines()[-1].startswith(case["error"]),
                "early failure input/disposition changed: " + row["id"])
        require(row["invocation"]["role"] == "S01b-early-source-process" and
                row["invocation"]["mode"] == row["id"] and row["invocation"] in invocations,
                "failure invocation scope changed")
        invocation = row["invocation"]
        command = invocation["argv"][invocation["argv"].index("--") + 1:]
        require(len(command) == (8 if case["mode"] == "stream" else 7) and
                command[0].endswith("/bin/python3.11") and command[1:4] == ["-I", "-S", "-B"] and
                command[4].endswith("/scripts/semantic_rank.py") and
                command[5:7] == ["--model-dir", "/nonexistent-s01b-model"] and
                (case["mode"] != "stream" or command[-1] == "--stream") and
                invocation["contract"]["environment"] == dict(WORKER_ENV, PWD="/"),
                "early failure command/model boundary changed")
        observed[row["id"]] = dict(id=row["id"], scope="Python/source-process/pre-initialization",
            freshness="reused", capture_origin="selected isolated early-process collection",
            input_sha256=row["files_sha256"]["stdin"],
            disposition=dict(go_error_or_result=unobserved("direct Python; no Go owner"),
                discovery_coverage=unobserved("no discovery call"),
                process_exit_kill=dict(state="observed", exit_code=1, killed=False),
                stdout_prefix=dict(state="observed", hex="", sha256=row["files_sha256"]["stdout"]),
                cache=unobserved("refused before Scorer construction; no cache created"),
                restarts=dict(state="observed", count=0, invocations=1)))
    return observed


def failure_evidence(value, root, fresh=None):
    ledger = failure_inventory(value)
    retained = {name: pinned(root, pin["relative"], pin["sha256"])
                for name, pin in ledger["retained"].items()}
    typed = load(retained["typed"])
    require(typed.get("schema") == "cairn.semantic-typed-refusals/1" and
            typed.get("cases") == value["typed_failures"], "retained typed case binding changed")
    report = load(retained["test-report"])
    require(report["sources"] == value["source_sha256"] and report["exit_codes"]["typed"] == 0 and
            report["logs_sha256"]["typed.stdout"] == ledger["retained"]["typed"]["sha256"],
            "retained typed source/input binding changed")
    # These outcomes locate existing fixture protection; a test pass is not a raw disposition tuple.
    go_raw = retained["go-events"].read_bytes()
    go = command_result(SimpleNamespace(returncode=report["exit_codes"]["go"], stdout=go_raw),
                        value["go_process"], "go")
    require(report["logs_sha256"]["go.stdout"] == ledger["retained"]["go-events"]["sha256"],
            "retained Go outcome binding changed")
    cases = []
    historical_ids = set()
    for row in ledger["cases"]:
        observed = []
        for reference in row["evidence"]:
            if reference.get("kind") == "historical-python":
                require(set(reference) == {"kind", "case"}, "unsupported historical failure claim")
                observed.append(historical_failure_observation(value, root, reference["case"]))
                historical_ids.add(reference["case"])
            elif reference.get("kind") == "historical-positive":
                require(set(reference) == {"kind", "case"}, "unsupported historical positive claim")
                observed.append(historical_failure_observation(value, root, reference["case"], positive=True))
            elif reference.get("kind") == "accepted-route":
                require(set(reference) == {"kind", "route"}, "unsupported accepted route claim")
                observed.append(accepted_route_observation(value, root, reference["route"]))
            elif reference.get("kind") == "typed-function":
                require(set(reference) == {"kind", "prefix"} and reference["prefix"], "unsupported typed claim")
                matches = [r for r in typed["cases"] if r["id"].startswith(reference["prefix"])]
                require(matches, "missing typed failure binding")
                observed.extend(typed_failure_observation(r) for r in matches)
            else:
                raise ValueError("unsupported failure evidence kind/accepted claim")
        fresh_rows = [fresh[s["id"]] for s in row["subcases"] if fresh and s["id"] in fresh]
        observed.extend(fresh_rows)
        remaining = [s["id"] for s in row["subcases"] if not fresh or s["id"] not in fresh or
                     fresh[s["id"]].get("complete") is False]
        cases.append(dict(id=row["id"], state="verified-boundary" if not remaining else
                          ("partial" if observed else "missing"),
                          expected_disposition=row["expected_disposition"], observations=observed,
                          residual_subcase_ids=remaining,
                          missing_evidence=[dict(id=s["id"], input=s["input"], expected=s["expected"],
                              prerequisite=("Go supervisor fixture with raw error/result, child exit/kill, cache and restart observations"
                                            if row["id"].startswith("F-PROCESS/") else
                                            "source-linked fixture execution with its declared real-model/test-double scope and raw disposition"))
                                            for s in row["subcases"] if s["id"] in remaining]))
    return dict(schema="cairn.semantic-refusal-evidence/1", cases=cases,
                reused_historical_ids=sorted(historical_ids), typed_function_count=len(typed["cases"]),
                supporting_go_tests=go, go_tuple_scope="named test outcomes only; not observed per-case tuples",
                remaining_ids=[row["id"] for row in cases if row["residual_subcase_ids"]],
                complete=all(not row["residual_subcase_ids"] for row in cases),
                separate_phases={name: value["failure_families"][name] for name in ("F-PACKAGE", "F-REBUILD")})


def verify_manifest(path):
    document = load(path)
    files = document["files"]
    if isinstance(files, dict):
        rows = list(files.items())
    else:
        rows = [(row["name"], row["sha256"]) for row in files]
    require(rows and len({row[0] for row in rows}) == len(rows), "empty/duplicate artifact manifest")
    for relative, expected in rows:
        pinned(path.parent, relative, expected)
    # The package's stream traces are a separate named section of its seal.
    for relative, expected in document.get("stream_trace_files", {}).items():
        pinned(path.parent / "attempt-02", relative, expected)
    return len(rows) + len(document.get("stream_trace_files", {}))


def verify_assets(value, root):
    return {name: str(pinned(root, name, expected))
            for name, expected in value["assets_sha256"].items()}


def python_events(raw, expected):
    documents = []
    for line in raw.splitlines():
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict) and document.get("schema") == "cairn.semantic-test-events/1":
            documents.append(document)
    require(len(documents) == 1, "exactly one actual unittest outcome required")
    document = documents[0]
    require(type(document.get("tests_ran")) is int, "invalid unittest count")
    require(document.get("successful") is True and document.get("skipped") == [],
            "invalid or unsuccessful unittest outcome")
    require(not document.get("expected_failures", []) and not document.get("unexpected_successes", []),
            "expected failure is not passing required coverage")
    try:
        return verify_python_events(document, expected)
    except ComparisonFailure as error:
        raise ValueError(str(error)) from error


def command_result(result, expected, kind):
    require(result.returncode == 0, "required test command failed")
    if kind == "python":
        return python_events(result.stdout, expected)
    try:
        summary = verify_go_events(result.stdout, expected)
    except ComparisonFailure as error:
        raise ValueError(str(error)) from error
    events = [json.loads(line) for line in result.stdout.splitlines() if line]
    require(all(row.get("Package") == "semantic" for row in events), "unexpected Go package")
    require(any(row.get("Action") == "pass" and "Test" not in row for row in events),
            "Go package completion missing")
    return summary


DRIVER = r'''
import json,sys,unittest
sys.path[:0]=[sys.argv[1],sys.argv[2]]
ids=[];skipped=[]
class Result(unittest.TextTestResult):
 def startTest(self,test):
  ids.append(test.id());super().startTest(test)
 def addSkip(self,test,reason):
  skipped.append({'id':test.id(),'reason':reason});super().addSkip(test,reason)
suite=unittest.TestLoader().loadTestsFromNames(json.loads(sys.argv[3]))
result=unittest.TextTestRunner(resultclass=Result,verbosity=2).run(suite)
print(json.dumps(dict(schema='cairn.semantic-test-events/1',ids=ids,skipped=skipped,
 tests_ran=result.testsRun,successful=result.wasSuccessful(),
 expected_failures=[test.id() for test,_ in result.expectedFailures],
 unexpected_successes=[test.id() for test in result.unexpectedSuccesses])))
sys.exit(0 if result.wasSuccessful() else 1)
'''


TYPED_DRIVER = r'''
import json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from tokenizers import Tokenizer,AddedToken
from embedding_worker import Embeddings
from semantic_fixture_cases import embedding_failure_cases
tokenizer=Tokenizer.from_file(sys.argv[3]);tokenizer.no_truncation();tokenizer.no_padding()
for value in json.loads(Path(sys.argv[3]).with_name('special_tokens_map.json').read_text()).values():
 tokenizer.add_special_tokens([value if isinstance(value,str) else AddedToken(**value)])
worker=Embeddings.__new__(Embeddings);worker.scorer=SimpleNamespace(tokenizer=tokenizer);worker.identity={}
calls=[]
def no_inference(texts):
 calls.append(texts);raise AssertionError('typed refusal attempted model inference')
worker.vectors=no_inference
rows=[]
for case in embedding_failure_cases():
 try:
  worker.respond({k:case[k] for k in ('id','operation','text')})
 except ValueError as error:
  rows.append(dict(id=case['id'],expected_exception=type(error).__name__,expected_message=str(error),
   scope='function/no-model',inference_calls=len(calls),result=None))
 else:
  raise AssertionError('typed input unexpectedly accepted: '+case['id'])
print(json.dumps(dict(schema='cairn.semantic-typed-refusals/1',cases=rows)))
'''


def typed_events(raw, value):
    document = json.loads(raw)
    require(document.get("schema") == "cairn.semantic-typed-refusals/1" and
            document.get("cases") == value["typed_failures"], "typed refusal tuple/count/order mismatch")
    return dict(cases=len(document["cases"]), inference_calls=0, scope="function/no-model")


def verify_runtime(value, binding_path, site, python_home):
    require(digest(binding_path) == value["retained"]["runtime-binding"]["sha256"],
            "reviewed runtime binding required")
    binding = load(binding_path)
    counts = {}
    for key, count in (("frozen_loaded_module_files", 481), ("frozen_numeric_native_files", 7)):
        rows = binding[key]
        require(len(rows) == count, "runtime inventory count changed")
        for row in rows:
            pinned(site, row["relative"], row["sha256"])
        counts[key] = count
    pinned(python_home, "bin/python3.11", value["python_sha256"])
    return counts


def native_executable(path):
    with path.open("rb") as stream:
        require(stream.read(4) == b"\x7fELF", "native Go executables required")


def tool_pins():
    return {name: digest(PROJECT / "scripts" / name)
            for name in ("check_semantic_reference.py", "test_check_semantic_reference.py",
                         "semantic_refusal_processes.py", "semantic_refusal_fixture.py",
                         "test_semantic_refusal_processes.py",
                         "semantic_refusal_source.py", "test_semantic_refusal_source.py",
                         "semantic_refusal_functions.py", "test_semantic_refusal_functions.py",
                         "semantic_refusal_function_evidence.py",
                         "semantic_refusal_native_residuals.py", "semantic_refusal_source_residuals.py",
                         "semantic_refusal_residual_fixture.py", "test_semantic_refusal_residuals.py",
                         "testdata/semantic-refusal-residual-driver/main.go",
                         "testdata/semantic-refusal-residual-driver/main_test.go",
                         "testdata/semantic-refusal-driver/main.go",
                         "testdata/semantic-refusal-driver/main_test.go")}


def run_tests(args, value):
    """Only fixed selectors; model execution and arbitrary candidate argv are absent."""
    assets = verify_assets(value, args.assets)
    runtime = verify_runtime(value, args.runtime_binding, args.site, args.python_home)
    for executable in (args.go_binary, args.go_test2json):
        native_executable(executable)
    output = args.output.absolute()
    require(not output.exists(), "test output must be fresh")
    env = dict(WORKER_ENV, CAIRN_EMBEDDING_TOKENIZER=assets["tokenizer.json"])
    require(not set(value["selectors_absent"]) & set(env), "ambient worker/database selector")
    mounts = [PROJECT, args.python_home, args.site, args.assets,
              args.go_binary, args.go_test2json]
    report = dict(schema="cairn.semantic-reference-tests/1", inventory_sha256=digest(INVENTORY),
                  sources=value["source_sha256"], runtime=runtime, commands={}, results={}, exit_codes={},
                  tool_sha256=tool_pins())
    try:
        with SemanticIsolation(read_only_paths=mounts, output_dir=output) as isolated:
            for phase in ("prepared", "mechanics", "go"):
                expected = value["go_process" if phase == "go" else "python_" + phase]
                if phase == "prepared" and args.prepared_report:
                    previous_path = args.prepared_report
                    require(args.prepared_report_sha256 and digest(previous_path) == args.prepared_report_sha256,
                            "pinned prepared report required")
                    previous = load(previous_path)
                    require(previous["sources"] == value["source_sha256"], "prepared source binding changed")
                    raw = pinned(previous_path.parent, "prepared.stdout", previous["logs_sha256"]["prepared.stdout"]).read_bytes()
                    result = SimpleNamespace(returncode=previous["exit_codes"]["prepared"], stdout=raw)
                    report["results"][phase] = command_result(result, expected, "python")
                    report["commands"][phase] = previous["commands"][phase]
                    report["exit_codes"][phase] = result.returncode
                    (output / "prepared.stdout").write_bytes(raw)
                    (output / "prepared.stderr").write_bytes((previous_path.parent / "prepared.stderr").read_bytes())
                    report["prepared_reuse"] = dict(report_sha256=args.prepared_report_sha256,
                                                   inventory_sha256=previous["inventory_sha256"],
                                                   reason="same 23 actual names and exact source bindings; no rerun")
                    continue
                if phase == "go":
                    argv = [str(args.go_test2json), "-t", "-p", "semantic", str(args.go_binary),
                            "-test.v=test2json", "-test.run", "^(" + "|".join(expected) + ")$"]
                else:
                    argv = [str(args.python_home / "bin/python3.11"), "-I", "-S", "-B", "-c",
                            DRIVER, str(PROJECT / "scripts"), str(args.site), json.dumps(expected)]
                result = isolated.run(argv, env=env, timeout=240, stdout_limit=4194304,
                                      role="S01-reference-tests", mode=phase)
                (output / (phase + ".stdout")).write_bytes(result.stdout)
                (output / (phase + ".stderr")).write_bytes(result.stderr)
                report["commands"][phase] = result.invocation
                report["exit_codes"][phase] = result.returncode
                report["results"][phase] = command_result(result, expected, "go" if phase == "go" else "python")
            argv = [str(args.python_home / "bin/python3.11"), "-I", "-S", "-B", "-c", TYPED_DRIVER,
                    str(PROJECT / "scripts"), str(args.site), assets["tokenizer.json"]]
            result = isolated.run(argv, env=env, timeout=60, stdout_limit=1048576,
                                  role="S01-reference-tests", mode="typed-refusal")
            (output / "typed.stdout").write_bytes(result.stdout)
            (output / "typed.stderr").write_bytes(result.stderr)
            report["commands"]["typed"] = result.invocation
            report["exit_codes"]["typed"] = result.returncode
            require(result.returncode == 0, "typed refusal command failed")
            report["results"]["typed"] = typed_events(result.stdout, value)
    finally:
        if output.exists():
            report["logs_sha256"] = {p.name: digest(p) for p in output.glob("*.stdout")}
            report["tool_after_sha256"] = tool_pins()
            save(output / "test-report.json", report)
            require(report["tool_sha256"] == report["tool_after_sha256"], "tool changed during tests")
    return report


def verify_test_report(value, path, expected_hash):
    require(expected_hash and digest(path) == expected_hash,
            "independently pinned test report required")
    tests = load(path)
    binding=producer_binding(value,'tests',expected_hash)
    require(tests.get("inventory_sha256") == binding['inventory'], "test inventory binding changed")
    require(tests.get("sources") == value["source_sha256"], "test source binding changed")
    require(tests.get("tool_sha256") == tests.get("tool_after_sha256") == binding['tools'],
            "test checker/source binding changed")
    require(set(tests.get("results", {})) == {"prepared", "mechanics", "go", "typed"}, "missing test phase")
    for phase in ("prepared", "mechanics", "go"):
        expected = value["go_process" if phase == "go" else "python_" + phase]
        raw = pinned(path.parent, phase + ".stdout", tests["logs_sha256"][phase + ".stdout"]).read_bytes()
        result = SimpleNamespace(returncode=tests["exit_codes"][phase], stdout=raw)
        actual = command_result(result, expected, "go" if phase == "go" else "python")
        require(actual == tests["results"][phase], "test summary disagrees with actual outcomes")
    raw = pinned(path.parent, "typed.stdout", tests["logs_sha256"]["typed.stdout"]).read_bytes()
    require(tests["exit_codes"]["typed"] == 0, "typed refusal command failed")
    require(typed_events(raw, value) == tests["results"]["typed"], "typed refusal summary changed")
    return tests


def verify_characterization(value, root):
    character = root / Path(value["retained"]["python-characterization"]["relative"]).parent
    sealed = verify_sealed_bundle(character, value["characterization_seal_sha256"])
    records = {row["name"]: row for row in load(character / "captures.json")["records"]}
    for expected in value["historical_refusals"]:
        row = records[expected["id"]]
        require(row["expected_exit"] == row["exit_code"] == expected["expected_exit"],
                "historical refusal exit mismatch")
        for key, relative in (("input", "inputs/" + expected["id"] + ".stdin"),
                              ("stdout", "raw/" + expected["id"] + ".stdout"),
                              ("stderr", "raw/" + expected["id"] + ".stderr")):
            pinned(character, relative, expected[key + "_sha256"])
    return len(sealed)


def audit(value, root, tests=None, failures=None, failure_sources=None):
    for row in value["criteria"]:
        if row["state"] == "reused":
            evidence = row.get("evidence")
            require(isinstance(evidence, list) and evidence and
                    all(isinstance(key, str) and key in value["retained"] for key in evidence),
                    "unqualified reference cell: " + row["id"])
    artifacts, manifests = {}, {}
    for name, pin in value["retained"].items():
        path = pinned(root, pin["relative"], pin["sha256"])
        artifacts[name] = pin["sha256"]
        if name.endswith("manifest"):
            manifests[name] = verify_manifest(path)
    sealed_count = verify_characterization(value, root)
    result = dict(schema="cairn.semantic-reference-evidence/1", inventory_sha256=digest(INVENTORY),
                  retained_sha256=artifacts, verified_manifest_files=manifests,
                  characterization_sealed_files=sealed_count,
                  criteria=[dict(row) for row in value["criteria"]],
                  stream_reference=value["stream_reference"],
                  historical_refusals=value.get("historical_refusals", []),
                  unbound_failure_families=value.get("failure_families", {}),
                  tests=None if tests is None else tests["results"],
                  prepared_reuse=None if tests is None else tests.get("prepared_reuse"),
                  checker_sha256=tool_pins(),
                  selected_refusal_reports=failure_sources or {},
                  model_execution=False, full_s01_accepted=False)
    if "failure_ledger" in value:
        evidence = failure_evidence(value, root, failures)
        result["failure_ledger"] = evidence
        result["unbound_failure_families"] = {name: dict(row) for name, row in value["failure_families"].items()}
        for name in FAILURE_COUNTS:
            result["unbound_failure_families"][name].update(
                state="per-case-ledger", expected_refusal_tuple="see failure_ledger.cases",
                unbound_case_ids=[identifier for identifier in evidence["remaining_ids"]
                                  if identifier.startswith(name + "/")])
        for row in result["criteria"]:
            if row["id"] == "failures/s01b":
                row.update(state="verified" if evidence["complete"] else "partial",
                           evidence=["checked per-case ledger; residual IDs in failure_ledger.remaining_ids"])
    if tests is not None:
        for row in result["criteria"]:
            if row["id"] == "tests/prepared-23":
                reused = tests.get("prepared_reuse")
                evidence = (["reused named prepared tests; tokenizer is one of 23",
                             "report-sha256:" + reused["report_sha256"],
                             "original-inventory-sha256:" + reused["inventory_sha256"]]
                            if reused is not None else ["fresh named prepared tests; tokenizer is one of 23"])
                row.update(state="verified", evidence=evidence)
    result["remaining"] = [row["id"] for row in result["criteria"] if row["state"] not in ("reused", "verified")]
    result["complete"] = not result["remaining"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="list committed criteria without running anything")
    plan.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("audit", help="verify exact retained bytes; never rerun a model")
    check.add_argument("--evidence-root", type=Path, required=True)
    check.add_argument("--tests", type=Path)
    check.add_argument("--tests-sha256")
    check.add_argument("--refusals", type=Path)
    check.add_argument("--refusals-sha256")
    check.add_argument("--processes", type=Path)
    check.add_argument("--processes-sha256")
    check.add_argument("--source-refusals", type=Path)
    check.add_argument("--source-refusals-sha256")
    check.add_argument("--functions", type=Path)
    check.add_argument("--functions-sha256")
    check.add_argument("--native-residuals", type=Path)
    check.add_argument("--native-residuals-sha256")
    check.add_argument("--source-residuals", type=Path)
    check.add_argument("--source-residuals-sha256")
    check.add_argument("--reuse-accepted", action="store_true",
                       help="recheck exact accepted report bytes and unchanged actors without replaying captures")
    check.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("tests", help="run only the fixed model-free suites in the unchanged wrapper")
    for option in ("python-home", "runtime-binding", "site", "assets", "go-binary", "go-test2json", "output"):
        run.add_argument("--" + option, type=Path, required=True)
    run.add_argument("--prepared-report", type=Path)
    run.add_argument("--prepared-report-sha256")
    failures = sub.add_parser("failures", help="collect fixed missing early Python refusals without initializing a model")
    failures.add_argument("--python-home", type=Path, required=True)
    failures.add_argument("--output", type=Path, required=True)
    processes = sub.add_parser("processes", help="collect missing Go dispositions with labelled disposable process doubles")
    producer = processes.add_mutually_exclusive_group(required=True)
    producer.add_argument("--driver", type=Path)
    producer.add_argument("--reuse-report", type=Path)
    processes.add_argument("--reuse-report-sha256")
    processes.add_argument("--output", type=Path, required=True)
    source = sub.add_parser("source-failures", help="collect fixed missing source-worker controls using the pinned runtime and assets")
    for option in ("evidence-root", "python-home", "site", "runtime-binding", "assets", "output"):
        source.add_argument("--" + option, type=Path, required=True)
    source.add_argument("--reuse-capture-manifest", type=Path)
    source.add_argument("--reuse-capture-manifest-sha256")
    functions = sub.add_parser("function-refusals", help="collect remaining source-function guards with labelled external doubles")
    for option in ("python-home", "site", "runtime-binding", "assets", "output"):
        functions.add_argument("--" + option, type=Path, required=True)
    native = sub.add_parser("native-residuals", help="collect fixed Go/store and idle process-double controls")
    for option in ("build", "output"):
        native.add_argument("--" + option, type=Path, required=True)
    residual = sub.add_parser("source-residuals", help="collect five short Source controls and two labelled serializer doubles")
    for option in ("python-home", "site", "runtime-binding", "assets", "output"):
        residual.add_argument("--" + option, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = inventory()
        if args.command == "native-residuals":
            from semantic_refusal_native_residuals import collect
            collect(args,value)
            return 0
        if args.command == "source-residuals":
            from semantic_refusal_source_residuals import collect
            collect(args,value)
            return 0
        if args.command == "function-refusals":
            from semantic_refusal_function_evidence import collect
            collect(args,value)
            return 0
        if args.command == "source-failures":
            from semantic_refusal_source import collect
            report = collect(args, value)
            return 2 if report["failed"] else 0
        if args.command == "processes":
            from semantic_refusal_processes import collect
            report = collect(args, value)
            return 2 if report["failed"] else 0
        if args.command == "failures":
            collect_failures(args, value)
            return 0
        if args.command == "tests":
            run_tests(args, value)
            return 0
        if args.command == "plan":
            report = dict(schema="cairn.semantic-reference-plan/1", criteria=value["criteria"],
                          stream_reference=value["stream_reference"],
                          failure_ledger=failure_inventory(value), complete=False)
        else:
            if args.reuse_accepted:
                require(not any((args.tests,args.refusals,args.processes,args.source_refusals)),
                        "accepted report selectors cannot be mixed with replacement selectors")
                baseline=accepted_baseline(value)
                for kind,option in (("tests","tests"),("early","refusals"),("process","processes"),("source","source_refusals")):
                    pin=baseline['reports'][kind]
                    setattr(args,option,pinned(args.evidence_root,pin['relative'],pin['sha256']))
                    setattr(args,option+'_sha256',pin['sha256'])
            tests = None
            if args.tests:
                tests = verify_test_report(value, args.tests, args.tests_sha256)
            failures = None
            if args.refusals:
                failures = verify_early_failures(value, args.refusals, args.refusals_sha256)
            if args.processes:
                from semantic_refusal_processes import verify
                processes = verify(value, args.processes, args.processes_sha256)
                failures = dict(failures or {}, **processes)
            if args.source_refusals:
                from semantic_refusal_source import verify
                source = verify(value, args.evidence_root, args.source_refusals, args.source_refusals_sha256)
                require(not set(source) & set(failures or {}), "overlapping refusal scopes need an explicit combined obligation")
                failures = dict(failures or {}, **source)
            if args.functions:
                from semantic_refusal_function_evidence import verify
                functions=verify(value,args.evidence_root,args.functions,args.functions_sha256)
                require(not set(functions)&set(failures or {}),'overlapping function obligations')
                failures=dict(failures or {},**functions)
            if args.native_residuals:
                from semantic_refusal_native_residuals import verify
                native=verify(value,args.native_residuals,args.native_residuals_sha256)
                require(not set(native)&set(failures or {}),'overlapping native residual obligations')
                failures=dict(failures or {},**native)
            if args.source_residuals:
                from semantic_refusal_source_residuals import verify
                residual=verify(value,args.evidence_root,args.source_residuals,args.source_residuals_sha256)
                require(not set(residual)&set(failures or {}),'overlapping Source residual obligations')
                failures=dict(failures or {},**residual)
            sources = {kind: dict(path=str(path), sha256=sha) for kind, path, sha in (
                ("early", args.refusals, args.refusals_sha256),
                ("Go-process", args.processes, args.processes_sha256),
                ("source-model", args.source_refusals, args.source_refusals_sha256)) if path}
            if args.functions:
                sources['functions']=dict(path=str(args.functions),sha256=args.functions_sha256)
            for kind,path,sha in [('native-residuals',args.native_residuals,args.native_residuals_sha256),
                                  ('source-residuals',args.source_residuals,args.source_residuals_sha256)]:
                if path:sources[kind]=dict(path=str(path),sha256=sha)
            report = audit(value, args.evidence_root, tests, failures, sources)
            if args.reuse_accepted:
                report['accepted_report_reuse']=dict(revision=baseline['revision'],review_record=baseline['review_record'],
                    reports=baseline['reports'],scope='reused exact accepted bytes; unchanged actors and raw tuples rechecked; no captures replayed')
        require(not args.output.exists(), "report output already exists")
        save(args.output, report)
        return 0 if report["complete"] else 3
    except (ValueError, OSError, KeyError, TypeError, IsolationError, EvidenceError) as error:
        print(json.dumps(dict(status="refused", error=str(error))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
