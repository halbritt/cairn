"""Terminal and process metadata seam, outside the success-only Frames grade path (V4).

Pure functions only: no process, socket, persistence or authentication. Four independent
sources feed one fixed-vocabulary attempt record and none of them can overwrite another:

  custody   the supervisor's `cairn.native-process-custody/1` record (actual wait status, input
            delivery, cleanup). Frames never sees it; finish(0) is only an assertion made after custody.
  stream    TerminalProbe, a tolerant scalar-only second reader of the same bytes. It never raises on a
            provider failure and is unaffected by a poisoned Frames reader.
  frames    the success-only reader's outcome plus its stream digest (Frames.stream_digest()).
  binding   supplied by the EXTERNAL owner that created the attempt: attempt id, session id and the
            assertion that the custody record belongs to this attempt. Nothing here can derive it.

V4 rule: no source is assumed to imply another. A frames success never implies a well-formed terminal
report, one result, no overflow, a matching stream or a bound custody record. Every applicable observation
is listed, and any unknown about the terminal report, the stream binding or the custody binding routes to
harness_unknown before a grading route is reachable. attempt_class is a routing label, never task
acceptance; free text, prompts, tool payloads and exception text are never retained.
"""
import hashlib
import json
import re

CLOSED = re.compile(r'[a-z][a-z0-9_]{0,39}\Z')
WEEKLY = re.compile(r"You've hit your weekly limit · resets [^\r\n]+\Z")
SESSION = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
CHUNK, LINE, TOTAL = 65536, 8 * 1024 * 1024, 64 * 1024 * 1024   # same anchors as scripts/trial_native_observation.capture


def closed(value):
    return value if isinstance(value, str) and CLOSED.fullmatch(value) else None


def admission_failure(result, assistant_error):
    """Same rule as scripts/trial_task_eval.claude_admission_failure (the test asserts equality):
    only terminal provider metadata can stop admission, never tool or answer text."""
    if result.get('is_error') is not True or result.get('terminal_reason') != 'api_error':
        return None
    status = result.get('api_error_status')
    if type(status) is not int:
        return None
    if status == 401:
        return dict(provider='claude', reason='authentication_failed', api_error_status=status)
    error, message = assistant_error
    if (status == 429 and error == 'rate_limit' and result.get('result') == message
            and isinstance(message, str) and WEEKLY.fullmatch(message)):
        return dict(provider='claude', reason='capacity_exhausted', api_error_status=status)
    return None


