"""Owned disposable request files; no provider, production memory or database."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import uuid

from test_trial_task_prospective import fixture
from trial_task_input import freeze_input
from trial_task_permissions import settings_permissions
import trial_paired_request as paired


NOW = dt.datetime(2026, 10, 9, 20, tzinfo=dt.timezone.utc)


def pinned(path):
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def request_fixture(root):
    (root/'registry').mkdir(mode=0o700)
    inputs = fixture(root / 'input')
    doc = json.loads((inputs / 'input.json').read_text())
    binary = pinned(Path(sys.executable).resolve())
    hook = root / 'hook.py'
    hook.write_text('# inert test component\n')
    doc['arms'] = ['CURRENT', 'CAND']
    doc['memory_arms'] = {arm: dict(binary=binary, hook=pinned(hook), api_revision=rev,
        recall_revision=rev, recall_mode='ambient', semantic_fallback=False)
        for arm, rev in [('CURRENT', '1'*40), ('CAND', '2'*40)]}
    (inputs / 'input.json').write_text(json.dumps(doc))
    freeze_input(inputs)
    artifact = root / 'review.json'
    artifact.write_text('{"fixture_only":true}')
    profile_source=root/'profile-source.json'
    profile_source.write_text(json.dumps(dict(commit='6fb0fcd07f9cd7f8959190088bb9d070ff863764',
        files={'scripts/trial_task_permissions.py':'43a097332584a501b752e8fe15ded91d204bb8a62af34180ec5694b763b188b2'})))
    profile_settings=root/'profile-settings.json'
    # The reviewed implementation profile retains the existing rules plus these
    # three operations. This fixture uses the unchanged existing constructor.
    settings=settings_permissions()
    settings['allow'] += ['Bash(git add *)','Bash(git commit *)','Bash(make contract)']
    profile_settings.write_text(json.dumps(settings))
    plan = dict(instrument='weekly-frozen-cases', cases=['new-work'], arms=['CURRENT','CAND'], seeds=[1], attempts=2)
    plan['sha256'] = paired.canonical_sha(plan)
    uid = lambda: str(uuid.uuid4())
    dispositions = {name: dict(artifact=pinned(artifact), subject_sha256='3'*64,
        reviewer_id=uid(), state='accepted', valid_until='2026-10-09T21:00:00Z')
        for name in paired.REVIEWS}
    capacity = root / 'capacity.json'
    account=root/'account.json';account.write_text(json.dumps(dict(schema='cairn.paired-account-binding/1',
        accounts=[dict(account_id='fixture-account',dedicated=True,interactive_use=False)])))
    routes=root/'routes.json';routes.write_text(json.dumps(dict(schema='cairn.paired-route-binding/1',
        plan_sha256=plan['sha256'],routes={role:dict(account_id='fixture-account',model='fixture-model')
            for role in ('CURRENT','CAND','primary','secondary')})))
    policy=root/'quota-policy.json';policy.write_text(json.dumps(dict(schema='cairn.paired-quota-policy/1',
        units=['calls','tokens'],percent_conversion=False)))
    resources=[dict(account_id='fixture-account', model='fixture-model', window_id='fixture-window',
        unit='calls', remaining=20, required=4)]
    reservation=root/'reservation.json';reservation.write_text(json.dumps(dict(schema='cairn.paired-reservation/1',
        plan_sha256=plan['sha256'],resources_sha256=paired.canonical_sha(resources),state='held',valid_until='2026-10-09T20:05:00Z')))
    capacity.write_text(json.dumps(dict(schema='cairn.paired-capacity-evidence/1',
        plan_sha256=plan['sha256'], state='supported', observed_at='2026-10-09T19:59:00Z',
        valid_until='2026-10-09T20:05:00Z', account_binding=pinned(account),
        route_binding=pinned(routes), quota_policy=pinned(policy), reservation=pinned(reservation),
        resources=resources,demands={role:[dict(resource_index=0,max_units_per_attempt=1)]
            for role in ('CURRENT','CAND','primary','secondary')})))
    provenance = root / 'corpus-selection.json'
    _, selection=paired.freeze_corpus([],dict(description='Empty fixture corpus',excluded_record_ids=[],
        forbidden_text=['fixture evaluator answer'],excluded_artifacts=[pinned(artifact)]))
    provenance.write_text(json.dumps(selection))
    graders = dict(primary=dict(agent_id=uid(), model='fixture-model'), secondary=dict(agent_id=uid(), model='fixture-model'),
        excluded_agents=[uid()], rubric=pinned(artifact), changed_pair_definition=pinned(artifact),
        blinding_policy=pinned(artifact), dispute_owner=uid(), review_budget=pinned(artifact))
    request = dict(schema=paired.SCHEMA, request_id=uid(), campaign_id=uid(), occurrence_id=uid(),
        scheduled_at='2026-10-09T19:59:00Z', expires_at='2026-10-09T20:05:00Z',
        frozen_input=dict(path=str(inputs), manifest_sha256=pinned(inputs/'FROZEN.json')['sha256']),
        plan=plan, interfaces={arm:dict(api_revision=spec['api_revision'], actor_base=doc['baseline_commit'],
            components={k:spec[k] for k in ('binary','hook')}) for arm,spec in doc['memory_arms'].items()},
        dispositions=dispositions, limits=dict(paired.ORIGINAL_LIMITS), capacity=pinned(capacity),
        corpus_selection=pinned(provenance), grading=graders, output=str(root/'output'),
        reply=dict(event_id=uid(), recipient_id=uid(), correlation_id=uid()), supervisor_source=pinned(artifact),
        permission_profile=dict(name='implementation/1',source=pinned(profile_source),settings=pinned(profile_settings)))
    subjects=dict(task=pinned(inputs/'input.json')['sha256'], oracle=pinned(artifact)['sha256'],
        comparator=paired.canonical_sha(dict(plan=plan, interfaces=request['interfaces'])),
        supervisor=pinned(artifact)['sha256'], native=paired.canonical_sha(dict(binary_sha256=binary['sha256'],permission_profile=request['permission_profile'])),
        integration=pinned(inputs/'FROZEN.json')['sha256'])
    for name,subject in subjects.items():request['dispositions'][name]['subject_sha256']=subject
    return request


class RequestTests(unittest.TestCase):
    def test_request_cannot_omit_the_reviewed_implementation_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            request.pop('permission_profile',None)
            with self.assertRaisesRegex(paired.Refusal,'request_shape'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_unreviewed_source_or_widened_permission_settings_cannot_prepare(self):
        for mutation,code in [('name','permission_profile_unknown'),('source','permission_source_mismatch'),
                              ('settings','permission_settings_mismatch')]:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);request=request_fixture(root)
                profile=request['permission_profile']
                if mutation=='name':profile['name']='existing/1'
                elif mutation=='source':
                    path=Path(profile['source']['path']);source=json.loads(path.read_text())
                    source['files']['scripts/trial_task_permissions.py']='0'*64
                    path.write_text(json.dumps(source));profile['source']=pinned(path)
                else:
                    path=Path(profile['settings']['path']);settings=json.loads(path.read_text())
                    settings['allow'].append('Bash(*)')
                    path.write_text(json.dumps(settings));profile['settings']=pinned(path)
                with self.assertRaisesRegex(paired.Refusal,code):
                    paired.prepare(request,registry=root/'registry',now=NOW)
                self.assertEqual(list((root/'registry').iterdir()),[])

    def test_original_tool_room_is_required_and_cannot_be_widened(self):
        for mutation in ('missing','wide'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);request=request_fixture(root)
                if mutation=='missing':request['limits'].pop('tool_room_bytes',None)
                else:request['limits']['tool_room_bytes']=1000000
                with self.assertRaisesRegex(paired.Refusal,'limits_changed'):
                    paired.prepare(request,registry=root/'registry',now=NOW)
                self.assertEqual(list((root/'registry').iterdir()),[])
                self.assertFalse((root/'output').exists())

    def test_mcp_arguments_pin_both_limits_instead_of_using_the_facade_default(self):
        binding=dict(socket='/tmp/trial/sock/api.sock',token_file='/tmp/trial/agent.token',
                     repo='/trial/project',task='task-eval/fixture',run='fixture')
        arguments=paired.mcp_arguments(binding)
        self.assertEqual(arguments,['mcp','--socket','/tmp/trial/sock/api.sock',
            '--token-file','/tmp/trial/agent.token','--repo','/trial/project',
            '--task','task-eval/fixture','--run','fixture','--tokens','32000','--max-tokens','32000'])
        self.assertNotIn('1000000',arguments)
        with self.assertRaisesRegex(paired.Refusal,'mcp_binding'):
            paired.mcp_arguments(dict(binding,extra_args=['--max-tokens','1000000']))


    def test_cli_rejects_launch_and_private_malformed_input_with_selected_errors(self):
        script = Path(paired.__file__)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'private-request.json'
            path.write_text('{"private_fixture_secret":"never print this", invalid}')
            for arguments, code in [(['check', str(path)], 'invalid_json'),
                                    (['launch', str(path)], 'provider_execution_unavailable')]:
                result = subprocess.run([sys.executable, str(script), *arguments], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout), {'status':'refused', 'code':code})
                self.assertEqual(result.stderr, '')
                self.assertNotIn('private_fixture_secret', result.stdout)

    def test_validation_rejects_relative_output_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            request['output']='relative-output'
            with self.assertRaisesRegex(paired.Refusal,'absolute_path_required'):
                paired.validate(request,now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_prepares_exact_review_request_without_admitting_runtime_or_reusing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            request=request_fixture(root)
            selected=paired.prepare(request, registry=root/'registry', now=NOW)
            self.assertEqual(selected['status'], 'prepared_for_review')
            self.assertFalse(selected['runtime_admitted'])
            self.assertEqual(json.loads((root/'output/request.json').read_text()), request)
            with self.assertRaisesRegex(paired.Refusal, 'target_exists'):
                paired.prepare(request, registry=root/'registry', now=NOW)

    def test_rejects_unknown_stale_capacity_and_changed_plan_before_creating_output(self):
        for mutation, code in [('extra','request_shape'), ('stale','request_expired'),
                               ('capacity','capacity_unknown'), ('plan','plan_mismatch')]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); request=request_fixture(root)
                if mutation=='extra': request['execute_model']=True
                if mutation=='stale': request['expires_at']='2026-10-09T19:00:00Z'
                if mutation=='plan': request['plan']['attempts']=3
                if mutation=='capacity':
                    path=Path(request['capacity']['path']); doc=json.loads(path.read_text())
                    doc['state']='unknown';path.write_text(json.dumps(doc));request['capacity']=pinned(path)
                with self.assertRaisesRegex(paired.Refusal, code):
                    paired.prepare(request, registry=root/'registry', now=NOW)
                self.assertFalse((root/'output').exists())

    def test_same_occurrence_cannot_move_to_a_fresh_output_or_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); request=request_fixture(root)
            paired.prepare(request, registry=root/'registry', now=NOW)
            request['request_id']=str(uuid.uuid4());request['output']=str(root/'other-output')
            with self.assertRaisesRegex(paired.Refusal,'occurrence_registered'):
                paired.prepare(request, registry=root/'registry', now=NOW)
            self.assertFalse((root/'other-output').exists())

    def test_review_is_bound_to_its_exact_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            request['dispositions']['oracle']['subject_sha256']='9'*64
            with self.assertRaisesRegex(paired.Refusal,'review_subject_mismatch'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_control_artifacts_cannot_mutate_the_frozen_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            request['output']=str(root/'input/prepared')
            with self.assertRaisesRegex(paired.Refusal,'control_path_overlap'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])
            self.assertFalse((root/'input/prepared').exists())

    def test_oversized_input_is_refused_before_loading_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            with (root/'input/oversized-fixture').open('wb') as target:
                target.truncate(paired.FILE_LIMIT+1)
            with self.assertRaisesRegex(paired.Refusal,'file_limit'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_pinned_component_can_exceed_the_json_artifact_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            binary=root/'inert-large-component'
            with binary.open('wb') as target:target.truncate(paired.FILE_LIMIT+1)
            inputs=root/'input';document=json.loads((inputs/'input.json').read_text())
            for arm in ('CURRENT','CAND'):
                document['memory_arms'][arm]['binary']=pinned(binary)
                request['interfaces'][arm]['components']['binary']=pinned(binary)
            (inputs/'input.json').write_text(json.dumps(document))
            (inputs/'FROZEN.json').unlink();freeze_input(inputs)
            request['frozen_input']['manifest_sha256']=pinned(inputs/'FROZEN.json')['sha256']
            request['dispositions']['task']['subject_sha256']=pinned(inputs/'input.json')['sha256']
            request['dispositions']['integration']['subject_sha256']=pinned(inputs/'FROZEN.json')['sha256']
            request['dispositions']['comparator']['subject_sha256']=paired.canonical_sha(dict(plan=request['plan'],interfaces=request['interfaces']))
            self.assertFalse(paired.prepare(request,registry=root/'registry',now=NOW)['runtime_admitted'])

    def test_source_reviewer_cannot_be_a_blind_grader(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            request['grading']['primary']['agent_id']=request['dispositions']['oracle']['reviewer_id']
            with self.assertRaisesRegex(paired.Refusal,'grader_not_independent'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_capacity_must_cover_both_actors_and_both_graders_for_the_complete_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            path=Path(request['capacity']['path']);capacity=json.loads(path.read_text())
            capacity['resources'][0]['required']=1
            path.write_text(json.dumps(capacity));request['capacity']=pinned(path)
            with self.assertRaisesRegex(paired.Refusal,'capacity_estimate_mismatch'):
                paired.prepare(request,registry=root/'registry',now=NOW)
            self.assertEqual(list((root/'registry').iterdir()),[])

    def test_corpus_freeze_preserves_source_version_and_unicode_and_excludes_oracle_content(self):
        body='Existing fixture decision: café follows the documented API.'
        record=str(uuid.uuid4())
        opening=dict(record_id=record, version=7, body=body,
            body_sha256=hashlib.sha256(body.encode()).hexdigest(), kind='decision', shareable=True,
            lifecycle='active', repo='fixture-repo', entities=[])
        policy=dict(description='Explicit fixture selection', excluded_record_ids=[],
            forbidden_text=['sealed evaluator'], excluded_artifacts=[])
        contaminated=dict(opening,record_id=str(uuid.uuid4()),body='sealed evaluator answers',
            body_sha256=hashlib.sha256(b'sealed evaluator answers').hexdigest())
        corpus, selection=paired.freeze_corpus([opening,contaminated],policy)
        self.assertEqual(corpus['notes'][0]['body'],body)
        self.assertEqual(selection['notes'][0]['version'],7)
        self.assertEqual(selection['notes'][0]['record_id'],record)
        self.assertEqual(selection['excluded'][0]['reason'],'forbidden_text')
        self.assertNotIn('sealed evaluator answers',json.dumps((corpus,selection)))

    def test_selection_cannot_describe_a_different_frozen_corpus(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            path=Path(request['corpus_selection']['path']);selection=json.loads(path.read_text())
            selection['corpus_sha256']='9'*64;path.write_text(json.dumps(selection))
            request['corpus_selection']=pinned(path)
            with self.assertRaisesRegex(paired.Refusal,'corpus_selection_mismatch'):
                paired.prepare(request,registry=root/'registry',now=NOW)

    def test_selection_exclusion_audit_cannot_carry_unselected_answer_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);request=request_fixture(root)
            path=Path(request['corpus_selection']['path']);selection=json.loads(path.read_text())
            selection['excluded']=[dict(record_id=str(uuid.uuid4()),version=1,reason='forbidden_text',body='private answer')]
            path.write_text(json.dumps(selection));request['corpus_selection']=pinned(path)
            with self.assertRaisesRegex(paired.Refusal,'excluded_source_shape'):
                paired.prepare(request,registry=root/'registry',now=NOW)


if __name__ == '__main__':
    unittest.main()
