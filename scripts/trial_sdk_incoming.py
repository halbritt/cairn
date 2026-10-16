"""Pure bounded incoming-JSON decoding for sdk-hooks/1; no I/O, process or admission.

Raw wire bytes are hashed and strictly UTF-8 decoded one caller-owned chunk at
a time, then released. Decoding retains at most three undecoded bytes, makes
no raw copy larger than six bytes and never assembles a raw line.
Decoded text, parsed graphs and canonical encoder tokens are working
allocations inside the attempt's aggregate memory limit, which also contains
native, backend and producer processes. Nothing here measures or guarantees
that limit, the owner's raw-queue reservations or kernel buffering.

MAX_DOCUMENT_BYTES is the selected first-repair per-document semantic limit.
It is neither the aggregate raw queue nor permission to hold that many raw
bytes. Larger documents are unsupported here. Constructors merely cannot exceed
NATIVE_STREAM_BYTES, the existing native stream partition; that ceiling is no
claim that larger documents, producers or working sets fit.
Hashes cover exact original bytes. A line hash excludes its LF terminator;
any CR remains frame content and parses only as JSON whitespace. Validation,
digests and owned graphs establish no native origin, schema or delivery.
"""
import codecs
import dataclasses
import hashlib
import json
import math
import re


MAX_DOCUMENT_BYTES = 128 * 1024
NATIVE_STREAM_BYTES = 3 * 1024 * 1024
HTTP_STREAM_BYTES = 768 * 1024
MAX_CHUNK_BYTES = 8192
MAX_DEPTH = 32
MAX_NODES = 131072
INTEGER_BITS = 1024
CARRY_BYTES = 3
# One source character escapes to at most six UTF-8 bytes (\u00XX).
_SLICE_CHARS = MAX_CHUNK_BYTES // 6
_SURROGATE = re.compile('[\ud800-\udfff]')
_LF = re.compile(b'\n')
_OWNER = object()


class IncomingError(ValueError):
    """A fixed refusal code. A refused decoder or assembler cannot be reused."""

    @property
    def code(self):
        return self.args[0]


def _limit(max_bytes):
    if type(max_bytes) is not int or not 0 < max_bytes <= NATIVE_STREAM_BYTES:
        raise IncomingError('incoming_limit')
    return max_bytes


def _view(chunk):
    error = None
    if type(chunk) is bytes:
        view = memoryview(chunk)
    elif type(chunk) is memoryview:
        view = chunk
        if not (view.ndim == 1 and view.itemsize == 1 and view.contiguous
                and view.format in ('B', 'b', 'c')):
            error = 'incoming_chunk'
        else:
            view = view.cast('B')
    else:
        error = 'incoming_chunk'
    if error is None and not 0 < len(view) <= MAX_CHUNK_BYTES:
        error = 'incoming_chunk'
    if error is not None:
        raise IncomingError(error)
    return view


class BodyDecoder:
    """One strict UTF-8 body. feed returns None; finish returns the whole text.

    raw_bytes counts accepted original bytes. raw_sha256 is set only after a
    successful finish. A refusal keeps a labelled consumed-prefix digest, with
    hash_complete true only when finish confirmed no further body bytes.
    """

    def __init__(self, max_bytes):
        self._max = _limit(max_bytes)
        self._sha = hashlib.sha256()
        self._count = 0
        self._carry = b''
        self._parts = []
        self._digest = None
        self._refusal = None
        self._finished = False

    @property
    def max_bytes(self):
        return self._max

    @property
    def raw_bytes(self):
        return self._count

    @property
    def raw_sha256(self):
        return self._digest

    @property
    def carry_bytes(self):
        return len(self._carry)

    @property
    def finished(self):
        return self._finished

    @property
    def refusal(self):
        return None if self._refusal is None else dict(self._refusal)

    def _usable(self):
        if self._refusal is not None or self._finished:
            raise IncomingError('incoming_unusable')

    def _refuse(self, code, complete=False):
        self._refusal = dict(code=code, consumed_prefix_bytes=self._count,
                             consumed_prefix_sha256=self._sha.hexdigest(),
                             hash_complete=complete, origin_attested=False)
        self._parts.clear()
        self._carry = b''
        raise IncomingError(code)

    def feed(self, chunk):
        self._usable()
        try:
            view = _view(chunk)
        except IncomingError as error:
            self._refuse(error.code)
        try:
            self._feed(view)
        finally:
            view.release()

    def _feed(self, view):
        # Limit is checked before any decode, hash or text allocation.
        if self._count + len(view) > self._max:
            self._refuse('incoming_bytes_exceeded')
        error = False
        try:
            rest = view
            if self._carry:
                # Complete at most one pending character from <=3 new bytes.
                head = self._carry + bytes(view[:CARRY_BYTES])
                text, used = codecs.utf_8_decode(head, 'strict', False)
                if used < len(self._carry):
                    pending, rest = head, view[len(view):]
                else:
                    pending, rest = b'', view[used - len(self._carry):]
                    self._parts.append(text)
            else:
                pending = b''
            if rest:
                text, used = codecs.utf_8_decode(rest, 'strict', False)
                self._parts.append(text)
                pending = bytes(rest[used:])
        except UnicodeDecodeError:
            error = True
        if error or len(pending) > CARRY_BYTES:
            self._refuse('incoming_utf8')
        self._carry = pending
        self._sha.update(view)
        self._count += len(view)

    def finish(self):
        self._usable()
        if self._carry:
            self._refuse('incoming_utf8', complete=True)
        text = ''.join(self._parts)
        self._parts.clear()
        self._digest = self._sha.hexdigest()
        self._finished = True
        return text


