"""Selected provider observations across Hermes native hook callbacks."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class HermesProviderObservation(unittest.TestCase):
    def test_only_owner_provider_failure_and_recovery_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'cairn-coordination.json').write_text(json.dumps(dict(script='engine',config='config')))
            modules = {'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda:home),
                'tools.terminal_tool':types.SimpleNamespace(get_session_cwd=lambda _:directory,resolve_task_overrides=lambda _:{})}
            spec = importlib.util.spec_from_file_location('hermes_coordination_test',ROOT/'integrations/hermes/coordination.py')
            plugin = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,modules):
                spec.loader.exec_module(plugin)
            callbacks = {}
            ctx = Mock()
            ctx.register_hook.side_effect = lambda name,callback:callbacks.update({name:callback})
            plugin.register(ctx)
            sent = []
            def run(*_,**kwargs):
                sent.append(json.loads(kwargs['input']))
                return types.SimpleNamespace(returncode=0,stdout='{}')
            with patch.object(plugin.subprocess,'run',side_effect=run), patch.dict(os.environ,CAIRN_WAKE_CONTEXT='wake.json'):
                callbacks['pre_llm_call'](session_id='owner',platform='cli')
                callbacks['pre_llm_call'](session_id='child',parent_session_id='owner')
                callbacks['api_request_error'](session_id='child',status_code=429,reason='rate_limit')
                self.assertEqual(len(sent),1)
                callbacks['api_request_error'](session_id='owner',status_code=429,reason='rate_limit',error={'message':'PRIVATE'},request={'body':'PRIVATE'})
                self.assertEqual(sent[-1]['provider_failure'],dict(harness='hermes',source='native-hook',kind='rate_limit',code='rate_limit',status=429))
                callbacks['post_api_request'](session_id='owner',response={'text':'PRIVATE'})
                self.assertIsNone(sent[-1]['provider_failure'])
                callbacks['api_request_error'](session_id='owner',status_code=402,reason='billing')
                self.assertEqual(sent[-1]['provider_failure']['kind'],'billing')
                callbacks['api_request_error'](session_id='owner',status_code=500,reason='server_error')
                self.assertIsNone(sent[-1]['provider_failure'])
                self.assertNotIn('PRIVATE',json.dumps(sent))
