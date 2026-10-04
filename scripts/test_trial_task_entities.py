"""Explicit file/symbol associations survive frozen prospective input and the real import.

The shape tests need no store. The integration tests run only through
scripts/trial-task-eval.sh with CAIRN_TASK_EVAL_BINARY: an owned disposable
PostgreSQL cluster, a real `cairn remember` import and real hosted searches.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import trial_task_eval as te
from trial_task_input import corpus_entities, freeze_input, load_input
from test_trial_task_prospective import fixture

FILE = {'kind': 'file', 'name': 'core/entity_fixture.go'}
SYMBOL = {'kind': 'symbol', 'name': 'core.EntityFixture'}
NOTE = {'id': 'entity-note', 'kind': 'lesson', 'body': 'Synthetic cobalt restore lesson.'}


class EntityShapeTests(unittest.TestCase):
    def test_absent_declared_order_and_empty_are_accepted_exactly(self):
        self.assertEqual(corpus_entities(NOTE), [])
        self.assertEqual(corpus_entities(dict(NOTE, entities=[])), [])
        self.assertEqual(corpus_entities(dict(NOTE, entities=[SYMBOL, FILE])),
                         [('symbol', 'core.EntityFixture'), ('file', 'core/entity_fixture.go')])

    def test_every_other_shape_fails_instead_of_being_dropped(self):
        bad = {
            'not a list': {'file': 'core/a.go'},
            'string': 'core/a.go',
            'null': None,
            'item is a string': ['core/a.go'],
            'item is a list': [['file', 'core/a.go']],
            'extra key': [dict(FILE, alias='old/name.go')],
            'extra confidence': [dict(FILE, confidence=0.9)],
            'missing name': [{'kind': 'file'}],
            'missing kind': [{'name': 'core/a.go'}],
            'unknown kind': [{'kind': 'class', 'name': 'X'}],
            'kind case': [{'kind': 'File', 'name': 'core/a.go'}],
            'empty kind': [{'kind': '', 'name': 'core/a.go'}],
            'non-string kind': [{'kind': 1, 'name': 'core/a.go'}],
            'non-string name': [{'kind': 'file', 'name': 7}],
            'null name': [{'kind': 'file', 'name': None}],
            'empty name': [{'kind': 'file', 'name': ''}],
            'repeated pair': [FILE, dict(FILE)],
        }
        for label, entities in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                corpus_entities(dict(NOTE, entities=entities))

    def test_legacy_validation_reports_the_note_and_old_notes_are_unchanged(self):
        problems = te.validate([], [dict(NOTE, entities=[{'kind': 'class', 'name': 'X'}]), dict(NOTE, id='plain')], prospective=True)
        self.assertEqual([p for p in problems if 'entit' in p], ['entity-note: each entity must be exactly {"kind": "file" or "symbol", "name": nonempty string}'])
        self.assertEqual(te.validate([], [dict(NOTE, entities=[FILE, SYMBOL]), dict(NOTE, id='plain')], prospective=True), [])

    def test_frozen_input_keeps_entities_and_refuses_bad_or_unsupported_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(Path(directory) / 'input')
            (root / 'corpus.json').write_text(json.dumps({'notes': [dict(NOTE, entities=[FILE, SYMBOL])]}))
            freeze_input(root)
            notes = load_input(root)['notes']
            self.assertEqual(notes[0]['entities'], [FILE, SYMBOL])
            (root / 'FROZEN.json').unlink()
            for label, change in {
                'unknown kind': dict(entities=[{'kind': 'module', 'name': 'x'}]),
                'extra key': dict(entities=[dict(FILE, line=3)]),
                'not a list': dict(entities={'file': 'core/a.go'}),
                # Support for entities must not loosen anything around them.
                'pins': dict(entities=[FILE], pins={'task_class': 'repair'}),
                'relations': dict(entities=[FILE], relations=[{'record_id': 'x'}]),
                'citations': dict(entities=[FILE], evidence_citations=[]),
                'authority': dict(entities=[FILE], authority='A'),
                'history': dict(entities=[FILE], versions=[{'version': 1}]),
            }.items():
                with self.subTest(label):
                    (root / 'corpus.json').write_text(json.dumps({'notes': [dict(NOTE, **change)]}))
                    with self.assertRaises(ValueError):
                        freeze_input(root)

    def test_input_without_entities_freezes_and_imports_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(Path(directory) / 'input')
            corpus = {'notes': [dict(NOTE), dict(NOTE, id='second', shareable=False, supersede_with='entity-note')]}
            (root / 'corpus.json').write_text(json.dumps(corpus))
            freeze_input(root)
            loaded = load_input(root)
            self.assertEqual(loaded['notes'], corpus['notes'])
            self.assertEqual(loaded['frozen']['corpus_sha256'], hashlib.sha256((root / 'corpus.json').read_bytes()).hexdigest())
        rows = te.import_provenance(corpus['notes'], {'entity-note': 'r1', 'second': 'r2'})
        self.assertTrue(all('entities' not in row for row in rows))
        self.assertEqual(set(rows[0]), {'input_id', 'imported_record_id', 'imported_version', 'body_sha256', 'provenance'})

    def test_import_map_records_declared_associations_only_when_present(self):
        rows = te.import_provenance([dict(NOTE, entities=[SYMBOL, FILE]), dict(NOTE, id='plain')], {'entity-note': 'r1', 'plain': 'r2'})
        self.assertEqual(rows[0]['entities'], [SYMBOL, FILE])
        self.assertNotIn('entities', rows[1])

    def test_remember_passes_each_association_as_a_cli_flag_and_checks_what_was_stored(self):
        store = object.__new__(te.TrialStore)
        store.binary, store.env, store.ids, store.names = Path('/fixture/cairn'), {}, {}, {}
        seen = []

        def remember(command, **kwargs):
            seen.append([str(c) for c in command])
            return type('Result', (), {'stdout': json.dumps({'ok': True, 'data': {'record_id': 'r1'}}).encode()})()

        dash = {'kind': 'symbol', 'name': '-dash.Symbol=name'}
        entities = [FILE, SYMBOL, dash]
        with patch.object(te, 'run', side_effect=remember), \
             patch.object(te, 'cairn_json', return_value={'entities': entities}) as get:
            store.remember(dict(NOTE, entities=entities))
        command = seen[0]
        self.assertIn('--entity-file=core/entity_fixture.go', command)
        self.assertIn('--entity-symbol=core.EntityFixture', command)
        self.assertIn('--entity-symbol=-dash.Symbol=name', command)  # one argv element, never a flag
        self.assertEqual(get.call_args.args[1], ['get', 'r1'])
        # No association, no flag and no extra read: old inputs run the earlier command.
        seen.clear()
        with patch.object(te, 'run', side_effect=remember), patch.object(te, 'cairn_json') as get:
            store.remember(dict(NOTE, id='plain'))
        self.assertFalse([c for c in seen[0] if c.startswith('--entity')])
        get.assert_not_called()
        # A store that dropped, altered or added an association is an import failure, not a silent pass.
        for label, kept in {'dropped': [FILE], 'altered': [FILE, dict(SYMBOL, name='core.Other'), dash],
                            'none': [], 'extra': entities + [{'kind': 'file', 'name': 'x.go'}]}.items():
            store.ids.clear()
            with self.subTest(label), patch.object(te, 'run', side_effect=remember), \
                 patch.object(te, 'cairn_json', return_value={'entities': kept}), \
                 self.assertRaisesRegex(RuntimeError, 'differ from the input'):
                store.remember(dict(NOTE, entities=entities))
            self.assertEqual(store.ids, {})


@unittest.skipUnless(os.environ.get('CAIRN_TASK_EVAL_PG') and os.environ.get('CAIRN_TASK_EVAL_BINARY'),
                     'requires owned wrapper and explicit test binary')
class EntityImportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        directory = cls.resources.enter_context(tempfile.TemporaryDirectory(prefix='cairn-entity-import-test-'))
        cls.store = te.TrialStore(Path(directory) / 'store', os.environ['CAIRN_TASK_EVAL_BINARY'], 'entities_' + uuid.uuid4().hex[:8])
        cls.resources.callback(cls.store.stop)
        # Bodies share no words with the entity names or with each other, so a hit by entity is not a lexical hit.
        cls.corpus = [
            dict(id='shared', kind='lesson', body='Quartz ledger rotates monthly.', entities=[FILE, SYMBOL,
                 {'kind': 'symbol', 'name': '-dash.Symbol=name'}]),
            dict(id='other', kind='lesson', body='Amber glacier reports weekly.', entities=[{'kind': 'file', 'name': 'core/other.go'}]),
            dict(id='private', kind='lesson', body='Violet harbor stays internal.', shareable=False, entities=[FILE]),
            dict(id='plain', kind='lesson', body='Copper meadow has no association.'),
            dict(id='old', kind='lesson', body='Silver orchard predecessor.', supersede_with='new',
                 entities=[{'kind': 'file', 'name': 'core/replaced.go'}]),
            dict(id='new', kind='lesson', body='Jade orchard replacement.', entities=[{'kind': 'file', 'name': 'core/replaced.go'}]),
        ]
        cls.store.seed_corpus(cls.corpus, workers=1)
        cls.store.start()

    def search(self, *flags):
        return self.store.agent('search', '--repo', te.TRIAL_REPO, '--task', 'entity', '--run', 'read', '--tokens', '32000', *flags)

    def found(self, *flags):
        return {self.store.names[entry['record_id']]: entry for entry in self.search(*flags).get('index', [])}

    def test_entity_only_hosted_search_finds_the_imported_shareable_note_with_its_associations(self):
        by_file = self.found('--entity-file=core/entity_fixture.go')
        self.assertEqual(set(by_file), {'shared'})  # not the unassociated, other-entity or local-only notes
        self.assertEqual({(e['kind'], e['name']) for e in by_file['shared']['entities']},
                         {('file', 'core/entity_fixture.go'), ('symbol', 'core.EntityFixture'), ('symbol', '-dash.Symbol=name')})
        self.assertEqual(set(self.found('--entity-symbol=core.EntityFixture')), {'shared'})
        self.assertEqual(set(self.found('--entity-symbol=-dash.Symbol=name')), {'shared'})
        self.assertEqual(set(self.found('--entity-file=core/other.go')), {'other'})
        # A name nobody declared finds nothing, and so does a note that declared none.
        self.assertEqual(self.found('--entity-file=core/absent.go'), {})
        self.assertNotIn('plain', self.found('--entity-file=core/entity_fixture.go', '--entity-file=core/other.go'))

    def test_stored_associations_match_the_input_for_every_declared_note(self):
        for note in self.corpus:
            stored = te.cairn_json(self.store.binary, ['get', self.store.ids[note['id']]], self.store.env)
            kept = sorted((e['kind'], e['name']) for e in stored.get('entities') or [])
            self.assertEqual(kept, sorted(te.corpus_entities(note)), note['id'])

    def test_local_only_note_keeps_its_association_but_is_never_delivered_to_the_hosted_agent(self):
        stored = te.cairn_json(self.store.binary, ['get', self.store.ids['private']], self.store.env)
        self.assertEqual([(e['kind'], e['name']) for e in stored['entities']], [('file', 'core/entity_fixture.go')])
        self.assertEqual(stored['sensitivity'], 'local')
        everything = json.dumps(self.search('--entity-file=core/entity_fixture.go', '--kind', 'lesson'))
        self.assertNotIn(self.store.ids['private'], everything)
        self.assertNotIn('Violet', everything)

    def test_superseded_note_is_not_retrieved_by_the_entity_it_shares_with_its_replacement(self):
        self.assertEqual(set(self.found('--entity-file=core/replaced.go')), {'new'})

    def test_an_invalid_association_refuses_the_import_and_creates_no_record(self):
        before = self.store.fingerprint()
        for label, entities in {
            'absolute file path': [{'kind': 'file', 'name': '/etc/passwd'}],
            'parent escape': [{'kind': 'file', 'name': '../escape.go'}],
            'backslash': [{'kind': 'file', 'name': 'core\\x.go'}],
            'non canonical': [{'kind': 'file', 'name': 'core/./x.go'}],
            'surrounding space': [{'kind': 'symbol', 'name': ' core.X'}],
            'too many': [{'kind': 'file', 'name': f'core/f{i}.go'} for i in range(17)],
        }.items():
            note = dict(id='bad-' + label.replace(' ', '-'), kind='lesson', body='Refused fixture ' + label, entities=entities)
            with self.subTest(label), self.assertRaisesRegex(RuntimeError, 'remember failed'):
                self.store.remember(note)
            self.assertNotIn(note['id'], self.store.ids)
        self.assertTrue(te.same_corpus(before, self.store.fingerprint()))

    def test_real_prospective_frozen_input_round_trips_into_the_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(Path(directory) / 'input')
            notes = [dict(id='frozen', kind='lesson', body='Granite weir inspection note.', entities=[FILE])]
            (root / 'corpus.json').write_text(json.dumps({'notes': notes}))
            freeze_input(root)
            loaded = load_input(root)['notes']
            store = te.TrialStore(Path(directory) / 'frozen-store', os.environ['CAIRN_TASK_EVAL_BINARY'], 'frozen_' + uuid.uuid4().hex[:8])
            try:
                store.seed_corpus(loaded, workers=1)
                row = te.import_provenance(loaded, store.ids)[0]
                self.assertEqual(row['entities'], [FILE])
                stored = te.cairn_json(store.binary, ['get', store.ids['frozen']], store.env)
                self.assertEqual(stored['entities'], [FILE])
            finally:
                store.stop()


if __name__ == '__main__':
    unittest.main()
