"""Automatic semantic queries distinguish inferred paths from explicit literals."""
import importlib.util
import copy
import shlex
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('semantic_anchor_hook',
    Path(__file__).resolve().parents[1]/'integrations/lifecycle/memory.py')
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class SemanticRecallAnchors(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root/'.git').mkdir()
        for name in ('CONTRIBUTING.md','README.md','AGENTS.md'):
            (self.root/name).write_text('repository file')
        self.event = dict(hook_event_name='UserPromptSubmit',cwd=str(self.root),
                          session_id='12345678-1234-4234-8234-123456789abc')

    def search_for(self, prompt, *, semantic=True, mode='agent_tools', state=None, response=None, **event_fields):
        memory = hook.Memory(dict(cairn='unused',socket='unused',token_file='unused',repo='fixture',
            harness='claude',recall_mode=mode,semantic_fallback=semantic,context_bytes=9500),self.event['session_id'])
        with patch.object(memory,'search',return_value=response or dict(status='READY',selected=[],index=[])) as search:
            hook.recall(memory,dict(self.event,prompt=prompt,**event_fields),state or {})
        self.assertEqual(search.call_count,1)
        return search.call_args.args[0], search.call_args.kwargs

    def test_semantic_boundary_keeps_task_and_setup_paths_without_invented_literals(self):
        prompt='Repair currency rounding in src/rates.py. Read CONTRIBUTING.md before coding.'
        query,args=self.search_for(prompt)
        self.assertTrue(args['semantic'])
        self.assertEqual(args['entities'],['src/rates.py','CONTRIBUTING.md'])
        self.assertIn('src/rates.py',query)
        self.assertIn('CONTRIBUTING.md',query)
        self.assertNotIn('"src/rates.py"',query)
        self.assertNotIn('"CONTRIBUTING.md"',query)
        self.assertIn('rounding',query.split())

    def test_explicit_file_and_error_quotes_or_backticks_keep_exact_preference(self):
        for mark in ('"','`'):
            with self.subTest(mark=mark):
                query,args=self.search_for(f'Repair {mark}src/rates.py{mark} after {mark}RATE_OVERFLOW{mark}.')
                self.assertIn('"src/rates.py"',query)
                self.assertIn('"RATE_OVERFLOW"',query)
                self.assertEqual(args['entities'],['src/rates.py'])

    def test_legitimate_document_edit_keeps_file_text_and_entity(self):
        for filename in ('README.md','AGENTS.md'):
            with self.subTest(filename=filename):
                query,args=self.search_for(f'Correct {filename} installation instructions.')
                self.assertIn(filename,shlex.split(query))
                self.assertEqual(args['entities'],[filename])
                self.assertNotIn('"'+filename+'"',query)
                lexical,_=self.search_for(f'Correct {filename} installation instructions.',semantic=False)
                self.assertIn('"'+filename+'"',lexical)

    def test_legacy_unassociated_file_lookup_loses_only_implicit_literal(self):
        prompt='Investigate src/cache.c eviction.'
        query,args=self.search_for(prompt)
        lexical,old_args=self.search_for(prompt,semantic=False)
        self.assertIn('src/cache.c',shlex.split(query))
        self.assertNotIn('"src/cache.c"',query)
        self.assertIn('"src/cache.c"',lexical)
        self.assertEqual(args['entities'],old_args['entities'])
        self.assertEqual(set(shlex.split(query)),set(shlex.split(lexical)))

    def test_quoted_setup_is_deliberate_and_not_classified_by_filename(self):
        query,args=self.search_for('Fix currency rounding. Read `CONTRIBUTING.md`.')
        self.assertIn('"CONTRIBUTING.md"',query)
        self.assertIn('CONTRIBUTING.md',args['entities'])

    def test_recent_paths_and_errors_survive_without_refunding_or_mutating_hints(self):
        state=dict(hints=dict(files={'old/expired.c':0,'src/cache.c':1000},
                             errors=['RATE_OVERFLOW'],error_at=1000))
        before=copy.deepcopy(state['hints'])
        with patch.object(hook.time,'time',return_value=1100):
            query,args=self.search_for('Repair currency rounding.',state=state)
        self.assertEqual(args['entities'],['src/cache.c'])
        self.assertIn('src/cache.c',shlex.split(query))
        self.assertNotIn('"src/cache.c"',query)
        self.assertIn('"RATE_OVERFLOW"',query)
        self.assertNotIn('old/expired.c',query)
        self.assertEqual(state['hints'],before)

    def test_late_explicit_anchors_survive_long_preface_and_byte_ceiling(self):
        prompt=' '.join('背景%04d'%i for i in range(1200)) + ' Repair "src/final.py" after "RATE_OVERFLOW".'
        query,args=self.search_for(prompt)
        self.assertLessEqual(len(query.encode()),4000)
        self.assertIn('"src/final.py"',query)
        self.assertIn('"RATE_OVERFLOW"',query)
        self.assertEqual(args['entities'],['src/final.py'])
        allowed=hook.terms(prompt)|{self.root.name,'src/final.py','RATE_OVERFLOW'}
        self.assertTrue(set(shlex.split(query))<=allowed)

    def test_labelled_semantic_fallback_does_not_retry_or_requote(self):
        response=dict(status='DEGRADED_NO_EMBEDDINGS',selected=[],index=[],
                      discovery=dict(state='unavailable'))
        query,args=self.search_for('Investigate src/cache.c eviction.',response=response)
        self.assertTrue(args['semantic'])
        self.assertNotIn('"src/cache.c"',query)
        # search_for asserts exactly one actual search call even on fallback.

    def test_lexical_ambient_deferred_and_capture_queries_keep_existing_contract(self):
        prompt='Repair src/rates.py after "RATE_OVERFLOW".'
        expected=hook.retrieval_intent(dict(self.event,prompt=prompt),{})['query']
        for mode in ('agent_tools','ambient'):
            with self.subTest(mode=mode):
                query,args=self.search_for(prompt,semantic=False,mode=mode)
                self.assertEqual(query,expected)
                self.assertFalse(args['semantic'])
        query,args=self.search_for('',hook_event_name='SessionStart')
        self.assertFalse(args['semantic'])
        self.assertEqual(query,self.root.name)
        for operation in ('handoff','durable'):
            memory=Mock();memory.search.return_value=dict(index=[])
            messages=[dict(role='user',text=prompt)]
            if operation=='handoff':hook.handoff_candidates(memory,self.event,messages,{})
            else:hook.durable_candidates(memory,self.event,messages)
            self.assertEqual(memory.search.call_args.args[0],expected)
            memory.search.assert_called_once()
            memory.call.assert_not_called()


if __name__=='__main__':unittest.main()
