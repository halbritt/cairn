"""Finite profile-5 transcript; no caller authority or transport effects."""
import hashlib
import struct
from types import MappingProxyType

REQUEST, REQUEST_END, BEGIN, SESSION_END = 33, 34, 35, 36
STDOUT, STDERR, STATUS, REFUSE, REQUEST_CREDIT, RESULT_META, SESSION_CLOSED = range(65, 72)
REQUEST_LIMIT = 128 * 1024
OUTPUT_LIMIT = 1024 * 1024
BOOTSTRAP = 173
STRUCTS = MappingProxyType({
    REQUEST_END: struct.Struct('>'), BEGIN: struct.Struct('>QI32s'),
    SESSION_END: struct.Struct('>'), STATUS: struct.Struct('>i'),
    REFUSE: struct.Struct('>B'), REQUEST_CREDIT: struct.Struct('>H'),
    RESULT_META: struct.Struct('>QQQQ'), SESSION_CLOSED: struct.Struct('>'),
})


class RPCError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise RPCError(code)


def request_shape(document):
    require(type(document) is dict and set(document) == {'operation', 'args', 'body'},
            'rpc_request_shape')
    require(type(document['operation']) is str and document['operation'] in
            ('search', 'pull', 'pull-evidence', 'history') and
            type(document['args']) is list and
            all(type(arg) is str for arg in document['args']) and
            (document['body'] is None or type(document['body']) is str),
            'rpc_request_shape')


class Transcript:
    def __init__(self, deadline_ns):
        require(type(deadline_ns) is int and deadline_ns > 0, 'rpc_deadline')
        self.callback_deadline = self.deadline = deadline_ns
        self.phase, self.operation = 'idle', 0
        self.request_size = self.request_count = self.permit = 0
        self.digest = self._hash = None
        self.raw_counts = self.wire_sizes = None
        self.wire_counts = [0, 0]
        self.returncode = self.refusal = None
        self.failed = False

    def observe(self, frame):
        require(not self.failed, 'rpc_transcript_failed')
        try:
            self._observe(frame)
        except BaseException:
            self.failed = True
            raise

    def _observe(self, frame):
        kind, operation = frame.kind, frame.operation
        with frame.body as body:
            fields = STRUCTS[kind].unpack(body) if kind in STRUCTS else ()
            if kind == BEGIN:
                deadline, size, digest = fields
                require(self.phase == 'idle' and operation > self.operation,
                        'rpc_begin_order')
                require(0 < deadline <= self.callback_deadline and
                        0 < size <= REQUEST_LIMIT, 'rpc_begin_bounds')
                self.deadline, self.operation = deadline, operation
                self.request_size, self.digest = size, digest
                self.request_count, self.permit = 0, 0
                self._hash = hashlib.sha256()
                self.raw_counts = self.wire_sizes = None
                self.wire_counts = [0, 0]
                self.returncode = self.refusal = None
                self.phase = 'credit'
                return
            if kind == SESSION_END:
                require(operation == 0 and self.phase in ('idle', 'refused'),
                        'rpc_session_end_order')
                self.phase = 'closing'
                return
            if kind == SESSION_CLOSED:
                require(operation == 0 and self.phase == 'closing', 'rpc_session_ack_order')
                self.phase = 'acknowledged'
                return
            require(operation == self.operation and operation > 0, 'rpc_operation')
            if kind == REFUSE:
                require(self.phase in ('credit', 'request', 'end', 'executing', 'output')
                        and fields[0] in (1, 2, 3, 4, 5), 'rpc_refusal')
                self.refusal, self.phase = fields[0], 'refused'
            elif kind == REQUEST_CREDIT:
                require(self.phase == 'credit', 'rpc_credit_order')
                remaining = self.request_size - self.request_count
                require((remaining == 0 and fields[0] == 0) or
                        0 < fields[0] <= min(512, remaining), 'rpc_credit_bounds')
                self.permit = fields[0]
                self.phase = 'request' if remaining else 'end'
            elif kind == REQUEST:
                require(self.phase == 'request' and 0 < len(body) <= self.permit,
                        'rpc_request_credit')
                self._hash.update(body)
                self.request_count += len(body)
                self.permit, self.phase = 0, 'credit'
            elif kind == REQUEST_END:
                require(self.phase == 'end' and self.request_count == self.request_size
                        and self._hash.digest() == self.digest, 'rpc_request_digest')
                self.phase = 'executing'
            elif kind == RESULT_META:
                stdout, stderr, stdout_wire, stderr_wire = fields
                require(self.phase == 'executing' and stdout <= OUTPUT_LIMIT and
                        stderr <= OUTPUT_LIMIT and stdout_wire == stdout and
                        stderr <= stderr_wire <= 3 * stderr, 'rpc_output_bounds')
                self.raw_counts, self.wire_sizes = fields[:2], fields[2:]
                self.phase = 'output'
            elif kind in (STDOUT, STDERR):
                index = 0 if kind == STDOUT else 1
                require(self.phase == 'output' and
                        (index == 0 or self.wire_counts[0] == self.wire_sizes[0]) and
                        self.wire_counts[index] + len(body) <= self.wire_sizes[index],
                        'rpc_output_order')
                self.wire_counts[index] += len(body)
            elif kind == STATUS:
                require(self.phase == 'output' and
                        tuple(self.wire_counts) == self.wire_sizes, 'rpc_status_order')
                self.returncode, self.phase = fields[0], 'idle'
            else:
                raise RPCError('rpc_kind')

    def terminal_eof(self):
        require(not self.failed and self.phase == 'acknowledged', 'rpc_terminal_order')
        self.phase = 'closed'

    def snapshot(self):
        return dict(phase=self.phase, operation=self.operation, deadline_ns=self.deadline,
                    request_size=self.request_size, request_count=self.request_count,
                    raw_counts=self.raw_counts, wire_sizes=self.wire_sizes,
                    wire_counts=tuple(self.wire_counts), returncode=self.returncode,
                    refusal=self.refusal, failed=self.failed)
