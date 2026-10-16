"""Generated synthetic drafts only; source correspondence is not custody."""
import io
import json
import copy
import tempfile
import os
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import trial_paired_request as paired
import trial_grader_delivery_plan as delivery
import trial_blind_packet_draft as blind
import trial_grading_custody as custody
from test_trial_blind_packet_draft import Fixture as BlindFixture, C, digest, pin, NOW
from test_trial_grading_custody import fixture as join_fixture, completed_inventory, seal


class Fixture:
    def __init__(self, root, *, all_channels=False):
        self.root = root
        self.bundle = join_fixture()
        completed_inventory(self.bundle)
        for row in self.bundle['selection']: row['state'] = 'unchanged'
        self.bundle['selection'][0]['state'] = 'changed'
        self.blind = BlindFixture(root)
        if all_channels: self.blind.all_channels()
        source = self.blind.source
        source.update({k: self.bundle['binding'][k] for k in
                       ('request_id', 'request_sha256', 'campaign_id', 'occurrence_id', 'schedule_sha256')})
        source.update(case='case-00', seed=11)
        self.blind.contract['case_key'] = 'case-00'
        for key in ('rubric', 'changed_pair_definition', 'blinding_policy'):
            self.bundle['binding'][key + '_sha256'] = self.blind.policy[key]['pin']['sha256']
        selected = self.blind.save()
        # Match the independently supplied original-attempt inventory.
        for sample, run in zip(self.blind.selection['samples'], ['run-0', 'run-1']):
            sample['source']['run_id'] = run
            for channel in sample['channels']:
                if channel['observation']:
                    path = Path(channel['observation']['path'])
                    observation = json.loads(path.read_bytes()); observation['source'] = sample['source']
                    channel['observation'] = pin(path, C(observation))
        selected = self.blind.write_selection()
        approval = self.blind.approve(selected)
        self.draft = root/'draft'
        blind.draft(selected, approval, self.draft, now=NOW)
        comparison = self.draft/'public/comparisons/comparison-A.json'
        self.bundle['packets'] = [dict(case='case-00', seed=11, pair_id='pair-A',
            packet_sha256=digest(comparison.read_bytes())['sha256'], samples=['opaque-B', 'opaque-A'])]
        seal(self.bundle)
        self.sessions = dict(schema='cairn.grader-session-selection/1',
            binding_sha256=paired.canonical_sha(self.bundle['binding']),
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            exclusions=[dict(role='actor_engineer', agent_id='engineer', session_id='engineer-session', execution_id='engineer-exec'),
                        dict(role='coordinator', agent_id='coordinator', session_id='coordinator-session', execution_id='coord-exec')],
            slots=[])
        for index, slot in enumerate(('primary', 'secondary')):
            self.sessions['slots'].append(dict(slot=slot, **self.bundle['policy']['graders'][slot],
                execution_id='execution-'+slot, freshness='unknown', observation=None,
                control=self.blind.review['graders'][index]['control'],
                route=pin(root/(slot+'-route'), b'subscription route declaration'),
                workload=pin(root/(slot+'-workload'), b'grader workload declaration'),
                envelope=dict(renderer=None, unit='tokens', semantics='unknown', limit=10000,
                    transport=None, framing=None, tool_schema=None,
                    declared_input_bytes=None, declared_input_tokens=None)))
        self.selection = dict(schema='cairn.grader-delivery-selection/1',
            crosswalk=self.existing(self.draft/'private/crosswalk.json'),
            draft_receipt=self.existing(self.draft/'private/receipt.json'), approval=approval,
            policy=self.blind.policy, grading_bundle=None, sessions=None,
            pairs=[dict(pair_id='pair-A', comparison=self.existing(comparison))])

    def full_occurrence(self):
        frozen = self.blind.selection
        template = copy.deepcopy(frozen['samples'][0])
        contracts, samples, comparisons = [], [], []
        for case in self.bundle['plan']['cases']:
            contract = dict(self.blind.contract, case_key=case)
            reference = pin(self.root/(case+'-contract'), C(contract)); contracts.append(reference)
            for seed in self.bundle['plan']['seeds']:
                ids = []
                for arm in ('CURRENT','CAND'):
                    row = next(r for r in self.bundle['inventory'] if (r['case'],r['seed'],r['arm']) == (case,seed,arm))
                    sample = copy.deepcopy(template)
                    sample['source'].update(case=case,seed=seed,arm=arm,run_id=row['run_id'])
                    sample['sample_id'] = row['run_id']+'-sample'
                    sample['case_contract_sha256'] = reference['sha256']
                    samples.append(sample); ids.append(sample['sample_id'])
                comparisons.append(dict(comparison_id=case+'-'+str(seed),
                    pair_key={k:sample['source'][k] for k in blind.PAIR_KEY},samples=list(reversed(ids))))
        frozen.update(case_contracts=contracts,samples=samples,comparisons=comparisons)
        selected = self.blind.write_selection(); approval = self.blind.approve(selected)
        self.draft = self.root/'full-draft'
        blind.draft(selected, approval, self.draft, now=NOW)
        self.selection.update(crosswalk=self.existing(self.draft/'private/crosswalk.json'),
            draft_receipt=self.existing(self.draft/'private/receipt.json'),approval=approval,pairs=[])
        self.bundle['packets'] = []
        for row in self.bundle['selection']: row['state'] = 'changed'
        for pair in comparisons:
            reference = self.existing(self.draft/('public/comparisons/'+pair['comparison_id']+'.json'))
            self.selection['pairs'].append(dict(pair_id=pair['comparison_id'],comparison=reference))
            self.bundle['packets'].append(dict(case=pair['pair_key']['case'],seed=pair['pair_key']['seed'],
                pair_id=pair['comparison_id'],packet_sha256=reference['sha256'],samples=pair['samples']))
        seal(self.bundle); self.sessions['binding_sha256'] = paired.canonical_sha(self.bundle['binding'])

    def existing(self, path):
        return dict(path=str(path), **digest(path.read_bytes()))

    def save(self):
        self.selection['grading_bundle'] = pin(self.root/'join.json', C(self.bundle))
        self.selection['sessions'] = pin(self.root/'sessions.json', C(self.sessions))
        return pin(self.root/'delivery-selection.json', C(self.selection))

    def reseal_frozen(self, mutate):
        cross = json.loads(Path(self.selection['crosswalk']['path']).read_bytes())
        receipt = json.loads(Path(self.selection['draft_receipt']['path']).read_bytes())
        public_path = Path(self.selection['pairs'][0]['comparison']['path'])
        public = json.loads(public_path.read_bytes())
        mutate(cross['subject'], public)
        subject = cross['subject']
        for sample in public['samples']:
            sample['sample_packet_sha256'] = digest(C(sample['packet']))['sha256']
            subject['sample_packets'][sample['sample_id']] = digest(C(sample['packet']))
        subject['comparison_packets'][public['comparison_id']] = digest(C(public))
        self.selection['pairs'][0]['comparison'] = pin(public_path, C(public))
        self.bundle['packets'][0]['packet_sha256'] = digest(C(public))['sha256']
        cross['selection_pin'] = pin(Path(cross['selection_pin']['path']), C(subject['selection']))
        cross['subject_sha256'] = digest(C(subject))['sha256']
        approval = json.loads(Path(self.selection['approval']['path']).read_bytes())
        approval['subject_sha256'] = cross['subject_sha256']
        self.selection['approval'] = pin(Path(self.selection['approval']['path']), C(approval))
        cross['approval_pin'] = self.selection['approval']
        self.selection['crosswalk'] = pin(Path(self.selection['crosswalk']['path']), C(cross))
        receipt.update({k: cross[k] for k in ('selection_pin','approval_pin','subject_sha256')})
        receipt['private_crosswalk'] = digest(C(cross))
        receipt['public_files'] = {kind+'/'+name+'.json': value for kind, key in
            [('samples','sample_packets'), ('comparisons','comparison_packets')]
            for name, value in subject[key].items()}
        # Measurements are independent arithmetic over the selected comparison/control.
        for row in receipt['declared_grader_fit']['comparisons'][public['comparison_id']]:
            row['required'] = len(C(public)) + self.blind.review['graders'][row['slot']-1]['control']['bytes']
        self.selection['draft_receipt'] = pin(Path(self.selection['draft_receipt']['path']), C(receipt))
        seal(self.bundle)
        self.sessions['binding_sha256'] = paired.canonical_sha(self.bundle['binding'])