class TerminalProbe:
    def __init__(self, session_id):
        if not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
            raise ValueError('probe_session_id')
        self.session_id = session_id
        self.buffer, self.total, self.hash = bytearray(), 0, hashlib.sha256()
        self.assistant_error = (None, '')
        self.result, self.results, self.malformed, self.overflow = None, 0, 0, False

    def feed(self, chunk):
        """Tolerant: oversize input sets `overflow`, malformed lines are only counted. Once overflowed the
        probe stops reading, but the digest keeps covering every accepted chunk (as Frames does)."""
        if not isinstance(chunk, bytes) or len(chunk) > CHUNK:
            self.overflow = True
            return
        self.hash.update(chunk)
        self.total += len(chunk)
        if self.overflow:
            return
        if self.total > TOTAL:
            self.overflow = True
            self.buffer.clear()
            return
        self.buffer.extend(chunk)
        while True:
            pos = self.buffer.find(b'\n')
            if pos < 0:
                break
            raw = bytes(self.buffer[:pos])
            del self.buffer[:pos + 1]
            if len(raw) > LINE:          # a newline in the last chunk does not excuse an over-long line
                self.overflow = True
                self.buffer.clear()
                return
            self._line(raw)
        if len(self.buffer) > LINE:
            self.overflow = True
            self.buffer.clear()

    def _line(self, raw):
        try:
            event = json.loads(raw.decode('utf-8', errors='strict'))
        except (ValueError, UnicodeError, RecursionError):
            self.malformed += 1
            return
        if not isinstance(event, dict):
            self.malformed += 1
            return
        if event.get('type') == 'assistant':
            message = event.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            parts = [p for p in content if isinstance(p, dict)] if isinstance(content, list) else []
            self.assistant_error = (event.get('error'), ''.join(
                p['text'] for p in parts if p.get('type') == 'text' and isinstance(p.get('text'), str)))
        elif event.get('type') == 'result':
            self.results += 1
            self.result = event

    def report(self):
        """Call at end of stream. A pending unterminated tail is reported, never ignored."""
        result = self.result if isinstance(self.result, dict) else {}
        status = result.get('api_error_status')
        denials = result.get('permission_denials')
        failure = admission_failure(result, self.assistant_error) if self.result is not None else None
        error = self.assistant_error[0]
        seen = result.get('session_id')
        return dict(schema='cairn.terminal-probe/1', session_id=self.session_id, result_events=self.results,
                    result_seen=self.result is not None,
                    session_match=None if self.result is None or 'session_id' not in result else seen == self.session_id,
                    is_error=result.get('is_error') if type(result.get('is_error')) is bool else None,
                    subtype=closed(result.get('subtype')), terminal_reason=closed(result.get('terminal_reason')),
                    stop_reason=closed(result.get('stop_reason')),
                    api_error_status=status if type(status) is int and 100 <= status <= 599 else None,
                    assistant_error=closed(error) if error is not None else None,
                    permission_denials=len(denials) if isinstance(denials, list) else None,
                    admission_failure=failure, malformed_lines=self.malformed, overflow=self.overflow,
                    pending_bytes=len(self.buffer), stream_sha256=self.hash.hexdigest(), stream_bytes=self.total,
                    basis='scalar fields of the native result event; no text, payload or exception retained')


TERMINAL_UNKNOWN = ('terminal_report_missing', 'terminal_report_invalid', 'terminal_result_missing', 'terminal_multiple_results',
                    'terminal_malformed_lines', 'terminal_overflow', 'terminal_unterminated_tail', 'terminal_session_unobserved')
BINDING_UNKNOWN = ('terminal_session_mismatch', 'stream_binding_mismatch', 'frames_binding_missing', 'frames_terminal_inconsistent',
                   'attempt_binding_missing', 'attempt_binding_mismatch', 'custody_binding_unasserted')
CUSTODY_UNKNOWN = ('custody_missing', 'process_observer_fault', 'exit_unobserved', 'input_delivery_incomplete',
                   'process_custody_unknown', 'frames_outcome_missing')
PROCESS_FAILURE = ('process_timeout', 'process_output_limit', 'process_timing_limit', 'nonzero_exit')


