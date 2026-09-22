"""Recoverable Cairn control ledger for one verified native Hermes request.

The host calls this outside its lifecycle lock. This module never claims an
inbox and never retries an uncertain native mutation. Cairn mutations retain
their exact request UUID and payload until a response is observed.
"""
import json
import uuid
from pathlib import Path

from hermes_cancel import BridgeClient, ControlRefused, ControlUnavailable, ControlUncertain


IDENTITY = ('session_id', 'request_id', 'delivery_id', 'turn_id', 'ownership_token')
FLAGS = ('exclusive', 'revoked', 'cancelled', 'turn_ended', 'tool_admission_closed')


def text_id(value):
    return (isinstance(value, str) and bool(value.strip()) and
            len(value.encode()) <= 256 and not any(ord(c) < 32 for c in value))


def ownership(value):
    if (not isinstance(value, dict) or any(not text_id(value.get(k)) for k in IDENTITY)
            or any(type(value.get(k)) is not bool for k in FLAGS)
            or type(value.get('active_tool_calls')) is not int or value['active_tool_calls'] < 0
            or value['exclusive'] == value['revoked']):
        raise ControlUncertain('native ownership snapshot is invalid')
    return {k: value[k] for k in (*IDENTITY, *FLAGS, 'active_tool_calls')}


def evidence(result, expected):
    if not isinstance(result, dict):
        raise ControlUncertain('native control result is invalid')
    observed = ownership(result.get('ownership'))
    if any(observed[k] != expected[k] for k in IDENTITY):
        raise ControlUncertain('native control ownership differs from the held request')
    if (type(result.get('inventory_complete')) is not bool or
            result.get('terminal_scan') not in ('clear', 'unknown_remaining') or
            not isinstance(result.get('tools'), list) or len(result['tools']) > 64):
        raise ControlUncertain('native tool inventory is invalid or exceeds the supported limit')
    items = []
    seen = set()
    for tool in result['tools']:
        if (not isinstance(tool, dict) or not text_id(tool.get('item_id')) or
                not text_id(tool.get('process_id')) or tool.get('native_turn_id') != expected['turn_id'] or
                tool.get('stop_state') not in ('captured', 'terminated', 'unavailable') or tool['item_id'] in seen):
            raise ControlUncertain('native tool ownership or state is invalid')
        seen.add(tool['item_id'])
        items.append({k: tool[k] for k in ('item_id', 'process_id', 'native_turn_id', 'stop_state')})
    return observed, items, result['inventory_complete'], result['terminal_scan']


