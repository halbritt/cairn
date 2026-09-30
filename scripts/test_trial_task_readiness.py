"""Cold setup limit routing; no database, embedding worker or provider."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import trial_task_eval as te
from test_trial_task_prospective import fixture
from trial_corpus_fingerprint import SCHEMA, MEMORY_FIELDS, TABLES


class ColdReadinessTests(unittest.TestCase):
    def test_default_explicit_and_invalid_cli_limits_before_effects(self):
        base = ['agent', '--legacy-fixtures', '--output', 'unused']
        for extra, expected in (([], 1800), (['--cold-readiness-timeout', '3600'], 3600)):
            with patch.object(te, 'cmd_agent', return_value=0) as execute:
                self.assertEqual(te.main(base+extra), 0)
            args = execute.call_args.args[0]
            self.assertEqual(args.cold_readiness_timeout, expected)
            self.assertEqual(args.timeout, 900)
        for value in ('0', '-1', '1.5'):
            with patch.object(te, 'cmd_agent') as execute, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    te.main(base+['--cold-readiness-timeout', value])
                execute.assert_not_called()

    def test_explicit_limit_forwarded_and_recorded_without_changing_task_timeout(self):
        seen = []
        class Store:
            def __init__(self, root, binary, label, **kwargs):
                self.root = root
                self.home = root/'home'
                self.home.mkdir(parents=True)
                self.token_file = root/'token'
                self.ids = {}
                self.env = {}
                self.binary = binary
                self.backend = 'embedding-command'
                self.readiness = None
            def seed_corpus(self, notes, **kwargs): pass
            def grow(self, *args): pass
            def start(self): pass
            def stop(self): seen.append('stopped')
            def wait_for_full_coverage(self, timeout=1800):
                seen.append(timeout)
                self.readiness = {'state':'full_eligible_coverage'}
            def fingerprint(self):
                return dict(schema=SCHEMA, memory_record_columns=sorted((*MEMORY_FIELDS,'use_generation')),
                            corpus={key:{} for key in TABLES})
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = fixture(base/'input')
            hook = base/'memory.py'
            hook.write_text('# fixture\n')
            with contextlib.redirect_stdout(io.StringIO()):
                te.main(['freeze-input','--input',str(root)])
            arm = {'binary':'fixture-binary','hook':str(hook),'semantic_fallback':True,
                   'recall_mode':'agent_tools','embedding_worker':'fixture-worker'}
            record = dict(run_id='new-work.current.s0', case='new-work', arm='current',seed=0,
                          outcome='correct',selected_input={'status':'within_observed_limits'})
            with patch.object(te,'TrialStore',Store), patch.object(te,'copy_arms',return_value=(['current'],{'current':arm})), \
                 patch.object(te,'cairn_json',return_value={}), patch.object(te,'run_agent',return_value=record) as task, \
                 contextlib.redirect_stdout(io.StringIO()):
                code = te.main(['agent','--prospective-input',str(root),'--output',str(base/'out'),
                                '--model','fixture-model','--reasoning-effort','high','--parallel','1','--distractors','0',
                                '--timeout','123','--cold-readiness-timeout','3600'])
            self.assertEqual(code,0)
            self.assertEqual(seen,[3600,'stopped'])
            self.assertEqual(task.call_args.args[4].timeout,123)
            for name in ('plan.json','agent.json'):
                report = json.loads((base/'out'/name).read_text())
                self.assertEqual(report['cold_readiness_timeout_seconds'],3600)
            self.assertEqual(json.loads((base/'out/plan.json').read_text())['limits']['timeout'],123)


if __name__ == '__main__':
    unittest.main()
