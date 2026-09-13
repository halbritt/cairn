"""Profile-level Cairn controls, available before an agent is created."""
from contextvars import ContextVar
import shlex

from hermes_constants import get_hermes_home
from .controls import conversation_key, set_context

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
    except ValueError:
        return 'Use: /cairn context <project-directory> [workstream], or /cairn context clear'
    if parts and parts[0] == 'context':
        return set_context(get_hermes_home(), key, parts[1:])
    return 'Use: /cairn context [<project-directory> [workstream] | clear]'


def register(ctx):
    ctx.register_command('cairn', command, description='Cairn conversation context', args_hint='context [project-directory] [workstream]')
    ctx.register_hook('pre_command', observe_command)