@dataclasses.dataclass(frozen=True)
class DecodedLine:
    """Complete strict UTF-8 line text with exact LF-excluded wire facts."""
    text: str = dataclasses.field(repr=False)
    raw_bytes: int
    raw_sha256: str
    first_byte_ns: int

    def metadata(self):
        return dict(raw_bytes=self.raw_bytes, raw_sha256=self.raw_sha256,
                    first_byte_ns=self.first_byte_ns, hash_complete=True,
                    lf_excluded=True, origin_attested=False)


class LineAssembler:
    """LF-delimited lines from caller-owned chunks, each at most max_bytes.

    feed returns a generator of DecodedLine. The caller must exhaust it before
    the next feed; an abandoned generator makes the assembler unusable. A line
    keeps the time of the chunk holding its first byte. finish refuses a
    partial line and always closes the assembler.
    """

    def __init__(self, max_bytes):
        self._max = _limit(max_bytes)
        self._decoder = None
        self._first_ns = None
        self._clock = None
        self._busy = False
        self._generation = 0
        self._refusal = None
        self._closed = False

    @property
    def max_bytes(self):
        return self._max

    @property
    def pending_bytes(self):
        """Original bytes of the current partial line; they are not retained."""
        return 0 if self._decoder is None else self._decoder.raw_bytes

    @property
    def carry_bytes(self):
        return 0 if self._decoder is None else self._decoder.carry_bytes

    @property
    def refusal(self):
        return None if self._refusal is None else dict(self._refusal)

    @property
    def closed(self):
        return self._closed

    def _usable(self):
        if self._busy:
            self._fail(dict(code='incoming_feed_abandoned', hash_complete=False,
                            origin_attested=False))
        if self._refusal is not None or self._closed:
            raise IncomingError('incoming_unusable')

    def _fail(self, refusal):
        self._refusal = refusal
        self._decoder = None
        self._busy = False
        raise IncomingError(refusal['code'])

    def feed(self, chunk, first_byte_ns):
        self._usable()
        try:
            view = _view(chunk)
        except IncomingError as error:
            self._fail(dict(code=error.code, hash_complete=False, origin_attested=False))
        if (type(first_byte_ns) is not int or first_byte_ns < 0
                or (self._clock is not None and first_byte_ns < self._clock)):
            view.release()
            self._fail(dict(code='incoming_clock', hash_complete=False, origin_attested=False))
        self._clock = first_byte_ns
        self._busy = True
        self._generation += 1
        return self._lines(view, first_byte_ns, self._generation)

    def _current(self, generation):
        # An outstanding generator stops once the assembler is refused or closed.
        if self._refusal is not None or self._closed or generation != self._generation:
            raise IncomingError('incoming_unusable')

    def _segment(self, view, received):
        if self._decoder is None:
            self._decoder = BodyDecoder(self._max)
            self._first_ns = received
        if view:
            try:
                self._decoder._feed(view)
            except IncomingError:
                refusal = self._decoder.refusal
                self._fail(refusal)

    def _lines(self, view, received, generation):
        completed = False
        try:
            self._current(generation)
            start = 0
            while (match := _LF.search(view, start)) is not None:
                end = match.start()
                del match
                self._segment(view[start:end], received)
                decoder, first = self._decoder, self._first_ns
                self._decoder = self._first_ns = None
                try:
                    text = decoder.finish()
                except IncomingError:
                    self._fail(decoder.refusal)
                yield DecodedLine(text, decoder.raw_bytes, decoder.raw_sha256, first)
                self._current(generation)
                start = end + 1
            if start < len(view):
                self._segment(view[start:], received)
            completed = True
        finally:
            view.release()
            if generation == self._generation:
                if self._refusal is None and not completed:
                    self._refusal = dict(code='incoming_feed_abandoned', hash_complete=False,
                                         origin_attested=False)
                    self._decoder = None
                self._busy = False

    def finish(self):
        self._usable()
        decoder = self._decoder
        self._closed = True
        if decoder is not None:
            self._fail(dict(code='incoming_partial_line',
                            consumed_prefix_bytes=decoder.raw_bytes,
                            consumed_prefix_sha256=decoder._sha.hexdigest(),
                            hash_complete=False, origin_attested=False))


