"""Shared prospective toolchain boundary; real bwrap, no provider or host DB."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import trial_task_eval as te

class RuntimeTests(unittest.TestCase):
 @unittest.skipUnless(os.environ.get('CAIRN_TEST_RUNTIME_INPUTS'),'explicit owned runtime fixture required')
 def test_actual_worker_and_grader_go_cgo_offline_module_and_disposable_postgres(self):
  from trial_task_runtime import runtime_descriptor, prepare_identity, runtime_environment
  runtime=runtime_descriptor(Path(os.environ['CAIRN_TEST_RUNTIME_INPUTS']))
  with tempfile.TemporaryDirectory() as temporary:
   base=Path(temporary);root=base/'work';root.mkdir();work=root/'cairn';work.mkdir();identity=prepare_identity(base/'identity')
   (work/'go.mod').write_text('module fixture\n\ngo 1.25.0\n\nrequire github.com/google/uuid v1.6.0\n')
   (work/'main.go').write_text('package main\n/*\n#include <stdlib.h>\n*/\nimport "C"\nimport "github.com/google/uuid"\nfunc main(){_ = C.int(1); _ = uuid.New()}\n')
   wrapper=Path(te.__file__).with_name('trial-task-eval.sh')
   (work/'owned-pg.sh').write_bytes(wrapper.read_bytes())
   command='go list ./... && go build -o /tmp/fixture . && /tmp/fixture && bash owned-pg.sh -- bash -c \'"$CAIRN_TASK_EVAL_PG_BIN/psql" -h "$CAIRN_TASK_EVAL_PG" -d postgres -Atc "select 42"\''
   env=dict(HOME=str(Path.home()),PATH=str(Path.home()/'.local/go/bin')+':/usr/bin:/bin',LANG='C.UTF-8',**runtime_environment(runtime))
   argv=te.sandbox_command(root,'cairn',[],env,['bash','-c',command],harness='codex',common_runtime=runtime,runtime_identity=identity)
   # The worker route may use provider networking; this provider-free check does not.
   argv.insert(1,'--unshare-net')
   done=subprocess.run(argv,capture_output=True,text=True,timeout=120,env=env)
   self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertIn('42',done.stdout)
   ctx=dict(cwd=work,common_runtime=runtime,runtime_identity=identity)
   self.assertTrue(te.evaluate(dict(type='shell',run=command,timeout=120),ctx),ctx.get('check_log'))
   # Neither sandbox exposes host account data or mutable runtime input aliases.
   probe='test "$(wc -l </etc/passwd)" = 1 && test "$(id -un)" = fixture && test ! -e /var/run/postgresql && ! touch '+str(Path.home()/'.local/go/forbidden')
   self.assertTrue(te.evaluate(dict(type='shell',run=probe),ctx),ctx.get('check_log'))

 def test_missing_assets_produce_undetermined_grader_not_wrong_task(self):
  from trial_task_runtime import prepare_identity
  with tempfile.TemporaryDirectory() as temporary:
   base=Path(temporary);work=base/'work/cairn';work.mkdir(parents=True)
   identity=prepare_identity(base/'identity')
   runtime=dict(schema='cairn.task-runtime/go-postgres/1',root=str(base/'absent'),sha256='0'*64)
   case=dict(correct=[dict(type='shell',run='exit 0')],mistake=[])
   result=te.grade(case,dict(cwd=work,common_runtime=runtime,runtime_identity=identity))
   self.assertEqual(result['outcome'],'undetermined')
   self.assertIn('common runtime setup unavailable',result['correct'][0])

 def test_frozen_common_runtime_refuses_changed_content_and_writable_alias(self):
  from trial_task_runtime import runtime_descriptor, prepare_identity, runtime_mounts
  from trial_task_input import freeze_input, load_input
  from test_trial_task_prospective import fixture
  with tempfile.TemporaryDirectory() as temporary:
   base=Path(temporary);root=base/'runtime';(root/'go/bin').mkdir(parents=True);(root/'module-cache').mkdir()
   (root/'go/VERSION').write_text('go1.25.0\n');(root/'go/bin/go').write_text('fixture executable');(root/'go/bin/go').chmod(0o700)
   cache=root/'module-cache/module';cache.write_text('frozen module')
   runtime=runtime_descriptor(root);inputs=fixture(base/'input');p=inputs/'input.json';doc=json.loads(p.read_text());doc['runtime']=runtime;p.write_text(json.dumps(doc))
   freeze_input(inputs);self.assertEqual(load_input(inputs)['document']['runtime'],runtime)
   cache.write_text('changed module')
   with self.assertRaisesRegex(ValueError,'runtime changed'):load_input(inputs)
   identity=prepare_identity(base/'identity')
   with self.assertRaisesRegex(ValueError,'overlap'):runtime_mounts(runtime,identity,base)
