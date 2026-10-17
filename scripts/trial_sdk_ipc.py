"""Pure whole-record framing. Caller owns storage, transport and authentication."""
import struct
from types import MappingProxyType

MAX_SEQUENCE = (1 << 64) - 1
MAX_OPERATION = (1 << 53) - 1
HEADER = struct.Struct('>IBBHQQII')
# kind: (direction, minimum body, maximum body, correlated operation)
TABLE = MappingProxyType({
 1:('N2P_DATA',8,8,True), 2:('N2P_DATA',1,512,True),
 3:('N2P_DATA',0,0,True), 4:('N2P_DATA',1,512,True),
 5:('N2P_DATA',0,0,True), 6:('N2P_DATA',2,2,False),
 7:('N2P_DATA',0,0,False), 8:('N2P_DATA',0,0,True),
 129:('P2N_DATA',0,0,True),130:('P2N_DATA',1,1,True),
 131:('P2N_DATA',2,2,True),132:('P2N_DATA',0,0,False),
 133:('P2N_DATA',1,1,True),134:('P2N_DATA',1,512,False),
 135:('P2N_DATA',0,0,False),136:('P2N_DATA',1,512,False),
 137:('P2N_DATA',8,8,False),138:('P2N_DATA',2,2,False),
 139:('P2N_DATA',1,1,True),201:('N2P_CONTROL',1,1,True),
 202:('P2N_CONTROL',1,1,True),
})
DIRECTIONS = frozenset(row[0] for row in TABLE.values())
TABLE_PROFILE_2 = MappingProxyType({
    **{kind: row for kind, row in TABLE.items() if kind not in (4, 131)},
    9: ('N2P_DATA', 1, 512, True),
    140: ('P2N_DATA', 2, 2, True),
    133: ('P2N_DATA', 5, 5, True),
})

# Python memory-broker protocol only. These are not SDK input/output operations;
# the Node codec/native addon intentionally implement profiles 1 and 2 only.
TABLE_PROFILE_3 = MappingProxyType({
    33: ('C2B', 1, 512, True), 34: ('C2B', 0, 0, True),
    65: ('B2C', 1, 512, True), 66: ('B2C', 1, 512, True),
    67: ('B2C', 4, 4, True), 68: ('B2C', 1, 1, True),
})
# Python command-service transcript. Existing profile encodings are unchanged;
# profile 3 kind 67 remains the original signed four-byte return code.
TABLE_PROFILE_4 = MappingProxyType({
    1: ('S2P', 0, 0, False), 2: ('P2S', 65, 65, True),
    3: ('P2S', 1, 512, True), 4: ('P2S', 0, 0, True),
    5: ('S2P', 40, 40, True), 6: ('P2S', 8, 8, True),
    7: ('P2S', 21, 21, True), 8: ('S2P', 24, 24, True),
    9: ('S2P', 192, 192, True), 10: ('P2S', 11, 11, True),
    11: ('S2P', 1, 512, True), 12: ('S2P', 1, 512, True),
    13: ('S2P', 8, 8, True), 14: ('S2P', 1, 1, True),
    15: ('P2S', 1, 1, True), 16: ('P2S', 8, 8, True),
    17: ('S2P', 9, 9, True), 18: ('P2S', 0, 0, False),
    19: ('S2P', 0, 0, False), 20: ('S2P', 8, 8, True),
})
PROFILES = MappingProxyType({1: TABLE, 2: TABLE_PROFILE_2, 3: TABLE_PROFILE_3,
                             4: TABLE_PROFILE_4})

class CodecError(ValueError):
    pass

class CreditDenied(CodecError):
    pass

def _view(value, writable=False):
    if (type(value) is not memoryview or value.ndim != 1 or value.format != 'B'
            or not value.c_contiguous or (writable and value.readonly)):
        raise CodecError('frame_view')
    return value

class Frame:
    """Borrowed immutable metadata; view aliases require external discipline."""
    def __init__(self, owner, record, kind, sequence, operation):
        self._owner, self._record = owner, record.toreadonly()
        self._metadata = (kind, sequence, operation)
    @property
    def kind(self): return self._metadata[0]
    @property
    def sequence(self): return self._metadata[1]
    @property
    def operation(self): return self._metadata[2]
    @property
    def record(self):
        if self._record is None: raise CodecError('frame_released')
        return self._record.toreadonly()
    @property
    def body(self): return self.record[32:]
    def release(self):
        if self._record is None: raise CodecError('frame_released')
        size = len(self._record)
        self._record = None
        self._owner._finish(size)

