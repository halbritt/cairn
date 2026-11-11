#!/usr/bin/env python3
"""Audit frozen S-01 evidence and run named, model-free reference tests.

This tool never captures a model reference or authorizes a held experiment.
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
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def pinned(root, relative, expected):
    relative = Path(relative)
    require(not relative.is_absolute() and ".." not in relative.parts,
            "artifact path must stay below its selected root")
    path = Path(root) / relative
    require(path.is_file() and not path.is_symlink(), "missing regular artifact: " + str(path))
    require(digest(path) == expected, "artifact hash mismatch: " + str(path))
    return path


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
    return value


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
            for name in ("check_semantic_reference.py", "test_check_semantic_reference.py")}


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
    require(tests.get("inventory_sha256") == digest(INVENTORY), "test inventory binding changed")
    require(tests.get("sources") == value["source_sha256"], "test source binding changed")
    require(tests.get("tool_sha256") == tests.get("tool_after_sha256") == tool_pins(),
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


def audit(value, root, tests=None):
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
                  model_execution=False, full_s01_accepted=False)
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
    check.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("tests", help="run only the fixed model-free suites in the unchanged wrapper")
    for option in ("python-home", "runtime-binding", "site", "assets", "go-binary", "go-test2json", "output"):
        run.add_argument("--" + option, type=Path, required=True)
    run.add_argument("--prepared-report", type=Path)
    run.add_argument("--prepared-report-sha256")
    args = parser.parse_args(argv)
    try:
        value = inventory()
        if args.command == "tests":
            run_tests(args, value)
            return 0
        if args.command == "plan":
            report = dict(schema="cairn.semantic-reference-plan/1", criteria=value["criteria"],
                          stream_reference=value["stream_reference"], complete=False)
        else:
            tests = None
            if args.tests:
                tests = verify_test_report(value, args.tests, args.tests_sha256)
            report = audit(value, args.evidence_root, tests)
        require(not args.output.exists(), "report output already exists")
        save(args.output, report)
        return 0 if report["complete"] else 3
    except (ValueError, OSError, KeyError, TypeError, IsolationError, EvidenceError) as error:
        print(json.dumps(dict(status="refused", error=str(error))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
