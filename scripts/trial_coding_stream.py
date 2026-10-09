"""Coding stream composition for an externally owned worker; no executor.

Raw transport stays transient in this worker. Returned data is selected evidence
and reported metadata, never authentication, cell validity or task acceptance.
"""
import copy
import hashlib
import json
import math
import re

from selected_native.evidence import CURRENT_READ_POLICIES
from selected_native.frames import Frames, FrameError, Profile
from selected_native.terminal_seam import TerminalProbe
from trial_attempt_actions import AttemptActions
from trial_coding_launch import BUILTINS, MEMORY_TOOLS, INPUT_BYTES, INPUT_RESERVE
from trial_native_observation import PublicActions, measure
from trial_source_delivery import hook_delivery


COMMON_FINAL = ('schema', 'commit', 'design_md_sha256', 'changed_paths', 'unmet', 'checks', 'memories')
TASK_FINAL = {'T1':'flag_basis', 'T2':'measured', 'T3':'ranking_effect', 'T4':'threshold',
              'T5':'field_bounds', 'T7':'owner_decisions', 'T8':'verdict'}
TOTAL, LINE, FRAMES = 4*1024*1024, 1024*1024, 2048


def hook_measurements(rows):
    """Project only bounded fields used by accounting; never traverse extra payloads."""
    if type(rows) is not list or len(rows) > 128:
        raise ValueError('hook_observation_bounds')
    projected = []
    for row in rows:
        if type(row) is not dict:
            raise ValueError('hook_observation_bounds')
        selected = {}
        event, digest = row.get('event'), row.get('stdout_sha256')
        if event not in ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure'):
            raise ValueError('hook_observation_bounds')
        if type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('hook_observation_bounds')
        selected.update(event=event, stdout_sha256=digest)
        for key in ('stdout_bytes', 'stderr_bytes'):
            value = row.get(key)
            if type(value) is not int or not 0 <= value <= 1000000000:
                raise ValueError('hook_observation_bounds')
            selected[key] = value
        elapsed, code = row.get('process_seconds'), row.get('exit_code')
        if (type(elapsed) not in (int, float) or not 0 <= elapsed <= 1e12
                or not math.isfinite(elapsed) or type(code) is not int or not -255 <= code <= 255):
            raise ValueError('hook_observation_bounds')
        counts = row.get('memory_call_attempts')
        keys = ('search', 'pull', 'pull-evidence', 'history', 'other')
        if type(counts) is not dict or any(type(counts.get(k)) is not int
                or not 0 <= counts[k] <= 1000000000 for k in keys):
            raise ValueError('hook_observation_bounds')
        selected.update(process_seconds=elapsed, exit_code=code,
                        memory_call_attempts={k:counts[k] for k in keys})
        projected.append(selected)
    return projected


def coding_profile(task_id):
    """Source declaration for the fixed seven tasks, not campaign freeze/admission.

    T6 remains withdrawn in the planned-eight ITT denominator outside this module.
    This profile cannot be used for the separate weekly frozen-case instrument.
    """
    if type(task_id) is not str or task_id not in TASK_FINAL:
        raise ValueError('coding_task_unavailable')
    return Profile('coding/'+task_id+'/1', set(BUILTINS+MEMORY_TOOLS), set(BUILTINS),
                   {'dontAsk'}, hooks='project', inspection_policies=CURRENT_READ_POLICIES,
                   final_kind='json_object', final_required_keys=COMMON_FINAL+(TASK_FINAL[task_id],),
                   max_total_bytes=TOTAL, max_line_bytes=LINE, max_frames=FRAMES)


