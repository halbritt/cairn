"""The host's native session record and its attempt-lifecycle transitions.

The ``cairn.native-session/1`` record is one JSON file per conversation. Its
inbox attempt lifecycle (intent, claim, journal, release, settlement, retirement)
used to be key pokes followed by ``write_state`` in a dozen coordination
functions and in the transcript-recovery verifiers. This module owns those
transitions; the key layout and every value written to disk are unchanged, so
installed older bundles and fixtures read the same file.

The session persists through its ``Host``: the store call, the journal, the
refusal type and the writer are injected, so a transition table runs offline
with an in-memory host. Cross-transition calls go through the host as well, so
the coordination entry points that tests and pinned bundles patch stay the one
place a transition is reached.
"""
from contextlib import contextmanager
import json
import sys
import uuid


SCHEMA = 'cairn.native-session/1'
ATTEMPT_KEYS = (
    'inbox_intent', 'inbox_attempt', 'inbox_close', 'inbox_completion', 'inbox_response',
    'inbox_turn_ended', 'inbox_result_policy', 'inbox_ack_reminded', 'inbox_ack_reminder_revision',
    'batch_advance_intent', 'held_refusal_observation',
    'cancel_turn_end', 'cancel_submission', 'cancel_owner_join',
    'cancel_scan_report', 'cancel_final_report',
    'tool_calls', 'opencode_turn_since', 'opencode_request_endpoint',
)
BATCH_NEXT_KEYS_CLEARED = ('inbox_close', 'inbox_completion', 'inbox_response', 'inbox_ack_reminded',
                           'inbox_ack_reminder_revision')
BATCH_ADVANCE_FINAL_REFUSALS = ('DELIVERY_ACTIVE', 'INVALID_REQUEST', 'STALE_SESSION', 'NOT_FOUND',
                                'AUTHORITY_DENIED', 'IDEMPOTENCY_CONFLICT')
BATCH_LIMIT = 3
LEASE_SECONDS = 90
SETTLEMENT_PENDING = 'turn_ended_pending'
RETIRED_SESSION_CODES = ('STALE_SESSION', 'NOT_FOUND')


class Host:
    """What a session needs from its host; every member is a callable or a class."""

    def __init__(self, *, call, persist, refusal, flush, journal_committed, turn_end_diagnostic,
                 session_ref, locate=None, lock=None, recover=None, release=None, renew=None,
                 end_turn=None, log=None):
        self.call, self.persist, self.refusal = call, persist, refusal
        self.flush, self.journal_committed, self.turn_end_diagnostic = flush, journal_committed, turn_end_diagnostic
        self.session_ref, self.locate, self.lock = session_ref, locate, lock
        self._recover, self._release, self._renew, self._end_turn = recover, release, renew, end_turn
        self.log = log or (lambda message: print(message, file=sys.stderr))

    # Cross-transition calls route through the host's entry points when given,
    # so a patched coordination function still intercepts every path to it.
    def recover(self, session):
        return self._recover(session.config, session.record, session.path) if self._recover else session.recover()

    def release(self, session, reason, fenced):
        if self._release:
            return self._release(session.config, session.record, session.path, reason, fenced=fenced)
        return session.release(reason, fenced=fenced)

    def renew(self, session, attempt):
        return self._renew(session.config, session.record, session.path, attempt) if self._renew else session.renew(attempt)

    def end_turn(self, session, fenced=False):
        if self._end_turn:
            return self._end_turn(session.config, session.record, session.path, fenced=fenced)
        return session.end_turn(fenced=fenced)


