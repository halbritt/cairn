"""Offline original weekly requests. No launcher or prospective reinterpretation."""
import argparse
import datetime as dt
import hashlib
import json

import trial_paired_request as paired
from trial_paired_companion import validate_companion
from trial_provider_mapping import original_plan
from trial_weekly_input import load_weekly_input

SCHEMA = 'cairn.weekly-eval.request/1'
FIELDS = ('schema request_id campaign_id occurrence_id scheduled_at expires_at '
          'weekly_input plan schedule dispositions limits capacity grading output reply '
          'supervisor_source permission_profile')


def review_subjects(request, bundle):
    """Deterministic reviewer subjects from a selected private bundle; no I/O.

    Check/prepare rebuild the bundle themselves. This public reviewer utility
    does not validate or admit a request supplied by its caller.
    """
    execution, source = bundle['execution'], bundle['source']
    interfaces = dict(actor_api_base=execution['actor_api_base'], memory_arms=execution['memory_arms'])
    corpus = dict(source=source, distractors=execution['distractors'])
    subjects = dict(
        task=dict(reference=request['weekly_input'], source=source, wording=bundle['schedule']['wording']),
        oracle=dict(source=source, grading=request['grading'],
                    legacy_grader_source=execution['distractors']['generator_source']),
        comparator=dict(plan=bundle['plan'], schedule_sha256=bundle['schedule']['sha256'],
                        interfaces=interfaces, corpus=corpus),
        supervisor={k:request[k] for k in ('supervisor_source','limits','permission_profile')},
        native=dict(native=execution['native'], **{k:request[k] for k in
                    ('permission_profile','limits','supervisor_source')}),
        integration=dict(request_core={k:v for k,v in request.items() if k != 'dispositions'}))
    return {name:paired.canonical_sha(dict(schema=f'cairn.weekly-{name}-review/1', **value))
            for name,value in subjects.items()}


def _checked(request, *, now, companion, review):
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    paired.shape(request, FIELDS, 'weekly_request_shape')
    paired.need(request['schema'] == SCHEMA, 'weekly_request_schema')
    paired.need(companion is not None and review is not None, 'weekly_companion_required')
    for name in ('request_id','campaign_id','occurrence_id'):
        paired.identifier(request[name])
    paired.need(paired.utc(request['scheduled_at']) <= now < paired.utc(request['expires_at']), 'request_expired')
    paired.need(paired.canonical_sha(request['limits']) == paired.canonical_sha(paired.ORIGINAL_LIMITS), 'limits_changed')
    paired.plain_path(request['output'])
    bundle = load_weekly_input(request['weekly_input'])
    paired.need(paired.canonical_sha(request['plan']) == paired.canonical_sha(bundle['plan']), 'weekly_plan_mismatch')
    original_plan(request['plan'])
    schedule = paired.read_json_pin(request['schedule'])
    paired.need(paired.canonical_sha(schedule) == paired.canonical_sha(bundle['schedule']), 'weekly_schedule_mismatch')
    paired.shape(request['dispositions'], ' '.join(paired.REVIEWS), 'dispositions_shape')
    paired.pin(request['supervisor_source'])
    paired.permission_profile_check(request['permission_profile'])
    paired.grading_check(request['grading'], {r.get('reviewer_id') for r in request['dispositions'].values()
                                             if isinstance(r,dict)})
    subjects = review_subjects(request,bundle)
    for name, disposition in request['dispositions'].items():
        paired.shape(disposition, 'artifact subject_sha256 reviewer_id state valid_until', 'review_shape')
        paired.need(disposition['state'] == 'accepted' and paired.utc(disposition['valid_until']) > now,
                    'review_unavailable')
        paired.identifier(disposition['reviewer_id'])
        paired.pin(disposition['artifact'])
        paired.need(disposition['subject_sha256'] == subjects[name], 'review_subject_mismatch')
    paired.shape(request['reply'], 'event_id recipient_id correlation_id', 'reply_shape')
    for ident in request['reply'].values():
        paired.identifier(ident)
    evidence, retained = validate_companion(request,companion,review,now=now,
        native_model=bundle['execution']['native']['model'])
    return request,bundle,evidence,retained


def _summary(request, bundle, evidence, status):
    return dict(schema='cairn.weekly-eval.preparation/1', status=status,
                request_sha256=paired.canonical_sha(request),
                reference_sha256=paired.canonical_sha(request['weekly_input']),
                plan_sha256=bundle['plan']['sha256'], schedule_sha256=bundle['schedule']['sha256'],
                schedule_file=request['schedule'], source_sha256=paired.canonical_sha(bundle['source']),
                execution_file=request['weekly_input']['execution'], provider_evidence=evidence,
                runtime_admitted=False, witness_valid=False, admission=False)


def check(request, *, now, companion=None, review=None):
    request,bundle,evidence,retained = _checked(request,now=now,companion=companion,review=review)
    retained = _selected(request,bundle,retained)
    _protect(request,bundle,retained,companion,review)
    return _receipt(request,bundle,evidence,retained,'validated_for_review')


