"""Internal owned-worker wire boundary; no launcher, admission or validity API."""
import hashlib
import os
import selectors
import uuid

from selected_native.frames import FrameError, parse
from trial_owned_workers import Unavailable

from trial_worker_protocol import OBSERVATIONS, ProtocolError, encoded, validate_end
from trial_worker_protocol import packet as protocol_packet, wait_pair as protocol_wait_pair


def packet(tag,payload):
    try:
        return protocol_packet(tag,payload)
    except ProtocolError as exc:
        raise Unavailable(str(exc)) from None


def wait_pair(value):
    try:
        return protocol_wait_pair(value)
    except ProtocolError:
        raise Unavailable('worker_wait_inconsistent') from None


class WorkerTransport:
    """Own bounded channels of domain-bound workers created by fixed root builders.

    The object contains no argv factory or native grant. Root owns actual
    actor metadata interpretation, source/profile acceptance and cell policy.
    """
    def __init__(self,domain,run_id,source_manifest_sha256,intent_sha256,*,stream_limit=4*1024*1024):
        try:
            canonical = type(run_id) is str and str(uuid.UUID(run_id)) == run_id
        except ValueError:
            canonical = False
        if not canonical:
            raise Unavailable('worker_run_identity')
        for value in [source_manifest_sha256,intent_sha256]:
            if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise Unavailable('worker_source_binding')
        if type(stream_limit) is not int or not 0<stream_limit<=64*1024*1024:
            raise Unavailable('worker_stream_bounds')
        self.domain,self.run_id=domain,run_id
        self.source_manifest_sha256,self.intent_sha256=source_manifest_sha256,intent_sha256
        self.stream_limit=stream_limit
        self.queue=bytearray()
        self.input=bytearray()
        self.auxiliary={}
        self.hash,self.stream_bytes=hashlib.sha256(),0
        self.observed_hash,self.observed_bytes=hashlib.sha256(),0
        self.budget_exhausted,self.transport_loss=False,False
        self.ready,self.gated,self.ended=False,False,False
        self.frozen_input_bytes=None

    def messages(self,role):
        child=self.domain.children[role]
        try:
            return [parse(line) for line in child.buffers['stdout'].split(b'\n')[:-1]]
        except FrameError:
            raise Unavailable('worker_control_malformed') from None

    def _read(self,role,name,block):
        child=self.domain.children[role]
        if not block:
            child.eof.add(name)
            return
        if role=='capture' and name=='stdout':
            if not self.gated or self.ended:
                raise Unavailable('worker_stream_order')
            self.observed_bytes+=len(block)
            self.observed_hash.update(block)
            if self.observed_bytes>self.stream_limit+128*1024:
                self.transport_loss=True
                raise Unavailable('stream_transport_bounds')
            admitted=block[:max(0,self.stream_limit-self.stream_bytes)]
            if len(self.queue)+len(admitted)+5>128*1024:
                self.transport_loss=True
                raise Unavailable('stream_transport_bounds')
            if admitted:
                self.queue.extend(packet(b'D',admitted))
                self.hash.update(admitted)
                self.stream_bytes+=len(admitted)
            if len(admitted)!=len(block) and not self.budget_exhausted:
                self.budget_exhausted=True
                raise Unavailable('worker_stream_budget_exhausted')
        else:
            child.buffers[name].extend(block)
            ceiling=128*1024 if role=='capture' and name=='stderr' else 8192
            if len(child.buffers[name])>ceiling:
                raise Unavailable('worker_control_bounds')

    def step(self,timeout=.005):
        try:
            self._step(timeout)
        except (OSError,ValueError,KeyError,RecursionError):
            self.transport_loss=True
            raise Unavailable('worker_channel_or_namespace_unknown') from None

    def _step(self,timeout):
        if (self.budget_exhausted or self.transport_loss) and 'capture' not in self.domain.stopped:
            raise Unavailable('worker_stream_stop_required')
        live={role:child for role,child in self.domain.children.items() if role not in self.domain.closed}
        if type(timeout) not in (int,float) or not 0<=timeout<=.05:
            raise Unavailable('worker_poll_bounds')
        with selectors.DefaultSelector() as selector:
            for role,child in live.items():
                for name,stream in [('stdout',child.process.stdout),('stderr',child.process.stderr),('status',child.status_fd)]:
                    if role=='capture' and name=='stdout' and len(self.queue)>=128*1024-5:
                        continue
                    if name not in child.eof:
                        fd=stream if type(stream) is int else stream.fileno()
                        selector.register(fd,selectors.EVENT_READ,(role,name))
            for key,_ in selector.select(timeout):
                role,name=key.data
                capacity=min(65536,128*1024-len(self.queue)-5) if role=='capture' and name=='stdout' else 65536
                self._read(role,name,os.read(key.fd,capacity))
        for role,child in live.items():
            child.observe_wait()
        sink=live.get('composition')
        if sink:
            messages=self.messages('composition')
            if messages and not self.ready:
                expected=dict(kind='READY',run_id=self.run_id,intent_sha256=self.intent_sha256)
                if messages[0]!=expected:
                    raise Unavailable('worker_intent_unknown')
                if self.domain.verified('composition'):
                    self.ready=True
            if self.queue and self.ready:
                try:
                    count=os.write(sink.process.stdin.fileno(),memoryview(self.queue)[:65536])
                    del self.queue[:count]
                except BlockingIOError:
                    pass
            elif self.ended and not sink.process.stdin.closed:
                sink.process.stdin.close()
        for role,pending in self.auxiliary.items():
            child=live.get(role)
            if child is None:
                continue
            if pending:
                try:
                    count=os.write(child.process.stdin.fileno(),pending)
                    del pending[:count]
                except BlockingIOError:
                    pass
            elif not child.process.stdin.closed:
                child.process.stdin.close()
        capture=live.get('capture')
        if capture and self.gated and self.input:
            try:
                count=os.write(capture.process.stdin.fileno(),self.input)
                del self.input[:count]
            except BlockingIOError:
                pass
        if capture and self.gated and not self.input and not capture.process.stdin.closed:
            capture.process.stdin.close()

    def gate_auxiliary(self,role,payload):
        if role not in ('diagnostic','export') or role in self.auxiliary or type(payload) is not bytes or len(payload)>65536:
            raise Unavailable('worker_auxiliary_bounds_or_order')
        if not self.domain.verified(role):
            return False
        self.auxiliary[role]=bytearray(payload)
        return True

    def closed_auxiliary(self,role):
        if role not in ('diagnostic','export') or role not in self.auxiliary or role not in self.domain.closed:
            return None
        child=self.domain.children[role];close=self.domain.closed[role]
        if (self.auxiliary[role] or not child.process.stdin.closed or not {'stdout','stderr'}<=child.eof or
            close['errors'] or close['namespace_identity'] is None or close['released'] is not True or
            close['wait'] is None or any(value is None for value in close['wait'].values()) or close['wait']['returncode']!=0):
            return None
        messages=self.messages(role)
        if len(messages)!=1 or child.buffers['stdout'][-1:]!=b'\n':
            raise Unavailable('worker_auxiliary_return_unknown')
        return dict(declaration=messages[0],wait=close['wait'],released=True,
                    namespace_identity=close['namespace_identity'])

    def capture_report(self):
        child=self.domain.children['capture']
        if 'stderr' not in child.eof:
            raise Unavailable('worker_capture_metadata_not_drained')
        data=child.buffers['stderr']
        try:
            value=parse(bytes(data).removesuffix(b'\n'))
        except FrameError:
            raise Unavailable('worker_capture_metadata_unknown') from None
        if (type(value) is not dict or set(value)!={'kind','run_id','actor_wait','input_bytes','input_bytes_written','observations'} or
            value['kind']!='ACTOR_WAIT' or value['run_id']!=self.run_id):
            raise Unavailable('worker_capture_metadata_unknown')
        # Root's fixed capture builder owns this declaration; E checks its
        # bounded shape while actual capture wait/release stays independent.
        return value

    def gate_capture(self,input_bytes):
        if not self.ready or self.gated or type(input_bytes) is not bytes or len(input_bytes)>8500:
            raise Unavailable('worker_gate_order_or_input_bounds')
        if not self.domain.verified('capture'):
            return False
        self.frozen_input_bytes=len(input_bytes)
        self.input=bytearray(b'G'+input_bytes)
        self.gated=True
        return True

    def end_capture(self,actor_wait,*,input_bytes,input_bytes_written,observations=()):
        if not self.gated or self.ended or 'capture' not in self.domain.closed:
            raise Unavailable('worker_end_order')
        close=self.domain.closed['capture']
        capture=self.domain.children['capture']
        if not {'stdout','stderr'}<=capture.eof:
            raise Unavailable('worker_stream_not_drained')
        if actor_wait is not None:
            if type(actor_wait) is not dict or set(actor_wait)!={'pid','start_ticks','boot_id','raw_status','returncode'}:
                raise Unavailable('worker_actor_wait_shape')
            for key in ('pid','start_ticks'):
                if actor_wait[key] is not None and (type(actor_wait[key]) is not int or actor_wait[key]<=0):
                    raise Unavailable('worker_actor_identity')
            boot=actor_wait['boot_id']
            try:
                canonical=boot is None or (type(boot) is str and str(uuid.UUID(boot))==boot)
            except ValueError:
                canonical=False
            if not canonical:
                raise Unavailable('worker_actor_identity')
            wait_pair(actor_wait)
        if (type(observations) not in (list,tuple) or
            not all(type(v) is str and v in OBSERVATIONS for v in observations) or len(observations)>len(OBSERVATIONS)):
            raise Unavailable('worker_observation_bounds')
        if (input_bytes is not None and (type(input_bytes) is not int or not 0<=input_bytes<=8500)) or (
            input_bytes_written is not None and (type(input_bytes_written) is not int or input_bytes is None or not 0<=input_bytes_written<=input_bytes)):
            raise Unavailable('worker_input_observation')
        if input_bytes is not None and input_bytes!=self.frozen_input_bytes:
            raise Unavailable('worker_input_binding')
        input_bytes=self.frozen_input_bytes
        facts=set(observations)
        if self.budget_exhausted:
            facts.add('stream_budget_exhausted')
        if self.transport_loss:
            facts.add('stream_transport_unknown')
        if actor_wait is None or any(v is None for v in actor_wait.values()):
            facts.add('actor_wait_unknown')
        if close['wait'] is None or any(v is None for v in close['wait'].values()):
            facts.add('capture_wait_unknown')
        if close['released'] is not True or close['errors']:
            facts.add('capture_release_unknown')
        if input_bytes is None or input_bytes_written!=input_bytes:
            facts.add('input_delivery_incomplete')
        value=dict(schema='cairn.worker-end-observation/1',run_id=self.run_id,
            source_manifest_sha256=self.source_manifest_sha256,stream_sha256=self.hash.hexdigest(),
            stream_bytes=self.stream_bytes,observed_stream_sha256=self.observed_hash.hexdigest(),
            observed_stream_bytes=self.observed_bytes,actor_wait=actor_wait,capture_wait=close['wait'],
            capture_released=close['released'],input_bytes=input_bytes,input_bytes_written=input_bytes_written,
            observations=sorted(facts),capture_cleanup_observations=close['errors'].copy())
        if actor_wait is not None:
            value['actor_wait']=actor_wait.copy()
        if close['wait'] is not None:
            value['capture_wait']=close['wait'].copy()
        try:
            value=validate_end(value,run_id=self.run_id,source_manifest_sha256=self.source_manifest_sha256)
        except ProtocolError as exc:
            raise Unavailable(str(exc)) from None
        terminal_packet=packet(b'E',encoded(value))
        if len(self.queue)+len(terminal_packet)>128*1024:
            raise Unavailable('stream_transport_bounds')
        self.queue.extend(terminal_packet)
        self.ended=True
        return value

    def closed_sink(self):
        if 'composition' not in self.domain.closed:
            return None
        close=self.domain.closed['composition']
        child=self.domain.children['composition']
        if (not self.ready or not self.ended or self.queue or not {'stdout','stderr'}<=child.eof or
            close['namespace_identity'] is None or not child.process.stdin.closed or close['errors'] or
            close['released'] is not True or close['wait'] is None or any(value is None for value in close['wait'].values()) or close['wait']['returncode']!=0):
            return None
        messages=self.messages('composition')
        candidates=[value for value in messages if type(value) is dict and value.get('kind')=='CLOSED']
        if len(candidates)!=1 or len(messages)!=2 or child.buffers['stdout'][-1:]!=b'\n':
            raise Unavailable('worker_sink_return_unknown')
        receipt=candidates[0]
        if (set(receipt)!={'kind','run_id','intent_sha256','outcome','manifest_sha256','selected_bytes'} or
            receipt['run_id']!=self.run_id or receipt['intent_sha256']!=self.intent_sha256 or
            receipt['outcome'] not in ('success_retention','failure_evidence') or
            type(receipt['selected_bytes']) is not int or not 0<receipt['selected_bytes']<=64*1024*1024 or
            type(receipt['manifest_sha256']) is not str or len(receipt['manifest_sha256'])!=64 or
            any(c not in '0123456789abcdef' for c in receipt['manifest_sha256'])):
            raise Unavailable('worker_sink_return_unknown')
        return dict(receipt=receipt,wait=close['wait'],released=True,
                    namespace_identity=close['namespace_identity'])

    def forget_transient(self):
        """Runner finally hook after selecting receipts; no raw/input retention."""
        self.input.clear()
        self.queue.clear()
        for pending in self.auxiliary.values():
            pending.clear()
        for child in self.domain.children.values():
            for buffer in child.buffers.values():
                buffer.clear()
