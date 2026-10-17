"""Table-integrity refusals; these checks never establish physical admission."""
import copy
import json
from pathlib import Path
import unittest
from check_sdk_owner_phase_table import validate

class PhaseTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parent.parent
        cls.table=json.loads((cls.root/'docs/sdk-owner-phase-table.json').read_text())

    def test_complete_named_coverage_remains_unproved(self):
        result=validate(self.table,self.root)
        self.assertEqual(result['status'],'TABLE_VALID_BOUNDS_INCOMPLETE')
        self.assertFalse(result['transition_allowed'])

    def test_omitted_cumulative_phase_refuses(self):
        table=copy.deepcopy(self.table)
        table['phases']=[p for p in table['phases'] if p['id']!='reply_detach_4']
        with self.assertRaises(AssertionError):validate(table,self.root)

    def test_later_command_working_reset_refuses(self):
        table=copy.deepcopy(self.table)
        phase=next(p for p in table['phases'] if p['id']=='interrupted_command_3')
        next(r for r in phase['reservations'] if r['ticket']=='C')['working']=129*1048576
        with self.assertRaises(AssertionError):validate(table,self.root)

    def test_uncertain_transition_cannot_point_to_earlier_charge(self):
        table=copy.deepcopy(self.table)
        phase=next(p for p in table['phases'] if p['id']=='before_status_proposed_3')
        phase['uncertain_publication']['old']='before_status_old_0'
        with self.assertRaises(AssertionError):validate(table,self.root)

    def test_private_stream_missing_from_later_phase_refuses(self):
        table=copy.deepcopy(self.table)
        phase=next(p for p in table['phases'] if p['id']=='terminal_recheck_4')
        phase['backings'].remove('private_stream')
        with self.assertRaises(AssertionError):validate(table,self.root)

    def test_paired_and_publication_backing_omissions_refuse(self):
        for phase_id,backing in [('first_hook_before_user','paired_selection'),('private_evidence_4','evidence_publication')]:
            table=copy.deepcopy(self.table)
            next(p for p in table['phases'] if p['id']==phase_id)['backings'].remove(backing)
            with self.assertRaises(AssertionError):validate(table,self.root)

    def test_source_drift_and_false_admission_refuse(self):
        for mutation in ('pin','admission'):
            table=copy.deepcopy(self.table)
            if mutation=='pin':next(iter(table['sources'].values()))['sha256']='0'*64
            else:table['phases'][0]['transition_allowed']=True
            with self.subTest(mutation=mutation),self.assertRaises(AssertionError):validate(table,self.root)

if __name__=='__main__':unittest.main()
