"""One service-owned CLI job; not a coordinator, command policy or admission.

The trusted service retains this inert object before start. Popen remains
synchronous and must run outside the callback coordinator. Reservation callbacks
belong to the ORIGINAL shared queue/working-memory/stream owners. No private
pool, spill file or command/source/credential authority is created here.
"""
import codecs
import hashlib
import signal
import sys
import time

from trial_sdk_engine_adapter import CommandTextOutcome, _text_utf8_size
from trial_sdk_native_input import NativeInputRejected
from trial_sdk_native_process_owner import NativeProcessOwner


OUTPUT_LIMIT=1024*1024  # Pinned engine ceiling, separately for stdout and stderr.
STREAM_LIMIT=4*1024*1024  # Existing aggregate ceiling; never a new per-job grant.
CHUNK=512
BLOCK=256
SLOTS=OUTPUT_LIMIT//BLOCK+1
RAW_RESERVATION=3*CHUNK+4*3
# Explicit conservative Unicode fragments + joined text + list/join references.
# CPython object sizes are a logical quote, not whole-process physical-fit proof.
_HEADER=max(sys.getsizeof(''),sys.getsizeof('\U0010ffff'))
_POINTER=sys.getsizeof([None])-sys.getsizeof([])
TEXT_RESERVATION=2*(8*OUTPUT_LIMIT+SLOTS*_HEADER+
                    2*(sys.getsizeof([])+SLOTS*_POINTER)+16*1024)


class CommandJobError(ValueError):
    pass


class _Text:
    """Bounded raw carry; Unicode storage is precharged working allocation."""
    def __init__(self,errors):
        self.errors=errors
        self.carry=b''
        self.parts=[None]*SLOTS
        self.used=0
        self.tail=''
        self.bad=self.ended=False

    def _append(self,text):
        self.tail+=text
        while len(self.tail)>=BLOCK:
            if self.used>=SLOTS:raise CommandJobError('command_text_storage')
            self.parts[self.used]=self.tail[:BLOCK]
            self.used+=1
            self.tail=self.tail[BLOCK:]

    def feed(self,view):
        if self.ended:raise CommandJobError('command_text_ended')
        if self.bad:return
        rest=memoryview(view)
        try:
            try:
                if self.carry:
                    with view[:3] as front:head=self.carry+bytes(front)
                    text,used=codecs.utf_8_decode(head,self.errors,False)
                    if used<len(self.carry):
                        self.carry=head
                        return
                    self._append(text)
                    offset=used-len(self.carry)
                    rest.release();rest=view[offset:]
                if rest:
                    text,used=codecs.utf_8_decode(rest,self.errors,False)
                    self._append(text)
                    with rest[used:] as suffix:self.carry=bytes(suffix)
                else:self.carry=b''
                if len(self.carry)>3:raise CommandJobError('command_decode_carry')
            except UnicodeError:
                # Original engine decodes after wait. Continue bounded capture so
                # output/timeout failures retain their original precedence.
                self.bad=True;self.carry=b'';self.parts=None;self.tail=''
        finally:rest.release()

    def eof(self):
        if self.ended:raise CommandJobError('command_text_ended')
        self.ended=True
        if self.bad:return
        try:
            text,_=codecs.utf_8_decode(self.carry,self.errors,True)
            self._append(text)
        except UnicodeError:
            self.bad=True;self.parts=None;self.tail=''
        finally:self.carry=b''

    def materialize(self):
        if not self.ended:raise CommandJobError('command_text_pending')
        if self.bad:raise UnicodeError('command_stdout_utf8')
        return ''.join(self.parts[i] if i<self.used else self.tail for i in range(self.used+1))


