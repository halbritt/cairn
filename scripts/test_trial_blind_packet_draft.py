"""Synthetic source correspondence only. No private campaign inputs or effects."""
import base64
import copy
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import trial_blind_packet_draft as packet
import trial_paired_request as paired

NOW = dt.datetime(2026,10,15,12,tzinfo=dt.timezone.utc)


def C(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def digest(content):
    return dict(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())


def pin(path,content):
    path.write_bytes(content)
    return dict(path=str(path),**digest(content))


class Fixture:
    def __init__(self,root):
        self.root=root; self.outputs={}
        self.policy=dict(schema='cairn.blind-policy/2',**{
            name:dict(version='synthetic-v1',pin=pin(root/(name+'.bin'),('PRIVATE '+name).encode()))
            for name in ('rubric','changed_pair_definition','blinding_policy','release_specification','review_budget')})
        self.policy_ref=dict(schema='cairn.blind-policy-ref/1',id='public-policy-A')
        self.contract=dict(schema='cairn.blind-case-contract/2',case_key='private-case-A',case_class='explicit-class',
            required_channels=['task_statement','grading_instructions','final_answer'],artifact_inventory=[])
        self.source=dict(schema='cairn.blind-source/2',request_id='request-A',request_sha256='1'*64,
            campaign_id='campaign-A',occurrence_id='occurrence-A',schedule_sha256='2'*64,
            case='private-case-A',seed=1,wording='task',arm='CURRENT',run_id='run-A')
        self.channels=[]
        for name,body in [('task_statement',b'Task.\r\n'),('grading_instructions',b'Selected instructions.\n'),
                          ('final_answer',b'Answer.\n')]:
            self.channels.append(dict(name=name,entries=[self.entry(name,body)],observation=None))
        self.review=dict(schema='cairn.blind-review-measurement/1',unit='bytes',
            semantics='comparison_plus_control',graders=[dict(slot=i,limit=100000,
                control=pin(root/f'grader-control-{i}',b'SYNTHETIC CONTROL')) for i in (1,2)])

    def entry(self,name,content,*,kind='content',representation='bytes',media_type='text/plain'):
        self.outputs[name]=content
        return dict(name=name,kind=kind,representation=representation,media_type=media_type,
            input=pin(self.root/(name.replace('/','_')+'.input'),content),
            transform=dict(kind='exact_copy'),output=digest(content))

    def all_channels(self):
        artifacts=[self.entry('dir/file-A',b'file bytes',kind='file'),
            self.entry('patch-A',b'diff selected\n',kind='patch',media_type='text/x-diff'),
            self.entry('index-A',b'index selected',kind='index',media_type='application/octet-stream'),
            self.entry('binary-A',b'\x00\xff\x80',kind='binary',media_type='application/octet-stream'),
            self.entry('deleted-A',b'{"schema":"cairn.blind-deletion/1","name":"deleted-A"}',
                       kind='deleted',representation='deletion_record',media_type='application/json'),
            self.entry('rename-A',b'{"schema":"cairn.blind-rename/1","from":"old-A","to":"new-A"}',
                       kind='rename',representation='rename_record',media_type='application/json')]
        self.channels.append(dict(name='artifact_bundle',entries=artifacts,observation=None))
        self.contract['artifact_inventory']=[{k:e[k] for k in ('name','kind','representation','media_type')} for e in artifacts]
        for name in ('action_trace','evaluator_observation'):
            self.channels.append(dict(name=name,entries=[self.entry(name,b'Selected synthetic observation.')],observation=None))
        self.contract['required_channels']=[c['name'] for c in self.channels]

    def save(self):
        contract=pin(self.root/'contract.json',C(self.contract))
        samples=[dict(source=dict(self.source,arm=arm,run_id=run),sample_id=sample,
                      case_contract_sha256=contract['sha256'],channels=copy.deepcopy(self.channels))
                 for arm,run,sample in [('CURRENT','run-A','opaque-A'),('CAND','run-B','opaque-B')]]
        for sample in samples:
            for channel in sample['channels']:
                if channel['name'] in ('action_trace','evaluator_observation'):
                    obs=dict(schema='cairn.blind-evaluator-observation/2',source=sample['source'],
                        channel=channel['name'],evidence=[e['input'] for e in channel['entries']],
                        role='evaluator_observation',subject=pin(self.root/'observation-subject.bin',b'PRIVATE selected observation subject'))
                    channel['observation']=pin(self.root/(sample['sample_id']+'-'+channel['name']+'.observation'),C(obs))
        pair_key={k:v for k,v in self.source.items() if k not in ('schema','arm','run_id')}
        self.selection=dict(schema='cairn.blind-selection/2',namespace='namespace-A',policy=self.policy,
            policy_ref=self.policy_ref,case_contracts=[contract],samples=samples,
            comparisons=[dict(comparison_id='comparison-A',pair_key=pair_key,samples=['opaque-B','opaque-A'])],
            review_measurement=self.review)
        return self.write_selection()

    def write_selection(self):
        return pin(self.root/'selection.json',C(self.selection))

    def approve(self,selected):
        self.subject=packet.approval_subject(selected)
        self.approval=dict(schema='cairn.blind-declared-approval/2',state='approved',
            subject_sha256=digest(C(self.subject))['sha256'],observed_at='2026-10-15T11:00:00Z',
            valid_until='2026-10-15T13:00:00Z',
            issuer_role=pin(self.root/'issuer-role.bin',b'PRIVATE declared evaluator role; no authentication'))
        return self.write_approval()

    def write_approval(self):
        return pin(self.root/'approval.json',C(self.approval))

    def expected_sample(self,sample_id):
        channels=[]
        for channel in self.channels:
            for entry in channel['entries']:
                content=self.outputs[entry['name']]
                channels.append(dict(kind=channel['name'],name=entry['name'],media_type=entry['media_type'],
                    encoding='base64',**digest(content),data=base64.b64encode(content).decode('ascii')))
        return dict(schema='cairn.blind-sample-draft/1',sample_id=sample_id,policy_ref=self.policy_ref,channels=channels)


class BlindPacketTests(unittest.TestCase):
    def test_unretained_projection_sources_above_eight_mib_do_not_consume_output_retention(self):
        # Parent341's concrete v2 review counterexample; no source/retention cap patched.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); entries=[]; source_bytes=0
            for index in range(3):
                body=C(dict(selected=index,unselected='x'*(3*1024*1024)))
                source_bytes+=len(body)
                entry=f.entry('artifact-'+str(index),body,kind='file',media_type='application/json')
                projected=C([dict(path=['selected'],value=index)])
                entry['transform']=dict(kind='closed_json_projection',paths=[['selected']])
                entry['output']=digest(projected); f.outputs[entry['name']]=projected
                entries.append(entry)
            self.assertEqual(source_bytes,9437277)
            self.assertEqual(sum(e['output']['bytes'] for e in entries),102)
            f.contract['required_channels'].append('artifact_bundle')
            f.contract['artifact_inventory']=[{k:e[k] for k in packet.META} for e in entries]
            f.channels.append(dict(name='artifact_bundle',entries=entries,observation=None))
            selected=f.save(); approval=f.approve(selected)
            receipt=packet.draft(selected,approval,root/'out',now=NOW)
            self.assertGreater(receipt['input_bytes'],8*1024*1024)
            retained=sum(p.stat().st_size for p in (root/'out').rglob('*') if p.is_file())+len(C(receipt))
            self.assertLess(retained,8*1024*1024)
            self.assertEqual(receipt['retention_fit'],'fits')
            self.assertEqual((root/'out/public/samples/opaque-A.json').read_bytes(),C(f.expected_sample('opaque-A')))

    def test_retention_exact_boundary_counts_embedded_copies_private_receipt_and_returned_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); selected=f.save(); approval=f.approve(selected)
            receipt=packet.draft(selected,approval,root/'baseline',now=NOW)
            output_bytes=sum(p.stat().st_size for p in (root/'baseline').rglob('*') if p.is_file())
            total=output_bytes+len(C(receipt))
            with patch.object(paired,'FILE_LIMIT',total-1):
                with self.assertRaisesRegex(paired.Refusal,'blind_retention_bound'):
                    packet.draft(selected,approval,root/'out',now=NOW)
            self.assertFalse((root/'out').exists())
            with patch.object(paired,'FILE_LIMIT',total):
                self.assertEqual(packet.draft(selected,approval,root/'exact',now=NOW),receipt)

    def test_real_eight_mib_retention_refuses_duplicate_embedded_binary_inflation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); f.all_channels()
            e=f.channels[3]['entries'][3]; body=b'x'*(2*1024*1024)
            e['input']=pin(root/'large-binary',body); e['output']=digest(body); f.outputs[e['name']]=body
            selected=f.save()
            with self.assertRaisesRegex(paired.Refusal,'blind_retention_bound'): packet.approval_subject(selected)
            with self.assertRaisesRegex(paired.Refusal,'blind_retention_bound'):
                # A valid approval cannot cure oversize public bytes; this is pre-approval preflight.
                packet.draft(selected,None,root/'out',now=NOW)
            self.assertFalse((root/'out').exists())

    def test_existing_actor_evidence_and_artifact_count_bounds_remain_independent(self):
        for mode,size in [('task_statement',9501),('final_answer',8193),('action_trace',32001),('artifact_count',4097)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.all_channels()
                if mode=='artifact_count': f.contract['artifact_inventory']=[f.contract['artifact_inventory'][0]]*size
                else:
                    channel=next(c for c in f.channels if c['name']==mode)
                    channel['entries']=[f.entry(mode,b'x'*size)]
                with self.assertRaises(paired.Refusal): packet.approval_subject(f.save())

    def test_partial_output_is_consumed_and_never_cleaned_or_retried(self):
        for failure in ('packet','crosswalk','receipt'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); selected=f.save(); approval=f.approve(selected); out=root/'out'
                original=paired.write_selected_bytes
                def interrupted(path,content):
                    if path.name=={'packet':'opaque-A.json','crosswalk':'crosswalk.json','receipt':'receipt.json'}[failure]:
                        path.write_bytes(content[:11]); raise OSError('private disk details')
                    return original(path,content)
                with patch.object(paired,'write_selected_bytes',side_effect=interrupted):
                    with self.assertRaisesRegex(paired.Refusal,'blind_draft_io_consumed'):
                        packet.draft(selected,approval,out,now=NOW)
                retained={str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
                self.assertTrue(any(len(b)==11 for b in retained.values()))
                with self.assertRaisesRegex(paired.Refusal,'target_exists'): packet.draft(selected,approval,out,now=NOW)
                self.assertEqual(retained,{str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()})

    def test_unavailable_delivery_and_bad_time_refuse_before_private_reads(self):
        class Unreadable:
            def __str__(self): raise AssertionError('argument inspected')
            def __fspath__(self): raise AssertionError('path inspected')
        with patch.object(paired,'read_bytes',side_effect=AssertionError('private read')):
            with self.assertRaisesRegex(paired.Refusal,'blind_delivery_unavailable'):
                packet.deliver(Unreadable(),output=Unreadable())
            with redirect_stdout(io.StringIO()) as stream, self.assertRaises(SystemExit) as exit:
                runpy.run_path(packet.__file__,run_name='__main__')
            self.assertEqual(exit.exception.code,2)
            self.assertEqual(json.loads(stream.getvalue()),dict(status='refused',code='blind_delivery_unavailable'))
            with self.assertRaises(paired.Refusal): packet.draft(None,None,None,now=None)

    def test_stale_mismatched_missing_approval_and_all_selected_mutations_refuse_before_write(self):
        for mode in ('missing','expired','future','unapproved','subject','issuer','policy_stale','policy_repin',
                     'evidence','transform','output_digest','public_token','order','sample_pair_substitution','budget'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); selected=f.save(); approval=f.approve(selected)
                if mode=='missing': approval=None
                if mode=='expired': f.approval['valid_until']=NOW.isoformat()
                if mode=='future': f.approval['observed_at']='2026-10-16T00:00:00Z'
                if mode=='unapproved': f.approval['state']='pending'
                if mode=='subject': f.approval['subject_sha256']='0'*64
                if mode=='issuer': Path(f.approval['issuer_role']['path']).write_bytes(b'changed')
                if mode in ('policy_stale','policy_repin'):
                    p=pin(Path(f.policy['rubric']['pin']['path']),b'changed policy')
                    if mode=='policy_repin': f.policy['rubric']['pin']=p
                entry=f.selection['samples'][0]['channels'][0]['entries'][0]
                if mode=='evidence': Path(entry['input']['path']).write_bytes(b'changed')
                if mode=='transform': entry['transform']['extra']=True
                if mode=='output_digest': entry['output']['sha256']='0'*64
                if mode=='public_token': f.selection['policy_ref']['id']='different-public-token'
                if mode=='order': f.selection['comparisons'][0]['samples'].reverse()
                if mode=='budget': f.selection['review_measurement']['graders'][1]['limit']=200000
                if mode=='sample_pair_substitution':
                    subject=copy.deepcopy(f.subject)
                    subject['comparison_packets']['comparison-A']=subject['sample_packets']['opaque-A']
                    f.approval['subject_sha256']=digest(C(subject))['sha256']
                if mode!='missing': approval=f.write_approval()
                selected=f.write_selection()
                with self.assertRaises(paired.Refusal): packet.draft(selected,approval,root/'out',now=NOW)
                self.assertFalse((root/'out').exists())

    def test_closed_metadata_rejects_duplicate_nonfinite_float_surrogate_boolean_lengths(self):
        for mode in ('duplicate','overflow','float','nan','surrogate','bool_pin','bool_output','bool_seed','extra_pin','extra','old_schema',
                     'public_policy_path','public_policy_private_pin'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.save()
                if mode=='bool_pin': f.policy['rubric']['pin']['bytes']=True
                if mode=='bool_output': f.selection['samples'][0]['channels'][0]['entries'][0]['output']['bytes']=True
                if mode=='bool_seed': f.selection['samples'][0]['source']['seed']=True
                if mode=='extra_pin': f.policy['rubric']['pin']['trusted']=True
                if mode=='extra': f.selection['approved']=True
                if mode=='old_schema': f.selection['schema']='cairn.blind-selection/1'
                if mode=='public_policy_path': f.selection['policy_ref']['id']=str(root/'private-policy')
                if mode=='public_policy_private_pin': f.selection['policy_ref']['rubric']=f.policy['rubric']['pin']
                content=C(f.selection)
                if mode=='duplicate': content=content.replace(b'"samples":',b'"samples":[],"samples":',1)
                if mode=='overflow': content=content.replace(b'"seed":1',b'"seed":1e400',1)
                if mode=='float': content=content.replace(b'"seed":1',b'"seed":1.0',1)
                if mode=='nan': content=content.replace(b'"seed":1',b'"seed":NaN',1)
                if mode=='surrogate': content=content.replace(b'namespace-A',b'\\ud800',1)
                with self.assertRaises(paired.Refusal): packet.approval_subject(pin(root/'selection.json',content))

    def test_opaque_policy_and_role_bodies_are_never_interpreted_or_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root)
            for name in ('rubric','changed_pair_definition','blinding_policy','release_specification','review_budget'):
                f.policy[name]['pin']=pin(root/(name+'.bin'),b'{"path":"/must-not-read","n":1e400,"PRIVATE":true}')
            selected=f.save(); f.approve(selected)
            f.approval['issuer_role']=pin(root/'issuer-role.bin',b'\x00opaque /must-not-read')
            receipt=packet.draft(selected,f.write_approval(),root/'out',now=NOW)
            self.assertFalse(receipt['release_authority_verified'])
            self.assertNotIn(b'must-not-read',(root/'out/public/comparisons/comparison-A.json').read_bytes())

    def test_unsafe_paths_and_transitive_output_overlaps_refuse(self):
        for mode in ('relative','parent','symlink','symlink_parent','missing','directory','noncanonical',
                     'selection','policy','approval','issuer','observation','observation_subject',
                     'contract','evidence','grader_control','existing_output','output_symlink','unsafe_sample','unsafe_name'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.all_channels(); selected=f.save(); approval=f.approve(selected)
                out=root/'out'; e=f.selection['samples'][0]['channels'][0]['entries'][0]; target=Path(e['input']['path'])
                if mode=='relative': e['input']['path']=target.name
                if mode=='parent': e['input']['path']=str(root/'..'/root.name/target.name)
                if mode=='noncanonical': e['input']['path']=str(root)+'//'+target.name
                if mode=='symlink': (root/'link').symlink_to(target); e['input']['path']=str(root/'link')
                if mode=='symlink_parent':
                    (root/'linkdir').symlink_to(root,target_is_directory=True); e['input']['path']=str(root/'linkdir'/target.name)
                if mode=='missing': target.unlink()
                if mode=='directory': e['input']['path']=str(root)
                if mode=='existing_output': out.mkdir()
                if mode=='output_symlink': out.symlink_to(root/'absent',target_is_directory=True)
                if mode=='unsafe_sample': f.selection['samples'][0]['sample_id']='../escape'
                if mode=='unsafe_name': e['name']='../escape'
                obs=f.selection['samples'][0]['channels'][4]['observation']
                refs=dict(selection=selected,policy=f.policy['blinding_policy']['pin'],approval=approval,
                    issuer=f.approval['issuer_role'],observation=obs,
                    observation_subject=json.loads(Path(obs['path']).read_bytes())['subject'],
                    contract=f.selection['case_contracts'][0],evidence=e['input'],grader_control=f.review['graders'][1]['control'])
                if mode in refs: out=Path(refs[mode]['path'])/'child'
                selected=f.write_selection()
                with self.assertRaises(paired.Refusal): packet.draft(selected,approval,out,now=NOW)
                if mode!='existing_output': self.assertFalse(out.exists())

    def test_grading_instructions_and_comparison_are_not_capped_as_actor_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root)
            f.channels[1]['entries']=[f.entry('instructions-large',b'g'*10000)]
            selected=f.save(); approval=f.approve(selected); receipt=packet.draft(selected,approval,root/'out',now=NOW)
            pair=(root/'out/public/comparisons/comparison-A.json').read_bytes()
            self.assertGreater(len(pair),9500)
            self.assertEqual(receipt['declared_grader_fit']['state'],'fits')

    def test_all_channels_artifact_representations_shared_inputs_and_identical_grader_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); f.all_channels()
            rename=f.channels[3]['entries'][-1]
            body=b'{"schema":"cairn.blind-rename/1","from":"dir/old name","to":"dir/new name"}'
            rename['input']=pin(root/'rename.input',body); rename['output']=digest(body); f.outputs[rename['name']]=body
            # A shared task/instruction source is legitimate, with distinct approved public names.
            entry=f.channels[1]['entries'][0]; task=f.channels[0]['entries'][0]
            entry['input']=task['input']; entry['output']=task['output']; f.outputs[entry['name']]=f.outputs[task['name']]
            selected=f.save(); approval=f.approve(selected); receipt=packet.draft(selected,approval,root/'out',now=NOW)
            a=(root/'out/public/samples/opaque-A.json').read_bytes(); self.assertEqual(a,C(f.expected_sample('opaque-A')))
            contents=json.loads(a)['channels']; self.assertEqual({c['kind'] for c in contents},set(packet.CHANNELS))
            self.assertEqual(base64.b64decode(next(c['data'] for c in contents if c['name']=='binary-A')),b'\x00\xff\x80')
            for c in contents:
                self.assertEqual(set(c),{'kind','name','media_type','encoding','bytes','sha256','data'})
                decoded=base64.b64decode(c['data'],validate=True)
                self.assertEqual(digest(decoded),{k:c[k] for k in ('bytes','sha256')})
                self.assertEqual(base64.b64encode(decoded).decode('ascii'),c['data'])
            # The future two consumers select this single retained comparison, with no per-grader rendering.
            selected_pair=root/'out/public/comparisons/comparison-A.json'
            grader_one=selected_pair.read_bytes(); grader_two=selected_pair.read_bytes()
            self.assertEqual(grader_one,grader_two)
            self.assertEqual(receipt['public_files']['comparisons/comparison-A.json'],digest(grader_one))
            private=json.loads((root/'out/private/crosswalk.json').read_bytes())
            self.assertEqual(private['subject']['selection'],f.selection)
            self.assertEqual(private['subject']['comparison_packets']['comparison-A'],digest(grader_one))
            for path in (root/'out',root/'out/public',root/'out/private'):
                self.assertEqual(path.stat().st_mode & 0o777,0o700)
            self.assertEqual((root/'out/private/crosswalk.json').stat().st_mode & 0o777,0o600)

    def test_exact_channel_contracts_and_artifact_inventory_refuse(self):
        for mode in ('unknown','missing','extra','duplicate','reorder','files_only','no_class','wrong_case',
                     'missing_binary','missing_deleted','missing_rename','extra_file','duplicate_name','bad_deletion',
                     'bad_rename','unknown_kind','unknown_media','bad_representation','multiple_answers','unused_contract'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.all_channels()
                if mode=='unknown': f.contract['required_channels'].append('stdout')
                if mode=='missing': f.channels.pop()
                if mode=='extra': f.channels.append(dict(name='stdout',entries=[],observation=None))
                if mode=='duplicate': f.channels.append(copy.deepcopy(f.channels[0]))
                if mode=='reorder': f.channels[:2]=reversed(f.channels[:2])
                if mode=='files_only': f.contract['required_channels']=['artifact_bundle']
                if mode=='no_class': del f.contract['case_class']
                if mode=='wrong_case': f.source['case']='wrong'
                entries=f.channels[3]['entries']
                if mode in ('missing_binary','missing_deleted','missing_rename'):
                    entries[:]=[e for e in entries if e['kind']!=mode.removeprefix('missing_')]
                if mode=='extra_file': entries.append(f.entry('extra',b'file',kind='file'))
                if mode=='duplicate_name': entries.append(copy.deepcopy(entries[0]))
                if mode in ('bad_deletion','bad_rename'):
                    e=entries[4 if mode=='bad_deletion' else 5]; body=b'{"exit":0}'
                    e['input']=pin(root/'bad-record',body); e['output']=digest(body)
                if mode=='unknown_kind': entries[0]['kind']='symlink'
                if mode=='unknown_media': entries[0]['media_type']='auto'
                if mode=='bad_representation': entries[0]['representation']='summary'
                if mode=='multiple_answers': f.channels[2]['entries'].append(copy.deepcopy(f.channels[2]['entries'][0]))
                selected=f.save()
                if mode=='unused_contract':
                    f.selection['case_contracts'].append(pin(root/'unused.json',C(dict(f.contract,case_key='unused'))))
                    selected=f.write_selection()
                with self.assertRaises(paired.Refusal): packet.approval_subject(selected)

    def test_action_observation_role_and_subject_are_required_not_inferred_from_success(self):
        for mode in ('missing','exit0','wrong_source','wrong_evidence','wrong_role','stale_subject','unneeded'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.all_channels(); f.save()
                channel=f.selection['samples'][0]['channels'][4]
                observation=json.loads(Path(channel['observation']['path']).read_bytes())
                if mode=='exit0': observation={'exit':0,'tool_success':True}
                if mode=='wrong_source': observation['source']['arm']='CAND'
                if mode=='wrong_evidence': observation['evidence']=[]
                if mode=='wrong_role': observation['role']='assistant_prose'
                if mode=='stale_subject': Path(observation['subject']['path']).write_bytes(b'changed')
                channel['observation']=pin(root/'bad-observation',C(observation))
                if mode=='missing': channel['observation']=None
                if mode=='unneeded': f.selection['samples'][0]['channels'][0]['observation']=channel['observation']
                with self.assertRaises(paired.Refusal): packet.approval_subject(f.write_selection())

    def test_scalar_projection_order_canonical_bytes_and_nested_leakage_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); entry=f.channels[2]['entries'][0]
            entry['input']=pin(root/'project.input',b'{"private":"PRIVATE", "a":{"text":"yes","secret":"PRIVATE"},"ok":true,"n":null}')
            entry['transform']=dict(kind='closed_json_projection',paths=[['ok'],['a','text'],['n']])
            expected=b'[{"path":["ok"],"value":true},{"path":["a","text"],"value":"yes"},{"path":["n"],"value":null}]\n'
            entry['output']=digest(expected); entry['media_type']='application/json'; f.outputs[entry['name']]=expected
            selected=f.save(); approval=f.approve(selected); packet.draft(selected,approval,root/'out',now=NOW)
            public=json.loads((root/'out/public/samples/opaque-A.json').read_bytes())
            self.assertEqual(base64.b64decode(public['channels'][2]['data']),expected)
            self.assertEqual(packet.canonical_bytes({'z':'é','a':1}),b'{"a":1,"z":"\xc3\xa9"}\n')
            for paths,body in [([['a']],b'{"a":{"secret":"PRIVATE"}}'),([['a']],b'{"a":[1]}'),
                    ([['a','0']],b'{"a":[1]}'),([['*']],b'{"*":1}'),([['a'],['a']],b'{"a":1}'),
                    ([['a'],['a','b']],b'{"a":{"b":1}}'),([],b'{}'),([['missing']],b'{}'),
                    ([['a']],b'{"a":1e400}'),([['a']],b'{"a":1.0}'),([['a']],b'{"a":9007199254740992}'),
                    ([['a']],b'{"a":1,"a":2}'),([['a']],b'{"a":NaN}'),([['a']],b'{"a":"\\ud800"}'),
                    ([['a']],b'{"a":"\xff"}'),([['a']],b'{"a":1,"private":Infinity}')]:
                with self.subTest(paths=paths,body=body):
                    entry['input']=pin(root/'project.input',body); entry['transform']['paths']=paths
                    with self.assertRaises(paired.Refusal): packet.approval_subject(f.save())

    def test_grader_fit_is_separate_unknown_or_overbudget_full_comparison_plus_both_controls(self):
        for unit,semantics,limit,state in [('tokens','comparison_plus_control',100000,'unknown'),
                ('unknown','unknown',None,'unknown'),('bytes','unknown',100000,'unknown'),
                ('bytes','comparison_plus_control',None,'unknown'),('bytes','comparison_plus_control',1,'over_budget')]:
            with self.subTest(unit=unit,semantics=semantics,limit=limit), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.review.update(unit=unit,semantics=semantics)
                f.review['graders'][1]['limit']=limit
                selected=f.save(); approval=f.approve(selected); receipt=packet.draft(selected,approval,root/'out',now=NOW)
                self.assertEqual(receipt['declared_grader_fit']['state'],state)
                self.assertEqual(receipt['retention_fit'],'fits')
                second=receipt['declared_grader_fit']['comparisons']['comparison-A'][1]
                if state=='unknown': self.assertIsNone(second['required'])
                else:
                    pair=(root/'out/public/comparisons/comparison-A.json').read_bytes()
                    self.assertEqual(second['required'],len(pair)+len(b'SYNTHETIC CONTROL'))
                self.assertFalse(receipt['delivery_available'])

    def test_review_measurement_is_closed_two_slots_and_control_pins_are_bound(self):
        for mode in ('missing_slot','reused_slot','bool_limit','extra','stale_control','unknown_unit','unknown_semantics'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); selected=f.save(); approval=f.approve(selected)
                review=f.selection['review_measurement']
                if mode=='missing_slot': review['graders'].pop()
                if mode=='reused_slot': review['graders'][1]['slot']=1
                if mode=='bool_limit': review['graders'][1]['limit']=True
                if mode=='extra': review['verified']=True
                if mode=='stale_control': Path(review['graders'][0]['control']['path']).write_bytes(b'changed')
                if mode=='unknown_unit': review['unit']='magic'
                if mode=='unknown_semantics': review['semantics']='character_count'
                with self.assertRaises(paired.Refusal): packet.approval_subject(f.write_selection())

    def test_pair_joins_reject_unrelated_self_reused_and_incomplete_rows(self):
        modes=['request_id','request_sha256','campaign_id','occurrence_id','schedule_sha256','seed','wording',
               'arm','self','missing_sample','duplicate_pair','duplicate_comparison_id','pair_key',
               'duplicate_sample','duplicate_run','duplicate_attempt','empty_pairs','unused_sample','extra_pair_field']
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); f=Fixture(root); f.save(); s=f.selection['samples']; pairs=f.selection['comparisons']
                if mode in ('request_id','campaign_id','occurrence_id'): s[1]['source'][mode]='unrelated'
                if mode in ('request_sha256','schedule_sha256'): s[1]['source'][mode]='9'*64
                if mode=='seed': s[1]['source']['seed']=2
                if mode=='wording': s[1]['source']['wording']='rephrased'
                if mode=='arm': s[1]['source']['arm']='CURRENT'
                if mode=='self': pairs[0]['samples']=['opaque-A','opaque-A']
                if mode=='missing_sample': pairs[0]['samples'][0]='missing'
                if mode=='duplicate_pair': pairs.append(dict(pairs[0],comparison_id='other'))
                if mode=='duplicate_comparison_id': pairs.append(copy.deepcopy(pairs[0]))
                if mode=='pair_key': pairs[0]['pair_key']['seed']=99
                if mode=='duplicate_sample': s[1]['sample_id']=s[0]['sample_id']
                if mode=='duplicate_run': s[1]['source']['run_id']=s[0]['source']['run_id']
                if mode=='duplicate_attempt': s[1]['source']=dict(s[0]['source'],run_id='new-run')
                if mode=='empty_pairs': f.selection['comparisons']=[]
                if mode=='unused_sample':
                    extra=copy.deepcopy(s[0]); extra['sample_id']='extra'; extra['source']['run_id']='extra'; extra['source']['seed']=9
                    s.append(extra)
                if mode=='extra_pair_field': pairs[0]['trusted']=True
                with self.assertRaises(paired.Refusal): packet.approval_subject(f.write_selection())
                self.assertFalse((root/'out').exists())

    def test_v2_exact_sample_and_pair_bytes_acyclic_subject_private_split(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); f=Fixture(root); selected=f.save(); approval=f.approve(selected)
            receipt=packet.draft(selected,approval,root/'out',now=NOW)
            expected=f.expected_sample('opaque-A')
            actual=(root/'out/public/samples/opaque-A.json').read_bytes()
            self.assertEqual(actual,C(expected))
            comparison=dict(schema='cairn.blind-comparison-draft/1',comparison_id='comparison-A',
                policy_ref=f.policy_ref,samples=[dict(sample_id=s,sample_packet_sha256=digest(C(f.expected_sample(s)))['sha256'],
                    packet=f.expected_sample(s)) for s in ('opaque-B','opaque-A')])
            pair_bytes=(root/'out/public/comparisons/comparison-A.json').read_bytes()
            self.assertEqual(pair_bytes,C(comparison))
            self.assertNotEqual(digest(actual),digest(pair_bytes))
            for marker in (str(root).encode(),b'PRIVATE',b'CURRENT',b'CAND',b'private-case',b'occurrence-A',b'campaign-A'):
                self.assertNotIn(marker,pair_bytes)
            self.assertEqual(set(expected),{'schema','sample_id','policy_ref','channels'})
            self.assertNotIn('approval',f.subject)
            self.assertEqual(receipt['subject_sha256'],digest(C(f.subject))['sha256'])
            for key in ('release_authority_verified','runtime_admitted','delivery_available'):
                self.assertIs(receipt[key],False)
            self.assertEqual(receipt['status'],'declared_correspondence_valid')
            self.assertEqual(receipt['retention_fit'],'fits')
            self.assertEqual(receipt['declared_grader_fit']['state'],'fits')


if __name__=='__main__': unittest.main()
