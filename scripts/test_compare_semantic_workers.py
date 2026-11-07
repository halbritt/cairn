import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import compare_semantic_workers as comparison
from semantic_acceptance_isolation import SemanticIsolation


class ExecutableComparisonTest(unittest.TestCase):
    def test_native_metadata_span_and_projection_mismatches_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = dict(
                query="query",
                notes=[
                    dict(
                        record_id="fixture",
                        version=1,
                        body="body",
                        body_sha256=hashlib.sha256(b"body").hexdigest(),
                    )
                ],
            )
            cases = [
                dict(id="q", operation="query", text="query", expected="accepted"),
                dict(id="d", operation="document", text="body", expected="accepted"),
            ]
            reference_rank = self.compile_worker(root, "reference-rank", 1, "a" * 64)
            reference_embed = self.compile_worker(root, "reference-embed", 3, "a" * 64)
            for index, (mode, defines, message) in enumerate(
                (
                    (1, dict(VERSION=2), "metadata"),
                    (3, dict(SPAN_LENGTH=3), "span"),
                    (3, dict(TOKENS=4), "projection"),
                )
            ):
                candidate = self.compile_worker(
                    root, "candidate-" + str(index), mode, "b" * 64, **defines
                )
                with (
                    self.subTest(defines=defines),
                    SemanticIsolation(
                        read_only_paths=[root], output_dir=root / ("out-" + str(index))
                    ) as isolation,
                ):
                    with self.assertRaisesRegex(comparison.ComparisonFailure, message):
                        if mode == 1:
                            comparison.compare_rank_case(
                                reference_rank,
                                candidate,
                                request,
                                isolation=isolation,
                                reference_identity="a" * 64,
                                candidate_identity="b" * 64,
                            )
                        else:
                            comparison.compare_embedding_cases(
                                reference_embed,
                                candidate,
                                cases,
                                isolation=isolation,
                                reference_identity="a" * 64,
                                candidate_identity="b" * 64,
                            )

    def compile_worker(self, root, name, mode, identity, **defines):
        source = (
            Path(__file__).resolve().parents[1]
            / "semantic/testdata/executable-harness/protocol-worker.c"
        )
        macros = dict(
            MODE=mode,
            MODEL=identity,
            BODY_HASH=hashlib.sha256(b"body").hexdigest(),
            QUERY_HASH=hashlib.sha256(b"query").hexdigest(),
            **defines,
        )
        path = root / name
        subprocess.run(
            [
                "gcc",
                *[f"-D{key}={json.dumps(value)}" for key, value in macros.items()],
                str(source),
                "-o",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        return path

    def test_mechanics_cli_runs_all_modes_and_marks_score_mismatch_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifests = []
            for label, identity in (("reference", "a" * 64), ("candidate", "b" * 64)):
                workers = {}
                for number, mode in enumerate(("rank", "stream", "embed"), 1):
                    path = self.compile_worker(
                        root, label + "-" + mode, number, identity
                    )
                    workers[mode] = dict(
                        path=str(path),
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        model_sha256=identity,
                    )
                manifest = root / (label + ".json")
                manifest.write_text(
                    json.dumps(
                        dict(
                            schema="cairn.semantic-comparison-worker/1",
                            kind="protocol-fixture",
                            workers=workers,
                            assets=[],
                        )
                    )
                )
                manifests.append(manifest)
            script = Path(__file__).with_name("compare_semantic_workers.py")
            inventory = (
                Path(__file__).resolve().parents[1]
                / "semantic/testdata/executable-harness/reference-inventory.json"
            )

            def run(output):
                return subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-B",
                        str(script),
                        "--phase",
                        "mechanics",
                        "--output",
                        str(output),
                        "--inventory",
                        str(inventory),
                        "--baseline-manifest",
                        str(manifests[0]),
                        "--candidate-manifest",
                        str(manifests[1]),
                    ],
                    capture_output=True,
                    timeout=20,
                )

            completed = run(root / "accepted")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads((root / "accepted/report.json").read_text())
            self.assertFalse(report["model_acceptance"])
            self.assertEqual(report["passed_modes"], ["rank", "stream", "embed"])
            changed = self.compile_worker(root, "wrong-score", 1, "b" * 64, SCORE=43)
            manifest = json.loads(manifests[1].read_text())
            manifest["workers"]["rank"].update(
                path=str(changed),
                sha256=hashlib.sha256(changed.read_bytes()).hexdigest(),
            )
            manifests[1].write_text(json.dumps(manifest))
            failed = run(root / "failed")
            self.assertNotEqual(failed.returncode, 0)
            failure = json.loads((root / "failed/report.json").read_text())
            self.assertIn("integer score mismatch", failure["failures"][0]["error"])
            evidence = failure["failures"][0]["evidence"]
            self.assertEqual(evidence["candidate"]["exit_code"], 0)
            raw = evidence["candidate"]["stdout"]
            self.assertEqual(
                (root / "failed" / raw["artifact"]).read_bytes()[-1:], b"\n"
            )

    def test_refusal_preserves_frames_stderr_and_exit_and_reaps_native_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = self.compile_worker(root, "refusing-stream", 2, "a" * 64)
            request = dict(query="refuse", notes=[])
            with SemanticIsolation(
                read_only_paths=[root], output_dir=root / "out"
            ) as isolation:
                with self.assertRaises(comparison.ComparisonFailure) as caught:
                    comparison.stream_rounds(
                        isolation, [str(worker)], request, "a" * 64, False
                    )
            evidence = caught.exception.evidence
            self.assertEqual(evidence["exit_code"], 7)
            self.assertEqual(evidence["stdout_frames"], [])
            self.assertIn(b"refused", evidence["stderr"])

    def test_skipped_zero_missing_and_duplicate_test_inventories_fail(self):
        expected = ["TestReal"]
        for events in (
            [],
            [dict(Action="skip", Test="TestReal")],
            [dict(Action="run", Test="Other"), dict(Action="pass", Test="Other")],
            [dict(Action="run", Test="TestReal")] * 2
            + [dict(Action="pass", Test="TestReal")],
        ):
            with (
                self.subTest(events=events),
                self.assertRaises(comparison.ComparisonFailure),
            ):
                comparison.verify_go_events(
                    b"\n".join(json.dumps(e).encode() for e in events), expected
                )
        for changed in (
            dict(tests_ran=0),
            dict(skipped=["Case.test"]),
            dict(ids=["Wrong.test"]),
        ):
            report = dict(tests_ran=1, skipped=[], successful=True, ids=["Case.test"])
            report.update(changed)
            with (
                self.subTest(report=report),
                self.assertRaises(comparison.ComparisonFailure),
            ):
                comparison.verify_python_events(report, ["Case.test"])

    def test_missing_assets_cells_and_legacy_qualification_fail_before_a_worker_starts(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = self.compile_worker(root, "worker", 1, "a" * 64)
            entry = dict(
                path=str(worker),
                sha256=hashlib.sha256(worker.read_bytes()).hexdigest(),
                model_sha256="a" * 64,
            )
            document = dict(
                schema="cairn.semantic-comparison-worker/1",
                kind="model",
                workers={mode: entry for mode in ("rank", "stream", "embed")},
                assets=[],
            )
            manifest = root / "worker.json"
            manifest.write_text(json.dumps(document))
            script = Path(__file__).with_name("compare_semantic_workers.py")

            def run(output, extra=()):
                return subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-B",
                        str(script),
                        "--output",
                        str(output),
                        "--phase",
                        "reference",
                        "--baseline-manifest",
                        str(manifest),
                        "--candidate-manifest",
                        str(manifest),
                        *extra,
                    ],
                    capture_output=True,
                    timeout=10,
                )

            result = run(root / "missing-assets")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "missing-assets/isolation").exists())
            document["assets"] = [dict(path=str(worker), sha256=entry["sha256"])]
            manifest.write_text(json.dumps(document))
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(dict(schema="cairn.semantic-reference-catalog/1", cells=[]))
            )
            result = run(root / "missing-cells", ("--reference-catalog", str(catalog)))
            self.assertNotEqual(result.returncode, 0)
            report = json.loads((root / "missing-cells/report.json").read_text())
            self.assertIn("incomplete reference cells", report["failures"][0]["error"])
            self.assertFalse((root / "missing-cells/isolation").exists())
            legacy = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-B",
                    str(script),
                    "--output",
                    str(root / "legacy"),
                    "--python",
                    "/usr/bin/python3",
                    "--model-dir",
                    str(root),
                    "--baseline",
                    str(worker),
                    "--candidate",
                    str(worker),
                ],
                capture_output=True,
                timeout=10,
            )
            self.assertNotEqual(legacy.returncode, 0)
            self.assertIn(b"raw-output qualification", legacy.stderr)

    def test_pairwise_cosine_failure_is_independent_from_vector_angle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workers = []
            for name, identity, extra in (
                ("reference", "a" * 64, {}),
                ("candidate", "b" * 64, dict(QUERY_Y=0.001, DOCUMENT_X=0.001)),
            ):
                workers.append(self.compile_worker(root, name, 3, identity, **extra))
            cases = [
                dict(id="q", operation="query", text="query", expected="accepted"),
                dict(id="d", operation="document", text="body", expected="accepted"),
            ]
            with SemanticIsolation(
                read_only_paths=[root], output_dir=root / "out"
            ) as isolation:
                with self.assertRaisesRegex(
                    comparison.ComparisonFailure, "pairwise cosine"
                ):
                    comparison.compare_embedding_cases(
                        *workers,
                        cases,
                        isolation=isolation,
                        reference_identity="a" * 64,
                        candidate_identity="b" * 64,
                    )

    def test_repeated_stream_frames_and_fresh_cache_restore_execute_native_workers(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = hashlib.sha256(b"body").hexdigest()
            workers = [
                self.compile_worker(root, name, 2, identity)
                for name, identity in (("reference", "a" * 64), ("candidate", "b" * 64))
            ]
            request = dict(
                query="query",
                notes=[
                    dict(
                        record_id="fixture", version=1, body="body", body_sha256=digest
                    )
                ],
            )
            with SemanticIsolation(
                read_only_paths=[root], output_dir=root / "out"
            ) as isolation:
                report = comparison.compare_stream_case(
                    *workers,
                    request,
                    isolation=isolation,
                    reference_identity="a" * 64,
                    candidate_identity="b" * 64,
                )
            self.assertEqual(report["successful_replies"], 6)
            self.assertTrue(report["fresh_restore_equal"])

    def test_direct_elf_candidate_and_distinct_manifest_identities_preserve_scores(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = hashlib.sha256(b"body").hexdigest()
            workers = [
                self.compile_worker(root, name, 1, identity)
                for name, identity in (("reference", "a" * 64), ("candidate", "b" * 64))
            ]
            self.assertTrue(
                all(path.read_bytes()[:4] == b"\x7fELF" for path in workers)
            )
            request = dict(
                query="query",
                notes=[
                    dict(
                        record_id="fixture", version=1, body="body", body_sha256=digest
                    )
                ],
            )
            with SemanticIsolation(
                read_only_paths=[root], output_dir=root / "out"
            ) as isolation:
                report = comparison.compare_rank_case(
                    *workers,
                    request,
                    isolation=isolation,
                    reference_identity="a" * 64,
                    candidate_identity="b" * 64,
                )
            self.assertTrue(report["exact_scores_equal"])
            self.assertEqual(report["candidate"]["argv"], [str(workers[1])])
            self.assertEqual(report["candidate"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
