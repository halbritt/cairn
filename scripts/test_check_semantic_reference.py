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
    def early_report(self, root):
        value = reference.inventory()
        report = dict(schema='cairn.semantic-early-refusals/1',ledger_sha256=value['failure_ledger']['sha256'],
            sources=reference.failure_inventory(value)['source_sha256'],tool_sha256=reference.tool_pins(),
            python_sha256=value['python_sha256'],cases=[],model_execution=False)
        invocations=[]
        for index,case in enumerate(reference.early_failure_inputs()):
            prefix=str(index).zfill(3)
            command=['/fixture/bin/python3.11','-I','-S','-B','/fixture/scripts/semantic_rank.py',
                     '--model-dir','/nonexistent-s01b-model']+(['--stream'] if case['mode']=='stream' else [])
            invocation=dict(pid=index+10,start_ticks=str(index+100),role='S01b-early-source-process',mode=case['id'],
                argv=['/usr/bin/bwrap','--',*command],contract=dict(environment=dict(reference.WORKER_ENV,PWD='/')))
            invocations.append(invocation)
            for suffix,raw in [('stdin',case['input']),('stdout',b''),('stderr',(case['error']+'\n').encode())]:
                (root/(prefix+'.'+suffix)).write_bytes(raw)
            report['cases'].append(dict(id=case['id'],mode=case['mode'],prefix=prefix,exit_code=1,invocation=invocation,
                files_sha256={suffix:reference.digest(root/(prefix+'.'+suffix)) for suffix in ('stdin','stdout','stderr')}))
        reference.save(root/'isolation-proof.json',dict(wrapper_sha256=value['source_sha256']['scripts/semantic_acceptance_isolation.py'],
            profile='default',read_only_mounts=[dict(read_only=True)]))
        reference.save(root/'cleanup.json',dict(all_observed_processes_exited=True,backing_removed=True,errors=[],
            launcher_exit_codes=[0]+[1]*len(invocations),invocations=[dict(role='preflight'),*invocations],
            isolation_proof_sha256=reference.digest(root/'isolation-proof.json')))
        report['receipts_sha256']={name:reference.digest(root/name) for name in ('cleanup.json','isolation-proof.json')}
        return value,report

    def test_early_refusal_tuple_requires_every_case_and_bound_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);value,original=self.early_report(root);path=root/'report.json'
            reference.save(path,original)
            self.assertEqual(len(reference.verify_early_failures(value,path,reference.digest(path))),32)
            for mutation,message in [('missing','case IDs'),('claim','unsupported'),('exit','disposition'),
                                     ('pid','identities'),('command','command/model')]:
                report=copy.deepcopy(original)
                if mutation=='missing':report['cases'].pop()
                elif mutation=='claim':report['accepted']=True
                elif mutation=='exit':report['cases'][0]['exit_code']=0
                elif mutation=='pid':
                    cleanup=reference.load(root/'cleanup.json');cleanup['invocations'][1]['pid']=1
                    reference.save(root/'bad-cleanup.json',cleanup)
                    # Mutated receipt remains hash-bound; semantic validation must still reject it.
                    reference.save(root/'cleanup.json',cleanup)
                    report['receipts_sha256']['cleanup.json']=reference.digest(root/'cleanup.json')
                else:
                    # Reset the prior negative receipt and change the model selector in both copies.
                    value,report=self.early_report(root)
                    report['cases'][0]['invocation']['argv'][-1]='/actual-model'
                    cleanup=reference.load(root/'cleanup.json')
                    cleanup['invocations'][1]=report['cases'][0]['invocation']
                    reference.save(root/'cleanup.json',cleanup)
                    report['receipts_sha256']['cleanup.json']=reference.digest(root/'cleanup.json')
                reference.save(path,report)
                with self.subTest(mutation=mutation),self.assertRaisesRegex(ValueError,message):
                    reference.verify_early_failures(value,path,reference.digest(path))

    def test_typed_scope_cannot_claim_process_observation(self):
        row = reference.load(reference.INVENTORY)['typed_failures'][0]
        observed = reference.typed_failure_observation(row)
        self.assertEqual(observed['scope'], 'function/no-model')
        self.assertEqual(observed['exception'], 'ValueError')
        self.assertEqual(observed['inference_calls'], 0)
        for field in reference.DISPOSITION_FIELDS:
            self.assertEqual(observed['disposition'][field]['state'], 'not-exercised')
        for mutation in (dict(scope='actual-model'), dict(inference_calls=True), dict(accepted=True)):
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'unsupported typed'):
                reference.typed_failure_observation(dict(row, **mutation))

    def test_failure_input_mutation_is_refused_after_the_ledger_digest(self):
        value = reference.load(reference.INVENTORY)
        ledger = reference.failure_inventory(value)
        ledger['cases'][0]['subcases'][0]['input']['variant'] = 'different input'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ledger['source_sha256']:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((reference.PROJECT / relative).read_bytes())
            path = root / 'ledger.json'
            reference.save(path, ledger)
            value['failure_ledger'] = dict(relative=path.name, sha256=reference.digest(path))
            with patch.object(reference, 'PROJECT', root):
                with self.assertRaisesRegex(ValueError, 'input/expected binding changed'):
                    reference.failure_inventory(value)

    def test_changed_source_or_evidence_bytes_are_not_rebound(self):
        value = reference.load(reference.INVENTORY)
        ledger = reference.failure_inventory(value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ledger['source_sha256']:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((reference.PROJECT / relative).read_bytes())
            path = root / 'ledger.json'
            reference.save(path, ledger)
            value['failure_ledger'] = dict(relative=path.name, sha256=reference.digest(path))
            (root / 'scripts/semantic_rank.py').write_text('changed')
            with patch.object(reference, 'PROJECT', root):
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    reference.failure_inventory(value)

    def test_failure_ledger_rejects_missing_disposition_and_unsupported_claim(self):
        value = reference.load(reference.INVENTORY)
        original = reference.failure_inventory(value)
        for change, message in [('field', 'disposition'), ('claim', 'unsupported')]:
            ledger = copy.deepcopy(original)
            if change == 'field':
                ledger['cases'][0]['expected_disposition'].pop('cache')
            else:
                ledger['cases'][0]['observed'] = True
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for relative in ledger['source_sha256']:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((reference.PROJECT / relative).read_bytes())
                path = root / 'ledger.json'
                reference.save(path, ledger)
                altered = copy.deepcopy(value)
                altered['failure_ledger'] = dict(relative=path.name, sha256=reference.digest(path))
                with patch.object(reference, 'PROJECT', root):
                    with self.assertRaisesRegex(ValueError, message):
                        reference.failure_inventory(altered)

    def test_failure_ledger_rejects_missing_and_duplicate_family_ids(self):
        value = reference.load(reference.INVENTORY)
        ledger = reference.failure_inventory(value)
        self.assertEqual(len(ledger['cases']), 78)
        for rows in (ledger['cases'][:-1], ledger['cases'] + [ledger['cases'][0]]):
            broken = dict(ledger, cases=rows)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'ledger.json'
                reference.save(path, broken)
                altered = copy.deepcopy(value)
                altered['failure_ledger'] = dict(relative=path.name, sha256=reference.digest(path))
                with patch.object(reference, 'PROJECT', Path(directory)):
                    with self.assertRaisesRegex(ValueError, 'failure case IDs'):
                        reference.failure_inventory(altered)

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

    def test_inventory_content_guards_refuse_omissions_after_digest_check(self):
        original = reference.load(reference.INVENTORY)
        mutations = [
            ("prepared selector", lambda v: v["python_prepared"].pop()),
            ("actual tokenizer selector", lambda v: v.update(actual_tokenizer="missing")),
            ("Go selector", lambda v: v["go_process"].pop()),
            ("native mechanics selector", lambda v: v["python_mechanics"].pop()),
            ("required criterion", lambda v: v["criteria"].pop(0)),
            ("five model assets", lambda v: v["assets_sha256"].pop("tokenizer.json")),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "committed-inventory.json"
            for message, mutate in mutations:
                value = copy.deepcopy(original)
                mutate(value)
                reference.save(path, value)
                with self.subTest(message=message), patch.object(reference, "INVENTORY", path):
                    with self.assertRaisesRegex(ValueError, message):
                        reference.inventory(path)

    def test_native_executable_missing_or_wrong_format_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native"
            with self.assertRaises(FileNotFoundError):
                reference.native_executable(path)
            path.write_bytes(b"#!/usr/bin/python3\n")
            with self.assertRaisesRegex(ValueError, "native Go executables"):
                reference.native_executable(path)

    def test_runtime_binding_missing_mismatched_or_incomplete_refuses(self):
        value = dict(retained={"runtime-binding": dict(sha256="a" * 64)}, python_sha256="b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "binding.json"
            with self.assertRaises(FileNotFoundError):
                reference.verify_runtime(value, path, Path(directory), Path(directory))
            reference.save(path, {})
            with self.assertRaisesRegex(ValueError, "reviewed runtime binding"):
                reference.verify_runtime(value, path, Path(directory), Path(directory))
            rows = dict(frozen_loaded_module_files=[dict(relative="module", sha256="c" * 64)] * 481,
                        frozen_numeric_native_files=[dict(relative="native", sha256="d" * 64)] * 7)
            for key in rows:
                broken = copy.deepcopy(rows)
                broken[key].pop()
                with patch.object(reference, "digest", return_value="a" * 64), \
                     patch.object(reference, "load", return_value=broken), \
                     patch.object(reference, "pinned", return_value=path):
                    with self.assertRaisesRegex(ValueError, "runtime inventory count changed"):
                        reference.verify_runtime(value, path, Path(directory), Path(directory))

    def test_reused_cell_needs_nonempty_known_retained_evidence(self):
        for evidence in ([], ["unknown"], [None]):
            value = dict(retained={}, criteria=[dict(id="rank/case", state="reused", evidence=evidence)])
            with self.subTest(evidence=evidence), self.assertRaisesRegex(ValueError, "unqualified reference cell"):
                reference.audit(value, Path("/unused"))

    def test_prepared_row_names_fresh_or_exact_reused_provenance(self):
        value = dict(retained={}, criteria=[dict(id="tests/prepared-23", state="missing", evidence=[])],
                     stream_reference={})
        reuse = dict(report_sha256="a" * 64, inventory_sha256="b" * 64)
        with patch.object(reference, "verify_characterization", return_value=0):
            fresh = reference.audit(value, Path("/unused"), dict(results={}))
            reused = reference.audit(value, Path("/unused"), dict(results={}, prepared_reuse=reuse))
        self.assertIn("fresh", fresh["criteria"][0]["evidence"][0])
        evidence = reused["criteria"][0]["evidence"]
        self.assertNotIn("fresh", " ".join(evidence))
        self.assertIn("report-sha256:" + reuse["report_sha256"], evidence)
        self.assertIn("original-inventory-sha256:" + reuse["inventory_sha256"], evidence)

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
        with self.assertRaisesRegex(ValueError, "expected failure"):
            reference.python_events(json.dumps(dict(document, expected_failures=["Class.test_one"])).encode(),
                                    ["Class.test_one"])

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
                        tool_sha256=reference.tool_pins(), tool_after_sha256=reference.tool_pins(),
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
