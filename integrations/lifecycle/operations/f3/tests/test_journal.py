import copy
import sys
import unittest
sys.path.insert(0, '/work')
from journal import Selection, JournalUnknown, validate


def pages():
    return [{'boot':'boot', 'scan_after':'pre', 'next_cursor':'post', 'terminal':True, 'end_seen':True,
             'loss':False, 'error':None,
             'events':[{'cursor':'exit', 'boot':'boot', 'unit':'fixture.service', 'subject_pid':111, 'class':'NORMAL_EXIT'}]}]


class JournalTests(unittest.TestCase):
    def setUp(self): self.selected = Selection('boot','pre','post','fixture.service',111,222)
    def test_consistent_projection_never_certifies_collection(self):
        result = validate(self.selected, pages())
        self.assertFalse(result['qualified'])
        self.assertEqual(result['selected_events'], 1)

    def test_missing_changed_loss_partial_ambiguous_or_private_projection_refuses(self):
        cases = []
        for field, value in [('boot','other'),('scan_after','wrong'),('next_cursor','wrong'),
                             ('terminal',False),('end_seen',False),('loss',True),('error','read-error')]:
            p=pages(); p[0][field]=value; cases.append(p)
        for field,value in [('boot','other'),('unit','other.service'),('subject_pid',999),('class','HIDDEN_KILL')]:
            p=pages(); p[0]['events'][0][field]=value; cases.append(p)
        p=pages(); p[0]['events']*=2; cases.append(p)
        p=pages(); p[0]['events'][0]['MESSAGE']='private'; cases.append(p)
        cases += [[], pages()*65]
        for p in cases:
            with self.subTest(case=len(str(p))):
                with self.assertRaises(JournalUnknown): validate(self.selected,p)

    def test_complete_pagination_has_exact_chain_and_bound(self):
        first=pages()[0]; first.update(next_cursor='middle',terminal=False,end_seen=False)
        last=pages()[0]; last.update(scan_after='middle',events=[])
        self.assertFalse(validate(self.selected,[first,last])['qualified'])
        broken=copy.deepcopy(last); broken['scan_after']='gap'
        with self.assertRaises(JournalUnknown): validate(self.selected,[first,broken])


if __name__ == '__main__': unittest.main(verbosity=2)
