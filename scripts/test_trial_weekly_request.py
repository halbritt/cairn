"""Owned synthetic weekly controls; no original fixture, native or store use."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import trial_paired_request as paired
import trial_task_eval as evaluator
import trial_weekly_input as weekly
import trial_weekly_request as dispatch
from trial_task_permissions import settings_permissions
from test_trial_provider_mapping import Fixture as ProviderFixture
from test_trial_paired_capacity import write_pin

NOW = dt.datetime(2026,10,9,20,tzinfo=dt.timezone.utc)
START, END = '2026-10-09T19:59:00Z', '2026-10-09T20:05:00Z'


def pin(path):
    return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class Fixture:
    def __init__(self, root):
        self.root=root
        (root/'registry').mkdir(mode=0o700)
        (root/'provider').mkdir()
        self.provider=ProviderFixture(root/'provider')
        plan,_,capacity,spend,mapping=self.provider.save()
        # The companion requires the reservation to cover the entire declared
        # capacity interval, as the integrated fixture does.
        reservation=paired.read_json_pin(self.provider.capacity['reservation'])
        reservation['valid_until']=self.provider.capacity['valid_until']
        self.provider.capacity['reservation']=write_pin(root/'provider/reservation.json',reservation)
        capacity=write_pin(root/'provider/capacity.json',self.provider.capacity)
        self.provider.mapping['capacity_sha256']=capacity['sha256']
        mapping=write_pin(root/'provider/mapping.json',self.provider.mapping)
        self.source=root/'weekly'
        (self.source/'workspaces/base').mkdir(parents=True)
        (self.source/'workspaces/base/setup.sh').write_text('# inert; never executed\n')
        (self.source/'workspaces/base/private-grade.txt').write_text('synthetic private grader asset')
        self.cases=[dict(id=ident,workspace='base',copy_to='repo',cwd='repo',
            wordings=dict(task='synthetic task '+ident,paraphrase='private paraphrase',direct='private direct'),
            provenance=dict(type='synthetic',source='fixture'),expected=['current'],acceptable=[],
            must_not_deliver=['local'],over_applied=['other'],correct=[dict(type='answer',pattern='ok')],
            mistake=[],review_checks={'flag':dict(type='answer',pattern='review')},
            descriptive={'detail':dict(type='answer',pattern='detail')}) for ident in plan['cases']]
        self.notes=[dict(id='current',body='PRIVATE WEEKLY NOTE',kind='lesson',shareable=True,
                         provenance=dict(record_id='synthetic-original',version=7)),
                    dict(id='old',body='old',kind='note',supersede_with='current'),
                    dict(id='local',body='PRIVATE LOCAL NOTE',kind='note',shareable=False),
                    dict(id='other',body='other collection',kind='note',repo='trial:other')]
        write_pin(self.source/'cases.json',dict(baseline_commit='1'*40,cases=self.cases))
        write_pin(self.source/'corpus.json',dict(notes=self.notes))
        self.freeze()
        component=root/'component';component.write_text('inert pinned file')
        self.component=pin(component)
        arm=dict(binary=self.component,hook=self.component,bridge=self.component,
            api_revision='2'*40,recall_revision='revision',recall_mode='agent_tools',semantic_fallback=True,
            embedding_worker=self.component,embedding_config=self.component,embedding_model_identity=self.component)
        self.reference=dict(schema=weekly.REFERENCE_SCHEMA,root=str(self.source),label_version=1,
                            frozen_sha256=pin(self.source/'FROZEN.json')['sha256'],execution={})
        self.execution=dict(schema=weekly.EXECUTION_SCHEMA,
            weekly_source={k:self.reference[k] for k in ('root','label_version','frozen_sha256')},
            actor_api_base='3'*40,native=dict(binary=self.component,model='fixture-model',effort='high'),
            arms=['CURRENT','CAND'],memory_arms={a:copy.deepcopy(arm) for a in ('CURRENT','CAND')},
            seeds=[1,2,3],wording='task',workload=plan['workload'],distractors=dict(count=1900,seed=20260928,
                generator_source=pin(Path(evaluator.__file__)),generated_sha256=weekly.generated_distractors()['sha256']))
        self.reference['execution']=write_pin(root/'execution.json',self.execution)
        self.bundle=weekly.load_weekly_input(self.reference)
        artifact=write_pin(root/'review-artifact.json',dict(synthetic_review=True))
        profile_source=write_pin(root/'profile-source.json',dict(commit=paired.IMPLEMENTATION_SOURCE_COMMIT,
            files={'scripts/trial_task_permissions.py':paired.IMPLEMENTATION_SOURCE_SHA}))
        settings=settings_permissions();settings['allow']+=['Bash(git add *)','Bash(git commit *)','Bash(make contract)']
        profile_settings=write_pin(root/'profile-settings.json',settings)
        uid=lambda:str(uuid.uuid4())
        self.request=dict(schema='cairn.weekly-eval.request/1',request_id=uid(),campaign_id=uid(),occurrence_id=uid(),
            scheduled_at=START,expires_at=END,weekly_input=self.reference,plan=self.bundle['plan'],
            schedule=write_pin(root/'schedule.json',self.bundle['schedule']),
            dispositions={name:dict(artifact=artifact,subject_sha256='0'*64,reviewer_id=uid(),
                state='accepted',valid_until=END) for name in paired.REVIEWS},limits=dict(paired.ORIGINAL_LIMITS),
            capacity=capacity,grading=dict(primary=dict(agent_id=uid(),model='fixture-model'),
                secondary=dict(agent_id=uid(),model='fixture-model'),excluded_agents=[uid()],rubric=artifact,
                changed_pair_definition=artifact,blinding_policy=artifact,review_budget=artifact,dispute_owner=uid()),
            output=str(root/'output'),reply=dict(event_id=uid(),recipient_id=uid(),correlation_id=uid()),
            supervisor_source=artifact,permission_profile=dict(name='implementation/1',source=profile_source,settings=profile_settings))
        self.companion=dict(schema='cairn.paired-companion/1',request_sha256='',spend_request=spend,
                            mapping=mapping,observed_at=START,valid_until=END)
        self.review=dict(schema='cairn.paired-companion-review/1',subject_sha256='',artifact=artifact,
            reviewer_id=uid(),state='accepted',observed_at=START,valid_until=END)
        self.seal_reviews()

    def freeze(self):
        frozen=evaluator.label_manifest(1,root=self.source)
        frozen['frozen_at']='synthetic metadata'
        write_pin(self.source/'FROZEN.json',frozen)

    def seal_reviews(self):
        for name,subject in dispatch.review_subjects(self.request,self.bundle).items():
            self.request['dispositions'][name]['subject_sha256']=subject
        self.seal_companion()

    def seal_companion(self):
        self.companion['request_sha256']=paired.canonical_sha(self.request)
        self.review['subject_sha256']=paired.canonical_sha(self.companion)
        self.references=dict(companion=write_pin(self.root/'companion.json',self.companion),
                             review=write_pin(self.root/'companion-review.json',self.review))

    def reseal_bundle(self):
        self.reference['frozen_sha256']=pin(self.source/'FROZEN.json')['sha256']
        self.execution['weekly_source']={k:self.reference[k] for k in ('root','label_version','frozen_sha256')}
        self.reference['execution']=write_pin(self.root/'execution.json',self.execution)
        self.bundle=weekly.load_weekly_input(self.reference)
        self.request.update(plan=self.bundle['plan'],schedule=write_pin(self.root/'schedule.json',self.bundle['schedule']))

    def save_provider(self):
        plan,_,capacity,spend,mapping=self.provider.save()
        reservation=paired.read_json_pin(self.provider.capacity['reservation'])
        reservation['valid_until']=self.provider.capacity['valid_until']
        self.provider.capacity['reservation']=write_pin(self.root/'provider/reservation.json',reservation)
        capacity=write_pin(self.root/'provider/capacity.json',self.provider.capacity)
        self.provider.mapping['capacity_sha256']=capacity['sha256']
        mapping=write_pin(self.root/'provider/mapping.json',self.provider.mapping)
        self.request['capacity']=capacity
        self.companion.update(spend_request=spend,mapping=mapping)
        self.execution['workload']=plan['workload']
        self.reseal_bundle()
        self.seal_reviews()

    def cli(self, command, *, request_path=None, extra=(), imported=False):
        path=request_path or self.root/'request.json'
        path.write_text(json.dumps(self.request))
        args=[command,str(path)]
        if command=='prepare':
            args+=['--registry',str(self.root/'registry')]
        for name,reference in self.references.items():
            args+=['--'+name,reference['path'],'--'+name+'-sha256',reference['sha256']]
        args+=list(extra)
        script=str(Path(dispatch.__file__).resolve())
        code="import datetime as d,sys,runpy; from unittest.mock import patch; "
        code+="Clock=type('Clock',(d.datetime,),{'now':classmethod(lambda cls,tz=None:d.datetime(2026,10,9,20,tzinfo=d.timezone.utc))}); "
        code+="sys.path.insert(0,"+repr(str(Path(script).parent))+"); sys.argv="+repr([script,*args])+"; "
        call="import trial_weekly_request as p; sys.exit(p.main())" if imported else "runpy.run_path(sys.argv[0],run_name='__main__')"
        code+="\nwith patch.object(d,'datetime',Clock):\n "+call
        return subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=20)


class WeeklyRequestTests(unittest.TestCase):
    def test_transitive_provider_evidence_and_components_cannot_overlap_registry(self):
        for target in ('workload_basis','price_source','account_basis','component'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory));registry=f.root/'registry'
                reference=write_pin(registry/'selected.json',dict(synthetic=True))
                if target=='workload_basis': f.provider.workload['roles']['CURRENT']['basis']=reference
                elif target=='price_source':
                    f.provider.mapping['quotes']['qA']['price_source']=reference
                    f.provider.prices['quotes']['qA']['source_sha256']=reference['sha256']
                elif target=='account_basis': f.provider.mapping['quotes']['qA']['accounts']['account-A']['basis']=reference
                else: f.execution['memory_arms']['CAND']['bridge']=reference
                f.save_provider()
                before={p.name:p.read_bytes() for p in registry.iterdir()}
                with self.assertRaisesRegex(paired.Refusal,'control_path_overlap'):
                    dispatch.prepare(f.request,registry=registry,now=NOW,**f.references)
                self.assertEqual({p.name:p.read_bytes() for p in registry.iterdir()},before)
                self.assertFalse((f.root/'output').exists())

    def test_retention_bound_precedes_any_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));artifact=f.root/'large-selected-artifact.bin'
            artifact.write_bytes(b'x'*paired.FILE_LIMIT)
            f.request['supervisor_source']=pin(artifact);f.seal_reviews()
            self.assert_unwritten(f,'weekly_retention_bound')

    def test_review_subjects_domain_separate_and_bind_complete_core(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));r=f.request;b=f.bundle;e=f.execution
            subjects={
                'task':dict(reference=r['weekly_input'],source=b['source'],wording='task'),
                'oracle':dict(source=b['source'],grading=r['grading'],legacy_grader_source=e['distractors']['generator_source']),
                'comparator':dict(plan=b['plan'],schedule_sha256=b['schedule']['sha256'],
                    interfaces=dict(actor_api_base=e['actor_api_base'],memory_arms=e['memory_arms']),
                    corpus=dict(source=b['source'],distractors=e['distractors'])),
                'supervisor':{k:r[k] for k in ('supervisor_source','limits','permission_profile')},
                'native':dict(native=e['native'],permission_profile=r['permission_profile'],limits=r['limits'],supervisor_source=r['supervisor_source']),
                'integration':dict(request_core={k:v for k,v in r.items() if k!='dispositions'})}
            expected={k:hashlib.sha256(json.dumps(dict(schema='cairn.weekly-'+k+'-review/1',**v),
                ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest() for k,v in subjects.items()}
            self.assertEqual(dispatch.review_subjects(r,b),expected)
            for disposition in r['dispositions'].values(): disposition['subject_sha256']='f'*64
            self.assertEqual(dispatch.review_subjects(r,b),expected)
            self.assertEqual(len(set(expected.values())),6)

    def test_native_model_seam_is_required_and_has_no_model_map_override(self):
        from trial_paired_companion import validate_companion
        with self.assertRaises(TypeError): validate_companion({},None,None,now=NOW)
        with self.assertRaises(TypeError): validate_companion({},None,None,now=NOW,native_model='x',models={})

    def test_cli_errors_are_sanitized_and_refuse_before_writes(self):
        for args,code in [(['check','/PRIVATE-request','--unexpected'],'invalid_arguments'),
                          (['check','/PRIVATE-request','--companion','/PRIVATE-companion'],'explicit_pin_required')]:
            result=subprocess.run([sys.executable,'-B',dispatch.__file__,*args],capture_output=True,text=True,timeout=20)
            self.assertEqual((result.returncode,result.stderr),(2,''))
            self.assertEqual(json.loads(result.stdout),dict(status='refused',code=code))
        with tempfile.TemporaryDirectory(prefix='PRIVATE-') as directory:
            f=Fixture(Path(directory));f.request['grading']['primary']['model']='PRIVATE-substitution';f.seal_reviews()
            for operation in ('check','prepare'):
                result=f.cli(operation)
                self.assertEqual((result.returncode,result.stderr),(2,''))
                self.assertEqual(json.loads(result.stdout),dict(status='refused',code='workload_role_mismatch'))
            self.assertEqual(list((f.root/'registry').iterdir()),[])

    def assert_unwritten(self, fixture, code, **references):
        before={p.name:p.read_bytes() for p in (fixture.root/'registry').iterdir()}
        for operation in ('check','prepare'):
            with self.subTest(operation=operation):
                kwargs=dict(now=NOW,**(references or fixture.references))
                if operation=='prepare':
                    kwargs['registry']=fixture.root/'registry'
                with self.assertRaisesRegex(paired.Refusal,'^'+code+'$'):
                    getattr(dispatch,operation)(fixture.request,**kwargs)
                self.assertFalse((fixture.root/'output').exists())
                self.assertEqual({p.name:p.read_bytes() for p in (fixture.root/'registry').iterdir()},before)

    def test_full_plan_and_schedule_equality_reject_self_hashed_substitution(self):
        for variant in ('plan_extra','plan_order','schedule_order','schedule_extra','schedule_bool'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory))
                if variant.startswith('plan'):
                    changed=copy.deepcopy(f.request['plan'])
                    if variant=='plan_extra': changed['unselected']=True
                    else: changed['cases'].reverse()
                    changed['sha256']=paired.canonical_sha({k:v for k,v in changed.items() if k!='sha256'})
                    f.request['plan']=changed
                else:
                    changed=copy.deepcopy(f.bundle['schedule'])
                    if variant=='schedule_order': changed['runs'][0:2]=reversed(changed['runs'][0:2])
                    elif variant=='schedule_extra': changed['unselected']=True
                    else: changed['runs'][0]['position']=False
                    changed['sha256']=paired.canonical_sha({k:v for k,v in changed.items() if k!='sha256'})
                    f.request['schedule']=write_pin(f.root/'schedule.json',changed)
                f.seal_reviews()
                self.assert_unwritten(f,'weekly_plan_mismatch' if variant.startswith('plan') else 'weekly_schedule_mismatch')

    def test_repinned_source_execution_and_grading_invalidate_selected_subjects(self):
        for variant in ('case','private_asset','corpus','frozen_metadata','model','effort','config','actor_api','rubric'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory));before=dispatch.review_subjects(f.request,f.bundle)
                plan=copy.deepcopy(f.request['plan'])
                if variant=='case':
                    f.cases[0]['wordings']['paraphrase']='changed private paraphrase'
                    write_pin(f.source/'cases.json',dict(baseline_commit='1'*40,cases=f.cases));f.freeze()
                elif variant=='private_asset':
                    (f.source/'workspaces/base/private-grade.txt').write_text('changed private asset');f.freeze()
                elif variant=='corpus':
                    f.notes[0]['body']='changed private corpus'
                    write_pin(f.source/'corpus.json',dict(notes=f.notes));f.freeze()
                elif variant=='frozen_metadata':
                    frozen=json.loads((f.source/'FROZEN.json').read_bytes());frozen['frozen_at']='changed metadata'
                    write_pin(f.source/'FROZEN.json',frozen)
                elif variant in ('model','effort'):
                    f.execution['native'][variant]='other-model' if variant=='model' else 'max'
                elif variant=='config':
                    config=write_pin(f.root/'new-embedding-config.json',dict(synthetic=True))
                    for arm in f.execution['memory_arms'].values(): arm['embedding_config']=config
                elif variant=='actor_api': f.execution['actor_api_base']='4'*40
                else: f.request['grading']['rubric']=write_pin(f.root/'new-rubric.json',dict(changed=True))
                f.reseal_bundle();f.seal_companion()
                after=dispatch.review_subjects(f.request,f.bundle)
                self.assertNotEqual(before['integration'],after['integration'])
                required={'case':('task','oracle','comparator'),'private_asset':('task','oracle','comparator'),
                    'corpus':('task','oracle','comparator'),'frozen_metadata':('task','oracle','comparator'),
                    'model':('task','native','comparator'),'effort':('task','native','comparator'),
                    'config':('task','comparator'),'actor_api':('task','comparator'),'rubric':('oracle',)}[variant]
                for subject in required: self.assertNotEqual(before[subject],after[subject],subject)
                self.assertEqual(plan,f.request['plan'])
                self.assert_unwritten(f,'review_subject_mismatch')

    def test_stale_companion_binds_entire_request_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));old=copy.deepcopy((f.companion,f.review))
            f.request['reply']['event_id']=str(uuid.uuid4());f.seal_reviews()
            f.companion,f.review=old
            f.references=dict(companion=write_pin(f.root/'companion.json',f.companion),
                              review=write_pin(f.root/'companion-review.json',f.review))
            self.assert_unwritten(f,'companion_request_mismatch')

    def test_model_substitution_reaches_real_companion_from_both_dispatchers(self):
        from test_trial_paired_companion import WeeklyFixture as PairedFixture
        from trial_task_input import freeze_input
        for schema in ('weekly','paired'):
            for role in ('native','primary','secondary'):
                with self.subTest(schema=schema,role=role),tempfile.TemporaryDirectory() as directory:
                    f=(Fixture if schema=='weekly' else PairedFixture)(Path(directory))
                    if role=='native' and schema=='weekly':
                        f.execution['native']['model']='substituted-native';f.reseal_bundle()
                    elif role=='native':
                        root=f.root/'input';document=json.loads((root/'input.json').read_bytes())
                        document['native']['model']='substituted-native'
                        (root/'input.json').write_text(json.dumps(document));(root/'FROZEN.json').unlink();freeze_input(root)
                        digest=pin(root/'FROZEN.json')['sha256']
                        f.request['frozen_input']['manifest_sha256']=digest
                        f.request['dispositions']['integration']['subject_sha256']=digest
                        f.request['dispositions']['task']['subject_sha256']=pin(root/'input.json')['sha256']
                    else: f.request['grading'][role]['model']='substituted-grader'
                    if schema=='weekly':
                        f.seal_reviews();self.assert_unwritten(f,'workload_role_mismatch')
                    else:
                        f.save()
                        with self.assertRaisesRegex(paired.Refusal,'workload_role_mismatch'):
                            paired.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)
                        self.assertFalse((f.root/'output').exists());self.assertEqual(list((f.root/'registry').iterdir()),[])

    def test_exact_actor_and_distinct_grader_models_are_derived_without_override(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));p=f.provider
            expected=dict(CURRENT='actor-model',CAND='actor-model',primary='primary-model',secondary='secondary-model')
            f.execution['native']['model']='actor-model'
            for role,model in expected.items():
                if role in ('primary','secondary'): f.request['grading'][role]['model']=model
                p.workload['roles'][role]['jobs'][0]['model']=model;p.routes['routes'][role]['model']=model
                old='qB' if role=='CAND' else 'qA';quote='q'+role
                p.prices['quotes'][quote]=dict(copy.deepcopy(p.prices['quotes'][old]),model=model)
                entry=copy.deepcopy(p.mapping['quotes'][old])
                for account in entry['accounts'].values(): account['model']=model
                p.mapping['quotes'][quote]=entry;p.binding['routes'][role]=dict(model=model,quotes=[quote])
            del p.prices['quotes']['qB'];del p.mapping['quotes']['qB']
            f.save_provider()
            dispatch.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)
            projection=json.loads((f.root/'output/provider-projection.json').read_bytes())
            self.assertEqual({role:projection['job_bindings'][role]['model'] for role in expected},expected)
            self.assertEqual(projection['spend']['total_units'],656450)

    def test_companion_and_review_are_required_and_must_remain_current(self):
        for variant,code in [('missing','weekly_companion_required'),('expired','companion_interval'),
                              ('reviewer','companion_reviewer_is_grader'),('stale','companion_review_subject')]:
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory))
                if variant=='missing':
                    self.assert_unwritten(f,code,companion=None,review=f.references['review']);continue
                if variant=='expired': f.review['valid_until']='2026-10-09T19:59:59Z'
                elif variant=='reviewer': f.review['reviewer_id']=f.request['grading']['primary']['agent_id']
                else: f.review['subject_sha256']='0'*64
                f.references['review']=write_pin(f.root/'companion-review.json',f.review)
                self.assert_unwritten(f,code)

    def test_scope_limits_permissions_freshness_and_grader_exclusions(self):
        changes=[(lambda f:f.request.update(extra=True),'weekly_request_shape'),
            (lambda f:f.request.update(schema=paired.SCHEMA),'weekly_request_schema'),
            (lambda f:f.request['limits'].update(pulls=5),'limits_changed'),
            (lambda f:f.request['limits'].update(native_searches=2.0),'limits_changed'),
            (lambda f:f.request.update(expires_at=START),'request_expired'),
            (lambda f:f.request['permission_profile'].update(name='unknown'),'permission_profile_unknown'),
            (lambda f:f.request['grading']['excluded_agents'].append(f.request['grading']['primary']['agent_id']),'grader_not_independent'),
            (lambda f:f.request['grading'].update(dispute_owner=f.request['grading']['secondary']['agent_id']),'grader_not_independent'),
            (lambda f:f.request['dispositions']['task'].update(reviewer_id=f.request['grading']['primary']['agent_id']),'grader_not_independent'),
            (lambda f:f.request['dispositions']['native'].update(valid_until=START),'review_unavailable')]
        for change,code in changes:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory));change(f);f.seal_reviews();self.assert_unwritten(f,code)

    def test_partial_fsync_failure_keeps_holds_and_never_retries(self):
        for count in (1,2,5):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory));original=os.fsync;calls=0
                def fail(fd):
                    nonlocal calls
                    calls+=1
                    if calls==count: raise OSError('synthetic fsync failure')
                    original(fd)
                with patch.object(os,'fsync',fail):
                    with self.assertRaises(OSError): dispatch.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)
                markers=list((f.root/'registry').iterdir())
                self.assertEqual(len(markers),1 if count==1 else 2)
                for marker in markers: self.assertEqual(json.loads(marker.read_bytes())['state'],'held_preparation')
                with self.assertRaisesRegex(paired.Refusal,'target_exists' if count==5 else 'occurrence_registered'):
                    dispatch.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)

    def test_cli_check_prepare_and_imported_main(self):
        for imported in (False,True):
            with self.subTest(imported=imported),tempfile.TemporaryDirectory() as directory:
                f=Fixture(Path(directory))
                for command in ('check','prepare'):
                    result=f.cli(command,imported=imported)
                    self.assertEqual((result.returncode,result.stderr),(0,''),result.stdout+result.stderr)
                    self.assertFalse(json.loads(result.stdout)['runtime_admitted'])

    def test_launch_refuses_before_reading_private_input(self):
        with patch.object(paired,'read_json',side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(paired.Refusal,'provider_execution_unavailable'):
                dispatch.launch('/PRIVATE-nonexistent')
        result=subprocess.run([sys.executable,'-B',dispatch.__file__,'launch','/PRIVATE-nonexistent'],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual((result.returncode,result.stderr),(2,''))
        self.assertEqual(json.loads(result.stdout),dict(status='refused',code='provider_execution_unavailable'))

    def test_cli_protects_selected_request_file_before_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));registry=f.root/'registry'
            result=f.cli('prepare',request_path=registry/'source-request.json')
            self.assertEqual((result.returncode,result.stderr),(2,''))
            self.assertEqual(json.loads(result.stdout),dict(status='refused',code='control_path_overlap'))
            self.assertEqual([p.name for p in registry.iterdir()],['source-request.json'])
            self.assertFalse((f.root/'output').exists())

    def test_registry_cannot_equal_selected_source_root(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));f.source.chmod(0o700)
            before=set(f.source.iterdir())
            with self.assertRaisesRegex(paired.Refusal,'control_path_overlap'):
                dispatch.prepare(f.request,registry=f.source,now=NOW,**f.references)
            self.assertEqual(set(f.source.iterdir()),before)
            self.assertFalse((f.root/'output').exists())

    def test_registry_cannot_contain_selected_external_review_source(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory));registry=f.root/'registry'
            f.request['supervisor_source']=write_pin(registry/'selected.json',dict(source='synthetic'))
            f.seal_reviews()
            before={p.name:p.read_bytes() for p in registry.iterdir()}
            with self.assertRaisesRegex(paired.Refusal,'control_path_overlap'):
                dispatch.prepare(f.request,registry=registry,now=NOW,**f.references)
            self.assertEqual({p.name:p.read_bytes() for p in registry.iterdir()},before)
            self.assertFalse((f.root/'output').exists())

    def test_preparation_retains_selected_bytes_and_holds_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(Path(directory))
            result=dispatch.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)
            output=f.root/'output'
            self.assertEqual(result['status'],'prepared_for_review')
            self.assertFalse(result['runtime_admitted'])
            self.assertEqual((output/'schedule.json').read_bytes(),(f.root/'schedule.json').read_bytes())
            self.assertEqual((output/'execution.json').read_bytes(),(f.root/'execution.json').read_bytes())
            self.assertEqual(json.loads((output/'request.json').read_bytes()),f.request)
            projection=json.loads((output/'provider-projection.json').read_bytes())
            self.assertEqual(projection['spend']['total_units'],656450)
            for item in output.iterdir():
                self.assertEqual(item.stat().st_mode & 0o777,0o600)
                self.assertNotIn(b'PRIVATE WEEKLY NOTE',item.read_bytes())
            self.assertEqual(output.stat().st_mode & 0o777,0o700)
            self.assertEqual(len(list((f.root/'registry').iterdir())),2)
            f.request['output']=str(f.root/'different-output');f.seal_reviews()
            with self.assertRaisesRegex(paired.Refusal,'occurrence_registered'):
                dispatch.prepare(f.request,registry=f.root/'registry',now=NOW,**f.references)
            self.assertFalse((f.root/'different-output').exists())

    def test_original_weekly_check_uses_real_companion_and_keeps_private_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Fixture(Path(directory));before=copy.deepcopy(fixture.request)
            result=dispatch.check(fixture.request,now=NOW,**fixture.references)
            self.assertEqual(fixture.request,before)
            self.assertEqual(result['status'],'validated_for_review')
            self.assertFalse(result['runtime_admitted'])
            self.assertEqual(result['schedule_sha256'],fixture.bundle['schedule']['sha256'])
            self.assertEqual(result['plan_sha256'],fixture.provider.plan['sha256'])
            self.assertEqual(len(fixture.bundle['schedule']['runs']),126)
            self.assertEqual(len(fixture.bundle['distractor_notes']),1900)
            self.assertEqual(fixture.bundle['notes'],fixture.notes)
            self.assertEqual(fixture.bundle['cases'],fixture.cases)
            self.assertIn('provider_evidence',result)
            self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertFalse((fixture.root/'output').exists())
            self.assertEqual(list((fixture.root/'registry').iterdir()),[])


if __name__=='__main__':
    unittest.main()