def _strict_int(text):
    # 2**1024 has 309 decimal digits; refuse longer literals before conversion.
    if len(text.lstrip('-')) > 309:
        raise IncomingError('incoming_integer_bounds')
    value = int(text)
    if value.bit_length() > INTEGER_BITS:
        raise IncomingError('incoming_integer_bounds')
    return value


def _strict_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise IncomingError('incoming_nonfinite')
    return value


def _constant(_):
    raise IncomingError('incoming_nonfinite')


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise IncomingError('incoming_duplicate_key')
        out[key] = value
    return out


def _utf8_length(text):
    # parse_document rejects surrogates first. Count scalar widths without
    # allocating encoded scratch that a forwarding caller would need to reserve.
    return sum(1 if ord(char) < 0x80 else 2 if ord(char) < 0x800
               else 3 if ord(char) < 0x10000 else 4 for char in text)


def parse_document(text, raw_bytes, max_bytes=MAX_DOCUMENT_BYTES):
    """Strictly parse one complete JSON object whose wire form had raw_bytes.

    Duplicate keys at any depth, NaN/Infinity, non-finite floats, integers over
    INTEGER_BITS bits, unpaired surrogates in keys or values, depth over
    MAX_DEPTH, more than MAX_NODES values plus keys and trailing data refuse.
    Integers and floats keep their JSON types; nothing is coerced. Widths are
    bounded by bytes, not by unverified schema guesses: every value or key
    costs at least two wire bytes beyond the first.
    """
    _limit(max_bytes)
    if type(text) is not str or type(raw_bytes) is not int:
        raise IncomingError('incoming_document_bytes')
    if not len(text) <= raw_bytes <= max_bytes:
        raise IncomingError('incoming_document_bytes')
    if _SURROGATE.search(text):
        raise IncomingError('incoming_surrogate')
    if _utf8_length(text) != raw_bytes:
        raise IncomingError('incoming_document_bytes')
    code, value = None, None
    try:
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant,
                           parse_float=_strict_float, parse_int=_strict_int)
    except IncomingError as error:
        code = error.code
    except RecursionError:
        code = 'incoming_depth'
    except (ValueError, TypeError):
        code = 'incoming_json'
    if code is None and type(value) is not dict:
        code = 'incoming_not_object'
    if code is None:
        code = _graph_check(value, min(MAX_NODES, (raw_bytes + 1) // 2))
    if code is not None:
        value = None
        raise IncomingError(code)
    return value


def _graph_check(value, node_limit):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_DEPTH:
            return 'incoming_depth'
        if nodes > node_limit:
            return 'incoming_graph_bounds'
        if type(item) is dict:
            for key, entry in item.items():
                nodes += 1
                if _SURROGATE.search(key):
                    return 'incoming_surrogate'
                pending.append((entry, depth + 1))
        elif type(item) is list:
            pending.extend((entry, depth + 1) for entry in item)
        elif type(item) is str and _SURROGATE.search(item):
            return 'incoming_surrogate'
    return None


class OwnedJSON:
    """One strictly parsed object handed off by the trusted driver.

    An ownership convention between this module and the controller, not
    authentication against in-process callers. take() returns the private
    plain graph exactly once and drops this token's reference.
    """
    __slots__ = ('_graph', '_kind', '_raw_bytes', '_raw_sha256', '_first_byte_ns', '_taken')

    def __init__(self, owner, graph, raw_bytes, raw_sha256, first_byte_ns):
        if owner is not _OWNER:
            raise IncomingError('incoming_owned_construction')
        kind = graph.get('type')
        for name, entry in (('_graph', graph), ('_kind', kind if type(kind) is str else None),
                            ('_raw_bytes', raw_bytes), ('_raw_sha256', raw_sha256),
                            ('_first_byte_ns', first_byte_ns), ('_taken', False)):
            object.__setattr__(self, name, entry)

    def __setattr__(self, name, value):
        raise AttributeError('OwnedJSON is read-only')

    def __delattr__(self, name):
        raise AttributeError('OwnedJSON is read-only')

    def __repr__(self):
        return 'OwnedJSON(kind=%r, raw_bytes=%d, taken=%r)' % (self._kind, self._raw_bytes, self._taken)

    @property
    def kind(self):
        """The outer "type" string, or None when absent or not a string."""
        return self._kind

    @property
    def raw_bytes(self):
        return self._raw_bytes

    @property
    def raw_sha256(self):
        return self._raw_sha256

    @property
    def first_byte_ns(self):
        return self._first_byte_ns

    @property
    def taken(self):
        return self._taken

    def metadata(self):
        return dict(kind=self._kind, raw_bytes=self._raw_bytes, raw_sha256=self._raw_sha256,
                    first_byte_ns=self._first_byte_ns, hash_complete=True,
                    lf_excluded=True, origin_attested=False)

    def take(self):
        if self._taken:
            raise IncomingError('incoming_owned_consumed')
        graph = self._graph
        object.__setattr__(self, '_graph', None)
        object.__setattr__(self, '_taken', True)
        return graph


def validate_line(line, max_bytes=MAX_DOCUMENT_BYTES):
    """Parse a DecodedLine once and wrap the exclusive graph for handoff."""
    if type(line) is not DecodedLine:
        raise IncomingError('incoming_line_type')
    graph = parse_document(line.text, line.raw_bytes, max_bytes)
    return OwnedJSON(_OWNER, graph, line.raw_bytes, line.raw_sha256, line.first_byte_ns)


def _string_pieces(text):
    yield '"'
    for start in range(0, len(text), _SLICE_CHARS):
        yield json.encoder.encode_basestring(text[start:start + _SLICE_CHARS])[1:-1]
    yield '"'


def _scalar(value):
    kind = type(value)
    if value is None:
        return 'null'
    if kind is bool:
        return 'true' if value else 'false'
    if kind is int:
        if value.bit_length() > INTEGER_BITS:
            raise IncomingError('canonical_integer')
        return int.__repr__(value)
    if kind is float:
        if not math.isfinite(value):
            raise IncomingError('canonical_nonfinite')
        return float.__repr__(value)
    raise IncomingError('canonical_shape')


def _pieces(value):
    # Iterative sort_keys / ensure_ascii=False / (',', ':') JSON pieces.
    stack, item, nodes = [], value, 0
    while True:
        nodes += 1 + (len(item) if type(item) is dict else 0)
        if len(stack) > MAX_DEPTH:
            raise IncomingError('canonical_depth')
        if nodes > MAX_NODES:
            raise IncomingError('canonical_graph_bounds')
        kind = type(item)
        if kind is dict:
            for key in item:
                if type(key) is not str:
                    raise IncomingError('canonical_shape')
                if _SURROGATE.search(key):
                    raise IncomingError('canonical_surrogate')
            stack.append([item, sorted(item), 0])
            yield '{'
        elif kind is list:
            stack.append([item, None, 0])
            yield '['
        elif kind is str:
            if _SURROGATE.search(item):
                raise IncomingError('canonical_surrogate')
            yield from _string_pieces(item)
        else:
            yield _scalar(item)
        while stack:
            frame = stack[-1]
            container, keys, index = frame
            if index < (len(container) if keys is None else len(keys)):
                frame[2] = index + 1
                if index:
                    yield ','
                if keys is None:
                    item = container[index]
                else:
                    yield from _string_pieces(keys[index])
                    yield ':'
                    item = container[keys[index]]
                break
            stack.pop()
            yield ']' if keys is None else '}'
        else:
            return


def canonical_chunks(value):
    """UTF-8 chunks of the sdk-hooks/1 canonical JSON, each <= MAX_CHUNK_BYTES.

    Concatenation equals json.dumps(value, ensure_ascii=False, sort_keys=True,
    separators=(',', ':'), allow_nan=False).encode(). Large strings are sliced
    before UTF-8 encoding; no whole canonical byte string exists. Raw scratch
    is one <= MAX_CHUNK_BYTES buffer and one encoded piece; each yielded chunk
    belongs to the caller. Refusal can follow earlier chunks, so size/hash first
    when partial output matters.
    """
    buffer = bytearray()
    for piece in _pieces(value):
        encoded = piece.encode('utf-8')
        if len(buffer) + len(encoded) > MAX_CHUNK_BYTES:
            yield bytes(buffer)
            buffer.clear()
        buffer += encoded
    if buffer:
        yield bytes(buffer)


def canonical_size_sha256(value):
    """Return (byte_count, lowercase_sha256) of the canonical encoding."""
    digest, total = hashlib.sha256(), 0
    for chunk in canonical_chunks(value):
        digest.update(chunk)
        total += len(chunk)
    return total, digest.hexdigest()