class Controller:
    def __init__(self, ledger, save, api, bridge):
        self.ledger, self.save, self.api, self.bridge = ledger, save, api, bridge

    def mutation(self, operation, body):
        pending = self.ledger.get('pending')
        if pending is not None:
            self.api(pending['operation'], pending['request'])
            self.ledger.pop('pending')
            self.save()
        request = dict(body, request_id=str(uuid.uuid4()))
        self.ledger['pending'] = dict(operation=operation, request=request)
        self.save()
        result = self.api(operation, request)
        self.ledger.pop('pending')
        self.save()
        return result

    def dispatch(self, kind, params, key):
        dispatched = self.ledger.setdefault('dispatch', {})
        if key in dispatched:
            return  # Sent or uncertain: status observations are the only recovery.
        dispatched[key] = dict(kind=kind, params=params)
        self.save()  # Persist before sending, including a crash at the send boundary.
        try:
            self.bridge.rpc('session/request_' + kind, params)
        except ControlUnavailable:
            dispatched.pop(key)
            self.save()  # Transport proved no bytes were sent; a later cycle may try.
            raise
        # A typed refusal is retained too. Deployment/operator review, not an
        # automatic retry loop, decides whether a new action is appropriate.

    def poll(self, session, binding):
        pending = self.ledger.get('pending')
        if pending:
            self.api(pending['operation'], pending['request'])
            self.ledger.pop('pending')
            self.save()
        attempt = self.api('session-inbox-control', session).get('attempt')
        if not attempt or attempt.get('finished_at'):
            return
        if (attempt['attempt_id'] != self.ledger['attempt_id'] or
                attempt['session'] != session or attempt.get('native_turn_id') != binding['turn_id'] or
                attempt['delivery']['delivery_id'] != binding['delivery_id']):
            raise ControlUncertain('held Cairn attempt differs from the native control binding')
        params = {k: binding[k] for k in ('session_id', 'request_id', 'turn_id', 'ownership_token')}
        observed, tools, complete, scan = evidence(self.bridge.rpc('session/request_status', params), binding)
        common = dict(session=session, attempt_id=attempt['attempt_id'])
        captured = {t['item_id']: t for t in attempt.get('tools', [])}
        for tool in tools:
            if tool['item_id'] in captured and captured[tool['item_id']]['process_id'] != tool['process_id']:
                raise ControlUncertain('native tool process changed for a captured item')
        new = [{k: t[k] for k in ('item_id', 'process_id', 'native_turn_id')}
               for t in tools if t['item_id'] not in captured]
        if new:
            self.mutation('session-tool-capture', dict(common, items=new))
        all_ids = set(captured) | {t['item_id'] for t in tools}
        terminal = {t['item_id'] for t in tools if t['stop_state'] == 'terminated'}
        terminal |= {i for i, t in captured.items() if t['stop_state'] == 'terminated'}
        quiescent = (observed['turn_ended'] and observed['tool_admission_closed'] and
                     observed['active_tool_calls'] == 0)
        clear = complete and scan == 'clear' and quiescent and all_ids <= terminal
        report = dict(common, owner_join=observed['revoked'],
                      terminal_scan='clear' if clear else 'unknown_remaining',
                      tools=[dict(item_id=t['item_id'], stop_state='terminated')
                             for t in tools if t['stop_state'] == 'terminated'])
        if quiescent:
            report['turn_stop'] = 'ended'
        self.mutation('session-tool-stop', report)
        if not attempt.get('cancel'):
            return
        if observed['revoked']:
            if clear:
                self.mutation('session-inbox-reconcile', dict(common, reason='exclusivity_revoked'))
            return  # No native mutation, including process kill, after owner join.
        if not attempt.get('turn_exclusive'):
            return  # A later snapshot cannot upgrade the original claim's authority.
        if clear:
            self.mutation('session-inbox-reconcile', dict(common, reason='cancel_confirmed'))
            return
        if not observed['cancelled']:
            self.dispatch('cancel', params, 'cancel')
            return  # Observe admission closure before planning any process kill.
        if not observed['tool_admission_closed']:
            return
        for tool in tools:
            if tool['stop_state'] != 'terminated':
                selected = {k: tool[k] for k in ('item_id', 'process_id')}
                self.dispatch('cleanup', dict(params, tools=[selected]), 'cleanup:' + tool['item_id'])


def poll_host(config, state, lifecycle):
    binding = state.get('hermes_request')
    attempt = state.get('inbox_attempt')
    if not binding or not attempt or state.get('retired') or not lifecycle.process_alive(state['process']):
        return
    if binding['session_id'] != state['agent']['native_session_id']:
        raise ControlUncertain('native conversation differs from the saved binding')
    endpoint = lifecycle.hermes_queue_endpoint(config, state['process'])
    if not endpoint:
        raise ControlUnavailable('verified native Hermes endpoint is unavailable')
    # This separate ledger lock never blocks native lifecycle hooks, and no
    # lifecycle state lock spans the RPC or its native callbacks.
    path = Path(config['state_dir']) / 'control' / (attempt['attempt_id'] + '.json')
    with lifecycle.session_lock(path):
        ledger = json.loads(path.read_text()) if path.exists() else dict(attempt_id=attempt['attempt_id'])
        if ledger['attempt_id'] != attempt['attempt_id']:
            raise ControlUncertain('control ledger belongs to another attempt')
        controller = Controller(ledger, lambda: lifecycle.write_state(path, ledger),
                                lambda op, body: lifecycle.call(config, op, body),
                                BridgeClient(endpoint, state['process']))
        controller.poll(lifecycle.session_ref(state['agent']), binding)
