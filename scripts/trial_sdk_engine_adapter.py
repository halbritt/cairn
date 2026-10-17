"""Fixed recall-only adapter for a separately source-custodied Cairn engine.

This replaces dispatch functions inside one trusted engine module. It is not
hostile-code containment, a producer broker, a loader, or proof of source custody.
The fixed builder must keep original credentials/socket outside the producer.
"""
import dataclasses
import subprocess
import types

from trial_sdk_control import EVENTS
from trial_sdk_incoming import canonical_size_sha256, MAX_DOCUMENT_BYTES
from trial_sdk_memory_gate import MemoryGateError


@dataclasses.dataclass(frozen=True)
class CommandOutcome:
    """Actual waited command outcome from the fixed downstream owner, raw bytes."""
    returncode: int
    stdout: bytes
    stderr: bytes


class EngineMemoryAdapter:
    """Use the real engine's run_json decoder with a gated command transport.

    config must be the builder's pinned recall-only config. The same-process
    channel implements call(operation,args,body,timeout); for a separate producer
    that channel must be a bounded broker client authenticated by the parent.
    Local gate channels are useful for source tests but do not share state across
    processes. No configurable fallback ever invokes the original subprocess.
    """
    def __init__(self, engine, config, channel):
        if (type(engine) is not types.ModuleType or type(config) is not dict
                or config.get('harness') != 'claude' or config.get('recall_mode') != 'agent_tools'
                or config.get('recall_observations') is not False or 'inbox_recall_binding' in config):
            raise MemoryGateError('engine_adapter_binding')
        keys=('cairn','socket','token_file')
        if any(type(config.get(key)) is not str or not config[key].startswith('/')
               or '\0' in config[key] for key in keys):
            raise MemoryGateError('engine_adapter_binding')
        functions=(getattr(engine,'run_json',None),getattr(engine,'bounded_command',None))
        if (any(type(f) is not types.FunctionType or f.__globals__ is not engine.__dict__
                for f in functions) or not callable(getattr(channel,'call',None))
                or not isinstance(getattr(engine,'HookError',None),type)
                or not issubclass(engine.HookError,Exception)
                or type(getattr(engine,'COMMAND_OUTPUT_BYTES',None)) is not int
                or not 0<engine.COMMAND_OUTPUT_BYTES<=4*1024*1024):
            raise MemoryGateError('engine_adapter_binding')
        self._engine,self._channel=engine,channel
        self._prefix=(config['cairn'],'agent','--socket',config['socket'],'--token-file',config['token_file'])
        self._original_json,self._original_command=functions
        self._json_wrapper,self._command_wrapper=self._run_json,self._bounded_command
        self._failure=None
        self._blocked=0
        self._entered=self._closed=False

    def _reject(self,code):
        self._blocked+=1
        if self._failure is None: self._failure=code
        raise self._engine.HookError('evaluation memory dispatch refused',reason='MEMORY_GATE_REFUSED')

    def _command(self,command):
        if (type(command) not in (list,tuple) or len(command)<len(self._prefix)+1
                or any(type(part) is not str for part in command)
                or tuple(command[:len(self._prefix)])!=self._prefix):
            self._reject('engine_dispatch_route')
        operation=command[len(self._prefix)]
        if operation not in ('search','pull','pull-evidence','history'):
            self._reject('engine_dispatch_operation')
        return operation,tuple(command[len(self._prefix)+1:])

    def _active(self):
        if (not self._entered or self._closed
                or self._engine.run_json is not self._json_wrapper
                or self._engine.bounded_command is not self._command_wrapper):
            self._reject('engine_dispatch_drift')
        if self._failure is not None:
            self._reject(self._failure)

    def _run_json(self,command,*,body=None,timeout=5,env=None,cwd=None,observation=None):
        self._active()
        self._command(command)
        # Retain the real function's exact JSON and HookError/BudgetRefused rules.
        try:
            result=self._original_json(command,body=body,timeout=timeout,env=env,cwd=cwd,
                                       observation=observation)
            if result.get('ok') is not True or type(result.get('data')) is not dict:
                if self._failure is None: self._failure='engine_operation_unconfirmed'
            return result  # Memory.call still raises its original NOT_CONFIRMED.
        except BaseException:
            if self._failure is None: self._failure='engine_result_failed'
            raise

    def _bounded_command(self,command,*,body,timeout,env,cwd,observation=None):
        self._active()
        operation,args=self._command(command)
        if cwd is not None or observation is not None:
            self._reject('engine_dispatch_options')
        try:
            outcome=self._channel.call(operation,args,body,timeout)
        except BaseException:
            # The real decoder may catch a transport exception. Preserve that
            # mapping, but never let a swallowed refusal qualify a callback.
            if self._failure is None: self._failure='engine_dispatch_failed'
            raise
        if (type(outcome) is not CommandOutcome or type(outcome.returncode) is not int
                or not -2**31<=outcome.returncode<2**31
                or type(outcome.stdout) is not bytes or type(outcome.stderr) is not bytes
                or len(outcome.stdout)>self._engine.COMMAND_OUTPUT_BYTES
                or len(outcome.stderr)>self._engine.COMMAND_OUTPUT_BYTES):
            self._reject('engine_command_outcome')
        return subprocess.CompletedProcess(command,outcome.returncode,
            outcome.stdout.decode('utf-8'),outcome.stderr.decode('utf-8',errors='replace'))

    def __enter__(self):
        if self._entered or self._closed:
            raise MemoryGateError('engine_adapter_consumed')
        if (self._engine.run_json is not self._original_json
                or self._engine.bounded_command is not self._original_command):
            raise MemoryGateError('engine_dispatch_drift')
        self._entered=True
        try:
            self._engine.run_json=self._json_wrapper
            self._engine.bounded_command=self._command_wrapper
        except BaseException:
            self.close()
            raise
        return self

    def check(self):
        if self._failure is not None: raise MemoryGateError(self._failure)

    def snapshot(self):
        return dict(schema='cairn.sdk-engine-adapter/1',failure=self._failure,
                    blocked_commands=self._blocked,entered=self._entered,closed=self._closed)

    def close(self):
        if self._closed: return
        if self._entered:
            if (self._engine.run_json is not self._json_wrapper
                    or self._engine.bounded_command is not self._command_wrapper):
                if self._failure is None: self._failure='engine_dispatch_drift'
            self._engine.run_json=self._original_json
            self._engine.bounded_command=self._original_command
        self._closed=True

    def __exit__(self,kind,error,traceback):
        self.close()
        if kind is None: self.check()
        return False


def engine_context(graph,event):
    """Validate the pinned engine's four-hook result; not model delivery proof."""
    if type(event) is not str or event not in EVENTS or type(graph) is not dict:
        raise MemoryGateError('engine_context_shape')
    if not graph: return ''
    if event not in ('SessionStart','UserPromptSubmit') or set(graph)!={'hookSpecificOutput'}:
        raise MemoryGateError('engine_context_shape')
    output=graph['hookSpecificOutput']
    if (type(output) is not dict or set(output)!={'hookEventName','additionalContext'}
            or output['hookEventName']!=event or type(output['additionalContext']) is not str):
        raise MemoryGateError('engine_context_shape')
    try:
        if canonical_size_sha256(graph)[0]>MAX_DOCUMENT_BYTES:
            raise MemoryGateError('engine_context_bound')
    except (ValueError,UnicodeError,RecursionError) as error:
        raise MemoryGateError('engine_context_shape') from error
    return output['additionalContext']
