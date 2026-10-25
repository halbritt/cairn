import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_weekly_accounts as accounts
import trial_paired_request as paired
import trial_codex_account_selection as codex_accounts


class WeeklyAccountAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cases = [f'case-{i:02d}' for i in range(21)]
        self.seeds = [1, 2, 3]
        self.plan = dict(cases=self.cases, arms=['CURRENT', 'CAND'], seeds=self.seeds)
        self.accounts = []
        for i, account_id in enumerate(('account-A', 'account-B', 'account-C')):
            home = self.root / f'codex-{i}'
            home.mkdir()
            auth = home / 'auth.json'
            auth.write_text('{}')
            self.accounts.append(dict(account_id=account_id, auth_file=str(auth),
                                      auth_sha256=hashlib.sha256(auth.read_bytes()).hexdigest(),
                                      config_home=str(home)))

    def tearDown(self):
        self.tmp.cleanup()

    def pin(self, document):
        path = self.root / 'account-assignment.json'
        path.write_text(json.dumps(document, sort_keys=True))
        return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def test_balanced_assignment_keeps_each_pair_together_and_covers_each_case_seed_once(self):
        document = accounts.balanced(self.cases, self.seeds, self.accounts)
        reference = self.pin(document)
        loaded, pin, counts = accounts.validate(reference, self.plan)
        self.assertEqual(loaded, document)
        self.assertEqual(pin, reference)
        self.assertEqual(counts, {'account-A': 21, 'account-B': 21, 'account-C': 21})
        self.assertEqual(len(document['assignments']), 126)
        for case in self.cases:
            for seed in self.seeds:
                self.assertEqual(accounts.account_for(document, case, 'CURRENT', seed)['account_id'],
                                 accounts.account_for(document, case, 'CAND', seed)['account_id'])
            self.assertEqual(len({accounts.account_for(document, case, 'CURRENT', seed)['account_id']
                                  for seed in self.seeds}), 3)

    def test_assignment_hash_change_refuses(self):
        reference = self.pin(accounts.balanced(self.cases, self.seeds, self.accounts))
        path = Path(reference['path'])
        path.write_text(path.read_text().replace('account-A', 'account-B'))
        with self.assertRaisesRegex(paired.Refusal, 'pin_mismatch'):
            accounts.validate(reference, self.plan)

    def test_missing_or_duplicate_pair_refuses(self):
        document = accounts.balanced(self.cases, self.seeds, self.accounts)
        document['assignments'].pop()
        with self.assertRaisesRegex(paired.Refusal, 'account_assignment_coverage'):
            accounts.validate(self.pin(document), self.plan)

    def test_unknown_account_refuses(self):
        document = accounts.balanced(self.cases, self.seeds, self.accounts)
        document['assignments'][0]['account_id'] = 'unbound-account'
        with self.assertRaisesRegex(paired.Refusal, 'account_assignment_coverage'):
            accounts.validate(self.pin(document), self.plan)

    def test_paired_arms_cannot_be_split_between_accounts(self):
        document = accounts.balanced(self.cases, self.seeds, self.accounts)
        current, candidate = document['assignments'][:2]
        candidate['account_id'] = 'account-C' if current['account_id'] != 'account-C' else 'account-B'
        with self.assertRaisesRegex(paired.Refusal, 'account_assignment_pair_split'):
            accounts.validate(self.pin(document), self.plan)

    def test_balanced_strategy_must_match_explicit_rotation(self):
        document = accounts.balanced(self.cases, self.seeds, self.accounts)
        document['assignments'][0]['account_id'] = 'account-C'
        document['assignments'][1]['account_id'] = 'account-C'
        with self.assertRaisesRegex(paired.Refusal, 'account_assignment_strategy'):
            accounts.validate(self.pin(document), self.plan)

    def test_auth_file_identity_must_match_its_assigned_subscription(self):
        account = self.accounts[0]
        path = Path(account['auth_file'])
        path.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='account-A'))))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        codex_accounts.verify_auth_file(str(path), digest, expected_account_id='account-A')
        with self.assertRaisesRegex(paired.Refusal, 'codex_account_identity_mismatch'):
            codex_accounts.verify_auth_file(str(path), digest, expected_account_id='account-B')

    def test_subscription_auth_rejects_api_key_mode_even_when_identity_matches(self):
        account = self.accounts[0]
        path = Path(account['auth_file'])
        path.write_text(json.dumps(dict(auth_mode='chatgpt', OPENAI_API_KEY='must-not-be-used',
                                        tokens=dict(account_id='account-A'))))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(paired.Refusal, 'codex_account_subscription_required'):
            codex_accounts.verify_auth_file(str(path), digest, expected_account_id='account-A')

    def test_subscription_preflight_reads_only_status_and_catalog_without_inference(self):
        account = self.accounts[0]
        auth = Path(account['auth_file'])
        auth.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='account-A'))))
        account['auth_sha256'] = hashlib.sha256(auth.read_bytes()).hexdigest()
        server = self.root/'fake-codex'
        server.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                          'for line in sys.stdin:\n'
                          ' r=json.loads(line); i=r.get("id")\n'
                          ' if i==1: v={}\n'
                          ' elif i==2: v={"ordinaryUsageAllowed":True,"rateLimits":{"limitId":"codex",'
                          '"primary":{"usedPercent":25,"windowDurationMins":10080,"resetsAt":123},'
                          '"secondary":None}}\n'
                          ' elif i==3: v={"data":[{"id":"gpt-6-luna"}]}\n'
                          ' else: continue\n'
                          ' print(json.dumps({"id":i,"result":v}),flush=True)\n')
        server.chmod(0o700)
        provider = dict(path=str(server), sha256=hashlib.sha256(server.read_bytes()).hexdigest())
        status = codex_accounts.subscription_preflight(account, provider, 'gpt-6-luna')
        self.assertTrue(status['ordinary_usage_allowed'])
        self.assertEqual(status['used_percent'], 25)
        self.assertTrue(status['catalog_model_present'])
        self.assertFalse(status['absolute_allowance_observed'])

    def test_subscription_preflight_refuses_quota_or_missing_model_without_fallback(self):
        account = self.accounts[0]
        auth = Path(account['auth_file'])
        auth.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='account-A'))))
        account['auth_sha256'] = hashlib.sha256(auth.read_bytes()).hexdigest()
        for allowed, model, code in ((False, 'gpt-6-luna', 'codex_subscription_not_allowed'),
                                     (True, 'other-model', 'codex_model_unavailable')):
            with self.subTest(code=code):
                server = self.root/'fake-codex'
                server.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                                  'for line in sys.stdin:\n'
                                  ' r=json.loads(line); i=r.get("id")\n'
                                  f' if i==1: v={{}}\n elif i==2: v={{"ordinaryUsageAllowed":{str(allowed)},"rateLimits":{{"limitId":"codex","primary":{{"usedPercent":10,"windowDurationMins":10080}},"secondary":None}}}}\n'
                                  f' elif i==3: v={{"data":[{{"id":"{model}"}}]}}\n else: continue\n'
                                  ' print(json.dumps({"id":i,"result":v}),flush=True)\n')
                server.chmod(0o700)
                provider = dict(path=str(server), sha256=hashlib.sha256(server.read_bytes()).hexdigest())
                with self.assertRaisesRegex(paired.Refusal, code):
                    codex_accounts.subscription_preflight(account, provider, 'gpt-6-luna')

    def test_subscription_postflight_reads_only_rate_limits_and_retains_normalized_snapshot(self):
        account = self.accounts[0]
        auth = Path(account['auth_file'])
        auth.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='account-A'))))
        account['auth_sha256'] = hashlib.sha256(auth.read_bytes()).hexdigest()
        server = self.root/'fake-codex-postflight'
        server.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                          'for line in sys.stdin:\n'
                          ' r=json.loads(line); i=r.get("id")\n'
                          ' if i==1: v={}\n'
                          ' elif i==2: v={"accountId":"account-A","ordinaryUsageAllowed":True,"rateLimits":{'
                          '"limitId":"codex","primary":{"usedPercent":41,"windowDurationMins":10080,'
                          '"resetsAt":"2026-10-30T22:43:17Z"},"secondary":None}}\n'
                          ' else: continue\n'
                          ' print(json.dumps({"id":i,"result":v}),flush=True)\n')
        server.chmod(0o700)
        provider = dict(path=str(server), sha256=hashlib.sha256(server.read_bytes()).hexdigest())
        snapshot = codex_accounts.subscription_postflight(account, provider)
        self.assertEqual(snapshot['account_id'], 'account-A')
        self.assertIsNone(snapshot['error'])
        self.assertTrue(snapshot['status']['response_account_id_observed'])
        self.assertEqual(snapshot['status']['primary'], dict(used_percent=41, window_duration_mins=10080,
                                                            resets_at='2026-10-30T22:43:17Z'))
        self.assertIsNone(snapshot['status']['secondary'])
        self.assertNotIn('token', json.dumps(snapshot).lower())

    def test_subscription_postflight_returns_null_without_raw_error_when_identity_or_read_fails(self):
        account = self.accounts[0]
        auth = Path(account['auth_file'])
        auth.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id='account-A'))))
        account['auth_sha256'] = hashlib.sha256(auth.read_bytes()).hexdigest()
        for response, returns in ((dict(accountId='other-account', rateLimits=dict(limitId='codex',
                                primary=dict(usedPercent=20, windowDurationMins=10080))), True),
                                  ({}, False)):
            with self.subTest(returns=returns):
                server = self.root/'fake-codex-postflight'
                if returns:
                    server.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                                      f'print(json.dumps({{"id":1,"result":{{}}}}),flush=True)\n'
                                      f'print(json.dumps({{"id":2,"result":{json.dumps(response)}}}),flush=True)\n'
                                      'for line in sys.stdin: pass\n')
                else:
                    server.write_text('#!/usr/bin/env python3\nimport json,sys\n'
                                      'for line in sys.stdin:\n'
                                      ' r=json.loads(line); i=r.get("id")\n'
                                      ' if i in (1,2): print(json.dumps({"id":i,"error":{"message":"private"}}),flush=True)\n')
                server.chmod(0o700)
                provider = dict(path=str(server), sha256=hashlib.sha256(server.read_bytes()).hexdigest())
                snapshot = codex_accounts.subscription_postflight(account, provider, timeout=1)
                self.assertIsNone(snapshot['status'])
                self.assertEqual(snapshot['error'], 'codex_subscription_status_unavailable')
                self.assertNotIn('private', json.dumps(snapshot))


if __name__ == '__main__':
    unittest.main()