class Codec:
    """One endpoint direction, one outstanding frame, no reentrant operations.

    first_sequence is a caller-selected test/checkpoint seam, not resume authority.
    A fresh production endpoint starts at one. reserve/release are trusted hooks.
    """
    def __init__(self, direction, reserve, release, first_sequence=1, profile=1):
        if (type(direction) is not str
                or not callable(reserve) or not callable(release)
                or type(first_sequence) is not int or not 1 <= first_sequence <= MAX_SEQUENCE
                or type(profile) is not int or profile not in PROFILES
                or direction not in {row[0] for row in PROFILES[profile].values()}):
            raise CodecError('frame_configuration')
        self._direction, self._reserve, self._release = direction, reserve, release
        self._next = first_sequence
        self._profile = profile
        self._table = PROFILES[profile]
        self._busy = False
        self.failed = False
        self.cleanup_unknown = False

    def _start(self):
        if self.failed: raise CodecError('frame_failed')
        if self._busy: raise CodecError('frame_busy')
        if self._next is None: raise CodecError('frame_sequence_exhausted')
        self._busy = True

    def _check(self, kind, operation, size):
        if type(kind) is not int or kind not in self._table: raise CodecError('frame_kind')
        direction, low, high, correlated = self._table[kind]
        if direction != self._direction: raise CodecError('frame_direction')
        if not low <= size <= high: raise CodecError('frame_length')
        if (type(operation) is not int or not 0 <= operation <= MAX_OPERATION
                or (operation != 0) != correlated):
            raise CodecError('frame_operation')

    def _acquire(self, size):
        try: answer = self._reserve(size)
        except BaseException: raise CodecError('frame_credit_unknown') from None
        if answer is False: raise CreditDenied('frame_credit')
        if answer is not True: raise CodecError('frame_credit_unknown')

    def _finish(self, size):
        # Stay busy during release: callbacks cannot admit another frame early.
        try: self._release(size)
        except BaseException:
            self.cleanup_unknown = self.failed = True
            raise CodecError('frame_release_unknown') from None
        finally: self._busy = False

    def _issue(self, record, kind, operation):
        frame = Frame(self, record, kind, self._next, operation)
        self._next = None if self._next == MAX_SEQUENCE else self._next + 1
        return frame

    def _refuse(self, exc, acquired):
        if not isinstance(exc, CreditDenied): self.failed = True
        if acquired:
            try: self._finish(acquired)
            except CodecError: pass  # cleanup_unknown preserves the second fault.
        else: self._busy = False

    def encode_into(self, destination, kind, operation, body):
        self._start(); acquired = 0
        try:
            destination, body = _view(destination, True), _view(body)
            self._check(kind, operation, len(body))
            size = 32 + len(body)
            if len(destination) != size: raise CodecError('frame_length')
            if destination.obj is body.obj: raise CodecError('frame_alias')
            self._acquire(size); acquired = size
            HEADER.pack_into(destination,0,0x43495043,self._profile,kind,0,self._next,operation,len(body),0)
            destination[32:] = body
            return self._issue(destination,kind,operation)
        except BaseException as exc:
            self._refuse(exc, acquired)
            raise

    def decode(self, record):
        self._start(); acquired = 0
        try:
            record = _view(record)
            if not 32 <= len(record) <= 544: raise CodecError('frame_length')
            magic, version, kind, flags, sequence, operation, size, reserved = HEADER.unpack_from(record)
            if magic != 0x43495043: raise CodecError('frame_magic')
            if version != self._profile: raise CodecError('frame_version')
            if flags or reserved: raise CodecError('frame_reserved')
            if len(record) != 32 + size: raise CodecError('frame_length')
            self._check(kind,operation,size)
            if sequence != self._next: raise CodecError('frame_sequence')
            self._acquire(len(record)); acquired = len(record)
            return self._issue(record,kind,operation)
        except BaseException as exc:
            self._refuse(exc, acquired)
            raise
