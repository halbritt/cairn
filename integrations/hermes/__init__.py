"""Cairn MemoryProvider: native Hermes boundaries, shared selection policy."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from agent.memory_provider import MemoryProvider
from agent.context_compressor import is_compaction_summary_message
from hermes_constants import get_hermes_home
from tools.terminal_tool import get_session_cwd, resolve_task_overrides
from .memory import bounded_dialogue, project_for
from .controls import conversation_key, read_control, change_control, dialogue_digest

logger = logging.getLogger(__name__)
CONTEXT_BYTES = 12000
ENGINE_TIMEOUT = 150


def run_engine(command, payload, timeout=ENGINE_TIMEOUT):
    # The engine starts a selector child. Own the entire group on timeout so
    # a timed-out hook cannot leave selection running after the turn returns.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding='utf-8', start_new_session=True) as process:
        try:
            stdout, stderr = process.communicate(json.dumps(payload), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The child exited between the deadline and cancellation.
            process.communicate(timeout=5)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def dialogue(messages):
    """Original dialogue only; api_content is deliberately never inspected."""
    result = []
    for message in messages[-64:]:
        if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                or message.get('tool_calls') or is_compaction_summary_message(message)):
            continue
        content = message.get('content')
        if isinstance(content, list):
            content = '\n'.join(b['text'] for b in content
                                if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str))
        if isinstance(content, str) and content.strip():
            result.append(dict(role=message['role'], text=content))
    return bounded_dialogue(result)


class CairnProvider(MemoryProvider):
    name = 'cairn'

    def __init__(self, ctx):
        self.ctx = ctx
        self.session_id = ''
        self.session_ids = []
        self.registrations = []
        self.lock = threading.RLock()
        self.context = ''
        self.turn_id = None
        self.task_id = None
        self.enabled = False
        self.compaction_attempted = False
        self.warning_callback = None
        self.last_dialogue = []
        self.capture_pending = False
        self.binding = None
        self.turn_capture = False

    def is_available(self):
        return (get_hermes_home() / 'cairn-lifecycle.json').is_file()

    def initialize(self, session_id, **kwargs):
        self.home = Path(kwargs['hermes_home'])
        self.config = json.loads((self.home / 'cairn-lifecycle.json').read_text())
        self.session_id = session_id
        self.session_ids.append(session_id)
        self.platform = kwargs.get('platform', 'cli')
        self.gateway_session_key = kwargs.get('gateway_session_key', '')
        self.control_key = conversation_key('cli' if self.platform == 'cli' else 'gateway',
                                            self.gateway_session_key or session_id)
        self.load_context()
        self.enabled = (kwargs.get('agent_context', 'primary') == 'primary'
                        and self.platform not in ('cron', 'subagent', 'flush', 'auxiliary'))
        self.warning_callback = kwargs.get('warning_callback')
        self.registrations = [self.ctx.register_hook('pre_llm_call', self.pre_turn),
                              self.ctx.register_hook('post_llm_call', self.post_turn),
                              self.ctx.register_hook('post_tool_call', self.post_tool),
                              self.ctx.register_hook('pre_command', self.pre_command),
                              self.ctx.register_middleware('llm_request', self.request)]

    def get_tool_schemas(self):
        return []  # Explicit tools belong to Cairn's ordinary authenticated MCP facade.

    def load_context(self):
        record = read_control(self.home, self.control_key)
        self.binding = record.get('binding')
        self.turn_capture = record.get('turn_capture', False)

    def selected_dialogue(self, messages):
        if self.turn_capture:
            start = next((i for i in range(len(messages)-1, -1, -1) if messages[i].get('role') == 'user'), len(messages))
            messages = messages[start:]
        return dialogue(messages)

    def update_control(self, **fields):
        try:
            with change_control(self.home, self.control_key) as record:
                record.update(fields)
        except (OSError, ValueError):
            logger.warning('Cairn status could not be stored; memory outcome is unavailable in /cairn status')

    def capture_result(self, result):
        self.capture_pending = result is None
        self.update_control(pending=self.capture_pending, session_id=self.session_id,
                            cwd=self.cwd(), platform=self.platform,
                            pending_digest=dialogue_digest(self.last_dialogue))

    def cwd(self):
        # The gateway uses per-task environment overrides; never another chat's cwd.
        return (get_session_cwd(self.task_id) or get_session_cwd(self.session_id)
                or resolve_task_overrides(self.task_id).get('cwd')
                or os.environ.get('TERMINAL_CWD') or os.getcwd())

    def active(self):
        if not self.enabled or os.environ.get('CAIRN_LIFECYCLE_DISABLED') == '1' or os.environ.get('CAIRN_LIFECYCLE_CHILD') == '1':
            return False
        cwd = Path(self.cwd())
        return not any((path / '.cairn-no-memory').exists() for path in (cwd,project_for(cwd),Path(self.binding["project_path"]) if self.binding else cwd))

    def discard_dialogue(self, disabled=True):
        self.context = ''
        self.last_dialogue = []
        self.capture_pending = False
        if disabled:
            self.update_control(pending=False, last_capture=dict(outcome='disabled', at=time.time()))

    def matches(self, session_id):
        return session_id in self.session_ids and get_hermes_home().resolve() == self.home.resolve()

    def invoke(self, event, **fields):
        if not self.active():
            return None
        started = time.monotonic()
        try:
            payload = dict(hook_event_name=event, session_id=self.session_id, cwd=self.cwd())
            payload.update(fields)
            if self.binding:
                payload.update(self.binding)
            process = run_engine([sys.executable, self.config['script'], '--config', self.config['engine_config']],
                                 payload, timeout=ENGINE_TIMEOUT)
            if process.returncode:
                raise RuntimeError('memory operation failed; selected save or retrieval not confirmed')
            result = json.loads(process.stdout)
            if not isinstance(result, dict):
                raise ValueError('invalid engine response')
            field = 'last_recall' if event == 'UserPromptSubmit' else 'last_capture'
            status = result.get('cairn_status', {})
            if event in ('UserPromptSubmit', 'PreCompact', 'SessionEnd') and field in status:
                self.update_control(**{field: dict(status[field], seconds=time.monotonic()-started)})
            if status.get('workstream'):
                self.update_control(workstream=status['workstream'])
            logger.info('Cairn %s completed in %.3fs', event, time.monotonic() - started)
            return result
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, RuntimeError):
            # Subprocess output can contain private dialogue. Only report operation/status.
            warning = f'Cairn {event} failed or timed out; use explicit memory tools or retry the checkpoint.'
            logger.warning(warning)
            if event in ('UserPromptSubmit', 'PreCompact', 'SessionEnd'):
                field = 'last_recall' if event == 'UserPromptSubmit' else 'last_capture'
                self.update_control(**{field: dict(outcome='failed', seconds=time.monotonic()-started, at=time.time())})
            if self.warning_callback:
                self.warning_callback(warning)
            return None

    def pre_turn(self, session_id='', turn_id=None, task_id=None, user_message='',
                 parent_session_id='', conversation_history=None, **kwargs):
        if not self.matches(session_id):
            return
        with self.lock:
            self.session_id = session_id
            if turn_id and turn_id == self.turn_id:
                return
            self.turn_id, self.task_id = turn_id, task_id
            self.load_context()
            self.context = ''
            self.compaction_attempted = False
            if parent_session_id:
                self.enabled = False
            if not self.active():
                self.discard_dialogue()
                return
            self.last_dialogue = self.selected_dialogue(conversation_history or [])
            self.capture_pending = True
            prompt = user_message if isinstance(user_message, str) else ''
            source = 'resume' if prompt.strip().lower().rstrip('.!') in ('continue', 'resume') else ''
            result = self.invoke('UserPromptSubmit', prompt=prompt, source=source, retained_record_ids=[]) or {}
            context = result.get('hookSpecificOutput', {}).get('additionalContext', '')
            if isinstance(context, str) and len(context.encode('utf-8')) <= CONTEXT_BYTES:
                self.context = context
            # Recall is injected at the request boundary, not Hermes's persisted api_content sidecar.

    def request(self, request, session_id='', **kwargs):
        if session_id != self.session_id or not self.matches(session_id) or not self.active() or not self.context:
            return
        # Hermes gives middleware a request copy. A separate message leaves all
        # user text and other plugins' sidecars intact. Nothing enters history.
        messages = request.get('messages')
        if not isinstance(messages, list):
            logger.warning('Cairn recall unavailable for this Hermes request format')
            return
        return {'request': dict(request, messages=[*messages, {'role': 'user', 'content': self.context}])}

    def post_turn(self, session_id='', conversation_history=None, **kwargs):
        if not self.matches(session_id):
            return
        with self.lock:
            if not self.active():
                self.discard_dialogue()
                return
            self.session_id = session_id
            self.last_dialogue = self.selected_dialogue(conversation_history or [])
            self.capture_result(self.invoke('SessionEnd', messages=self.last_dialogue))

    def pre_command(self, command='', session_key='', surface='', cairn_retry=False, cairn_discard=False, **kwargs):
        key = self.gateway_session_key if surface=='gateway' else self.session_id
        retry = command == 'cairn' and cairn_retry
        discard = command == 'cairn' and cairn_discard
        if (command!='compress' and not retry and not discard) or not key or key!=session_key or not self.matches(self.session_id):
            return
        with self.lock:
            if discard:
                self.discard_dialogue(disabled=False)
                return
            if retry:
                if not self.active():
                    self.discard_dialogue()
                    return {'cairn_retry': 'attempted'}
                if self.capture_pending and self.last_dialogue:
                    self.capture_result(self.invoke('SessionEnd', messages=self.last_dialogue))
                    return {'cairn_retry': 'attempted'}
                return
            if not self.active():
                self.discard_dialogue()
                return
            # Gateway manual compression deliberately skips provider initialization.
            # The live provider can retry its bounded original turn before that helper runs.
            result = self.invoke('PreCompact', messages=self.last_dialogue)
            self.capture_result(result)
            self.compaction_attempted = True
            self.context = ''

    def post_tool(self, session_id='', tool_name='', args=None, status='', error_type='', **kwargs):
        if not self.matches(session_id):
            return
        with self.lock:
            if status == 'error':
                self.invoke('PostToolUseFailure', error=error_type or '')
            elif tool_name in ('read_file', 'write_file', 'patch'):
                path = (args or {}).get('path')
                if isinstance(path, str):
                    self.invoke('PostToolUse', tool_name='Read', tool_input={'file_path': path})

    def on_pre_compress(self, messages):
        with self.lock:
            if not self.active():
                self.discard_dialogue()
                return ''
            if not self.turn_capture:
                self.last_dialogue = dialogue(messages)
            result = self.invoke('PreCompact', messages=self.last_dialogue)
            self.capture_result(result)
            self.context = ''
            self.compaction_attempted = True
        return ''

    def on_session_end(self, messages):
        with self.lock:
            if not self.active():
                self.discard_dialogue()
                return
            snapshot = self.selected_dialogue(messages)
            # /new's queued old-session callback may arrive after /resume or a
            # new owner turn. Completed turns already saved synchronously; only
            # retry the current pending turn, never rebind old text to a new ID.
            if self.compaction_attempted:
                snapshot = self.last_dialogue
            if self.capture_pending and self.last_dialogue and snapshot[:len(self.last_dialogue)] == self.last_dialogue:
                self.capture_result(self.invoke('SessionEnd', messages=snapshot))

    def on_session_switch(self, new_session_id, **kwargs):
        with self.lock:
            self.session_ids = [s for s in self.session_ids if s != new_session_id][-31:] + [new_session_id]
            # The next pre-turn event supplies the authoritative ID for its
            # dialogue. An asynchronously queued /new switch can be stale.
            if kwargs.get('reason') == 'compression':
                self.context = ''

    def shutdown(self):
        with self.lock:
            self.enabled = False
            self.discard_dialogue(disabled=False)
            for handle in reversed(self.registrations):
                handle.dispose()
            self.registrations.clear()


def register(ctx):
    provider = CairnProvider(ctx)
    ctx.register_memory_provider(provider)
