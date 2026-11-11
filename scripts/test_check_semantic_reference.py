"""Refusal controls for the S-01 evidence boundary; no model inference."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_semantic_reference as reference
from compare_semantic_workers import validate_embedding, validate_rank
from semantic_acceptance_isolation import WORKER_ENV


class ReferenceRunnerTest(unittest.TestCase):
    def python(self, names):
        return dict(schema="cairn.semantic-test-events/1", ids=names, skipped=[],
                    tests_ran=len(names), successful=True)

    def result(self, document, code=0):
        return SimpleNamespace(returncode=code, stdout=(json.dumps(document) + "\n").encode())

    def go(self, names):
        rows = [dict(Action=action, Test=name, Package="semantic") for name in names for action in ("run", "pass")]
        rows.append(dict(Action="pass", Package="semantic"))
        return SimpleNamespace(returncode=0, stdout=b"\n".join(json.dumps(r).encode() for r in rows))

    def test_committed_inventory_has_23_names_and_tokenizer_is_one_of_them(self):
        value = reference.inventory()
        self.assertEqual(len(value["python_prepared"]), 23)
        self.assertIn(value["actual_tokenizer"], value["python_prepared"])
        self.assertEqual(len(value["go_process"]), 21)

    def test_external_inventory_cannot_remove_a_required_selector(self):
        value = reference.load(reference.INVENTORY)
        value["python_prepared"].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            reference.save(path, value)
            with self.assertRaisesRegex(ValueError, "committed inventory"):
                reference.inventory(path)

    def test_missing_changed_and_symlinked_assets_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "asset"
            path.write_bytes(b"original")
            pin = reference.digest(path)
            self.assertEqual(reference.pinned(root, "asset", pin), path)
            path.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                reference.pinned(root, "asset", pin)
            path.unlink()
            with self.assertRaisesRegex(ValueError, "missing regular"):
                reference.pinned(root, "asset", pin)
            (root / "actual").write_bytes(b"original")
            path.symlink_to(root / "actual")
            with self.assertRaisesRegex(ValueError, "missing regular"):
                reference.pinned(root, "asset", pin)

    def test_artifact_path_cannot_escape_selected_root(self):
        for relative in ("../outside", "/absolute"):
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "selected root"):
                reference.pinned(Path("/unused"), relative, "a" * 64)

    def test_score_span_order_and_projection_mutations_break_the_retained_pin(self):
        original = dict(scores=[1, 2], spans=[dict(offset=0, length=2)], order=["a", "b"],
                        projection=dict(prefix_bytes=2))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.json"
            reference.save(path, original)
            pin = reference.digest(path)
            for key, replacement in (("scores", [1, 3]), ("spans", [dict(offset=1, length=2)]),
                                     ("order", ["b", "a"]), ("projection", dict(prefix_bytes=1))):
                mutated = copy.deepcopy(original)
                mutated[key] = replacement
                reference.save(path, mutated)
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, "hash mismatch"):
                    reference.pinned(path.parent, path.name, pin)

    def test_python_skipped_class_zero_missing_and_duplicate_outcomes_refuse(self):
        names = ["Class.test_one", "Class.test_two"]
        for mutation in (dict(skipped=[names[0]]), dict(tests_ran=0),
                         dict(ids=names[:1]), dict(ids=[names[0], names[0]]), dict(successful=False)):
            document = self.python(names)
            document.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                reference.command_result(self.result(document), names, "python")

    def test_python_extra_outcome_and_boolean_count_refuse(self):
        document = self.python(["Class.test_one"])
        for raw in (self.result(document).stdout * 2,
                    json.dumps(dict(document, tests_ran=True)).encode()):
            with self.assertRaises(ValueError):
                reference.python_events(raw, ["Class.test_one"])
        for change in (dict(successful=1), dict(skipped=False)):
            with self.assertRaises(ValueError):
                reference.python_events(json.dumps(dict(document, **change)).encode(), ["Class.test_one"])

    def test_actual_python_outcome_is_required_instead_of_ok_text(self):
        with self.assertRaisesRegex(ValueError, "actual unittest"):
            reference.python_events(b"Ran 23 tests\nOK\n", ["Class.test_one"])

    def test_command_failure_cannot_be_hidden_by_passing_outcomes(self):
        with self.assertRaisesRegex(ValueError, "command failed"):
            reference.command_result(self.result(self.python(["Class.test_one"]), 1),
                                     ["Class.test_one"], "python")

    def test_go_skip_fail_zero_missing_and_duplicate_tests_refuse(self):
        names = ["TestOne", "TestTwo"]
        valid = self.go(names)
        for actual in (self.go([]), self.go(names[:1]), self.go([names[0], names[0]])):
            with self.assertRaises(ValueError):
                reference.command_result(actual, names, "go")
        for action in ("skip", "fail"):
            actual = SimpleNamespace(returncode=0, stdout=valid.stdout + b"\n" +
                                     json.dumps(dict(Action=action, Test="TestOne/child")).encode())
            with self.assertRaises(ValueError):
                reference.command_result(actual, names, "go")

    def test_go_requires_package_completion(self):
        result = self.go(["TestOne"])
        result.stdout = b"\n".join(result.stdout.splitlines()[:-1])
        with self.assertRaisesRegex(ValueError, "completion missing"):
            reference.command_result(result, ["TestOne"], "go")

    def test_named_actual_outcomes_pass(self):
        self.assertEqual(reference.command_result(self.result(self.python(["Class.test_one"])),
                                                 ["Class.test_one"], "python")["tests_ran"], 1)
        self.assertEqual(reference.command_result(self.go(["TestOne"]), ["TestOne"], "go")["tests_ran"], 1)

    def test_duplicate_and_nonfinite_json_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for text in ('{"a":1,"a":2}', '{"value":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    reference.load(path)

    def test_missing_or_duplicate_manifest_artifact_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for files in ([], [dict(name="missing", sha256="a" * 64)],
                          [dict(name="same", sha256="a" * 64)] * 2):
                reference.save(path, dict(files=files))
                with self.assertRaises(ValueError):
                    reference.verify_manifest(path)

    def test_manifest_counts_and_verifies_separate_native_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "artifact").write_bytes(b"artifact")
            (root / "attempt-02").mkdir()
            trace = root / "attempt-02/trace"
            trace.write_bytes(b"native trace")
            manifest = root / "manifest.json"
            reference.save(manifest, dict(files={"artifact": reference.digest(root / "artifact")},
                                          stream_trace_files={"trace": reference.digest(trace)}))
            self.assertEqual(reference.verify_manifest(manifest), 2)
            trace.unlink()
            with self.assertRaisesRegex(ValueError, "missing regular"):
                reference.verify_manifest(manifest)

    def test_test_summary_needs_pinned_raw_outcomes(self):
        value = reference.load(reference.INVENTORY)
        document = dict(inventory_sha256=reference.digest(reference.INVENTORY),
                        sources=value["source_sha256"],
                        results={phase: dict(tests_ran=23, skipped=0) for phase in ("prepared", "mechanics", "go", "typed")},
                        logs_sha256={"prepared.stdout": "a" * 64})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test-report.json"
            reference.save(path, document)
            with self.assertRaisesRegex(ValueError, "missing regular"):
                reference.verify_test_report(value, path, reference.digest(path))

    def test_missing_changed_or_reordered_typed_refusal_fails(self):
        value = reference.load(reference.INVENTORY)
        cases = value["typed_failures"]
        for actual in (cases[:-1], list(reversed(cases)), [dict(cases[0], inference_calls=1)] + cases[1:]):
            raw = json.dumps(dict(schema="cairn.semantic-typed-refusals/1", cases=actual)).encode()
            with self.assertRaisesRegex(ValueError, "tuple/count/order"):
                reference.typed_events(raw, value)

    def test_held_missing_and_quality_gaps_remain_incomplete(self):
        value = dict(retained={}, criteria=[dict(id="oracle/punctuation", state="held", evidence=[]),
                     dict(id="quality", state="decision-required", evidence=[]),
                     dict(id="failure", state="missing", evidence=[])], stream_reference={})
        with patch.object(reference, "verify_characterization", return_value=0):
            report = reference.audit(value, Path("/unused"))
        self.assertFalse(report["complete"])
        self.assertFalse(report["full_s01_accepted"])
        self.assertEqual(report["remaining"], ["oracle/punctuation", "quality", "failure"])


class NativeReferenceMechanicsTest(unittest.TestCase):
    """Executed inside the caller's wrapper; never start a nested namespace."""

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        source = reference.PROJECT / "semantic/testdata/executable-harness/protocol-worker.c"
        cls.workers = {}
        for name, mode, identity in (("rank-reference", 1, "a" * 64),
                                      ("rank-candidate", 1, "b" * 64),
                                      ("stream", 2, "a" * 64), ("embed", 3, "a" * 64)):
            path = cls.root / name
            macros = dict(MODE=mode, MODEL=identity,
                          QUERY_HASH=hashlib.sha256(b"query").hexdigest())
            subprocess.run(["/usr/bin/gcc", *[f"-D{k}={json.dumps(v)}" for k, v in macros.items()],
                            str(source), "-o", str(path)], check=True, capture_output=True)
            cls.workers[name] = path

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_native(self, mode, frames):
        path = self.workers[mode]
        self.assertEqual(path.read_bytes()[:4], b"\x7fELF")
        env = dict(WORKER_ENV, CAIRN_SEMANTIC_STREAM_CACHE="1")
        raw = b"".join(json.dumps(frame, separators=(",", ":")).encode() + b"\n" for frame in frames)
        return subprocess.run([str(path)], input=raw, capture_output=True, env=env, timeout=25)

    def test_native_rank_direct_path_and_distinct_identities(self):
        case = reference.load(reference.PROJECT / "semantic/testdata/executable-harness/reference-inventory.json")["protocol_fixture"]["rank"]
        results = []
        for mode, identity in (("rank-reference", "a" * 64), ("rank-candidate", "b" * 64)):
            done = self.run_native(mode, [case])
            self.assertEqual(done.returncode, 0, done.stderr)
            reply = json.loads(done.stdout)
            validate_rank(reply, case, identity)
            results.append(reply["scores"])
        self.assertEqual(results[0], results[1])

    def test_native_stream_repeats_and_fresh_restore(self):
        first = self.run_native("stream", [dict(id="one", query="query"), dict(id="two", query="query")])
        self.assertEqual(first.returncode, 0, first.stderr)
        frames = [json.loads(line) for line in first.stdout.splitlines()]
        self.assertEqual([r["id"] for r in frames], ["one", "two"])
        self.assertEqual(frames[0]["result"], frames[1]["result"])
        restored = self.run_native("stream", [dict(id="fresh", query="query", cache=frames[-1]["cache"])])
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["result"], frames[0]["result"])

    def test_native_embedding_projection_and_original_span(self):
        cases = [dict(id="q", operation="query", text="query", expected="accepted"),
                 dict(id="d", operation="document", text="body", expected="accepted")]
        done = self.run_native("embed", [{k: case[k] for k in ("id", "operation", "text")} for case in cases])
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(len(rows), 2)
        for case, row in zip(cases, rows, strict=True):
            validate_embedding(row, case, "a" * 64)
        self.assertEqual(rows[0]["query_projection"]["prefix_sha256"], hashlib.sha256(b"query").hexdigest())
        self.assertEqual(rows[1]["passages"][0]["span"], dict(offset=0, length=4))

    def test_native_refusal_tuple(self):
        done = self.run_native("stream", [dict(id="refusal", query="refuse")])
        self.assertEqual((done.returncode, done.stdout, done.stderr), (7, b"", b"fixture refused\n"))

    def test_native_missing_id_refuses_without_output(self):
        done = self.run_native("stream", [dict(query="query")])
        self.assertEqual((done.returncode, done.stdout, done.stderr), (2, b"", b""))

    def test_native_fixture_rejects_extra_arguments(self):
        done = subprocess.run([str(self.workers["rank-candidate"]), "extra"], capture_output=True, timeout=25)
        self.assertEqual(done.returncode, 91)


if __name__ == "__main__":
    unittest.main()