def build(f, output=None):
    out = output or f.root/'out'
    receipt = delivery.build_plan(f.save(), out, now=NOW)
    return json.loads((out/'plan.json').read_bytes()), receipt


class DeliveryTests(unittest.TestCase):
    def test_full_original_occurrence_uses_one_pair_of_sessions_for_all_63_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); f.full_occurrence(); plan, receipt = build(f)
            self.assertEqual(len(f.bundle['schedule']['runs']),126)
            self.assertEqual(f.bundle['schedule']['distractors']['count'],1900)
            self.assertEqual(len(plan['pairs']),63)
            self.assertEqual(len({s['run_id'] for p in plan['pairs'] for s in p['sources']}),126)
            self.assertEqual(len({d['comparison']['path'] for p in plan['pairs'] for d in p['deliveries']}),126)
            for index, slot in enumerate(('primary','secondary')):
                expected = f.sessions['slots'][index]
                self.assertEqual({(d['agent_id'],d['session_id'],d['execution_id'])
                    for p in plan['pairs'] for d in p['deliveries'] if d['slot']==slot},
                    {(expected['agent_id'],expected['session_id'],expected['execution_id'])})
            self.assertEqual(receipt['qualification'],'unqualified')

    def test_frozen_sources_are_not_reopened_and_opaque_selected_bodies_are_not_pin_graphs(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory), all_channels=True)
            for sample in f.blind.selection['samples']:
                for channel in sample['channels']:
                    for entry in channel['entries']: Path(entry['input']['path']).unlink(missing_ok=True)
                    if channel['observation']: Path(channel['observation']['path']).unlink(missing_ok=True)
            f.sessions['slots'][0]['observation'] = pin(f.root/'opaque', C(dict(path='/forbidden/private',bytes=1,sha256='0'*64)))
            plan, _ = build(f)
            self.assertEqual(plan['wire_fit'],'unknown')

    def test_conflicting_private_pair_arm_run_and_dangling_mappings_refuse(self):
        for mutation in ('same_arm','wrong_seed','wrong_run','dangling_sample','duplicate_pair','wrong_order','sample_id'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                def mutate(subject, public):
                    samples = subject['selection']['samples']
                    if mutation == 'same_arm': samples[1]['source']['arm'] = 'CURRENT'
                    elif mutation == 'wrong_seed': samples[0]['source']['seed'] = 22
                    elif mutation == 'wrong_run': samples[0]['source']['run_id'] = 'unselected-run'
                    elif mutation == 'dangling_sample': samples.append(copy.deepcopy(samples[0]))
                    elif mutation == 'wrong_order': subject['selection']['comparisons'][0]['samples'].reverse()
                    elif mutation == 'sample_id': public['samples'][0]['packet']['sample_id'] = 'another-sample'
                if mutation == 'duplicate_pair': f.selection['pairs'].append(copy.deepcopy(f.selection['pairs'][0]))
                else: f.reseal_frozen(mutate)
                with self.assertRaises(paired.Refusal): build(f)
                self.assertFalse((f.root/'out').exists())

    def test_retained_input_declarations_preserve_original_bounds_and_closed_shapes(self):
        # Parent341's 2026-10-15 synthetic input-only bound counterexample.
        for mutation in ('input_bound','bool_bytes','bad_pin','extra_transform','bad_projection',
                         'observation','extra_entry','index_bound'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                def mutate(subject, public):
                    channel = subject['selection']['samples'][0]['channels'][0]
                    entry = channel['entries'][0]
                    if mutation == 'input_bound':
                        entry['input']['bytes'] = 9501
                        entry['transform'] = dict(kind='closed_json_projection', paths=[['selected']])
                    elif mutation == 'bool_bytes': entry['input']['bytes'] = True
                    elif mutation == 'bad_pin': entry['input']['sha256'] = 'not-a-digest'
                    elif mutation == 'extra_transform': entry['transform']['unselected'] = True
                    elif mutation == 'bad_projection': entry['transform'] = dict(kind='closed_json_projection', paths=[['a'],['a','b']])
                    elif mutation == 'observation': channel['observation'] = entry['input']
                    elif mutation == 'extra_entry': entry['runtime_verified'] = True
                    else: entry['input']['bytes'] = 16*1024*1024+1; entry['kind'] = 'index'
                f.reseal_frozen(mutate)
                with self.assertRaises(paired.Refusal): build(f)
                self.assertFalse((f.root/'out').exists())

    def test_hash_domains_never_substitute_sample_inner_or_blind_hashes(self):
        for mutation in ('sample_for_pair','plan_inner','schedule_inner','binding_C','schedule_source_inner','noncanonical'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                if mutation == 'sample_for_pair':
                    public = json.loads(Path(f.selection['pairs'][0]['comparison']['path']).read_bytes())
                    f.bundle['packets'][0]['packet_sha256'] = public['samples'][0]['sample_packet_sha256']
                    seal(f.bundle)
                elif mutation in ('plan_inner','schedule_inner'):
                    component = mutation.split('_')[0]
                    f.bundle['binding'][component+'_sha256'] = f.bundle[component]['sha256']
                elif mutation == 'binding_C':
                    f.sessions['binding_sha256'] = digest(C(f.bundle['binding']))['sha256']
                elif mutation == 'schedule_source_inner':
                    def mutate(subject, public):
                        for sample in subject['selection']['samples']:
                            sample['source']['schedule_sha256'] = f.bundle['schedule']['sha256']
                        subject['selection']['comparisons'][0]['pair_key']['schedule_sha256'] = f.bundle['schedule']['sha256']
                    f.reseal_frozen(mutate)
                else:
                    p = Path(f.selection['pairs'][0]['comparison']['path'])
                    f.selection['pairs'][0]['comparison'] = pin(p, p.read_bytes().rstrip(b'\n'))
                with self.assertRaises(paired.Refusal): build(f)
                self.assertFalse((f.root/'out').exists())

    def test_occurrence_sessions_reject_reuse_exposure_ambiguity_and_per_pair_overrides(self):
        for mutation in ('same_agent','same_session','same_execution','engineer','reviewer','coordinator',
                         'earlier_exposed','duplicate_slot','pair_override','stale','wrong_occurrence'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory)); primary, secondary = f.sessions['slots']
                if mutation.startswith('same_'):
                    field = mutation[5:]+'_id'
                    secondary[field] = primary[field]
                elif mutation in ('engineer','reviewer','coordinator','earlier_exposed'):
                    f.sessions['exclusions'].append(dict(role='actor_engineer' if mutation == 'engineer' else mutation,
                        **{k:primary[k] for k in ('agent_id','session_id','execution_id')}))
                elif mutation == 'duplicate_slot': secondary['slot'] = 'primary'
                elif mutation == 'pair_override':
                    f.selection['pairs'][0]['sessions'] = dict(primary='another-session')
                elif mutation == 'stale': f.sessions['valid_until'] = '2026-10-15T12:00:00Z'
                else: f.sessions['binding_sha256'] = '0'*64
                with self.assertRaises(paired.Refusal): build(f)
                self.assertFalse((f.root/'out').exists())

    def test_selected_control_policy_and_transitive_approval_mutations_refuse_before_writes(self):
        for mutation in ('control','route','workload','observation','renderer','transport','framing','tool_schema',
                         'rubric','review_budget','issuer','policy_version','expired'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory)); slot = f.sessions['slots'][0]
                if mutation in ('control','route','workload'): reference = slot[mutation]
                elif mutation == 'observation':
                    reference = slot[mutation] = pin(f.root/'observation', b'opaque declaration')
                elif mutation in ('renderer','transport','framing','tool_schema'):
                    reference = slot['envelope'][mutation] = pin(f.root/mutation, b'opaque envelope spec')
                elif mutation in ('rubric','review_budget'): reference = f.selection['policy'][mutation]['pin']
                elif mutation == 'issuer': reference = f.blind.approval['issuer_role']
                elif mutation == 'policy_version':
                    f.selection['policy']['rubric']['version'] = 'another'
                    reference = None
                else: reference = None
                selected = f.save()
                if reference: Path(reference['path']).write_bytes(b'mutated selected bytes')
                with self.assertRaises(paired.Refusal):
                    delivery.build_plan(selected, f.root/'out', now=NOW.replace(hour=14) if mutation == 'expired' else NOW)
                self.assertFalse((f.root/'out').exists())

    def test_closed_json_rejects_nonfinite_overflow_duplicate_keys_and_bool_lengths(self):
        for mutation in ('overflow','nan','duplicate','extra','bool'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory)); selected = f.save(); path = Path(selected['path'])
                if mutation == 'overflow': content = path.read_bytes().replace(b'{', b'{"extra":1e400,', 1)
                elif mutation == 'nan': content = path.read_bytes().replace(b'{', b'{"extra":NaN,', 1)
                elif mutation == 'duplicate': content = path.read_bytes().replace(b'{', b'{"schema":"duplicate",', 1)
                elif mutation == 'extra':
                    f.selection['verified'] = True; content = C(f.selection)
                else: content = path.read_bytes()
                selected = pin(path, content)
                if mutation == 'bool': selected['bytes'] = True
                with self.assertRaises(paired.Refusal): delivery.build_plan(selected, f.root/'out', now=NOW)
                self.assertFalse((f.root/'out').exists())

    def test_envelope_missing_or_unsupported_measurement_stays_unknown_and_undercount_refuses(self):
        for mode in ('tokens','unknown','renderer_claim','undercount'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                for slot in f.sessions['slots']:
                    env = slot['envelope']
                    env.update(unit='unknown' if mode == 'unknown' else 'tokens', semantics='full_rendered_request',
                               declared_input_tokens=1)
                    if mode == 'renderer_claim': env['renderer'] = pin(f.root/'renderer', b'{"verified":true}')
                    if mode == 'undercount': env.update(unit='bytes', declared_input_bytes=1)
                if mode == 'undercount':
                    with self.assertRaisesRegex(paired.Refusal, 'delivery_envelope_undercount'): build(f)
                    self.assertFalse((f.root/'out').exists())
                else:
                    plan, receipt = build(f)
                    self.assertEqual((plan['wire_fit'],plan['budget_state'],receipt['wire_fit']), ('unknown',)*3)

    def test_output_existing_symlink_and_all_selected_overlap_paths_refuse_before_writes(self):
        for mode in ('existing','symlink','policy','issuer','control','historical'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory)); out = f.root/'out'
                if mode == 'existing': out.mkdir(); (out/'sentinel').write_bytes(b'keep')
                elif mode == 'symlink': out.symlink_to(f.root/'absent', target_is_directory=True)
                elif mode == 'policy': f.selection['policy']['rubric']['pin']['path'] = str(out/'policy')
                elif mode == 'issuer':
                    # Output as ancestor of the transitive issuer path, without creating it.
                    role = f.blind.approval['issuer_role']; out = Path(role['path'])
                elif mode == 'control': f.sessions['slots'][0]['control']['path'] = str(out/'control')
                else:
                    def mutate(subject, public):
                        subject['selection']['samples'][0]['channels'][0]['entries'][0]['input']['path'] = str(out/'historical')
                    f.reseal_frozen(mutate)
                selected = f.save()
                before = {str(p):p.read_bytes() for p in f.root.rglob('*') if p.is_file() and not p.is_symlink()}
                with self.assertRaises(paired.Refusal): delivery.build_plan(selected, out, now=NOW)
                self.assertEqual(before, {str(p):p.read_bytes() for p in f.root.rglob('*') if p.is_file() and not p.is_symlink()})

    def test_retention_counts_both_pair_copies_private_plan_and_stored_and_returned_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory))
            shared = pin(f.root/'retained-framing', b'x'*20000)
            for slot in f.sessions['slots']: slot['envelope']['framing'] = shared
            out = f.root/'out-0'; _, receipt = build(f, out)
            total = sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) + len(C(receipt))
            self.assertEqual(len(list((out/'comparisons').iterdir())), 2)
            with patch.object(paired, 'FILE_LIMIT', total-1):
                with self.assertRaisesRegex(paired.Refusal, 'delivery_retention_bound'): build(f, f.root/'out-1')
            self.assertFalse((f.root/'out-1').exists())
            with patch.object(paired, 'FILE_LIMIT', total): build(f, f.root/'out-2')

    def test_copy_inflation_uses_real_eight_mib_cap_without_dedup_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory))
            shared = pin(f.root/'shared-overhead', b'x'*(2*1024*1024))
            for slot in f.sessions['slots']:
                for kind in ('transport','framing','tool_schema'): slot['envelope'][kind] = shared
            with self.assertRaisesRegex(paired.Refusal, 'delivery_retention_bound'): build(f)
            self.assertFalse((f.root/'out').exists())

    def test_fsync_failure_leaves_consumed_output_and_refuses_retry_without_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); selected = f.save(); out = f.root/'out'
            with patch.object(os, 'fsync', side_effect=OSError('private disk details')):
                with self.assertRaisesRegex(paired.Refusal, '^delivery_io_consumed$'):
                    delivery.build_plan(selected, out, now=NOW)
            before = {str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()}
            self.assertTrue(before)
            with self.assertRaisesRegex(paired.Refusal, 'target_exists'):
                delivery.build_plan(selected, out, now=NOW)
            self.assertEqual(before, {str(p.relative_to(out)):p.read_bytes() for p in out.rglob('*') if p.is_file()})

    def test_resealed_malformed_public_sample_cannot_hide_behind_declared_approval(self):
        for mutation in ('extra_channel_field','bad_base64','wrong_output_digest','missing_channel'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                f = Fixture(Path(directory))
                def mutate(subject, public):
                    channels = public['samples'][0]['packet']['channels']
                    if mutation == 'extra_channel_field': channels[0]['authority'] = True
                    elif mutation == 'bad_base64': channels[0]['data'] = '%%%'
                    elif mutation == 'wrong_output_digest': channels[0]['sha256'] = '0'*64
                    else: channels.pop()
                f.reseal_frozen(mutate)
                with self.assertRaises(paired.Refusal): build(f)
                self.assertFalse((f.root/'out').exists())

    def test_envelope_accounts_for_all_selected_parts_but_never_claims_wire_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory))
            for slot in f.sessions['slots']:
                env = slot['envelope']
                for part in ('renderer','transport','framing','tool_schema'):
                    env[part] = pin(f.root/(slot['slot']+'-'+part), b'1234567')
                env.update(unit='bytes', semantics='full_rendered_request', limit=1,
                           declared_input_bytes=999999)
            plan, _ = build(f)
            for row in plan['pairs'][0]['deliveries']:
                self.assertEqual(row['budget']['selected_component_bytes'],
                    row['comparison']['bytes'] + row['control']['bytes'] + 21)
                self.assertEqual(set(row['overhead']), {'transport','framing','tool_schema'})
                self.assertEqual(row['budget']['state'], 'over_budget')
                self.assertEqual(row['wire_fit'], 'unknown')
            self.assertEqual(plan['declared_grader_fit']['state'], 'fits')
            self.assertEqual(plan['budget_state'], 'over_budget')

    def test_frozen_draft_plan_and_separately_supplied_grades_join_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Fixture(Path(directory)); plan, receipt = build(f)
            pair = plan['pairs'][0]; rows = pair['deliveries']
            expected = (f.draft/'public/comparisons/comparison-A.json').read_bytes()
            self.assertEqual(pair['samples'], ['opaque-B', 'opaque-A'])
            self.assertEqual([s['arm'] for s in pair['sources']], ['CAND', 'CURRENT'])
            self.assertEqual([r['slot'] for r in rows], ['primary', 'secondary'])
            self.assertEqual(plan['binding_sha256'], paired.canonical_sha(f.bundle['binding']))
            grades = []
            for row in rows:
                self.assertEqual(Path(row['comparison']['path']).read_bytes(), expected)
                self.assertEqual(row['packet_sha256'], digest(expected)['sha256'])
                self.assertEqual(row['wire_fit'], 'unknown')
                grades.append(dict(submission_id=row['slot'], binding_sha256=plan['binding_sha256'],
                    pair_id=pair['pair_id'], packet_sha256=row['packet_sha256'],
                    rubric_sha256=plan['binding']['rubric_sha256'], slot=row['slot'],
                    agent_id=row['agent_id'], session_id=row['session_id'], state='determinate',
                    verdicts=dict.fromkeys(pair['samples'], 'pass'), reason_sha256='9'*64))
            joined = custody.join_results(f.bundle, grades, [])
            self.assertEqual(joined['aggregate']['declared_raw_agreement'], dict(numerator=1, denominator=1, rate=1.0))
            self.assertEqual(joined['aggregate']['custody'], 'unknown')
            self.assertEqual(receipt['qualification'], 'unqualified')
            for flag in ('release_authority_verified', 'runtime_admitted', 'delivery_available'):
                self.assertIs(receipt[flag], False)
            for reference in receipt['files'].values():
                self.assertEqual(f.existing(Path(reference['path'])), reference)
            self.assertNotIn('submissions', plan)

    def test_operational_entrypoints_refuse_before_arguments_or_private_reads(self):
        class Unreadable:
            def __str__(self): raise AssertionError('inspected argument')
            def __iter__(self): raise AssertionError('inspected argv')
        with patch.object(paired, 'read_bytes', side_effect=AssertionError('private read')):
            with self.assertRaisesRegex(paired.Refusal, 'grader_delivery_unavailable'):
                delivery.deliver(Unreadable(), selection=Unreadable())
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(delivery.main(Unreadable()), 2)
            self.assertEqual(json.loads(output.getvalue()),
                             dict(status='refused', code='grader_delivery_unavailable'))


if __name__ == '__main__':
    unittest.main()
