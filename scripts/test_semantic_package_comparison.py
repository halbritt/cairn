"""Model-free protection for the packaged numerical reporter's refusal paths."""

import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location(
    "comparison", Path(__file__).with_name("check_semantic_package_comparison.py"))
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


class PackageComparisonTest(unittest.TestCase):
    def report(self):
        return dict(rank=dict(scores=dict(within500=269), changed_pairs=[]),
                    stream=dict(scores=dict(within500=538), changed_pairs=[]),
                    embedding=dict(paired=549, within1e_6=549),
                    raw_firstnormalized=dict(paired=294, within1e_6=294),
                    distinct_union=dict(paired=821, within1e_6=821))

    def score_rows(self, values):
        return [dict(record_id=str(i), version=1, body_sha256="a" * 64, score=v)
                for i, v in enumerate(values)]

    def compare(self, before, after):
        reference = [dict(id="case", scores=self.score_rows(before))]
        result = dict(model_sha256="b" * 64, algorithm="bge-max-chunk/1",
                      scores=self.score_rows(after))
        return C.score_comparison([dict(id="case", result=result)], reference, "b" * 64)

    def test_500_is_allowed_and_501_is_reported_outside_bound(self):
        self.assertEqual(self.compare([0], [500])["scores"]["within500"], 1)
        result = self.compare([0], [501])["scores"]
        self.assertEqual(result["within500"], 0)
        self.assertEqual(result["maximum_absolute"], 501)

    def test_identical_and_within_one_are_diagnostics(self):
        result = self.compare([0, 1000], [2, 1002])["scores"]
        self.assertEqual((result["within500"], result["exact"], result["within1"]), (2, 0, 0))
        C.require_rule_c(self.report())

    def test_inversion_keeps_reference_and_candidate_margins(self):
        result = self.compare([100, 101], [102, 100])
        self.assertEqual(result["changed_pairs"][0]["reference_margin"], -1)
        self.assertEqual(result["changed_pairs"][0]["candidate_margin"], 2)
        self.assertIsNone(result["outside_neartie_breaches"])

    def test_wrong_identity_refuses(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            C.score_comparison([dict(id="case", result=dict(model_sha256="wrong", algorithm="bge-max-chunk/1"))], [dict(id="case", scores=[])], "b" * 64)

    def test_missing_duplicate_and_reordered_frames_refuse(self):
        for rows in ([dict(id="a")], [dict(id="a"), dict(id="a")], [dict(id="b"), dict(id="a")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                C.exact_frames(rows, ["a", "b"])

    def test_unterminated_capture_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture"
            path.write_text('{"id":"a"}')
            with self.assertRaisesRegex(ValueError, "incomplete"):
                C.frames(path)

    def test_raw_overlap_failures_cannot_hide_in_union(self):
        report = self.report()
        report["raw_firstnormalized"]["within1e_6"] = 293
        with self.assertRaisesRegex(ValueError, "vector"):
            C.require_rule_c(report)

    def test_missing_required_vector_or_score_refuses(self):
        for section, key in (("embedding", "paired"), ("distinct_union", "within1e_6")):
            report = copy.deepcopy(self.report())
            report[section][key] -= 1
            with self.subTest(section=section), self.assertRaises(ValueError):
                C.require_rule_c(report)
        report = self.report()
        report["stream"]["scores"]["within500"] = 537
        with self.assertRaisesRegex(ValueError, "score"):
            C.require_rule_c(report)

    def test_rank_change_cannot_be_called_no_breach(self):
        report = self.report()
        report["rank"]["changed_pairs"] = [dict(reference_margin=1, candidate_margin=-1)]
        with self.assertRaisesRegex(ValueError, "near-tie"):
            C.require_rule_c(report)

    def test_go_thread_fragments_do_not_replace_observed_worker_trace(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "actual-workers.json").write_text(json.dumps(dict(workers=[dict(pid=7, cmdline=["rank", "--stream"])])))
            (root / "stream-trace.7").write_text("worker")
            (root / "stream-trace.8").write_text("incomplete Go thread fragment")
            expected = dict(write=[dict(id=str(i), cache={}, result={}) for i in range(38)],
                            read=[dict(id=str(i), request={}) for i in range(38)])
            (root / "stream.stdout").write_text(('{}\n'.format(json.dumps(dict(result={})))) * 38)
            (root / "stream-inputs.jsonl").write_text(('{}\n'.format(json.dumps(dict(request={})))) * 38)
            def parse(text):
                if text != "worker":
                    raise ValueError("unrelated incomplete parent stream")
                return expected
            self.assertEqual(C.stream_wire(root, SimpleNamespace(trace_frames=parse)), expected)


if __name__ == "__main__":
    unittest.main()
