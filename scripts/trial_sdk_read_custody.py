"""Callback-local offers from held service results, never producer inventories.

This narrows current-read admission; the downstream server remains authoritative.
Parsing/retention uses the original command working quote transferred unchanged
by CallbackPhases. It is not a complete physical quote or a new allowance.
"""
import json
import uuid

from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_engine_adapter import CommandTextOutcome
from trial_sdk_incoming import canonical_size_sha256, MAX_DOCUMENT_BYTES, parse_document


class ReadCustodyError(ValueError):
    pass


def require(value, code):
    if not value:
        raise ReadCustodyError(code)


def identifier(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0
    except ValueError:
        return False


class ReadCustody:
    """One original RPC allocation, one fixed policy, actual completed results.

    No public add-offer API. Identity of the RPC/operation/result and original
    held allocation precedes parsing. A Python caller with object mutation
    access is outside this trusted-source boundary, not authenticated by it.
    """
    def __init__(self, rpc, policy):
        from trial_sdk_engine_memory_rpc import ProducerRPC
        require(type(rpc) is ProducerRPC and type(policy) is FixedCommandPolicy,
                'read_custody_binding')
        require(rpc._phases is not None and rpc._phase == 'idle'
                and rpc._operation is None and rpc._read_custody is None,
                'read_custody_binding')
        rpc.resources._lookup(rpc.resources._memory, rpc._allocation)
        self._rpc, self._policy = rpc, policy
        self._allocation = rpc._allocation
        self._offers = {}
        self._last_operation = None
        self._failed = False

    def _live(self):
        require(not self._failed and self._rpc._allocation is self._allocation
                and self._rpc._read_custody is self, 'read_custody_unavailable')
        self._rpc.resources._check()
        self._rpc.resources._lookup(self._rpc.resources._memory, self._allocation)

    def _publish(self, proposed):
        metadata = [[*key, value[0], value[1], [list(s) for s in value[2]]]
                    for key, value in proposed.items()]
        require(canonical_size_sha256(metadata)[0] <= MAX_DOCUMENT_BYTES,
                'read_custody_inventory_bound')
        self._offers = proposed

    def authorize_pending(self):
        """Inspect only the RPC's held request; return no payload/permit borrow."""
        try:
            self._live()
            pending = self._rpc.pending_request()
            require(pending is not None, 'read_custody_pending')
            request = pending.request
            plan = self._policy.plan(request.operation, request.args, request.body)
            if request.operation == 'search':
                return  # Fixed scope/grammar; server selects current source.
            require(request.operation == 'pull', 'read_custody_unsupported')
            body = json.loads(plan.body)
            require(set(body) <= {'request_id', 'receipt_id', 'handle', 'span', 'opening_bytes'}
                    and identifier(body.get('request_id')), 'read_custody_form')
            offer = self._offers.get((body['receipt_id'], body['handle']))
            require(offer is not None, 'read_custody_unoffered')
            if 'span' in body:
                span = body['span']
                require((span['offset'], span['length']) in offer[2], 'read_custody_span')
            if body.get('opening_bytes', 0):
                require('span' in body and body['opening_bytes'] <= min(768, body['span']['offset']),
                        'read_custody_opening')
        except BaseException:
            self._failed = True
            raise

    def observe_completed(self):
        """Only called at the RPC's actual service-result handoff, before send."""
        try:
            self._live()
            rpc = self._rpc
            operation = rpc._operation
            require(operation is not None and operation._closed and not operation._unknown
                    and not operation._retired and operation is not self._last_operation
                    and rpc._result is operation.result()
                    and type(rpc._result) is CommandTextOutcome,
                    'read_custody_result')
            row = rpc.resources._lookup(rpc.resources._memory, operation._parent)
            require(row[0] is rpc.resources.local and row[1] == 'operation' and row[3] > 0
                    and rpc._phases.state.phase == 'command', 'read_custody_charge')
            # Consume observation before parsing; uncertainty never retries.
            self._last_operation = operation
            if rpc._result.returncode != 0:
                return
            result = parse_document(rpc._result.stdout, rpc._result.stdout_bytes,
                                    max_bytes=1024*1024)
            require(type(result) is dict, 'read_custody_response')
            if result.get('ok') is not True:
                return
            # The actual service applies its own FixedCommandPolicy before creating
            # a command. Recheck the held request against our scope here: only a
            # successful service result can populate offers. A different service
            # policy refuses downstream before executing; no equality assertion
            # about a separately supplied Python policy object is used as evidence.
            request = rpc._request.request
            self._policy.plan(request.operation, request.args, request.body)
            data = result.get('data')
            require(type(data) is dict, 'read_custody_response')
            if rpc._request.request.operation == 'pull':
                requested = json.loads(rpc._request.request.body)
                offer = self._offers[(requested['receipt_id'], requested['handle'])]
                selection = data.get('selection')
                require(type(selection) is dict and type(selection.get('record')) is dict,
                        'read_custody_selection')
                record = selection['record']
                require((record.get('record_id'), record.get('version')) == offer[:2],
                        'read_custody_selection')
                span = data.get('span')
                if span is not None:
                    require(type(span) is dict and type(span.get('offset')) is int
                            and type(span.get('end')) is int and type(span.get('total_bytes')) is int
                            and 0 <= span['offset'] < span['end'] <= span['total_bytes'] <= 65536,
                            'read_custody_span_response')
                    expected = requested.get('span')
                    require(expected is not None and span['offset'] == expected['offset']
                            and span['end'] <= expected['offset'] + expected['length'],
                            'read_custody_span_response')
                    length = min(768, span['offset'])
                    if length:
                        # Actual source-bound reply establishes the engine's prefix request.
                        key = (requested['receipt_id'], requested['handle'])
                        proposed = dict(self._offers)
                        proposed[key] = (offer[0], offer[1], tuple(sorted(set(offer[2]) | {(0, length)})))
                        self._publish(proposed)
                return  # No evidence/history authority inferred from opaque fields.
            require(rpc._request.request.operation == 'search', 'read_custody_unsupported')
            require(data.get('status') in ('READY', 'SCOPE_EMPTY', 'DEGRADED_NO_EMBEDDINGS')
                    and type(data.get('destination')) is dict
                    and data['destination'].get('name') == 'hosted', 'read_custody_destination')
            entries = data.get('index', [])
            require(type(entries) is list, 'read_custody_index')
            proposed = dict(self._offers)
            for entry in entries:
                require(type(entry) is dict, 'read_custody_offer')
                args = entry.get('pull_arguments')
                require(type(args) is dict and set(args) == {'request_id', 'receipt_id', 'handle'}
                        and all(identifier(args[k]) for k in args), 'read_custody_offer')
                require(args['receipt_id'] == data.get('receipt_id')
                        and identifier(entry.get('record_id'))
                        and type(entry.get('version')) is int and entry['version'] > 0,
                        'read_custody_offer')
                spans = []
                # Only exact server-offered spans; no guessed record extent.
                if entry.get('class') in ('A', 'B') and not entry.get('mandatory') and not entry.get('conflicts'):
                    for key in ('summary_span', 'match_span'):
                        span = entry.get(key)
                        if span is not None:
                            require(type(span) is dict and set(span) == {'offset', 'length'}
                                    and type(span['offset']) is int and span['offset'] >= 0
                                    and type(span['length']) is int and span['length'] > 0,
                                    'read_custody_span')
                            require(span['offset'] < 65536 and span['length'] <= 4096,
                                    'read_custody_span')
                            if key == 'match_span':
                                spans.append((span['offset'], span['length']))
                            elif 'match_span' not in entry:
                                spans.append((span['offset'], min(1536, 65536-span['offset'])))
                key = (args['receipt_id'], args['handle'])
                offer = (entry['record_id'], entry['version'], tuple(spans))
                require(key not in proposed or proposed[key] == offer, 'read_custody_conflict')
                proposed[key] = offer
            # Existing document ceiling bounds retained normalized metadata;
            # working parsing/old+new overlap stays under held command quote.
            self._publish(proposed)
        except BaseException:
            self._failed = True
            raise
