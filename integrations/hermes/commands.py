"""Profile-level Cairn controls, available before an agent is created."""
from contextvars import ContextVar
import shlex
import sqlite3

from hermes_constants import get_hermes_home
from .controls import conversation_key, set_context, read_control, change_control, describe_status, dialogue_digest

_command_context = ContextVar('cairn_command_context', default=None)


def observe_command(command='', surface='', session_key='', **kwargs):
    if command == 'cairn':
        _command_context.set((surface, session_key))


def command(arguments):
    context = _command_context.get()
    _command_context.set(None)
    key = conversation_key('cli') if context is None else conversation_key(
        'cli' if context[0] == 'cli' else 'gateway', context[1])
    try:
        parts = shlex.split(arguments)
        if parts and parts[0] == 'context':
            return set_context(get_hermes_home(), key, parts[1:])
        if not parts or parts == ['status']:
            return describe_status(read_control(get_hermes_home(), key))
        if parts == ['retry']:
            return retry(get_hermes_home(), key)
        if parts == ['discard']:
            with change_control(get_hermes_home(), key) as record:
                record.update(pending=False, last_capture=dict(outcome='discarded'))
            from hermes_cli.lifecycle import invoke_hook
            surface = 'cli' if key.startswith('cli/') else 'gateway'
            try:
                invoke_hook('pre_command', command='cairn', cairn_discard=True, surface=surface,
                            session_key=record.get('session_id', '') if surface == 'cli' else key.removeprefix('gateway/'))
            finally:
                _command_context.set(None)
            return 'Pending Cairn retry discarded. Existing shared notes are unchanged.'
    except (OSError, ValueError, sqlite3.Error):
        return 'Cairn controls unavailable; inspect the local control record or use explicit memory tools.'
    return 'Use: /cairn status | retry | discard | context [<project-directory> [workstream] | clear]'


def retry(home, key):
    from hermes_cli.lifecycle import invoke_hook
    record = read_control(home, key)
    if not record.get('pending'):
        return describe_status(record)
    surface = 'cli' if key.startswith('cli/') else 'gateway'
    session_key = record.get('session_id', '') if surface == 'cli' else key.removeprefix('gateway/')
    try:
        results = invoke_hook('pre_command', command='cairn', cairn_retry=True,
                              surface=surface, session_key=session_key)
    finally:
        _command_context.set(None)
    if any(isinstance(result, dict) and result.get('cairn_retry') == 'attempted' for result in results):
        return describe_status(read_control(home, key))
    # Recover only the exact failed snapshot from Hermes's existing history.
    # Active rows and LIMIT keep this bounded; no second transcript spool exists.
    from hermes_state import SessionDB
    from plugins.memory import load_memory_provider
    db = SessionDB(read_only=True)
    try:
        history = db.get_messages(record['session_id'], limit=64, latest=True)
    finally:
        db.close()
    provider = load_memory_provider('cairn', register_skills=False)
    if provider is None:
        return 'Cairn retry unavailable: memory provider could not be loaded.'
    try:
        provider.initialize(record['session_id'], hermes_home=home,
                            platform=record['platform'], gateway_session_key=session_key if surface == 'gateway' else '')
        snapshot = provider.selected_dialogue(history)
        if not snapshot or dialogue_digest(snapshot) != record.get('pending_digest'):
            return 'Cairn retry unavailable: the exact pending turn is no longer in active history. Use explicit memory tools.'
        if not provider.active():
            provider.discard_dialogue()
        else:
            provider.last_dialogue = snapshot
            provider.capture_result(provider.invoke('SessionEnd', messages=snapshot, cwd=record['cwd']))
        return describe_status(read_control(home, key))
    finally:
        provider.shutdown()


def register(ctx):
    ctx.register_command('cairn', command, description='Cairn context, status and capture retry', args_hint='status | retry | context [project-directory] [workstream]')
    ctx.register_hook('pre_command', observe_command)
