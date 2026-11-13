"""Guard against reporting the blocked census as Agy qualification."""
import copy
import unittest

from check_r06_agy_readback import GAP, not_ready


class AgyReadbackGapTests(unittest.TestCase):
    def result(self):
        return dict(Census=dict(FixtureReady=False, ExternalCoverage='unknown', NativeRefresh='unobserved'), Error=GAP)

    def test_exact_refusal_is_reported_as_not_ready(self):
        result = self.result()
        before = copy.deepcopy(result)
        report = not_ready(result)
        self.assertEqual(report, dict(status='NOTREADY', actual_error=GAP,
            native_qualification=False, authenticated_d2=False))
        self.assertEqual(result, before)

    def test_empty_fixture_association_does_not_claim_native_or_d2(self):
        result = self.result()
        result['Census']['FixtureReady'], result['Error'] = True, ''
        report = not_ready(result, association_only=True)
        self.assertEqual(report['status'], 'ASSOCIATION_ONLY')
        self.assertFalse(report['native_qualification'])
        self.assertFalse(report['authenticated_d2'])
        with self.assertRaises(AssertionError):
            not_ready(result)

    def test_different_failure_or_positive_coverage_cannot_pass_the_probe(self):
        for field, changed in (('Error', 'different setup failure'), ('FixtureReady', True),
                               ('ExternalCoverage', 'known'), ('NativeRefresh', 'observed')):
            result = self.result()
            if field == 'Error':
                result[field] = changed
            else:
                result['Census'][field] = changed
            with self.subTest(field=field), self.assertRaises(AssertionError):
                not_ready(result)


if __name__ == '__main__':
    unittest.main()
