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
    'organic_root', 'organic_advance_intent', 'organic_claim_intent', 'inbox_intent_kind',
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
        record['inbox_intent_kind'] = 'ordinary'
        record['inbox_result_policy'] = dict(claim_request_id=record['inbox_intent']['request_id'], version=1)
        self.save()
        return self.host.recover(self)

    def admit_organic_notice(self, native_owner_turn_id, owner_input_origin=None, attestation=None):
        """Persist a signed observation independently of any existing inbox hold."""
        request = dict(session=self.host.session_ref(self.record['agent']),
                       native_owner_turn_id=native_owner_turn_id,
                       owner_input_origin=owner_input_origin, owner_input_attestation=attestation)
        pending = self.record.get('organic_claim_intent')
        fresh = pending is None
        if pending is not None:
            if {k: v for k, v in pending.items() if k != 'request_id'} != request:
                raise self.host.refusal('INTENT_PENDING', 'another signed observation is unresolved')
        else:
            self.record['organic_claim_intent'] = dict(request_id=str(uuid.uuid4()), **request)
            self.save()
        return self._recover_organic_claim(fresh=fresh)

    def _recover_organic_claim(self, *, fresh=False):
        record, refusal = self.record, self.host.refusal
        request = record['organic_claim_intent']
        if request.get('session') != self.host.session_ref(record['agent']):
            raise refusal('STALE_SESSION', 'signed observation left its execution')
        try:
            result = self.host.call(self.config, 'session-organic-notice-claim', request)
        except refusal as exc:
            if fresh and exc.code in BATCH_ADVANCE_FINAL_REFUSALS + ('FEATURE_DISABLED', 'VERSION_CONFLICT',
                                                                   'CONFLICT', 'INPUT_ID_REUSED'):
                record.pop('organic_claim_intent')
                self.save()
            raise
        if not isinstance(result, dict) or result.get('decision') not in ('none', 'admitted', 'exhausted'):
            raise refusal('INVALID_HOST', 'signed observation returned an invalid result')
        selected = result.get('attempt')
        if selected is not None:
            if (not isinstance(selected, dict) or selected.get('session') != request['session']
                    or selected.get('native_turn_id') != request['native_owner_turn_id']
                    or selected.get('admission_kind') != 'organic'
                    or not result.get('root_id') or selected.get('organic_root') != result['root_id']
                    or not selected.get('attempt_id') or selected.get('finished_at')
                    or selected.get('turn_exclusive')
                    or (selected.get('delivery') or {}).get('event', {}).get('kind') != 'notice'):
                raise refusal('INVALID_HOST', 'signed observation changed item ownership')
            old = record.get('inbox_attempt')
            if old and old.get('attempt_id') != selected['attempt_id']:
                raise refusal('INVALID_HOST', 'signed observation replaced an existing inbox hold')
            if not old:
                record['inbox_attempt'] = selected
                record['organic_root'] = result['root_id']
                record['inbox_intent'] = dict(request_id=selected['attempt_id'], session=request['session'],
                                               native_turn_id=selected['native_turn_id'])
                record['inbox_intent_kind'] = 'organic'
                record['inbox_result_policy'] = dict(claim_request_id=selected['attempt_id'], version=1)
        record.pop('organic_claim_intent')
        self.save()
        return result

    def advance_organic_notice(self, request=None):
        """Persist the next exact handoff, then use the shared batch transition."""
        record = self.record
        old = record.get('inbox_attempt') or {}
        if old.get('admission_kind') != 'organic' or not old.get('organic_root'):
            raise self.host.refusal('INVALID_REQUEST', 'no organic notice is held')
        pending = record.get('organic_advance_intent')
        fresh = pending is None
        if pending is None:
            request = request or dict(request_id=str(uuid.uuid4()), session=old['session'],
                attempt_id=old['attempt_id'], root_id=old['organic_root'], version=1)
            record['organic_advance_intent'] = request
            self.save()
        elif request is not None and request != pending:
            raise self.host.refusal('SESSION_BUSY', 'another notice handoff is unresolved')
        return self.advance_batch(fresh_organic=fresh)

    def recover(self):
        """Replay a saved batch handoff, then claim a pending intent; the attempt or None."""
        record = self.record
        if record.get('organic_claim_intent'):
            self._recover_organic_claim()
        if record.get('batch_advance_intent') or record.get('organic_advance_intent'):
            self.advance_batch()
        if record.get('inbox_intent') and not record.get('inbox_attempt'):
            if record.get('inbox_intent_kind') == 'organic' or 'native_owner_turn_id' in record['inbox_intent']:
                result = self.host.call(self.config, 'session-organic-notice-claim', record['inbox_intent'])
                if result and result.get('attempt'):
                    record['inbox_attempt'] = result['attempt']
                    record['organic_root'] = result.get('root_id')
                else:
                    record.pop('inbox_intent', None)
                    record.pop('inbox_intent_kind', None)
                self.save()
            else:
                result = self.host.call(self.config, 'session-inbox-claim', record['inbox_intent'])
                if result.get('attempt'):
                    record['inbox_attempt'] = result['attempt']
                else:
                    record.pop('inbox_intent', None)
                    record.pop('inbox_intent_kind', None)
                self.save()
        return record.get('inbox_attempt')

    def refresh_attempt(self, attempt):
        """Adopt the store's current view of the held attempt."""
        self.record['inbox_attempt'] = attempt
        self.save()
        return attempt

    def advance_batch(self, *, fresh_organic=False):
        """Replay one saved atomic notice handoff before interpreting local state."""
        record, refusal = self.record, self.host.refusal
        organic = bool(record.get('organic_advance_intent'))
        intent_key = 'organic_advance_intent' if organic else 'batch_advance_intent'
        if organic and record.get('batch_advance_intent'):
            raise refusal('SESSION_BUSY', 'two notice handoffs are unresolved')
        request = record.get(intent_key)
        fields = {'request_id', 'session', 'attempt_id', 'version'} | ({'root_id'} if organic else set())
        if not isinstance(request, dict) or set(request) != fields:
            raise refusal('INVALID_HOST', 'native batch advance intent is malformed')
        old = record.get('inbox_attempt') or {}
        if (request['attempt_id'] != old.get('attempt_id') or request['session'] != old.get('session')
                or request['version'] != 1 or not old.get('native_turn_id')
                or organic != (old.get('admission_kind') == 'organic')
                or (organic and request['root_id'] != old.get('organic_root'))):
            raise refusal('STALE_SESSION', 'native batch advance left its active turn')
        try:
            # Native inbox mutations require the base profile; the execution is
            # fenced by the explicit session in the request body.
            result = self.host.call(self.config, 'session-organic-notice-advance' if organic else 'session-inbox-advance', request)
        except refusal as exc:
            # A definite refusal transferred no hold. Do not replay it ahead of
            # later settlement on every hook. Unknown transport results retain
            # the exact intent for idempotent recovery.
            if (not organic or fresh_organic) and exc.code in BATCH_ADVANCE_FINAL_REFUSALS:
                record.pop(intent_key)
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
                    (organic and (next_attempt.get('admission_kind') != 'organic' or
                                  next_attempt.get('organic_root') != request['root_id'])) or
                    (next_attempt.get('delivery') or {}).get('event', {}).get('kind') != 'notice'):
                raise refusal('INVALID_HOST', 'native batch advance changed item ownership')
            record['inbox_attempt'] = next_attempt
            record['inbox_intent'] = dict(request_id=next_attempt['attempt_id'], session=request['session'],
                                          native_turn_id=next_attempt['native_turn_id'])
            record['inbox_result_policy'] = dict(claim_request_id=next_attempt['attempt_id'], version=1)
            record['inbox_intent_kind'] = 'organic' if organic else 'ordinary'
            for key in BATCH_NEXT_KEYS_CLEARED:
                record.pop(key, None)
        record.pop(intent_key)
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
        if record.get('organic_claim_intent') or record.get('organic_advance_intent'):
            # An unknown observation/handoff reply can own a Store hold even
            # when no local inbox_intent exists yet. Resolve that exact saved
            # operation before leaving fences access to its receipt.
            try:
                self.recover()
            except refusal as exc:
                if exc.code not in ('NOT_FOUND', 'AUTHORITY_DENIED'):
                    raise
                # A cached reply can become source-ineligible after its hold
                # committed. Only Store's actual owned attempt metadata can
                # recover that hold; request IDs need not equal its identity.
                self._retirement_organic_control()
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

    def _retirement_organic_control(self):
        record, refusal = self.record, self.host.refusal
        pending = record.get('organic_claim_intent')
        advance = record.get('organic_advance_intent')
        old = record.get('inbox_attempt') or {}
        result = self.host.call(self.config, 'session-inbox-control', self.host.session_ref(record['agent']))
        selected = result.get('attempt') if isinstance(result, dict) else None
        owner_turn = pending.get('native_owner_turn_id') if pending else old.get('native_turn_id')
        if (not isinstance(selected, dict) or selected.get('session') != self.host.session_ref(record['agent'])
                or selected.get('native_turn_id') != owner_turn or not owner_turn
                or selected.get('admission_kind') != 'organic' or not selected.get('organic_root')
                or not selected.get('attempt_id') or selected.get('finished_at') or selected.get('turn_exclusive')
                or (selected.get('delivery') or {}).get('event', {}).get('kind') != 'notice'):
            raise refusal('INTENT_PENDING', 'Store did not prove the exact organic hold for retirement')
        if advance:
            if (selected.get('organic_root') != advance.get('root_id')
                    or selected.get('attempt_id') == old.get('attempt_id')
                    or selected.get('batch_root') != (old.get('batch_root') or old.get('attempt_id'))
                    or selected.get('batch_index') != old.get('batch_index', 1) + 1):
                raise refusal('INTENT_PENDING', 'Store did not prove the uncertain organic handoff')
        elif old and old.get('attempt_id') != selected['attempt_id']:
            raise refusal('INTENT_PENDING', 'Store control differs from the previously held item')
        if old.get('attempt_id') != selected['attempt_id']:
            for key in BATCH_NEXT_KEYS_CLEARED:
                record.pop(key, None)
        record['inbox_attempt'] = selected
        record['inbox_intent'] = dict(request_id=selected['attempt_id'], session=selected['session'],
                                      native_turn_id=selected['native_turn_id'])
        record['inbox_intent_kind'] = 'organic'
        record['organic_root'] = selected['organic_root']
        record['inbox_result_policy'] = dict(claim_request_id=selected['attempt_id'], version=1)
        record.pop('organic_claim_intent', None)
        record.pop('organic_advance_intent', None)
        self.save()


@contextmanager
def open_session(config, native_id, host, *, create=False):
    """The locked record of one conversation; a missing record is an error unless ``create``."""
    path = host.locate(config, native_id)
    with host.lock(path):
        record = {} if create and not path.exists() else json.loads(path.read_text())
        yield NativeSession(config, path, record, host)
