"""Selected native action metadata, without commands, output or reasoning."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from trial_native_observation import measure


def observed(events, workspace, commands=None, times=None, denials=None):
    events = [dict(type='system', subtype='init', model='fixture'),
              dict(type='user', message=dict(content='ordinary task'))] + events + [
              dict(type='result', permission_denials=denials or [])]
    text = ''.join(json.dumps(e, ensure_ascii=True) + '\n' for e in events)
    timings = [dict(line=n+1, received_seconds=(times or {}).get(n,n/10), sha256=hashlib.sha256(line.encode()).hexdigest())
               for n, line in enumerate(text.splitlines(keepends=True))]
    return measure(text, timings, [], input_bytes=500, expects_hooks=False,
                   model='fixture', prompt='ordinary task', workspace=workspace,
                   public_validation_commands=commands)


def call(ident, name, data):
    return dict(type='assistant', message=dict(content=[dict(type='thinking', thinking='PRIVATE REASONING'),
        dict(type='tool_use', id=ident, name=name, input=data)]))


def result(ident, error=False, **extra):
    return dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id=ident,
        is_error=error, content='PRIVATE OUTPUT')]), **extra)


class PublicActionTests(unittest.TestCase):
    def test_public_paths_command_label_and_native_result_without_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'é.go').write_text('synthetic')
            command='go test ./core'
            events=[call('read','Read',dict(file_path=str(root/'é.go'))),result('read'),
                    call('edit','Edit',dict(file_path='é.go',old_string='PRIVATE OLD',new_string='PRIVATE NEW')),result('edit'),
                    call('test','Bash',dict(command=command)),result('test',tool_use_result=dict(exit_code=0))]
            output=observed(events,root,{'core_tests':command})['public_actions']
            self.assertEqual(output['status'],'observed')
            self.assertEqual(len(output['actions']),3)
            read,edit,test=output['actions']
            self.assertEqual(read['path'],'é.go');self.assertEqual(edit['path'],'é.go')
            self.assertEqual(test['validation_label'],'core_tests')
            self.assertEqual(test['command_sha256'],hashlib.sha256(command.encode()).hexdigest())
            self.assertEqual(test['state'],'tool_success');self.assertEqual(test['exit_code'],0)
            self.assertLess(test['started_seconds'],test['result_seconds'])
            self.assertNotIn(command,json.dumps(output));self.assertNotIn('PRIVATE',json.dumps(output))
            self.assertNotIn(str(root),json.dumps(output))

    def test_unknown_outside_symlink_and_traversal_paths_never_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root=Path(directory); target=Path(outside)/'PRIVATE.py'; target.write_text('PRIVATE')
            (root/'escape').symlink_to(outside)
            events=[]
            for n,path in enumerate((str(target),'../PRIVATE.py','escape/PRIVATE.py','bad\nPRIVATE.py')):
                events.extend([call(str(n),'Read',dict(file_path=path)),result(str(n))])
            events.extend([call('bad','PRIVATE tool',dict(secret='PRIVATE')),result('bad')])
            output=observed(events,root)['public_actions']
            self.assertEqual(output['status'],'unknown')
            self.assertTrue(all(row.get('path') is None for row in output['actions']))
            self.assertNotIn('PRIVATE',json.dumps(output))
            self.assertIn('action_path_unverified',output['unknown'])
            self.assertIn('action_shape_unknown',output['unknown'])

    def test_background_missing_flags_and_errors_are_not_test_passes(self):
        with tempfile.TemporaryDirectory() as root:
            background=result('bg',tool_use_result=dict(backgroundTaskId='PRIVATE TASK'))
            missing=result('missing');del missing['message']['content'][0]['is_error']
            events=[call('bg','Bash',dict(command='echo PRIVATE',run_in_background=True)),background,
                    call('missing','Bash',dict(command='make check')),missing,
                    call('err','Bash',dict(command='PRIVATE=secret go test ./core')),result('err',True),
                    call('success','Bash',dict(command='make check')),result('success')]
            output=observed(events,root,{'make-check':'make check'})['public_actions']
            self.assertEqual([a['state'] for a in output['actions']],['background','unknown','tool_error','tool_success'])
            self.assertTrue(all(a['exit_code'] is None for a in output['actions']))
            self.assertNotIn('test_passed',json.dumps(output));self.assertNotIn('PRIVATE',json.dumps(output))
            self.assertEqual(output['status'],'unknown')

    def test_duplicate_unmatched_unfinished_and_omitted_are_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            events=[call('dup','Bash',dict(command='make check')),result('dup'),result('dup'),
                    call('twice','Bash',dict(command='make check')),call('twice','Bash',dict(command='PRIVATE')),result('twice'),
                    result('unmatched'),call('unfinished','Bash',dict(command='make check'))]
            output=observed(events,root)['public_actions']
            self.assertEqual(output['duplicate_events'],2)
            self.assertEqual(output['unmatched_results'],1)
            self.assertEqual(output['actions'][0]['state'],'unknown')
            self.assertEqual(output['actions'][1]['state'],'unknown')
            self.assertIn('unfinished_actions',output['unknown'])
            events=[]
            for n in range(257): events.extend([call(str(n),'Bash',dict(command='make check')),result(str(n))])
            output=observed(events,root)['public_actions']
            self.assertEqual(len(output['actions']),256)
            self.assertEqual(output['actions_omitted'],1)
            self.assertEqual(output['unmatched_results'],1)
            self.assertEqual(output['status'],'unknown')

    def test_tool_error_overrides_requested_or_reported_background(self):
        with tempfile.TemporaryDirectory() as root:
            for requested in (True, False):
                with self.subTest(requested=requested):
                    events=[call('denied','Bash',dict(command='make check',run_in_background=requested)),
                            result('denied',True,tool_use_result=dict(backgroundTaskId='PRIVATE TASK'))]
                    output=observed(events,root)['public_actions']
                    self.assertEqual(output['actions'][0]['state'],'tool_error')
                    self.assertNotIn('background_completion_unobserved',output['unknown'])
                    self.assertNotIn('PRIVATE',json.dumps(output))

    def test_terminal_denial_overrides_background_without_hiding_other_work(self):
        with tempfile.TemporaryDirectory() as root:
            for other_background in (False, True):
                with self.subTest(other_background=other_background):
                    events=[call('denied','Bash',dict(command='make check',run_in_background=True)),result('denied')]
                    if other_background:
                        events += [call('running','Bash',dict(command='go test ./core',run_in_background=True)),result('running')]
                    denial=dict(tool_name='Bash',tool_use_id='denied',reason='PRIVATE REASON',tool_input=dict(command='PRIVATE COMMAND'))
                    measurement=observed(events,root,denials=[denial])
                    output=measurement['public_actions']
                    self.assertEqual(output['actions'][0]['state'],'tool_error')
                    self.assertIsNone(output['actions'][0]['exit_code'])
                    self.assertEqual('background_completion_unobserved' in output['unknown'],other_background)
                    self.assertIn('native_permission_denial',measurement['failures'])
                    self.assertNotIn('PRIVATE',json.dumps(output))

    def test_terminal_denial_cannot_resolve_duplicate_action_identity(self):
        with tempfile.TemporaryDirectory() as root:
            events=[call('dup','Bash',dict(command='make check',run_in_background=True)),
                    call('dup','Bash',dict(command='go test ./core')),result('dup')]
            denial=dict(tool_name='Bash',tool_use_id='dup')
            output=observed(events,root,denials=[denial])['public_actions']
            self.assertEqual(output['actions'][0]['state'],'unknown')
            self.assertIn('duplicate_action_identity',output['unknown'])

    def test_source_delivery_precedes_action_without_retaining_either_payload(self):
        with tempfile.TemporaryDirectory() as root:
            source=dict(record_id='b69027d6-3b8a-4d2d-bf07-ac75aef044ce',version=2,body='PRIVATE guidance')
            output=json.dumps(dict(hookSpecificOutput=dict(additionalContext=json.dumps(dict(
                selected=[dict(record=source,mandatory=False)])))))+'\n'
            events=[dict(type='system',subtype='hook_started',hook_id='hook',hook_event='UserPromptSubmit'),
                    dict(type='system',subtype='hook_response',hook_id='hook',hook_event='UserPromptSubmit',
                         stdout=output,outcome='success',exit_code=0),
                    call('implementation','Write',dict(file_path='src/new.go',content='PRIVATE code')),
                    result('implementation')]
            measurement=observed(events,root)
            delivered=measurement['source_deliveries'][0]
            action=measurement['public_actions']['actions'][0]
            self.assertEqual(delivered['delivery']['items'][0]['record_id'],source['record_id'])
            self.assertLess(delivered['received_seconds'],action['started_seconds'])
            self.assertEqual(action['path'],'src/new.go')
            self.assertNotIn('PRIVATE',json.dumps(measurement))

    def test_malformed_command_unicode_or_result_type_remains_bounded_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            events=[call('bad','Bash',dict(command='\ud800')),result('bad',tool_use_result=dict(exit_code=True))]
            output=observed(events,root)['public_actions']
            self.assertEqual(output['status'],'unknown')
            self.assertIsNone(output['actions'][0]['command_sha256'])
            self.assertIsNone(output['actions'][0]['exit_code'])

    def test_missing_negative_and_nonfinite_timestamps_are_unknown(self):
        with tempfile.TemporaryDirectory() as root:
            events=[call('t','Bash',dict(command='make check')),result('t')]
            for stamp in (None,-1,float('inf'),float('nan'),True):
                with self.subTest(stamp=stamp):
                    output=observed(events,root,times={2:stamp,3:stamp})['public_actions']
                    self.assertEqual(output['status'],'unknown')
                    self.assertIsNone(output['actions'][0]['started_seconds'])
                    self.assertIsNone(output['actions'][0]['result_seconds'])
                    self.assertIn('action_timing_unknown',output['unknown'])

    def test_reversed_time_and_shared_exit_metadata_are_not_attributed(self):
        with tempfile.TemporaryDirectory() as root:
            events=[call('a','Bash',dict(command='make check')),result('a')]
            output=observed(events,root,times={2:2,3:1})['public_actions']
            self.assertIsNone(output['actions'][0]['result_seconds'])
            self.assertIn('action_timing_unknown',output['unknown'])
            merged=result('a',tool_use_result=dict(exit_code=0))
            merged['message']['content'].extend(result('b')['message']['content'])
            output=observed([call('a','Bash',dict(command='make check')),
                             call('b','Bash',dict(command='make test-integration')),merged],root)['public_actions']
            self.assertTrue(all(row['exit_code'] is None for row in output['actions']))
            self.assertIn('action_exit_code_unknown',output['unknown'])

    def test_unsupported_native_or_empty_format_is_not_observed_zero(self):
        with tempfile.TemporaryDirectory() as root:
            output=observed([dict(type='item.completed',item=dict(type='command_execution',command='PRIVATE'))],root)['public_actions']
            self.assertEqual(output['status'],'unknown')
            self.assertEqual(output['actions'],[])
            self.assertIn('native_action_format_unsupported',output['unknown'])
            self.assertNotIn('PRIVATE',json.dumps(output))
            empty=observed([],root)['public_actions']
            self.assertEqual(empty['status'],'unknown')
            self.assertIn('no_observed_actions',empty['unknown'])
            malformed=observed([dict(type='assistant',message='PRIVATE')],root)['public_actions']
            self.assertIn('native_action_format_unknown',malformed['unknown'])
