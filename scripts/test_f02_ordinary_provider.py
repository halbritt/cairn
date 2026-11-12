"""Provider-selected recall through the installed Source and checked Go entry."""
import copy
import json
import os
from pathlib import Path
import unittest

import test_f02_opencode_ambient as ambient
import test_f02_checked_ordinary as ordinary


class OrdinaryProviderTests(ambient.OpenCodeAmbientTests):
    def setUp(self):
        super().setUp()
        self.responses = {'recall': {'result': {'structured_output': {'index': 0}}}}
        self.provider_sides = {}
        self.provider = self.root / 'private-selector'
        ordinary.write(self.provider, b'''#!/usr/bin/python3
import json,os,pathlib,sys,time
root=pathlib.Path(__file__).parent
argv=sys.argv[1:]
schema=json.loads(argv[argv.index('--json-schema')+1])
stage='preview' if 'indices' in schema['properties'] else 'recall'
body=json.load(sys.stdin)
spec=json.loads((root/'provider.json').read_text())[stage]
stat=pathlib.Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
call=dict(stage=stage,argv=argv,body=body,pid=os.getpid(),start=stat[19],
          cwd=os.getcwd(),mode=os.stat('.').st_mode&0o777,entries=os.listdir(),
          child=os.environ.get('CAIRN_LIFECYCLE_CHILD'),claudecode=os.environ.get('CLAUDECODE'))
with (root/'provider.calls').open('a') as log:log.write(json.dumps(call)+'\\n')
if spec.get('delay'):time.sleep(spec['delay'])
sys.stdout.write(spec.get('stdout',json.dumps(spec.get('result',{}),ensure_ascii=False,separators=(',',':'))))
sys.stderr.write(spec.get('stderr',''))
sys.exit(spec.get('exit',0))
''', 0o700)
        self.configure(semantic_fallback=True, claude=str(self.provider),
                       preview_model='fixture-preview', recall_model='fixture-recall')
        original = self.f.env
        self.f.env = lambda: dict(original(), CLAUDECODE='synthetic-parent')

    def invoke_provider_side(self, side, invoke):
        log = self.root / 'provider.calls'
        log.unlink(missing_ok=True)
        ordinary.write(self.root / 'provider.json', ordinary.encoded(self.responses))
        result = invoke()
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        for call in calls:
            pid, started = call.pop('pid'), call.pop('start')
            self.assertIs(type(pid), int)
            self.assertGreater(pid, 1)
            proc = Path('/proc', str(pid), 'stat')
            if proc.exists():
                self.assertNotEqual(proc.read_text().rsplit(')', 1)[1].split()[19], started)
            cwd = Path(call.pop('cwd'))
            self.assertTrue(cwd.name.startswith('cairn-selection-'))
            self.assertFalse(cwd.exists())
            self.assertEqual(call.pop('mode'), 0o700)
            self.assertEqual(call.pop('entries'), [])
            self.assertEqual(call['child'], '1')
            self.assertIsNone(call['claudecode'])
        self.provider_sides[side] = calls
        return result

    def source(self):
        return self.invoke_provider_side('source', lambda: super(OrdinaryProviderTests, self).source())

    def go(self):
        return self.invoke_provider_side('go', lambda: super(OrdinaryProviderTests, self).go())

    def measured_status(self, status):
        for key in ('at', 'duration_ms', 'elapsed_seconds', 'preview_seconds', 'pull_seconds', 'model_seconds'):
            if key in status:
                self.measurement(status[key])
                status[key] = 0
        for key in ('preview_process', 'model_process'):
            for field in ('spawn_ms', 'first_stdout_ms', 'first_stderr_ms', 'elapsed_ms'):
                process = status.get(key, {})
                if field in process:
                    self.measurement(process[field])
                    process[field] = 0

    def normalize(self, row, side):
        row = copy.deepcopy(row)
        for name, item in row['files'].items():
            if name.endswith('.json'):
                body = json.loads(item['raw'])
                self.measured_status(body.get('last_recall', {}))
                item['raw'] = ordinary.encoded(body).decode()
        for operation, body, _ in row['calls']:
            if operation == 'recall-observation':
                for call in body['selector_calls']:
                    self.measurement(call['elapsed_ms'])
                    call['elapsed_ms'] = 0
        return super().normalize(row, side)

    def pair(self):
        result = super().pair()
        self.assertEqual(self.provider_sides['go'], self.provider_sides['source'])
        return result

    def test_checked_ambient_verifies_a_current_body(self):
        self.required('Required retention policy.')
        self.optional_note('Use PostgreSQL for durable storage. 日本語 😀')
        self.event('SessionStart', prompt='Which durable backend is used here?')
        result = self.pair()
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        context = self.context(result)
        self.assertEqual(len(context['selected']), 1)
        self.assertTrue(context['selected'][0]['mandatory'])
        for field in ('record_id', 'version', 'body'):
            self.assertEqual(context['selected'][0]['record'][field], self.selected[0]['record'][field])
        self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
        self.assertEqual(len(self.provider_sides['go']), 1)
        self.assertEqual(self.provider_sides['go'][0]['stage'], 'recall')
        self.assertEqual(self.state()[self.state_key()+'.json']['last_recall']['discovery'], 'verified')


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(OrdinaryProviderTests(name) for name in
                             OrdinaryProviderTests.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
