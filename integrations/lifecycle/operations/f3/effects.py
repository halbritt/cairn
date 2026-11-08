"""Fixed operational effects, causal outcomes and retained uncertainty. No live binding."""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from domains import clock_receipt, integer

SECOND = 1_000_000_000
MAX_REPLY = 8192
MAX_STDERR = 2048
SCHEMA = 'cairn.remote-effect-request/1'


class Action(str, Enum):
    SIGNAL_MAIN = 'signal-selected-main'
    RELOAD_MANAGER = 'reload-selected-manager'
    START_SELECTED = 'start-selected-unit'


class ClockClaimFault(Exception):
    """F5 adapter exposes clock claim failure without changing a known effect result."""


class ExchangeUnknown(Exception):
    def __init__(self, reason, child_identity=None):
        super().__init__(reason)
        self.child_identity = child_identity


@dataclass(frozen=True)
class ExchangeResult:
    stdout: bytes
    stderr: bytes
    returncode: int | None
    child_identity: str
    eof: bool


def canonical(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
def digest(value): return hashlib.sha256(value).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def positive(result, request):
    if not isinstance(result, ExchangeResult): raise ValueError('transport result shape')
    if type(result.returncode) is not int or result.returncode != 0 or result.eof is not True:
        raise ValueError('transport exit/eof unknown or nonzero')
    if len(result.stdout) > MAX_REPLY or len(result.stderr) > MAX_STDERR or result.stderr:
        raise ValueError('reply output unqualified')
    value = json.loads(result.stdout, object_pairs_hook=unique_object)
    expected = {'schema': 'cairn.remote-effect-outcome/1',
                **{key: request[key] for key in ('run_id', 'effect_id', 'action', 'binding_sha256', 'request_sha256')},
                'outcome': 'APPLIED', 'executor_exit': 0}
    if value != expected or type(value.get('executor_exit')) is not int:
        raise ValueError('causal outcome mismatch')
    return value


class Intent:
    """A fresh private receipt directory; existence never grants replay authority."""
    def __init__(self, directory, run_id, binding_sha256):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id): raise ValueError('run identity')
        if not re.fullmatch(r'[a-f0-9]{64}', binding_sha256): raise ValueError('binding digest')
        self.directory = Path(directory)
        parent = self.directory.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError('intent parent must be private owned directory')
        self.directory.mkdir(mode=0o700)
        self.run_id = run_id; self.binding_sha256 = binding_sha256; self.sequence = 0
        self.write('intent', {'run_id': run_id, 'binding_sha256': binding_sha256, 'replay_authorized': False})
        fd = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)

    def write(self, kind, value):
        self.sequence += 1
        name = '%04d-%s.json' % (self.sequence, kind)
        with os.fdopen(os.open(self.directory/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(canonical(value) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)


class EffectRunner:
    """Transport port receives a fixed action request, never arbitrary argv or a path."""
    def __init__(self, intent, transport, clock):
        self.intent = intent; self.transport = transport; self.clock = clock
        self.actions = set(); self.stopped = False; self.signalled = False; self.outcomes = []

    def stop(self, reason, child=None):
        self.stopped = True
        value = {'status': 'STOP_UNCERTAIN', 'reason': reason, 'signalled': self.signalled,
                 'start_blocking': True, 'retry_authorized': False,
                 'child_identity': child, 'late_execution_possible': self.signalled}
        self.intent.write('uncertain', value); self.outcomes.append(value)
        return value

    def perform(self, action, *, signal_deadline_ns=None, prelaunch=None):
        if self.stopped: return self.stop('already_uncertain_no_reopen')
        if not isinstance(action, Action): return self.stop('fixed_action_required')
        if action in self.actions: return self.stop('duplicate_effect_not_launched')
        claim_errors = []
        try: before = clock_receipt(self.clock.receipt())
        except Exception:
            if action is Action.SIGNAL_MAIN:
                return self.stop('pre_signal_clock_unknown_not_launched')
            before = None; claim_errors.append('pre_effect_clock_receipt_unknown')
        try: started = integer(self.clock.monotonic_ns(), 'monotonic_ns')
        except Exception: return self.stop('effect_clock_domain_unknown_not_launched')
        budget = 2 * SECOND
        if action is Action.SIGNAL_MAIN:
            if not callable(prelaunch): return self.stop('signal_admission_UNBOUND_not_launched')
            try: integer(signal_deadline_ns, 'signal_deadline_ns')
            except Exception: return self.stop('signal_deadline_domain_unknown_not_launched')
            if signal_deadline_ns is None or signal_deadline_ns <= started:
                return self.stop('signal_budget_exhausted_not_launched')
            budget = min(budget, signal_deadline_ns - started)
        request = {'schema': SCHEMA, 'run_id': self.intent.run_id,
                   'effect_id': '%s-%d' % (self.intent.run_id, len(self.actions)+1),
                   'action': action.value, 'binding_sha256': self.intent.binding_sha256}
        request['request_sha256'] = digest(canonical(request))
        self.actions.add(action)
        try: self.intent.write('effect-requested', request)
        except Exception: return self.stop('requested_receipt_unknown_not_launched')
        prelaunch_receipt = None
        if action is Action.SIGNAL_MAIN:
            # Source-owned admission port: reverify the SAME complete evidence and
            # current timing after durable/preparation work. Payload flags are not gates.
            try: prelaunch_receipt = clock_receipt(prelaunch())
            except Exception: return self.stop('prelaunch_admission_failed_not_launched')
        try: elapsed = integer(self.clock.monotonic_ns(), 'monotonic_ns') - started
        except Exception: return self.stop('pre_send_clock_domain_unknown_not_launched')
        if elapsed < 0: return self.stop('pre_send_monotonic_order_unknown_not_launched')
        remaining = budget - elapsed
        if remaining <= 0:
            return self.stop('durable_intent_exhausted_budget_not_launched')
        if action is Action.SIGNAL_MAIN: self.signalled = True
        # From here any failed exchange is an uncertain effect, even a launch exception.
        # No kill, termination, cancellation, second session or replay follows.
        child = None
        try:
            result = self.transport.exchange(request, remaining)
            child = result.child_identity
            finished = integer(self.clock.monotonic_ns(), 'monotonic_ns')
            if finished < started or finished - started > budget:
                return self.stop('effect_outcome_late', child)
            outcome = positive(result, request)
        except Exception as error:
            if isinstance(error, ExchangeUnknown): child = error.child_identity
            return self.stop('effect_unknown:' + type(error).__name__, child)
        try: after = clock_receipt(self.clock.receipt())
        except Exception:
            after = None; claim_errors.append('clock_receipt_unknown')
        if after is not None and before is not None and before['epoch'] != after['epoch']:
            claim_errors.append('clock_epoch_changed')
        value = {'status': 'POSITIVE_OUTCOME', 'action': action.value, 'before': before,
                 'after': after, 'outcome': outcome, 'claim_errors': claim_errors,
                 'prelaunch_receipt': prelaunch_receipt,
                 'wait_budget_ns': budget, 'observed_duration_ns': finished-started,
                 'child_identity': child, 'normal_join_inferred': False,
                 'remote_descendant_terminal_guarantee': False}
        try: self.intent.write('positive-outcome', value)
        except Exception: return self.stop('positive_receipt_persistence_unknown', child)
        self.outcomes.append(value)
        return value
