"""Compile the prospective coding interface; no process or runtime admission.

Mount names belong to the future external owner. Configuration is a declaration,
never evidence that the CLI loaded it or that the mounts are isolated.
"""
import dataclasses
import hashlib
import json
import re
import uuid

from trial_task_permissions import settings_permissions


BUILTINS = ('Bash', 'Edit', 'Glob', 'Grep', 'Read', 'Write')
MEMORY_TOOLS = ('mcp__cairn__cairn_search', 'mcp__cairn__cairn_pull')
DISALLOWED = tuple('mcp__cairn__cairn_' + name for name in (
    'assess', 'assessments', 'client_info', 'edit', 'history',
    'prepare_note', 'pull_evidence', 'remember'))
INPUT_BYTES = 9500
INPUT_RESERVE = 512
MIN_HOOK_BYTES = 1000
MAX_TURNS = 8


@dataclasses.dataclass(frozen=True)
class CodingLaunch:
    argv: tuple
    input_message: bytes
    permission_json: bytes
    hook_context_bytes: int

    @property
    def permission_sha256(self):
        return hashlib.sha256(self.permission_json).hexdigest()


@dataclasses.dataclass(frozen=True)
class SDKLaunch(CodingLaunch):
    control: object


def compile_launch(*, session_id, model, effort, prompt, memory_instruction):
    """One frozen user message and fixed original flags, for a future owner.

    The owner must close stdin after delivering this message. Queued additional
    messages can renew the native turn allowance. The returned permission object
    must be composed with reviewed hooks; it is not a complete settings file.
    The owner binds /native/claude, /config/settings.json and /config/mcp.json.
    No argument extension, tool override or turn-budget override is accepted.
    """
    if not isinstance(session_id, str):
        raise ValueError('coding_session')
    try:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError('coding_session')
    except (ValueError, AttributeError):
        raise ValueError('coding_session') from None
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', model):
        raise ValueError('coding_model')
    if not isinstance(effort, str) or effort not in {'low', 'medium', 'high', 'xhigh', 'max'}:
        raise ValueError('coding_effort')
    if not isinstance(prompt, str) or not prompt or not isinstance(memory_instruction, str) or not memory_instruction:
        raise ValueError('coding_input')
    # Bound before JSON encoding, which may expand control characters. Neither
    # the prompt nor a failed encoding is retained in an error or exception chain.
    try:
        prompt_bytes = prompt.encode('utf-8')
        instruction_bytes = memory_instruction.encode('utf-8')
    except UnicodeError:
        raise ValueError('coding_input') from None
    if len(prompt_bytes) > INPUT_BYTES or len(instruction_bytes) > INPUT_BYTES:
        raise ValueError('coding_input_budget')
    message = (json.dumps(dict(type='user', message=dict(role='user', content=prompt)),
                          ensure_ascii=False) + '\n').encode('utf-8')
    context_bytes = INPUT_BYTES - len(message) - INPUT_RESERVE - len(instruction_bytes)
    if context_bytes < MIN_HOOK_BYTES:
        raise ValueError('coding_input_budget')
    permission_json = json.dumps(settings_permissions(profile='implementation/1'),
                                 sort_keys=True, separators=(',', ':')).encode('utf-8')
    argv = ('/native/claude', '-p', '--model', model, '--effort', effort,
            '--session-id', session_id, '--output-format', 'stream-json', '--verbose',
            '--input-format', 'stream-json', '--replay-user-messages', '--include-hook-events',
            '--tools', ','.join(BUILTINS), '--permission-mode', 'dontAsk',
            '--permission-prompts', 'none', '--disallowedTools', ','.join(DISALLOWED),
            '--setting-sources', '', '--no-session-persistence', '--strict-mcp-config',
            '--settings', '/config/settings.json', '--mcp-config', '/config/mcp.json',
            '--max-turns', str(MAX_TURNS), '--append-system-prompt', memory_instruction)
    return CodingLaunch(argv, message, permission_json, context_bytes)


def compile_sdk_launch(*, session_id, model, effort, prompt, memory_instruction):
    """Compile sdk-hooks/1 with one user and bounded control-only continuation.

    The controller owns initialization and subsequent writes. The owner must
    consume its write queue, rather than write input_message a second time or
    close stdin after that user. This declaration does not admit a runtime.
    """
    from trial_sdk_control import SDKControl

    if isinstance(memory_instruction, str) and '\0' in memory_instruction:
        raise ValueError('coding_input')
    coding = compile_launch(session_id=session_id, model=model, effort=effort,
                            prompt=prompt, memory_instruction=memory_instruction)
    control = SDKControl(session_id, coding.input_message,
                         len(memory_instruction.encode('utf-8')))
    return SDKLaunch(coding.argv, coding.input_message, coding.permission_json,
                     control.guaranteed_context_bytes, control)