def _selected(request, bundle, retained):
    """Bound selected control artifacts; cases/corpus/grader assets stay private."""
    retained = dict(retained)

    def keep(name, content):
        retained[name] = content
        paired.need(sum(map(len,retained.values())) <= paired.FILE_LIMIT, 'weekly_retention_bound')

    for name,value in (('request',request),('weekly-input',request['weekly_input']),
                       ('plan',bundle['plan']),('source',bundle['source'])):
        keep(name+'.json',paired.selected_bytes(value))
    for name,reference in [('schedule',request['schedule']),('execution',request['weekly_input']['execution']),
                          ('supervisor-source',request['supervisor_source']),
                          *[('permission-'+k,request['permission_profile'][k]) for k in ('source','settings')],
                          *[('grading-'+k,request['grading'][k]) for k in
                            ('rubric','changed_pair_definition','blinding_policy','review_budget')],
                          *[('review-'+k,v['artifact']) for k,v in request['dispositions'].items()]]:
        keep(name+('.json' if name in ('schedule','execution') else '.bin'),paired.read_pin_bytes(reference))
    return retained


def _receipt(request,bundle,evidence,retained,status):
    result = _summary(request,bundle,evidence,status)
    result['files'] = {name:dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
                       for name,content in retained.items()}
    paired.need(sum(map(len,retained.values()))+len(paired.selected_bytes(result)) <= paired.FILE_LIMIT,
                'weekly_retention_bound')
    return result


def _protect(request,bundle,retained,companion,review,registry=None):
    """Protect selected sources, including transitive provider evidence pins.

    Opaque artifact bodies/configuration are not interpreted as more manifests.
    Trusted quiescent files remain required; this is not concurrent FS custody.
    """
    sources = {bundle['root']}

    def pins(value):
        if isinstance(value,dict):
            if set(value) == {'path','sha256'}:
                sources.add(paired.plain_path(value['path']))
            else:
                for item in value.values():
                    pins(item)
        elif isinstance(value,list):
            for item in value:
                pins(item)

    for value in (request,bundle['execution'],companion,review):
        pins(value)
    for name,content in retained.items():
        if name.endswith('.json'):
            pins(paired.decode_json(content))
    output = paired.plain_path(request['output'])
    controls = [output] if registry is None else [output,registry]
    if registry is not None:
        paired.need(output != registry and output not in registry.parents and registry not in output.parents,
                    'control_path_overlap')
    for control in controls:
        for source in sources:
            paired.need(control != source and control not in source.parents and source not in control.parents,
                        'control_path_overlap')


def prepare(request, *, registry, now, companion=None, review=None):
    request,bundle,evidence,retained = _checked(request,now=now,companion=companion,review=review)
    retained = _selected(request,bundle,retained)
    output,registry = paired.plain_path(request['output']),paired.plain_path(str(registry))
    paired.need(not output.exists(), 'target_exists')
    paired.need(registry.is_dir() and registry.stat().st_mode & 0o077 == 0, 'private_registry_required')
    _protect(request,bundle,retained,companion,review,registry)
    result = _receipt(request,bundle,evidence,retained,'prepared_for_review')
    # Preparation holds are not execution intent. Failed writes remain held;
    # never remove a registration or manufacture a replacement/retry.
    for namespace,ident in (('occurrence',request['occurrence_id']),('request',request['request_id'])):
        try:
            paired.write_selected(registry/(namespace+'-'+ident+'.json'),
                dict(schema='cairn.paired-preparation-intent/1',request_id=request['request_id'],
                     occurrence_id=request['occurrence_id'],request_sha256=result['request_sha256'],
                     output_sha256=paired.canonical_sha(str(output)),state='held_preparation',provider_launched=False))
        except FileExistsError:
            raise paired.Refusal(namespace+'_registered') from None
    output.mkdir(mode=0o700)
    for name,content in retained.items():
        paired.write_selected_bytes(output/name,content)
    paired.write_selected(output/'preparation.json',result)
    return result


def launch(request):
    """No runtime implementation is selected by this source boundary."""
    raise paired.Refusal('provider_execution_unavailable')


def main(argv=None):
    class PublicParser(argparse.ArgumentParser):
        def error(self, message):
            raise paired.Refusal('invalid_arguments')

    parser=PublicParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('check','prepare','launch'):
        command=commands.add_parser(name)
        command.add_argument('request')
        if name=='prepare':
            command.add_argument('--registry',required=True)
        if name!='launch':
            for field in ('companion','review'):
                command.add_argument('--'+field)
                command.add_argument('--'+field+'-sha256')
    try:
        args=parser.parse_args(argv)
        if args.command=='launch':
            launch(args.request)
        references={}
        for name in ('companion','review'):
            path,digest=getattr(args,name),getattr(args,name+'_sha256')
            paired.need((path is None)==(digest is None),'explicit_pin_required')
            references[name]=None if path is None else dict(path=path,sha256=digest)
        request=paired.read_json(args.request)
        # The CLI's selected request file is also a control source. The API
        # accepts an in-memory request and has no request filename to protect.
        source=paired.plain_path(args.request)
        controls=[paired.plain_path(request['output'])]
        if args.command=='prepare':
            controls.append(paired.plain_path(args.registry))
        paired.need(all(source!=c and source not in c.parents and c not in source.parents for c in controls),
                    'control_path_overlap')
        now=dt.datetime.now(dt.timezone.utc)
        selected=(prepare(request,registry=args.registry,now=now,**references) if args.command=='prepare'
                  else check(request,now=now,**references))
    except paired.Refusal as error:
        selected=dict(status='refused',code=str(error))
    except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
        selected=dict(status='refused',code='invalid_or_unavailable_input')
    print(json.dumps(selected,sort_keys=True))
    return 2 if selected['status']=='refused' else 0


if __name__=='__main__':
    from trial_weekly_request import main
    raise SystemExit(main())
