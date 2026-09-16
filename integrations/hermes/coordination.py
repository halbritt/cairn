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

    def invoke(session_id, event, task_id='', model=''):
        cwd = (get_session_cwd(task_id) or get_session_cwd(session_id)
               or resolve_task_overrides(task_id).get('cwd') or os.environ.get('TERMINAL_CWD') or os.getcwd())
        result = subprocess.run([sys.executable, config['script'], 'hook', '--config', config['config']],
            input=json.dumps(dict(session_id=session_id, hook_event_name=event, cwd=cwd, host_pid=os.getpid(), model=model)),
            text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('Cairn session presence unavailable')
        return json.loads(result.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')

    def before(session_id='', turn_id='', task_id='', model='', parent_session_id='', platform='', **_):
        if not session_id or parent_session_id or platform in ('cron', 'subagent', 'flush', 'auxiliary'):
            return
        with lock:
            if session_id in sessions:
                sessions[session_id]['context'] = ''
            try:
                context = invoke(session_id, 'TurnStart', task_id, model)
                sessions[session_id] = dict(context=context, task=task_id, model=model)
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

    ctx.register_hook('pre_llm_call', before)
    ctx.register_hook('post_llm_call', after)
    ctx.register_middleware('llm_request', request)
    ctx.on_unload(close)
