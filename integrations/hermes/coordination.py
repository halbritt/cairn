"""Native Hermes session presence, independent of memory capture settings."""
import json
import logging
import os
import subprocess
import sys
import threading

from hermes_constants import get_hermes_home
from tools.terminal_tool import get_session_cwd, resolve_task_overrides

logger = logging.getLogger(__name__)


def register(ctx):
    home = get_hermes_home()
    config = json.loads((home / 'cairn-coordination.json').read_text())
    sessions = {}
    lock = threading.RLock()

    def invoke(session_id, event, task_id='', model='', **selected):
        cwd = (get_session_cwd(task_id) or get_session_cwd(session_id)
               or resolve_task_overrides(task_id).get('cwd') or os.environ.get('TERMINAL_CWD') or os.getcwd())
        result = subprocess.run([sys.executable, config['script'], 'hook', '--config', config['config']],
            input=json.dumps(dict(session_id=session_id, hook_event_name=event, cwd=cwd, host_pid=os.getpid(), model=model, **selected)),
            text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Cairn session presence unavailable')
        return json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')

    def before(session_id='', turn_id='', task_id='', model='', parent_session_id='', platform='', **_):
        if not session_id or parent_session_id or platform in ('cron', 'subagent', 'flush', 'auxiliary'):
            return
        with lock:
            sessions[session_id] = dict(context='', task=task_id, model=model)
            try:
                context = invoke(session_id, 'TurnStart', task_id, model)
                sessions[session_id]['context'] = context
            except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                logger.warning('Cairn native session registration/context unavailable')

    def request(request, session_id='', **_):
        with lock:
            context = sessions.get(session_id, {}).get('context')
            if context and isinstance(request.get('messages'), list):
                return {'request': dict(request, messages=[*request['messages'], {'role': 'user', 'content': context}])}

    def after(session_id='', platform='', **_):
        with lock:
            state = sessions.get(session_id)
            if state is None:
                return
            try:
                # Gateway agent objects finish after their turn. Its long-lived
                # gateway PID alone cannot establish an idle conversation's reachability.
                invoke(session_id, 'TurnEnd' if platform == 'cli' else 'SessionEnd', state['task'], state['model'])
                state['context'] = ''
                if platform != 'cli':
                    sessions.pop(session_id, None)
            except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                logger.warning('Cairn native session stop unavailable; presence will expire')

    def close():
        with lock:
            for ident, state in list(sessions.items()):
                try:
                    invoke(ident, 'SessionEnd', state['task'], state['model'])
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                    logger.warning('Cairn native session leave unavailable; presence will expire')
            sessions.clear()

    def provider_observation(session_id, failure):
        if not os.environ.get('CAIRN_WAKE_CONTEXT'):
            return
        with lock:
            state = sessions.get(session_id)
            if state is None:
                return
            try:
                invoke(session_id, 'ProviderObservation', state['task'], state['model'], provider_failure=failure)
            except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                logger.warning('Cairn provider observation could not be retained')

    def api_error(session_id='', status_code=None, reason=None, **_):
        # These are Hermes classifier outputs. Exclude unverified billing and
        # unrelated server/auth/tool failures; no request or diagnostic is saved.
        failure = None
        if reason in ('rate_limit', 'billing'):
            status = status_code if type(status_code) is int and 100 <= status_code <= 599 else 0
            failure = dict(harness='hermes', source='native-hook', kind=reason, code=reason, status=status)
        provider_observation(session_id, failure)

    def api_success(session_id='', **_):
        provider_observation(session_id, None)

    ctx.register_hook('pre_llm_call', before)
    ctx.register_hook('post_llm_call', after)
    ctx.register_hook('api_request_error', api_error)
    ctx.register_hook('post_api_request', api_success)
    ctx.register_middleware('llm_request', request)
    ctx.on_unload(close)