class CommandJob:
    """One retained job. All methods require trusted serialized ownership.

    budgets.reserve(queue,working)->bool and release(queue,working)->None bind
    existing reservations; stream_room()->int and charge_stream(n)->None bind
    the original cumulative traffic meter. Constructor is inert. prepare must
    precede gate dispatch, then start consumes the sole launch. No request object
    is retained by the forward; the builder transfers separately owned body text.
    """
    def __init__(self,budgets,deadline_ns,*,clock=time.monotonic_ns):
        if (type(deadline_ns) is not int or not callable(clock)
                or any(not callable(getattr(budgets,name,None)) for name in
                       ('reserve','release','stream_room','charge_stream'))):
            raise CommandJobError('command_binding')
        self.budgets,self.deadline,self.clock=budgets,deadline_ns,clock
        self.owner=NativeProcessOwner(allow_stdin_epipe=True)
        self._prepared=self._started=self._taken=self._retired=False
        self._reserved=False;self._reservation_unknown=False
        self._raw=self._input=self._text=None
        self._body=None;self._source_offset=self._pending_size=self._pending_offset=0
        self._generated=self._written=0
        self._write_hash=hashlib.sha256()
        self._counts={name:0 for name in ('stdout','stderr')}
        self._hashes={name:hashlib.sha256() for name in self._counts}
        self._eof={name:False for name in self._counts}
        self._last=None;self._primary=None;self._unknown=False
        self._pending=None;self._busy=False;self._close_attempted=False
        self._stdin_terminal=None;self._wait=self._release=None
        self._signal_attempted=False;self._cleanup=[]
        self._working=0

    def _fail(self,code,unknown=False):
        if self._primary is None:self._primary=code
        self._unknown|=unknown
        raise CommandJobError(self._primary)

    def _now(self):
        try:now=self.clock()
        except BaseException:self._fail('command_clock_unknown',True)
        if type(now) is not int or now<0 or (self._last is not None and now<self._last):
            self._fail('command_clock_unknown',True)
        self._last=now
        if now>=self.deadline:self._fail('command_deadline')

    def prepare(self,body):
        if self._prepared or self._primary is not None:self._fail('command_prepare_consumed')
        self._prepared=True
        try:
            self._now()
            if body is not None and type(body) is not str:self._fail('command_body')
            if body is not None and (len(body)>STREAM_LIMIT or _text_utf8_size(body)>STREAM_LIMIT):
                self._fail('command_body_stream_limit')
            self._working=TEXT_RESERVATION+(0 if body is None else sys.getsizeof(body))
            self._reservation_unknown=True
            answer=self.budgets.reserve(RAW_RESERVATION,self._working)
            if answer is False:
                self._reservation_unknown=False
                self._fail('command_credit_refused')
            if answer is not True:self._fail('command_credit_unknown',True)
            self._reserved=True;self._reservation_unknown=False
            self._body=body
            self._raw=bytearray(CHUNK);self._input=bytearray(CHUNK)
            self._text={name:_Text('strict' if name=='stdout' else 'replace') for name in self._counts}
            self._now()
            return self
        except BaseException:
            if self._primary is None:self._primary='command_prepare_unknown'
            self._unknown|=self._reservation_unknown
            raise

    def start(self,argv,cwd,env):
        if self._started or not self._reserved or self._primary is not None:
            self._fail('command_launch_consumed')
        self._started=True
        try:
            self._now();self._pending='launch'
            self.owner.start(argv,cwd,env)
            self._pending=None;self._now()
        except BaseException:
            self._unknown|=self._pending is not None
            if self._primary is None:self._primary='command_launch_unknown'
            raise

    def _room(self):
        room=self.budgets.stream_room()
        if type(room) is not int or not 0<=room<=STREAM_LIMIT:self._fail('command_stream_unknown',True)
        if room==0:self._fail('command_stream_exhausted')
        return min(CHUNK,room)

    def _charge_stream(self,count):
        if self.budgets.charge_stream(count) is not None:self._fail('command_stream_unknown',True)

    def _close_stdin(self,reason):
        if self._close_attempted:return
        self._close_attempted=True;self._stdin_terminal=reason
        self._pending='stdin_close'
        self.owner.close_stdin()
        self._pending=None
        self._body=None;self._pending_size=self._pending_offset=0

    def _write(self):
        if self._close_attempted:return False
        if self._pending_offset==self._pending_size:
            if self._body is None or self._source_offset==len(self._body):
                self._close_stdin('all_written')
                return True
            text=self._body[self._source_offset:self._source_offset+128]
            chunk=text.encode('utf-8')
            self._input[:len(chunk)]=chunk
            self._pending_size=len(chunk);self._pending_offset=0
            self._source_offset+=len(text);self._generated+=len(chunk)
            del chunk,text
        amount=min(self._room(),self._pending_size-self._pending_offset)
        with memoryview(self._input) as backing:
            view=backing[self._pending_offset:self._pending_offset+amount].toreadonly()
            try:
                self._pending='stdin_write'
                try:count=self.owner.write(view)
                except BlockingIOError:
                    self._pending=None
                    return False
                except NativeInputRejected:
                    state=self.owner.snapshot()['native_input']
                    if state['write_unknown'] or not state['input_rejected'] or state['known_written']!=self._written:
                        self._fail('command_input_unknown',True)
                    self._pending=None
                    self._close_stdin('known_epipe')
                    return True
                if type(count) is not int or not 0<count<=len(view):self._fail('command_write_unknown',True)
                self._written+=count
                with view[:count] as accepted:self._write_hash.update(accepted)
                self._pending_offset+=count
                self._charge_stream(count)
                self._pending=None;self._now()
                return True
            finally:view.release()

    def _read(self,name):
        if self._eof[name]:return False
        room=min(self._room(),OUTPUT_LIMIT+1-self._counts[name])
        if room<=0:self._fail('command_output_limit')
        with memoryview(self._raw) as backing:
            view=backing[:room]
            try:
                self._pending=name+'_read'
                count=self.owner.read_into(name,view)
                if count==-1:
                    self._pending=None
                    return False
                if type(count) is not int or not 0<=count<=room:self._fail('command_read_unknown',True)
                if count:
                    self._counts[name]+=count
                    with view[:count] as received:
                        self._hashes[name].update(received)
                        self._charge_stream(count)
                        if self._counts[name]>OUTPUT_LIMIT:
                            self._pending=None;self._fail('command_output_limit')
                        self._text[name].feed(received)
                else:
                    self._eof[name]=True
                    self._text[name].eof()
                self._pending=None;self._now()
                return True
            finally:view.release()

    def step(self):
        if self._busy:self._fail('command_busy')
        if self._primary is not None:raise CommandJobError(self._primary)
        if not self._started:self._fail('command_not_started')
        self._busy=True
        try:
            try:
                self._now();self._pending='wait'
                self._wait=self.owner.observe_wait()
                self._pending=None
                progress=False
                if self._wait is not None and not self._close_attempted:
                    self._close_stdin('peer_ended');progress=True
                if self._wait is None:progress=self._write() or progress
                for name in self._counts:progress=self._read(name) or progress
                if self._wait is not None and all(self._eof.values()) and self._release is None:
                    self._pending='owner_release'
                    self._release=self.owner.release()
                    self._pending=None
                    if self._release.get('released') is not True:self._fail('command_release_unknown',True)
                    progress=True
                self._now()
                return progress
            finally:self._busy=False
        except BaseException:
            self._unknown|=self._pending is not None or self._primary is None
            if self._primary is None:self._primary='command_io_unknown'
            raise

    def take_outcome(self):
        if self._primary is not None:raise CommandJobError(self._primary)
        if self._taken or self._release is None:self._fail('command_outcome_pending')
        self._taken=True
        try:
            self._now()
            stdout=self._text['stdout'].materialize()
            stderr=self._text['stderr'].materialize()
            outcome=CommandTextOutcome(self._wait['returncode'],stdout,stderr,
                                       self._counts['stdout'],self._counts['stderr'])
            self._now()
            return outcome
        except BaseException:
            if self._primary is None:self._primary='command_materialize_failed'
            raise

    def abort(self):
        if self._primary is None:self._primary='command_cancelled'

    def interests(self):
        """Readiness-only borrows; no dup/close/transfer or claimed readiness."""
        if not self._started:return {}
        names=[]
        if self._wait is None:names.append('pidfd')
        if self._primary is None:
            if not self._close_attempted and self._wait is None:names.append('stdin')
            names.extend(name for name in self._counts if not self._eof[name])
        result={}
        for name in names:
            try:result[name]=self.owner.fileno(name)
            except BaseException:
                self._cleanup.append('readiness_unknown')
                self._fail('command_readiness_unknown',True)
        return result

    def cleanup_step(self):
        """Bounded ordinary cleanup; outer owner retains final wall containment."""
        try:self._wait=self.owner.observe_wait()
        except BaseException:self._cleanup.append('wait_unknown')
        state=None
        try:state=self.owner.snapshot()
        except BaseException:self._cleanup.append('owner_state_unknown')
        if self._wait is None and state is not None and state['launch_attempted'] and not self._signal_attempted:
            self._signal_attempted=True
            try:self.owner.signal(1,int(signal.SIGKILL))
            except BaseException:self._cleanup.append('signal_unknown')
        if self._wait is not None and self._release is None:
            try:self._release=self.owner.cleanup_after_wait()
            except BaseException:self._cleanup.append('release_unknown')
        elif state is not None and not state['launch_attempted'] and self._release is None:
            try:self._release=self.owner.cleanup_unlaunched()
            except BaseException:self._cleanup.append('unlaunched_release_unknown')
        return self._release is not None

    def retire(self):
        """Caller first ends ALL outcome/text aliases or transfers their charge.

        This trusted ownership discipline is not a receiver acknowledgement or
        proof of another process wait. Real broker handoff still needs binding.
        """
        if self._retired:raise CommandJobError('command_retire_consumed')
        if self._release is None or self._release.get('released') is not True:
            raise CommandJobError('command_retire_pending')
        self._retired=True
        self._raw=self._input=self._text=self._body=None
        if self._reserved:
            self._reservation_unknown=True
            if self.budgets.release(RAW_RESERVATION,self._working) is not None:
                self._fail('command_credit_release_unknown',True)
            self._reserved=False;self._reservation_unknown=False

    def snapshot(self):
        return dict(primary=self._primary,outcome_unknown=self._unknown,pending=self._pending,
                    prepared=self._prepared,started=self._started,taken=self._taken,retired=self._retired,
                    input_generated=self._generated,input_written=self._written,
                    input_sha256=None if self._unknown else self._write_hash.hexdigest(),
                    stdin_terminal=self._stdin_terminal,
                    streams={name:dict(raw_bytes=self._counts[name],
                                       raw_sha256=None if self._unknown else self._hashes[name].hexdigest(),
                                       hash_complete=self._eof[name] and not self._unknown,
                                       eof=self._eof[name]) for name in self._counts},
                    queue_reservation=RAW_RESERVATION,working_reservation=self._working,
                    reserved=self._reserved,reservation_unknown=self._reservation_unknown,
                    wait=self._wait,release=self._release,cleanup=tuple(self._cleanup),
                    owner=self.owner.snapshot(),api_confirmation=None,qualification='unqualified')