def classify_attempt(custody, frames, terminal, binding):
    """Fixed-vocabulary routing; every applicable observation is kept in `observations`.

    `custody`  cairn.native-process-custody/1 mapping or None.
    `frames`   {'status': 'ok'|'refused'|'not_run', 'code': fixed code|None, plus Frames.stream_digest() fields}.
    `terminal` TerminalProbe.report() for the same bytes, or None.
    `binding`  {'attempt_id': str, 'session_id': str, 'custody_bound': True}, asserted by the external owner after it
               verified the custody record belongs to this attempt; absent or false is an unknown, never a pass."""
    observations = []
    if not isinstance(custody, dict) or custody.get('schema') != 'cairn.native-process-custody/1':
        observations.append('custody_missing')
        custody = {}
    cleanup = custody.get('cleanup') if isinstance(custody.get('cleanup'), dict) else {}
    returncode, classification = custody.get('observed_returncode'), custody.get('classification')
    if custody:
        if classification in ('timeout', 'output_limit', 'timing_limit'):
            observations.append('process_' + classification)
        elif classification != 'completed':
            observations.append('process_observer_fault')
        if type(returncode) is not int:
            observations.append('exit_unobserved')
        elif returncode != 0 and classification == 'completed':
            observations.append('nonzero_exit')
        if custody.get('input_bytes_written') != custody.get('input_bytes') or type(custody.get('input_bytes')) is not int:
            observations.append('input_delivery_incomplete')
        if cleanup.get('status') != 'observed_clear' or custody.get('leader_reaped') is not True:
            observations.append('process_custody_unknown')
    # --- terminal report: every defect is its own observation
    t = terminal if isinstance(terminal, dict) else None
    if t is None:
        observations.append('terminal_report_missing')
    elif t.get('schema') != 'cairn.terminal-probe/1':
        observations.append('terminal_report_invalid')
        t = None
    else:
        if not t.get('result_seen') or t.get('result_events') == 0:
            observations.append('terminal_result_missing')
        elif t.get('result_events') != 1:
            observations.append('terminal_multiple_results')
        if t.get('malformed_lines') != 0:
            observations.append('terminal_malformed_lines')
        if t.get('overflow') is not False:
            observations.append('terminal_overflow')
        if t.get('pending_bytes') != 0:
            observations.append('terminal_unterminated_tail')
        if t.get('session_match') is False:
            observations.append('terminal_session_mismatch')
        elif t.get('result_seen') and t.get('session_match') is None:
            observations.append('terminal_session_unobserved')
    stop = t.get('admission_failure') if t else None
    if stop:
        observations.append('provider_admission_' + str(stop.get('reason')))
    if t and t.get('is_error') is True and not stop:
        observations.append('provider_error')
    # --- frames outcome and its binding to the terminal report
    if not isinstance(frames, dict) or frames.get('status') not in ('ok', 'refused', 'not_run'):
        observations.append('frames_outcome_missing')
        frames = {}
    elif frames['status'] == 'refused':
        observations.append('frames_refused')
    elif frames['status'] == 'not_run':
        observations.append('frames_not_run')
    if frames.get('status') == 'ok':
        if not (isinstance(frames.get('stream_sha256'), str) and type(frames.get('stream_bytes')) is int
                and isinstance(frames.get('session_id'), str)):
            observations.append('frames_binding_missing')
        elif t is not None and (frames['stream_sha256'] != t.get('stream_sha256') or frames['stream_bytes'] != t.get('stream_bytes')):
            observations.append('stream_binding_mismatch')
        if t is not None and (t.get('is_error') is not False or t.get('subtype') not in ('success', None)):
            observations.append('frames_terminal_inconsistent')   # a success stream cannot carry an error result
    elif frames.get('stream_sha256') is not None and t is not None and (
            frames['stream_sha256'] != t.get('stream_sha256') or frames.get('stream_bytes') != t.get('stream_bytes')):
        observations.append('stream_binding_mismatch')
    # --- binding asserted by the external owner
    if not (isinstance(binding, dict) and isinstance(binding.get('attempt_id'), str) and binding['attempt_id']
            and isinstance(binding.get('session_id'), str) and binding['session_id']):
        observations.append('attempt_binding_missing')
    else:
        sessions = {binding['session_id'], frames.get('session_id') or binding['session_id'],
                    (t or {}).get('session_id') or binding['session_id']}
        if len(sessions) != 1:
            observations.append('attempt_binding_mismatch')
        if binding.get('custody_bound') is not True:
            observations.append('custody_binding_unasserted')
    have = set(observations)
    terminal_ok = not (have & set(TERMINAL_UNKNOWN))
    if have & set(CUSTODY_UNKNOWN) or have & set(BINDING_UNKNOWN):
        route = 'harness_unknown'
    elif stop and terminal_ok:
        route = 'provider_admission_stop'
    elif have & set(PROCESS_FAILURE):
        route = 'process_failure'
    elif not terminal_ok:
        route = 'harness_unknown'
    elif 'provider_error' in have:
        route = 'provider_error'
    elif 'frames_refused' in have or 'frames_not_run' in have:
        route = 'frames_refused'
    else:
        route = 'graded_candidate'
    return dict(schema='cairn.attempt-route/2', attempt_class=route, observations=sorted(have),
                admission_stop_observed=bool(stop),
                frames_code=frames.get('code') if closed(frames.get('code')) else None,
                basis='routing label only; not task acceptance, provider admission or custody attestation; '
                      'binding is asserted by the external owner, not derived here')
