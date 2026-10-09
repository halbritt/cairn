"""Bounded reported action/denial facts, independent of success-only Frames.

No launch, persistence, admission or validity decision. Required command matches
are comparisons with caller declarations, never permission authority. The owner
must bind these observations to its actual attempt, stream and effective settings.
"""
import copy
import hashlib
import math
import re

from selected_native.frames import FrameError, parse
from trial_hook_observer import bounded_identity
from trial_native_observation import PublicActions


SESSION = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
OPERATIONS = frozenset({'git_add', 'git_commit', 'make_contract', 'go_test',
                        'make_check', 'make_test_integration'})
ACTOR_INCOMPLETION = frozenset({'no_observed_actions', 'unfinished_actions',
                               'background_completion_unobserved'})
CHUNK = 65536


class AttemptActions:
    def __init__(self, session_id, workspace, required_commands, *, total_limit=4*1024*1024,
                 line_limit=1024*1024, frame_limit=2048):
        if type(session_id) is not str or not SESSION.fullmatch(session_id):
            raise ValueError('attempt_actions_session')
        try:
            commands_valid = (type(required_commands) is dict and set(required_commands) <= OPERATIONS
                and all(type(v) is str and 0 < len(v.encode()) <= 65536
                        for v in required_commands.values())
                and len(set(required_commands.values())) == len(required_commands))
        except UnicodeError:
            commands_valid = False
        if not commands_valid:
            raise ValueError('attempt_actions_commands')
        for value, maximum in ((total_limit, 64*1024*1024), (line_limit, 8*1024*1024),
                               (frame_limit, 20000)):
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError('attempt_actions_bounds')
        if line_limit > total_limit:
            raise ValueError('attempt_actions_bounds')
        self.session_id = session_id
        self.actions = PublicActions(workspace, dict(required_commands))
        self.command_pins = {label: hashlib.sha256(value.encode()).hexdigest()
                             for label, value in required_commands.items()}
        self.total_limit, self.line_limit, self.frame_limit = total_limit, line_limit, frame_limit
        self.buffer, self.hash, self.total, self.frames = bytearray(), hashlib.sha256(), 0, 0
        self.unknown, self.denials = set(), []
        self.init_seen, self.result_events, self.stopped, self.closed = False, 0, False, False
        self.last_received = None

    def feed(self, chunk, received_seconds):
        if self.closed:
            raise ValueError('attempt_actions_closed')
        if type(chunk) is not bytes or len(chunk) > CHUNK:
            self.unknown.add('chunk_bounds')
            self.stopped = True
            self.buffer.clear()
            return
        self.hash.update(chunk)
        self.total += len(chunk)
        if self.stopped:
            return
        if self.total > self.total_limit:
            self._stop('stream_bounds')
            return
        if (type(received_seconds) not in (int, float) or not 0 <= received_seconds <= 1e12
                or not math.isfinite(received_seconds) or (self.last_received is not None
                                            and received_seconds < self.last_received)):
            self.unknown.add('capture_timing_unknown')
            received_seconds = None
        else:
            self.last_received = received_seconds
        self.buffer.extend(chunk)
        while True:
            end = self.buffer.find(b'\n')
            if end < 0:
                break
            if end > self.line_limit or self.frames >= self.frame_limit:
                self._stop('line_or_frame_bounds')
                return
            raw = bytes(self.buffer[:end])
            del self.buffer[:end+1]
            self.frames += 1
            self._line(raw, received_seconds)
        if len(self.buffer) > self.line_limit:
            self._stop('line_or_frame_bounds')

    def _stop(self, reason):
        self.unknown.add(reason)
        self.stopped = True
        self.buffer.clear()

    def _line(self, raw, received):
        try:
            event = parse(raw)
        except FrameError:
            self.unknown.add('malformed_event')
            return
        if type(event) is not dict:
            self.unknown.add('malformed_event')
            return
        if event.get('session_id') != self.session_id:
            self.unknown.add('session_binding_unknown')
            return
        if self.result_events:
            self.unknown.add('event_after_result')
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            if self.init_seen or self.frames != 1:
                self.unknown.add('init_order_unknown')
            self.init_seen = True
            return
        if not self.init_seen:
            self.unknown.add('init_missing')
            return
        if kind not in ('assistant', 'user', 'result'):
            return  # No housekeeping or hook payload retained; separate owners observe them.
        # One event commits all its projected facts, or none. A later defect
        # preserves earlier facts and marks coverage unknown; it never clears them.
        try:
            staged = copy.deepcopy(self.actions)
        except BaseException:
            self.unknown.add('projection_internal_fault')
            raise
        facts = []
        try:
            if kind == 'result':
                self.result_events += 1
                denials = event.get('permission_denials')
                if type(denials) is not list or len(denials)+len(self.denials) > 256:
                    raise ValueError('denial_shape_or_bounds')
                for denial in denials:
                    fact = self._denial(staged, denial)
                    if fact['action_sha256'] is not None and any(
                            row['action_sha256'] == fact['action_sha256'] for row in facts+self.denials):
                        self.unknown.add('duplicate_denial')
                    facts.append(fact)
                staged.observe_denials(denials)
            else:
                message = event.get('message')
                parts = message.get('content') if type(message) is dict else None
                if type(parts) is str:
                    return  # Transcript text is outside this projection.
                if type(parts) is not list or len(parts) > 128:
                    raise ValueError('action_event_shape')
                for part in parts:
                    if type(part) is not dict:
                        raise ValueError('action_event_shape')
                    tag = part.get('type')
                    if tag in ('text', 'thinking', 'redacted_thinking'):
                        continue
                    if tag not in ('tool_use', 'tool_result'):
                        raise ValueError('action_event_shape')
                    staged.consume(part, event, received)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self.unknown.add('projection_event_unknown')
            return
        except BaseException:
            self.unknown.add('projection_internal_fault')
            raise
        self.actions = staged
        self.denials.extend(facts)

    def _denial(self, actions, denial):
        if type(denial) is not dict:
            self.unknown.add('denial_match_unknown')
            return dict(tool='unknown', action_sha256=None, matched_call=False,
                        command_sha256=None, declared_required_operation=None,
                        reported_command_consistency='unobserved',
                        prior_reported_action_state=None, prior_reported_exit_code=None)
        tool = denial.get('tool_name')
        tool = tool if type(tool) is str and tool in PublicActions.TOOLS else 'unknown'
        ident = bounded_identity(denial.get('tool_use_id'))['sha256']
        row = actions.by_id.get(ident)
        matched = (row is not None and ident not in actions.ambiguous_ids
                   and tool != 'unknown' and row['tool'] == tool)
        if not matched:
            self.unknown.add('denial_match_unknown')
        consistency = 'unobserved'
        if matched and tool == 'Bash' and 'tool_input' in denial:
            data = denial['tool_input']
            command = data.get('command') if type(data) is dict else None
            try:
                encoded = command.encode() if type(command) is str else None
            except UnicodeError:
                encoded = None
            consistency = ('matched' if encoded is not None and len(encoded) <= 65536
                           and hashlib.sha256(encoded).hexdigest() == row.get('command_sha256')
                           else 'conflicting')
            if consistency == 'conflicting':
                self.unknown.add('denial_input_conflicting')
        return dict(tool=tool, action_sha256=ident, matched_call=matched,
                    command_sha256=row.get('command_sha256') if matched else None,
                    declared_required_operation=(row.get('validation_label')
                        if matched and consistency != 'conflicting' else None),
                    reported_command_consistency=consistency,
                    prior_reported_action_state=row['state'] if matched else None,
                    prior_reported_exit_code=row['exit_code'] if matched else None)

    def finish(self):
        if self.closed:
            raise ValueError('attempt_actions_closed')
        self.closed = True
        if self.buffer:
            self.unknown.add('unterminated_event')
        if not self.init_seen:
            self.unknown.add('init_missing')
        actions = copy.deepcopy(self.actions).finish()
        if set(actions['unknown']) - ACTOR_INCOMPLETION:
            self.unknown.add('action_observation_unknown')
        return dict(schema='cairn.attempt-actions/1', session_id=self.session_id,
                    coverage='unknown' if self.unknown else 'observed', unknown=sorted(self.unknown),
                    stream_sha256=self.hash.hexdigest(), stream_bytes=self.total,
                    observed_frames=self.frames, terminal_events=self.result_events,
                    declared_required_command_sha256=self.command_pins.copy(),
                    actions=actions, native_reported_denials=copy.deepcopy(self.denials),
                    source='native_reported_events', runtime_authenticated=False,
                    validity_decided=False, task_acceptance=False,
                    basis='bounded reported facts; required matches are caller declarations; '
                          'owner must bind stream/attempt and effective prompt/profile/settings')
