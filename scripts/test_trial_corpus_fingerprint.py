"""Owned disposable store only. Run through trial-task-eval.sh -- python3 ..."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

import trial_task_eval as te
from trial_corpus_fingerprint import same_corpus


@unittest.skipUnless(os.environ.get('CAIRN_TASK_EVAL_PG') and os.environ.get('CAIRN_TASK_EVAL_BINARY'),
                     'requires owned wrapper and explicit test binary')
class FingerprintIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        directory = cls.resources.enter_context(tempfile.TemporaryDirectory(prefix='cairn-fingerprint-test-'))
        cls.store = te.TrialStore(Path(directory)/'store',os.environ['CAIRN_TASK_EVAL_BINARY'],'fingerprint_'+uuid.uuid4().hex[:8])
        cls.resources.callback(cls.store.stop)
        cls.record = cls.store.remember(dict(id='synthetic',kind='lesson',body='Synthetic cobalt restore fixture 日本語.',shareable=True))
        cls.store.start()

    def test_real_search_and_pull_only_change_operational_hash(self):
        before = self.store.fingerprint()
        found = self.store.agent('search','--repo',te.TRIAL_REPO,'--task','fixture','--run','read','--tokens','32000','cobalt restore')
        self.assertEqual(len(found['index']),1)
        search = self.store.fingerprint()
        self.assertTrue(same_corpus(before,search))
        self.assertNotEqual(before['operational'],search['operational'])
        pulled = self.store.agent('pull',payload=found['index'][0]['pull_arguments'])
        self.assertEqual(pulled['selection']['record']['body'],'Synthetic cobalt restore fixture 日本語.')
        after = self.store.fingerprint()
        self.assertTrue(same_corpus(search,after))
        self.assertNotEqual(search['operational'],after['operational'])

    def test_actual_body_version_pins_and_entity_changes_are_detected(self):
        draft=dict(kind='lesson',body='Synthetic other body.',scope=dict(repo=te.TRIAL_REPO,task_id='*',run_id='*'),
                   sensitivity='shareable',claim_type='self',pins={'task_class':'repair'},
                   entities=[{'kind':'file','name':'core/fixture.go'}])
        record=self.store.agent('create',payload=dict(request_id=str(uuid.uuid4()),draft=draft))
        before=self.store.fingerprint()
        self.store.agent('edit',payload=dict(request_id=str(uuid.uuid4()),record_id=record['record_id'],expected_version=1,
                                            draft=dict(draft,body='Synthetic updated.')))
        after=self.store.fingerprint()
        self.assertFalse(same_corpus(before,after))
        self.assertNotEqual(before['corpus']['record_version'],after['corpus']['record_version'])
        ident=str(uuid.UUID(record['record_id']))
        # Independent owned-fixture mutations prevent version changes masking omitted fields.
        for table,assignment in [('record_applicability',"pins='{\"task_class\":\"review\"}'"),
                                 ('record_entities',"entities='[{\"kind\":\"file\",\"name\":\"core/other.go\"}]'"),
                                 ('memory_record',"sensitivity='local'")]:
            before=self.store.fingerprint()
            te.run([self.store.pg_bin/'psql','-X','-v','ON_ERROR_STOP=1','-h',self.store.pg_socket,'-d',self.store.database,
                    '-c',f"UPDATE cairn.{table} SET {assignment} WHERE record_id='{ident}'"],env=self.store.env)
            after=self.store.fingerprint()
            self.assertFalse(same_corpus(before,after))
            self.assertNotEqual(before['corpus'][table],after['corpus'][table])

    def test_unreviewed_column_and_incomplete_stamp_refuse(self):
        stamp=self.store.fingerprint()
        with self.assertRaises(ValueError): same_corpus({},stamp)
        command=[self.store.pg_bin/'psql','-X','-v','ON_ERROR_STOP=1','-h',self.store.pg_socket,'-d',self.store.database,'-c']
        te.run(command+['ALTER TABLE cairn.memory_record ADD COLUMN synthetic_contract text'],env=self.store.env)
        try:
            with self.assertRaisesRegex(ValueError,'unsupported memory_record schema'):
                self.store.fingerprint()
        finally:
            te.run(command+['ALTER TABLE cairn.memory_record DROP COLUMN synthetic_contract'],env=self.store.env)