def reported_usage(result, model):
    """Counters on failed terminal results remain reported or explicitly unknown."""
    data = result if type(result) is dict else {}
    usage = data.get('modelUsage')
    bound = type(usage) is dict and set(usage) == {model} and type(usage[model]) is dict
    bound = bound and usage[model].get('provider') == 'firstParty'
    values, unknown = {}, []
    for key in ('inputTokens', 'outputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens'):
        value = usage[model].get(key) if bound else None
        values[key] = value if type(value) is int and 0 <= value <= 1000000000 else None
        if values[key] is None:
            unknown.append(key)
    cost, turns = data.get('total_cost_usd'), data.get('num_turns')
    cost = cost if type(cost) in (int, float) and 0 <= cost <= 1000 and math.isfinite(cost) else None
    turns = turns if type(turns) is int and 0 <= turns <= 1000 else None
    if cost is None:
        unknown.append('total_cost_usd')
    if turns is None:
        unknown.append('num_turns')
    return dict(schema='cairn.reported-terminal-usage/1', model_identity_matched=bool(bound),
                counters=values, reported_cost_usd=cost, reported_turns=turns,
                unknown=unknown, basis='native-reported counters; not billing attestation or budget enforcement')


class CodingStream:
    """One bounded transport feeds independent success/action/terminal channels.

    The owner supplies the frozen input charge and actual capture timing. Raw
    bytes are used only for existing measurement; no raw stream ingestion into
    persistence is exposed. The caller still owns Retention.prepare(reader,
    actual_exit) and must never manufacture a successful exit after failure.
    """
    def __init__(self, task_id, session_id, model, cli_version, *, workspace,
                 required_commands, prompt, input_charge):
        if type(prompt) is not str or not prompt or type(input_charge) is not int:
            raise ValueError('coding_stream_input')
        try:
            prompt_size = len(prompt.encode())
            if prompt_size > INPUT_BYTES:
                raise ValueError('coding_stream_input_charge')
            wire_size = len((json.dumps(dict(type='user', message=dict(role='user', content=prompt)),
                                       ensure_ascii=False)+'\n').encode())
        except UnicodeError:
            raise ValueError('coding_stream_input') from None
        if not wire_size+INPUT_RESERVE <= input_charge <= INPUT_BYTES-1000:
            raise ValueError('coding_stream_input_charge')
        self.profile = coding_profile(task_id)
        self.prompt, self.input_charge, self.model = prompt, input_charge, model
        self.failure_actions = AttemptActions(session_id, workspace, required_commands,
            total_limit=TOTAL, line_limit=LINE, frame_limit=FRAMES)
        self.workspace, self.commands = workspace, dict(required_commands)
        self.terminal = TerminalProbe(session_id)
        self.received = None
        self.reader = Frames(session_id, model, cli_version, profile=self.profile,
            actions=PublicActions(workspace, required_commands), clock=lambda:self.received,
            hook_projector=hook_delivery)
        self.raw, self.pending, self.timings = bytearray(), bytearray(), []
        self.total, self.hash, self.frame_failure = 0, hashlib.sha256(), None
        self.unknown, self.stopped, self.closed = set(), False, False
        self.last_received, self.digest_complete = None, True

    def _stop(self, code):
        self.unknown.add(code)
        self.stopped = True
        self.raw.clear()
        self.pending.clear()
        self.terminal.overflow = True
        self.terminal.buffer.clear()

    def feed(self, chunk, received_seconds):
        if self.closed:
            raise ValueError('coding_stream_closed')
        if type(chunk) is not bytes or len(chunk) > 65536:
            self.digest_complete = False
            self._stop('transport_chunk_unknown')
            self.failure_actions.feed(chunk, received_seconds)
            return
        self.total += len(chunk)
        self.hash.update(chunk)
        try:
            self.failure_actions.feed(chunk, received_seconds)
        except BaseException:
            self._stop('action_reader_interrupted')
            raise
        if (type(received_seconds) not in (int, float) or not 0 <= received_seconds <= 1e12
                or not math.isfinite(received_seconds)
                or self.last_received is not None and received_seconds < self.last_received):
            self.unknown.add('capture_timing_unknown')
            received_seconds = None
        else:
            self.last_received = received_seconds
        self.received = received_seconds
        if self.stopped or self.total > TOTAL:
            self._stop('transport_bounds')
            self.terminal.feed(chunk)  # Overflowed probe hashes without parsing.
            return
        self.raw.extend(chunk)
        self.pending.extend(chunk)
        while b'\n' in self.pending:
            end = self.pending.find(b'\n')
            if end > LINE or len(self.timings) >= FRAMES:
                self._stop('transport_bounds')
                self.terminal.feed(chunk)
                return
            line = bytes(self.pending[:end+1])
            del self.pending[:end+1]
            self.timings.append(dict(sha256=hashlib.sha256(line).hexdigest(),
                                     received_seconds=received_seconds))
        if len(self.pending) > LINE:
            self._stop('transport_bounds')
            self.terminal.feed(chunk)
            return
        self.terminal.feed(chunk)
        if self.frame_failure is None:
            try:
                self.reader.feed(chunk)
            except FrameError as exc:
                self.frame_failure = str(exc)  # Fixed codes from the reviewed reader, no payload text.
            except BaseException:
                self.unknown.add('success_reader_interrupted')
                raise

    def observations(self, hook_observations):
        """Close this composer, not the process or success retention.

        A successful worker must call Retention.prepare with its actual exit
        snapshot before this method. This method discards transient parser
        buffers and cannot be followed by a new success-retention attempt.

        Full-stream digests stay separate from the success reader's accepted
        prefix. A matching prefix is not silently relabelled a complete stream.
        Caller-supplied hook measurements are declarations until owner-bound.
        """
        if self.closed:
            raise ValueError('coding_stream_closed')
        self.closed = True
        accounting = None
        try:
            if self.pending:
                self.unknown.add('unterminated_transport')
            try:
                hooks = hook_measurements(hook_observations)
            except ValueError:
                self.unknown.add('hook_observation_bounds')
                hooks = None
            if hooks is not None and not self.stopped and not self.pending:
                try:
                    accounting = measure(self.raw.decode('utf-8', errors='strict'), self.timings,
                        hooks, input_bytes=self.input_charge, expects_hooks=True,
                        model=self.model, prompt=self.prompt, workspace=self.workspace,
                        public_validation_commands=self.commands)
                except Exception:
                    self.unknown.add('measurement_unknown')
            prefix = self.reader.stream_digest()
            terminal = self.terminal.report()
            usage = reported_usage(self.terminal.result, self.model)
            return dict(schema='cairn.coding-stream-observations/1',
                full_stream=dict(stream_sha256=self.hash.hexdigest(), stream_bytes=self.total,
                                 digest_complete=self.digest_complete),
                profile=copy.deepcopy(self.profile.document), profile_sha256=self.profile.sha256,
                success_reader=dict(refusal=self.frame_failure, consumed_prefix=prefix,
                    whole_stream_consumed=prefix['stream_sha256']==self.hash.hexdigest()
                                          and prefix['stream_bytes']==self.total),
                reported_init=copy.deepcopy(self.reader.init),
                accepted_prefix_memory=self.reader.ledger.selected(),
                accepted_prefix_hooks=self.reader._hooks_report(),
                failed_attempt_actions=self.failure_actions.finish(), terminal=terminal,
                reported_usage=usage, selected_input=accounting,
                composition_unknown=sorted(self.unknown), runtime_authenticated=False,
                validity_decided=False, task_acceptance=False,
                basis='reported channels and prefix evidence; external owner binds actual wait, '
                      'release, input, hook telemetry, request and persistence')
        finally:
            self.raw.clear()
            self.pending.clear()
            self.timings.clear()
            self.terminal.buffer.clear()
            self.terminal.result = None
            self.terminal.assistant_error = (None, '')
            self.reader.buffer.clear()
            self.reader.closed = True  # Clearing a tail must never enable a later successful finish.
            self.prompt, self.commands = None, None