class MemoryHost(Host):
    """An in-memory host for transition tables: a scripted store and a persistence log."""

    class Refusal(Exception):
        def __init__(self, code, message=''):
            super().__init__(f"{code}: {message}")
            self.code = code

    def __init__(self, responses=None, *, flush=True, journal_committed=False):
        self.calls, self.persisted, self.logged = [], [], []
        self.responses = dict(responses or {})
        self._flush_result, self._committed = flush, journal_committed
        super().__init__(call=self._call, persist=self._persist, refusal=self.Refusal,
                         flush=lambda config, record, scope='attempt': self._flush_result,
                         journal_committed=lambda config, record: self._committed,
                         turn_end_diagnostic=lambda config, record: None,
                         session_ref=lambda agent: dict(agent_id=agent['agent_id'], execution_id=agent['execution_id']),
                         log=self.logged.append)

    def _call(self, config, operation, body, **kwargs):
        self.calls.append((operation, json.loads(json.dumps(body))))
        response = self.responses.get(operation)
        if callable(response):
            response = response(body)
        if isinstance(response, Exception):
            raise response
        return response

    def _persist(self, path, record):
        self.persisted.append(json.loads(json.dumps(record)))


class NativeSession:
    """One conversation's record under its session lock; transitions persist themselves."""

    def __init__(self, config, path, record, host):
        self.config, self.path, self.record, self.host = config, path, record, host

    # --- reads -------------------------------------------------------------
    @property
    def attempt(self):
        return self.record.get('inbox_attempt')

    @property
    def intent(self):
        return self.record.get('inbox_intent')

    def save(self):
        self.host.persist(self.path, self.record)

    # --- admission and claim ---------------------------------------------------
    def admit_inbox(self, binding=None):
        """Intend one claim for this execution's slot and recover it; the attempt or None."""
        record = self.record
        record['inbox_intent'] = dict(request_id=str(uuid.uuid4()),
                                      session=self.host.session_ref(record['agent']), **(binding or {}))
        record['inbox_result_policy'] = dict(claim_request_id=record['inbox_intent']['request_id'], version=1)
        self.save()
        return self.host.recover(self)

    def recover(self):
        """Replay a saved batch handoff, then claim a pending intent; the attempt or None."""
        record = self.record
        if record.get('batch_advance_intent'):
            self.advance_batch()
        if record.get('inbox_intent') and not record.get('inbox_attempt'):
            result = self.host.call(self.config, 'session-inbox-claim', record['inbox_intent'])
            if result['attempt']:
                record['inbox_attempt'] = result['attempt']
            else:
                record.pop('inbox_intent')
            self.save()
        return record.get('inbox_attempt')

    def refresh_attempt(self, attempt):
        """Adopt the store's current view of the held attempt."""
        self.record['inbox_attempt'] = attempt
        self.save()
        return attempt

    def advance_batch(self):
        """Replay one saved atomic notice handoff before interpreting local state."""
        record, refusal = self.record, self.host.refusal
        request = record.get('batch_advance_intent')
        if not isinstance(request, dict) or set(request) != {'request_id', 'session', 'attempt_id', 'version'}:
            raise refusal('INVALID_HOST', 'native batch advance intent is malformed')
        old = record.get('inbox_attempt') or {}
        if (request['attempt_id'] != old.get('attempt_id') or request['session'] != old.get('session')
                or request['version'] != 1 or not old.get('native_turn_id')):
            raise refusal('STALE_SESSION', 'native batch advance left its active turn')
        try:
            # Native inbox mutations require the base profile; the execution is
            # fenced by the explicit session in the request body.
            result = self.host.call(self.config, 'session-inbox-advance', request)
        except refusal as exc:
            # A definite refusal transferred no hold. Do not replay it ahead of
            # later settlement on every hook. Unknown transport results retain
            # the exact intent for idempotent recovery.
            if exc.code in BATCH_ADVANCE_FINAL_REFUSALS:
                record.pop('batch_advance_intent')
                self.save()
            raise
        if not isinstance(result, dict) or not isinstance(result.get('remainder', ''), str) or 'attempt' not in result:
            raise refusal('INVALID_HOST', 'native batch advance returned an invalid result')
        next_attempt = result['attempt']
        if next_attempt is not None:
            expected_index = old.get('batch_index') or 1
            if (not isinstance(next_attempt, dict) or next_attempt.get('attempt_id') != request['request_id'] or
                    next_attempt.get('session') != request['session'] or
                    next_attempt.get('native_turn_id') != old['native_turn_id'] or
                    next_attempt.get('batch_root') != (old.get('batch_root') or old['attempt_id']) or
                    next_attempt.get('batch_index') != expected_index + 1 or
                    next_attempt.get('batch_limit') != BATCH_LIMIT or next_attempt.get('finished_at') or
                    (next_attempt.get('delivery') or {}).get('event', {}).get('kind') != 'notice'):
                raise refusal('INVALID_HOST', 'native batch advance changed item ownership')
            record['inbox_attempt'] = next_attempt
            record['inbox_intent'] = dict(request_id=next_attempt['attempt_id'], session=request['session'],
                                          native_turn_id=next_attempt['native_turn_id'])
            record['inbox_result_policy'] = dict(claim_request_id=next_attempt['attempt_id'], version=1)
            for key in BATCH_NEXT_KEYS_CLEARED:
                record.pop(key, None)
        record.pop('batch_advance_intent')
        self.save()
        return result

    # --- the held turn ---------------------------------------------------------
    def renew(self, attempt):
        """Renew a live lease. A lapsed lease leaves the durable hold in place."""
        delivery = attempt['delivery']
        if attempt.get('lease_lapsed'):
            return delivery
        try:
            delivery = self.host.call(self.config, 'event-renew', dict(
                delivery_id=delivery['delivery_id'], lease_id=delivery['lease_id'], lease_seconds=LEASE_SECONDS),
                session=attempt['session'])
        except self.host.refusal as exc:
            if exc.code != 'STALE_LEASE':
                raise
            # The hold still excludes every other consumer. Completion under this
            # exact lease is either accepted late or refused explicitly.
            attempt['lease_lapsed'] = True
            self.host.log(f"Cairn presence {self.config['binding']}: lease lapsed for attempt {attempt['attempt_id']}; "
                          "the hold remains until completion or reconciliation")
        attempt['delivery'] = delivery
        self.save()
        return delivery

    def mark_turn_ended(self):
        """Remember that the owning turn ended; survives an uncertain flush for the watcher."""
        if not self.record.get('inbox_turn_ended'):
            self.record['inbox_turn_ended'] = True
            self.save()

    def mark_delivered(self, delivered):
        self.record['delivered_since_idle'] = bool(delivered)
        self.save()

    # --- release and settlement ----------------------------------------------
    def release(self, reason, fenced=False):
        """Reconcile the held attempt with the store under one saved close, then clear it."""
        record, refusal = self.record, self.host.refusal
        if not fenced:
            self.host.recover(self)
        intent = record.get('inbox_intent')
        if not intent:
            return True
        request = dict(session=intent['session'], attempt_id=intent['request_id'], reason=reason)
        saved = record.get('inbox_close', {})
        if saved.get('attempt_id') == request['attempt_id'] and saved.get('reason') != reason:
            # The saved reconciliation may have committed before its reply was lost.
            # Finish it under its own UUID; a new reason never replaces it unless the
            # store proved nothing committed.
            try:
                self.host.call(self.config, 'session-inbox-reconcile', saved)
            except refusal as exc:
                if not (exc.code == 'DELIVERY_ACTIVE' and saved['reason'] == 'delivery_completed'):
                    if not (fenced and exc.code == 'NOT_FOUND'):
                        raise
                else:
                    saved = {}
                    record.pop('inbox_close')
                    self.save()
            if saved:
                return self.clear()
        if any(saved.get(k) != v for k, v in request.items()):
            record['inbox_close'] = dict(request_id=str(uuid.uuid4()), **request)
            self.save()
        try:
            self.host.call(self.config, 'session-inbox-reconcile', record['inbox_close'])
        except refusal as exc:
            if exc.code == 'DELIVERY_ACTIVE' and reason == 'delivery_completed':
                # A refusal commits nothing, so a later reason may use a new UUID.
                record.pop('inbox_close')
                self.save()
                return False
            # Leaving or a restore fence prevents a delayed claim from committing
            # after an unknown attempt was found absent.
            if not (fenced and exc.code == 'NOT_FOUND'):
                raise
        return self.clear()

    def clear(self):
        """Forget the attempt and everything that belonged to it."""
        for key in ATTEMPT_KEYS:
            self.record.pop(key, None)
        self.save()
        return True

    def end_turn(self, fenced=False):
        """Close an ended turn's attempt, naming completion when the journal proves it.

        ``delivery_completed`` means the delivery was explicitly handled; ``turn_ended``
        means the turn ended without it. A store refusal of the former falls back.
        """
        if self.host.journal_committed(self.config, self.record) and self.host.release(self, 'delivery_completed', fenced):
            return True
        if not fenced:
            self.host.turn_end_diagnostic(self.config, self.record)
            self.save()
        return self.host.release(self, 'turn_ended', fenced)

    def settle(self, kind, evidence, *, prompt=None, strict=True):
        """End the owning turn with this evidence record, reconcile, and record the settlement.

        ``evidence['settlement']`` arrives as ``turn_ended`` (a proven refusal) or
        ``pending`` (a proof whose close reason the store will name). ``strict``
        records ``turn_ended_pending`` when reconciliation did not complete; a
        non-strict settlement records it only when reconciliation raised, leaving
        the watcher to finish it.
        """
        record = self.record
        record[kind] = dict(evidence)
        record['inbox_turn_ended'] = True
        if prompt is not None and (record.get('active_prompt') or {}).get('id') == prompt:
            record.pop('active_prompt')  # The refused prompt never reaches a native Stop.
        record['delivered_since_idle'] = False
        self.save()
        failed = False
        try:
            settled = bool(self.host.flush(self.config, record)) and bool(self.host.end_turn(self))
        except (self.host.refusal, OSError, ValueError, KeyError, TypeError):
            settled, failed = False, True
        outcome = record[kind]
        if failed or (strict and not settled):
            outcome['settlement'] = SETTLEMENT_PENDING
        elif settled and outcome.get('settlement') == 'pending':
            outcome['settlement'] = (record.get('inbox_close') or {}).get('reason', 'turn_ended')
        self.save()
        return outcome

    def retire(self, reason='process_exited', *, timeout=4):
        """Persist the logical end, leave the directory, release the hold and retire the record."""
        record, refusal = self.record, self.host.refusal
        # Persist the logical end before contacting the API. A long-lived gateway
        # process must not cause its finished conversation to be revived on recovery.
        record['ending'] = True
        record.setdefault('ending_reason', reason)
        self.save()
        if not record.get('agent'):
            record['agent'] = self.host.call(self.config, 'agent-register', record['registration'], timeout=timeout)
            self.save()
        if not self.host.flush(self.config, record, 'all'):
            # Leaving would strand a result or reply that may still commit under this execution.
            raise refusal('INTENT_PENDING', 'a journaled inbox command is still uncertain; the session stays until it settles')
        try:
            self.host.call(self.config, 'agent-leave', self.host.session_ref(record['agent']), timeout=timeout)
        except refusal as exc:
            if exc.code not in RETIRED_SESSION_CODES:
                raise
        self.host.release(self, record['ending_reason'], True)
        record['retired'] = True
        self.save()


@contextmanager
def open_session(config, native_id, host, *, create=False):
    """The locked record of one conversation; a missing record is an error unless ``create``."""
    path = host.locate(config, native_id)
    with host.lock(path):
        record = {} if create and not path.exists() else json.loads(path.read_text())
        yield NativeSession(config, path, record, host)
