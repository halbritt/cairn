"""New input and permission contracts; never launches a provider."""
import contextlib
import io
import json
import os
import hashlib
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import trial_task_eval as te


def fixture(root):
    root = Path(root)
    workspace = root / 'workspaces' / 'new-work'
    workspace.mkdir(parents=True)
    (workspace / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm base\n')
    (workspace / 'README.md').write_text('A fresh synthetic compatibility fixture.\n')
    case = dict(id='new-work', workspace='new-work', copy_to='cairn', cwd='cairn',
                category='component', provenance={'type':'synthetic'}, expected=[],
                wordings={'task':'Inspect this new workspace.'},
                preflight=[['git','status','--porcelain']],
                correct=[{'type':'answer','pattern':'inspected'}], mistake=[])
    (root/'input.json').write_text(json.dumps(dict(schema='cairn.task-eval.input/1',
        baseline_commit='0'*40, arms=['none'], memory_arms={},
        native={'binary':{'path':sys.executable,'sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()},'model':'fixture-model','effort':'high'}, corpus_policy={'description':'Empty synthetic compatibility corpus; no production selection.'}, cases=[case])))
    (root/'corpus.json').write_text('{"notes": []}\n')
    return root


class ProspectiveInputTests(unittest.TestCase):
    def test_freeze_fresh_input_without_legacy_overlays_and_refuse_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(Path(directory)/'input')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(te.main(['freeze-input','--input',str(root)]),0)
            from trial_task_input import load_input
            bundle = load_input(root)
            self.assertEqual([c['id'] for c in bundle['cases']],['new-work'])
            self.assertEqual(bundle['cases'][0]['wordings'],{'task':'Inspect this new workspace.'})
            (root/'workspaces/new-work/README.md').write_text('changed')
            with self.assertRaisesRegex(ValueError,'changed'):
                load_input(root)

    def test_prospective_native_path_uses_permissions_and_retains_denial(self):
        import argparse
        import sys
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = fixture(base/'input')
            with contextlib.redirect_stdout(io.StringIO()):
                te.main(['freeze-input','--input',str(root)])
            child = base/'native-fixture.py'
            child.write_text('''import json,sys
from pathlib import Path
print(json.dumps({'type':'system','subtype':'init','model':'fixture-model'}))
print(sys.stdin.readline().strip())
print(json.dumps({'type':'assistant','message':{'content':[{'type':'tool_use','id':'toolu_test','name':'Bash','input':{'command':'go test ./... SECRET'}}]}}))
print(json.dumps({'type':'result','is_error':False,'result':'inspected','num_turns':1,'permission_denials':[{'tool_name':'Bash','tool_use_id':'toolu_test','tool_input':{'command':'go test ./... SECRET'},'reason':'SECRET'}]}))
''')
            calls = []
            def sandbox(work, cwd, binds, env, argv, harness='claude', **kwargs):
                calls.append(list(argv))
                if argv[0] == 'claude':
                    return [sys.executable,str(child),*argv[1:]]
                return argv
            original_run = te.run
            def run(argv, **kwargs):
                if argv[:2] == ['claude','--version']:
                    import subprocess
                    return subprocess.CompletedProcess(argv,0,stdout=b'fixture native version')
                return original_run(argv,**kwargs)
            # Mock only the native transport; real input/copy/setup/report paths run.
            with patch.object(te,'sandbox_command',side_effect=sandbox), patch.object(te,'run',side_effect=run), contextlib.redirect_stdout(io.StringIO()):
                code = te.main(['agent','--prospective-input',str(root),'--output',str(base/'out'),
                                '--model','fixture-model','--reasoning-effort','high','--parallel','1','--distractors','0'])
            self.assertEqual(code,2,(base/"out/agent.json").read_text())
            argv = next(row for row in calls if row[0]=='claude')
            self.assertNotIn('bypassPermissions',argv)
            self.assertEqual(argv[argv.index('--permission-prompts')+1],'none')
            settings = json.loads((base/'out/runs/new-work.none.s0/settings.json').read_text())
            self.assertIn('Bash(make test-integration)',settings['permissions']['allow'])
            self.assertIn('mcp__cairn__cairn_remember',settings['permissions']['deny'])
            report = json.loads((base/'out/agent.json').read_text())
            self.assertEqual(len(report['records']),1)
            observation = report['records'][0]['trace']['permission_observation']
            self.assertEqual(observation['denial_count'],1)
            self.assertNotIn('SECRET',json.dumps(observation))
            self.assertEqual(report['records'][0]['case'],'new-work')
            self.assertEqual(report['records'][0]['outcome'],'correct')
            self.assertEqual(report['records'][0]['selected_input']['failures'],['native_permission_denial'])
            self.assertFalse((base/'out/runs/new-work.none.s0/stream.jsonl').exists())
            if os.environ.get('CAPLAB_CHECKOUT'):
                sys.path.insert(0,str(Path(os.environ['CAPLAB_CHECKOUT'])/'src'))
                from caplab.retrieval.native_task import import_task_run, verify_task_run
                retained=base/'caplab-evidence'
                import_task_run(base/'out/agent.json', plan_path=base/'out/plan.json', corpus_path=base/'out/input/corpus.json',
                                cairn_checkout=Path(te.__file__).resolve().parents[1],output=retained)
                verified=verify_task_run(retained)
                self.assertEqual(verified['counts']['streams_missing'],1)
                self.assertTrue(verified['verified'])

    def test_no_implicit_historical_execution_and_unsupported_scope_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            te.main(['agent','--output','unused','--model','fixture'])
        with tempfile.TemporaryDirectory() as directory:
            root=fixture(Path(directory)/'input')
            from trial_task_input import freeze_input, load_input
            note={'id':'lesson','kind':'lesson','body':'Synthetic reusable lesson.'}
            (root/'corpus.json').write_text(json.dumps({'notes':[note]}))
            freeze_input(root)
            self.assertEqual(load_input(root)['notes'][0]['kind'],'lesson')
            (root/'FROZEN.json').unlink()
            (root/'corpus.json').write_text(json.dumps({'notes':[dict(note,pins={'task_class':'repair'})]}))
            with self.assertRaisesRegex(ValueError,'unsupported corpus note'):
                freeze_input(root)

    def test_prerequisite_failure_never_launches_native_and_keeps_first_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); root=fixture(base/'input')
            document=json.loads((root/'input.json').read_text())
            document['cases'][0]['preflight']=[[sys.executable,'-c','raise SystemExit(9)']]
            (root/'input.json').write_text(json.dumps(document))
            with contextlib.redirect_stdout(io.StringIO()):te.main(['freeze-input','--input',str(root)])
            def sandbox(work,cwd,binds,env,argv,harness='claude',**kwargs):
                self.assertNotEqual(argv[0],'claude','provider must not be constructed after failed prerequisite')
                return argv
            with patch.object(te,'sandbox_command',side_effect=sandbox),contextlib.redirect_stdout(io.StringIO()):
                code=te.main(['agent','--prospective-input',str(root),'--model','fixture-model','--reasoning-effort','high',
                              '--output',str(base/'out'),'--distractors','0','--parallel','1'])
            self.assertEqual(code,2)
            checks=json.loads((base/'out/runs/new-work.none.s0/preflight.json').read_text())
            self.assertEqual(checks[0]['exit'],9)
            report=json.loads((base/'out/agent.json').read_text())
            self.assertEqual(report['records'][0]['outcome'],'harness_error')
            self.assertFalse((base/'out/runs/new-work.none.s0/stream.jsonl').exists())
            with self.assertRaises(FileExistsError),contextlib.redirect_stdout(io.StringIO()):
                te.main(['agent','--prospective-input',str(root),'--model','fixture-model','--reasoning-effort','high',
                         '--output',str(base/'out'),'--distractors','0','--parallel','1'])

    def test_observer_exception_preserves_independent_task_correctness(self):
        for failing_phase in ('measurement', 'hook_summary'):
            with self.subTest(phase=failing_phase), tempfile.TemporaryDirectory() as directory:
                base=Path(directory); root=fixture(base/'input')
                with contextlib.redirect_stdout(io.StringIO()):
                    te.main(['freeze-input','--input',str(root)])
                child=base/'native.py'
                child.write_text("import json,sys\nprint(json.dumps({'type':'system','subtype':'init','model':'fixture-model'}))\nprint(sys.stdin.readline().strip())\nprint(json.dumps({'type':'result','is_error':False,'result':'inspected','permission_denials':[]}))\n")
                def sandbox(work,cwd,binds,env,argv,harness='claude',**kwargs):
                    return [sys.executable,str(child)] if argv[0]=='claude' else argv
                target='measure' if failing_phase=='measurement' else 'hook_observations'
                with patch.object(te,'sandbox_command',side_effect=sandbox), patch.object(te,target,side_effect=AttributeError('PRIVATE failure payload')), contextlib.redirect_stdout(io.StringIO()):
                    te.main(['agent','--prospective-input',str(root),'--model','fixture-model','--reasoning-effort','high',
                             '--output',str(base/'out'),'--distractors','0','--parallel','1'])
                report=json.loads((base/'out/agent.json').read_text())
                record=report['records'][0]
                self.assertEqual(record['outcome'],'correct')
                self.assertIsInstance(record['seconds'],(int,float))
                selected=record['selected_input']
                if failing_phase=='measurement':
                    self.assertEqual(selected['status'],'unknown')
                    self.assertIsNone(selected['total_bytes'])
                    self.assertIsNone(selected['total_actual_pull_calls'])
                    error=selected['observation_error']
                else:
                    error=record['memory']['observation_error']
                self.assertEqual(error,{'phase':failing_phase,'exception_type':'AttributeError'})
                self.assertEqual(selected['status'],'unknown')
                error_file='observation-error.json' if failing_phase=='measurement' else 'hook-summary-error.json'
                self.assertEqual(json.loads((base/'out/runs/new-work.none.s0'/error_file).read_text()),error)
                self.assertNotIn('PRIVATE',json.dumps(report))
                self.assertFalse((base/'out/runs/new-work.none.s0/stream.jsonl').exists())

    def test_selected_terminal_stop_survives_grading_failure_without_raw_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory); root=fixture(base/'input')
            document=json.loads((root/'input.json').read_text())
            second=dict(document['cases'][0], id='second-work')
            document['cases'].append(second)
            (root/'input.json').write_text(json.dumps(document))
            with contextlib.redirect_stdout(io.StringIO()):
                te.main(['freeze-input','--input',str(root)])
            child=base/'native-fixture.py'
            source_id='12345678-1234-4234-8234-123456789abc'
            events=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'toolu_evidence','name':'mcp__cairn__cairn_pull','input':{'PRIVATE_QUERY':True}}]}},
                    {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'toolu_evidence','content':[{'type':'text','text':json.dumps({'selection':{'record':{'record_id':source_id,'version':1,'body':'PRIVATE_BODY'}}})}]}]}},
                    {'type':'result','is_error':True,'terminal_reason':'api_error','api_error_status':401,'result':'PRIVATE provider error'}]
            child.write_text('import json\nfor event in '+repr(events)+': print(json.dumps(event))\n')
            launches=[]
            def sandbox(work,cwd,binds,env,argv,harness='claude',**kwargs):
                if argv[0]=='claude':
                    launches.append(argv)
                    return [sys.executable,str(child)]
                return argv
            with patch.object(te,'sandbox_command',side_effect=sandbox), patch.object(te,'grade',side_effect=RuntimeError('PRIVATE grader error')), contextlib.redirect_stdout(io.StringIO()):
                code=te.main(['agent','--prospective-input',str(root),'--model','fixture-model','--reasoning-effort','high',
                              '--output',str(base/'out'),'--distractors','0','--parallel','1'])
            report=json.loads((base/'out/agent.json').read_text())
            self.assertEqual(code,2)
            self.assertEqual(len(launches),1)
            self.assertEqual(report['admission']['stop']['reason'],'authentication_failed')
            self.assertEqual(len(report['admission']['not_started']),1)
            run=base/'out/runs'/report['records'][0]['run_id']
            self.assertFalse((run/'stream.jsonl').exists())
            selected=(run/'native-terminal.json').read_text()
            self.assertNotIn('PRIVATE',selected)
            self.assertEqual(json.loads(selected)['admission_failure']['api_error_status'],401)
            references=report['records'][0]['memory']['source_deliveries']
            self.assertEqual(references[0]['delivery']['items'][0]['record_id'],source_id)
            self.assertEqual(report['records'][0]['memory']['relevance_status'],'unknown_unlabelled')
            self.assertNotIn('PRIVATE_',json.dumps(references))
            self.assertTrue((run/'selected-input.json').is_file())

    def test_direct_note_prompt_keeps_missing_source_channel_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);root=fixture(base/'input')
            document=json.loads((root/'input.json').read_text())
            document['arms']=['direct'];document['cases'][0]['expected']=['note']
            (root/'input.json').write_text(json.dumps(document))
            (root/'corpus.json').write_text(json.dumps({'notes':[{'id':'note','kind':'lesson','body':'PRIVATE_DIRECT_NOTE'}]}))
            with contextlib.redirect_stdout(io.StringIO()):te.main(['freeze-input','--input',str(root)])
            child=base/'fake.py';child.write_text("import json,sys\nprint(json.dumps({'type':'system','subtype':'init','model':'fixture-model'}))\nprint(sys.stdin.readline().strip())\nprint(json.dumps({'type':'result','is_error':False,'result':'inspected','permission_denials':[]}))\n")
            def sandbox(work,cwd,binds,env,argv,harness='claude',**kwargs):
                return [sys.executable,str(child)] if argv[0]=='claude' else argv
            with patch.object(te,'sandbox_command',side_effect=sandbox),contextlib.redirect_stdout(io.StringIO()):
                code=te.main(['agent','--prospective-input',str(root),'--model','fixture-model','--reasoning-effort','high',
                             '--output',str(base/'out'),'--distractors','0','--parallel','1'])
            self.assertEqual(code,2)
            report=json.loads((base/'out/agent.json').read_text());record=report['records'][0]
            self.assertEqual(record['outcome'],'correct')
            self.assertEqual(record['selected_input']['status'],'unknown')
            self.assertIn('direct_prompt_source_delivery_unobserved',record['selected_input']['unknown'])
            self.assertNotIn('PRIVATE_DIRECT_NOTE',json.dumps(record['selected_input']))
