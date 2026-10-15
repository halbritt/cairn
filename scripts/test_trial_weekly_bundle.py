"""Synthetic frozen weekly roots only; never execute setup, stores or providers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_task_eval as weekly
import trial_weekly_input as bundle
from trial_provider_mapping import original_plan


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class WeeklyBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = bundle.generated_distractors()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'weekly'
        self.root.mkdir()
        self.cases = [dict(id=f'case-{i:02}', workspace='base', copy_to='repo', cwd='repo',
            category='synthetic', primary=True, provenance=dict(type='synthetic', source='fixture'),
            wordings=dict(task=f'Task {i}', paraphrase=f'Paraphrase {i}', direct=f'Direct {i}'),
            expected=['N-current'], acceptable=[], must_not_deliver=['N-local'], over_applied=['N-other'],
            correct=[dict(type='answer', pattern='correct')], mistake=[],
            review_checks={'private_review':dict(type='answer', pattern='review')},
            descriptive={'private_label':dict(type='answer', pattern='description')}) for i in range(21)]
        self.notes = [dict(id='N-current', body='fixture paraphrase', kind='lesson', shareable=True,
                           provenance={'record_id':'historical-source', 'version':7},
                           entities=[dict(kind='file',name='src/synthetic.py')]),
                      dict(id='N-old', body='old guidance', kind='lesson', supersede_with='N-current'),
                      dict(id='N-local', body='inaccessible fixture', kind='note', shareable=False),
                      dict(id='N-other', body='other collection', kind='note', repo='trial:other')]
        write(self.root/'cases.json', dict(baseline_commit='1'*40, cases=self.cases))
        write(self.root/'corpus.json', dict(notes=self.notes))
        (self.root/'workspaces/base').mkdir(parents=True)
        (self.root/'workspaces/base/setup.sh').write_text('# never executed\n')
        write(self.root/'revisions/v2.json', dict(cases={'case-00':dict(workspace='revisions/v2/source')},
                                                corpus={'N-current':dict(body='selected revision')}))
        (self.root/'revisions/v2/source').mkdir(parents=True)
        (self.root/'revisions/v2/source/setup.sh').write_text('# never executed either\n')
        write(self.root/'revisions/v3.json', dict(cases={'case-00':dict(wordings={'task':'FUTURE'})},
                                                corpus={'N-current':dict(body='FUTURE')}))
        self.freeze()
        self.component = self.base/'inert-component'
        self.component.write_text('not an executable\n')
        self.component_pin = pin(self.component)
        arm = dict(binary=self.component_pin, hook=self.component_pin, bridge=self.component_pin,
                   api_revision='2'*40, recall_revision='3'*40, recall_mode='agent_tools',
                   semantic_fallback=True, embedding_worker=self.component_pin,
                   embedding_config=self.component_pin, embedding_model_identity=self.component_pin)
        self.reference = dict(schema='cairn.weekly-input-reference/1', root=str(self.root),
                              label_version=2, frozen_sha256=pin(self.root/'FROZEN-v2.json')['sha256'],
                              execution={})
        self.execution = dict(schema='cairn.weekly-execution/1',
            weekly_source={k:self.reference[k] for k in ('root','label_version','frozen_sha256')},
            actor_api_base='4'*40, native=dict(binary=self.component_pin, model='DECLARED-MODEL', effort='high'),
            arms=['CURRENT','CAND'], memory_arms={'CURRENT':deepcopy(arm), 'CAND':deepcopy(arm)},
            seeds=[9,2,17], wording='task', workload=self.component_pin,
            distractors=dict(count=1900, seed=20260928,
                generator_source=pin(Path(weekly.__file__)), generated_sha256=self.generated['sha256']))
        self.seal_execution()
        self.effects = []
        for target in ('subprocess.Popen','subprocess.run','subprocess.call','os.system','os.fork',
                       'socket.socket','socket.socketpair'):
            guard = patch(target, side_effect=AssertionError('runtime effect forbidden'))
            self.effects.append(guard.start())
            self.addCleanup(guard.stop)

    def freeze(self, version=2):
        frozen = weekly.label_manifest(version, root=self.root)
        frozen['frozen_at'] = 'synthetic-retained-metadata'
        write(weekly.frozen_path(version, root=self.root), frozen)
        return frozen

    def seal_execution(self):
        write(self.base/'execution.json', self.execution)
        self.reference['execution'] = pin(self.base/'execution.json')

    def reseal_source(self):
        self.freeze()
        self.reference['frozen_sha256'] = pin(self.root/'FROZEN-v2.json')['sha256']
        self.execution['weekly_source']['frozen_sha256'] = self.reference['frozen_sha256']
        self.seal_execution()

    def load(self):
        return bundle.load_weekly_input(self.reference)

    def test_preserves_private_fields_corpus_controls_and_original_identity(self):
        result = self.load()
        self.assertEqual(result['cases'], weekly.load_cases(2, root=self.root))
        self.assertEqual(result['notes'], weekly.load_corpus(2, root=self.root))
        self.assertEqual(result['cases'][0]['review_checks'], self.cases[0]['review_checks'])
        self.assertEqual(result['cases'][0]['descriptive'], self.cases[0]['descriptive'])
        self.assertEqual(set(result['cases'][0]['wordings']), {'task','paraphrase','direct'})
        self.assertFalse(result['notes'][2]['shareable'])
        self.assertEqual(result['notes'][1]['supersede_with'], 'N-current')
        self.assertEqual(result['notes'][0]['provenance']['version'], 7)
        self.assertEqual(result['frozen']['baseline_commit'], '1'*40)
        self.assertEqual(result['execution']['actor_api_base'], '4'*40)
        self.assertFalse(result['runtime_admitted'])
        self.assertEqual(result['workspace_paths']['case-00'], str(self.root/'revisions/v2/source'))
        self.assertNotIn('_prospective_workspace', result['cases'][0])

    def test_exact_126_order_and_seeds(self):
        result = self.load()
        runs = result['schedule']['runs']
        # Fixed baseline from cc826dc's cmd_agent ordering loop.
        # No production shuffle is repeated here.
        self.assertEqual(bundle.canonical_sha(runs),
                         'e974d3e01e8756363b61a822caa84aadd6b4e63bd19f4c50d40fc03743491b5d')
        self.assertEqual(runs[:2],[dict(case='case-00',arm='CURRENT',seed=9,position=0),
                                  dict(case='case-00',arm='CAND',seed=9,position=1)])
        self.assertEqual(runs[-2:],[dict(case='case-20',arm='CURRENT',seed=17,position=0),
                                   dict(case='case-20',arm='CAND',seed=17,position=1)])
        self.assertEqual(len(runs),126)
        for pair in range(63):
            left,right=runs[2*pair:2*pair+2]
            expected_seed=[9,2,17][pair//21]
            expected_case=f'case-{pair%21:02}'
            self.assertEqual([left['seed'],right['seed']],[expected_seed,expected_seed])
            self.assertEqual([left['case'],right['case']],[expected_case,expected_case])
            self.assertEqual([left['position'],right['position']],[0,1])
            self.assertEqual({left['arm'],right['arm']},{'CURRENT','CAND'})
        self.assertEqual(result['plan']['workload'], self.component_pin)

    def test_capacity_plan_accepted_by_actual_original_plan(self):
        plan=self.load()['plan']
        self.assertIsNone(original_plan(plan))
        self.assertEqual(set(plan),{'instrument','cases','arms','seeds','attempts','workload','sha256'})
        changed=deepcopy(plan);changed['attempts']=124
        with self.assertRaisesRegex(ValueError,'mapping_original_attempts'):original_plan(changed)
        changed=deepcopy(plan);changed['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'plan_mismatch'):original_plan(changed)

    def test_closed_schedule_binds_plan_source_and_execution(self):
        result=self.load();schedule=result['schedule']
        self.assertEqual(set(schedule),{'schema','plan_sha256','source','execution','actor_api_base',
                                        'wording','distractors','runs','sha256'})
        self.assertEqual(schedule['schema'],'cairn.weekly-schedule/1')
        self.assertEqual(schedule['plan_sha256'],result['plan']['sha256'])
        self.assertEqual(schedule['source'],result['source'])
        self.assertEqual(schedule['execution'],self.reference['execution'])
        self.assertEqual(schedule['actor_api_base'],'4'*40)
        self.assertEqual(schedule['wording'],'task')
        self.assertEqual(schedule['distractors'],result['generated'])
        self.assertEqual(schedule['sha256'],bundle.canonical_sha(
            {k:v for k,v in schedule.items() if k!='sha256'}))
        self.execution['seeds']=[17,2,9];self.seal_execution();changed=self.load()
        self.assertNotEqual(result['plan']['sha256'],changed['plan']['sha256'])
        self.assertEqual(changed['schedule']['plan_sha256'],changed['plan']['sha256'])
        self.assertNotEqual(schedule['sha256'],changed['schedule']['sha256'])

    def test_existing_weekly_private_grade_and_relevance_semantics(self):
        case=self.load()['cases'][0]
        grade=weekly.grade(case,dict(cwd=self.base,answer='correct review description'))
        self.assertEqual(grade['outcome'],'needs_review')
        self.assertEqual(grade['review_flags'],{'private_review':True})
        self.assertEqual(grade['descriptive'],{'private_label':True})
        memory=dict(delivered=['N-current','N-local','N-other'])
        weekly.classify_relevance(memory,case)
        self.assertEqual(memory['delivered_expected'],['N-current'])
        self.assertEqual(memory['forbidden'],['N-local','N-other'])
        self.assertEqual(memory['irrelevant'],['N-local','N-other'])

    def test_future_overlays_are_not_loaded_or_hashed(self):
        before = self.load()
        (self.root/'revisions/v3.json').write_text('{ deliberately invalid future JSON')
        (self.root/'revisions/v3').mkdir()
        (self.root/'revisions/v3/private.txt').write_text('future asset')
        after = self.load()
        self.assertEqual(before, after)

    def test_selected_case_corpus_overlay_and_workspace_drift_refuse(self):
        for name in ('cases.json','corpus.json','revisions/v2.json','revisions/v2/source/setup.sh'):
            path = self.root/name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original+b' ')
                with self.assertRaises(ValueError): self.load()
                path.write_bytes(original)

    def test_full_manifest_identity_not_only_labels_hash(self):
        path = self.root/'FROZEN-v2.json'
        original = path.read_bytes()
        for field,value in [('cases_sha256','0'*64),('corpus_sha256','0'*64),('baseline_commit','0'*40),
                            ('label_version',1),('files',999),('schema','other'),('extra','unselected')]:
            with self.subTest(field=field):
                doc = json.loads(original);doc[field]=value;write(path,doc)
                self.reference['frozen_sha256']=pin(path)['sha256']
                self.execution['weekly_source']['frozen_sha256']=self.reference['frozen_sha256']
                self.seal_execution()
                with self.assertRaises(ValueError):self.load()
        path.write_bytes(original)

    def test_manifest_byte_drift_and_execution_byte_drift_refuse(self):
        for path in (self.root/'FROZEN-v2.json', self.base/'execution.json'):
            original=path.read_bytes();path.write_bytes(original+b' ')
            with self.assertRaises(ValueError):self.load()
            path.write_bytes(original)

    def test_root_version_and_reference_shape_bound(self):
        original=deepcopy(self.reference)
        for key,value in [('root',str(self.base)),('label_version',1),('label_version',True),
                          ('label_version',0),('schema','other'),('extra',1)]:
            with self.subTest(key=key,value=value):
                self.reference=deepcopy(original);self.reference[key]=value
                with self.assertRaises(ValueError):self.load()

    def test_case_count_wordings_private_override_and_future_workspace(self):
        original=json.loads((self.root/'cases.json').read_text())
        changes=[lambda d:d['cases'].pop(),
                 lambda d:d['cases'][1]['wordings'].pop('direct'),
                 lambda d:d['cases'][1].update(_prospective_workspace=str(self.base)),
                 lambda d:d['cases'][1].update(workspace='revisions/v3/hidden'),
                 lambda d:d['cases'][1].update(cwd='../escape')]
        for change in changes:
            doc=deepcopy(original);change(doc);write(self.root/'cases.json',doc);self.reseal_source()
            with self.assertRaises(ValueError):self.load()

    def test_seed_shape_and_two_exact_arms(self):
        original=deepcopy(self.execution)
        for key,value in [('seeds',[1,2]),('seeds',[1,2,2]),('seeds',[True,2,3]),
                          ('seeds',[1,2,3,4]),('arms',['CURRENT','OTHER']),('wording','paraphrase')]:
            self.execution=deepcopy(original);self.execution[key]=value;self.seal_execution()
            with self.assertRaises(ValueError):self.load()

    def test_same_embedding_launcher_config_and_model_identity(self):
        other=self.base/'different';other.write_text('different bytes')
        for field in ('embedding_worker','embedding_config','embedding_model_identity'):
            original=deepcopy(self.execution)
            self.execution['memory_arms']['CAND'][field]=pin(other);self.seal_execution()
            with self.assertRaisesRegex(ValueError,'weekly_embedding_mismatch'):self.load()
            self.execution=original

    def test_component_and_workload_pin_drift(self):
        self.component.write_text('changed')
        with self.assertRaises(ValueError):self.load()

    def test_independent_workload_pin_is_required_without_invented_demands(self):
        workload=self.base/'workload';workload.write_text('opaque caller demand declaration')
        self.execution['workload']=pin(workload);self.seal_execution()
        self.assertEqual(self.load()['plan']['workload'],pin(workload))
        workload.write_text('changed independent workload')
        with self.assertRaisesRegex(ValueError,'pin_mismatch'):self.load()

    def test_version_one_filename_and_type_exact_execution_binding(self):
        self.freeze(1)
        self.reference['label_version']=1
        self.reference['frozen_sha256']=pin(self.root/'FROZEN.json')['sha256']
        self.execution['weekly_source']={k:self.reference[k] for k in ('root','label_version','frozen_sha256')}
        self.seal_execution()
        result=self.load()
        self.assertEqual(result['cases'],self.cases)
        self.assertEqual(result['notes'],self.notes)
        self.execution['weekly_source']['label_version']=True;self.seal_execution()
        with self.assertRaisesRegex(ValueError,'weekly_source_binding'):self.load()

    def test_legacy_embedding_backend_not_conflated_with_hook_selector(self):
        for mode,semantic in [('ambient',False),('agent_tools',False),('ambient',True)]:
            for arm in self.execution['memory_arms'].values():
                arm.update(recall_mode=mode,semantic_fallback=semantic)
            self.seal_execution();result=self.load()
            self.assertEqual(result['execution']['memory_arms']['CURRENT']['semantic_fallback'],semantic)
            self.assertFalse(result['admission'])
        self.execution['memory_arms']['CURRENT']['semantic_fallback']=1;self.seal_execution()
        with self.assertRaisesRegex(ValueError,'weekly_recall_declaration'):self.load()

    def test_selected_duplicate_keys_refuse_even_when_frozen(self):
        path=self.root/'cases.json'
        original=path.read_text()
        path.write_text(original[:-1]+',"baseline_commit":"'+'1'*40+'"}')
        self.reseal_source()
        with self.assertRaisesRegex(ValueError,'duplicate_json_key'):self.load()

    def test_frozen_at_retained_but_not_identity_and_reference_pin_is_owned(self):
        before=self.load()
        path=self.root/'FROZEN-v2.json';doc=json.loads(path.read_text())
        doc['frozen_at']='different metadata';write(path,doc)
        self.reference['frozen_sha256']=pin(path)['sha256']
        self.execution['weekly_source']['frozen_sha256']=self.reference['frozen_sha256']
        self.seal_execution();after=self.load()
        self.assertEqual(before['source']['frozen_identity'],after['source']['frozen_identity'])
        self.assertEqual(before['plan'],after['plan'])
        self.assertNotEqual(before['source']['manifest_sha256'],after['source']['manifest_sha256'])
        self.assertNotEqual(before['schedule']['sha256'],after['schedule']['sha256'])
        self.assertEqual(after['frozen']['frozen_at'],'different metadata')
        self.reference['execution']['sha256']='0'*64
        self.assertNotEqual(after['schedule']['execution']['sha256'],'0'*64)

    def test_distractor_count_seed_source_digest_and_collision(self):
        original=deepcopy(self.execution)
        for key,value in [('count',1899),('seed',0),('generated_sha256','0'*64),
                          ('generator_source',self.component_pin)]:
            self.execution=deepcopy(original);self.execution['distractors'][key]=value;self.seal_execution()
            with self.assertRaises(ValueError):self.load()
        self.execution=original;self.seal_execution()
        doc=json.loads((self.root/'corpus.json').read_text());doc['notes'].append(dict(id='D-00000',body='collision',kind='note'))
        write(self.root/'corpus.json',doc);self.reseal_source()
        with self.assertRaisesRegex(ValueError,'weekly_distractor_collision'):self.load()

    def test_distractors_separate_original_identity_and_stable_generator(self):
        result=self.load()
        self.assertEqual(len(result['distractor_notes']),1900)
        self.assertEqual(result['distractor_notes'][0],weekly.distractor(0,seed=20260928))
        self.assertEqual(result['distractor_notes'][-1],weekly.distractor(1899,seed=20260928))
        self.assertEqual(result['generated']['sha256'],self.generated['sha256'])
        self.assertEqual(len(result['notes']),4)
        self.assertNotEqual(result['generated']['sha256'],result['frozen']['corpus_sha256'])

    def test_plain_regular_bounded_source_no_symlink(self):
        link=self.root/'workspaces/base/link';link.symlink_to(self.component)
        with self.assertRaises(ValueError):self.load()
        link.unlink()
        with patch.object(bundle,'SOURCE_BYTES',1):
            with self.assertRaisesRegex(ValueError,'weekly_source_bounds'):self.load()

    def test_no_runtime_effects_and_inputs_not_mutated(self):
        before=deepcopy(self.reference);result=self.load()
        self.assertEqual(before,self.reference)
        result['cases'][0]['wordings']['task']='caller mutation'
        self.assertNotEqual(self.load()['cases'][0]['wordings']['task'],'caller mutation')
        for effect in self.effects:effect.assert_not_called()


if __name__=='__main__':
    unittest.main()
