"""Internal coding clock/packet branch. No provider launcher or caller clock."""
import hashlib
import os
from pathlib import Path
import time

from selected_native.frames import FrameError,parse
from trial_owned_workers import Unavailable
from trial_worker_protocol import ProtocolError,encoded
from trial_worker_transport import WorkerTransport
from trial_coding_protocol import (T_LIMIT,coding_packet,validate_start,
                                  validate_timing,validate_hooks,validate_coding_end)


class CodingTransport(WorkerTransport):
    def __init__(self,domain,run_id,source_manifest_sha256,intent_sha256):
        super().__init__(domain,run_id,source_manifest_sha256,intent_sha256)
        self.binding=dict(run_id=run_id,source_manifest_sha256=source_manifest_sha256)
        self.start_ready=False
        self.start_record=None
        self.start_ns,self.read_ns=None,None
        self.clock_verified=False
        self.coding_unknown=set()
        self.wire=hashlib.sha256();self.wire_bytes,self.timings=0,0
        self.hook_snapshot=None
        self.coding_end=None
        self._pending_end=None

    def _verify_clock(self):
        if self.clock_verified:
            return True
        if not self.domain.verified('capture'):
            return False
        child=self.domain.children['capture'];group=self.domain.groups['capture']
        ident=child.namespace_identity;pid=ident['host_init_pid']
        acquired=[]
        try:
            before=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            if int(before)!=ident['host_init_start_ticks'] or Path(f'/proc/{pid}/cgroup').read_text().strip()!='0::'+group.relative:
                raise Unavailable('coding_clock_namespace_unknown')
            identities={}
            for owner,base in [('controller','/proc/self'),('capture',f'/proc/{pid}')]:
                for name in ('time','time_for_children'):
                    fd=os.open(base+'/ns/'+name,os.O_RDONLY|os.O_CLOEXEC)
                    acquired.append(fd);identities[owner+'_'+name]=os.fstat(fd).st_ino
            if len(set(identities.values()))!=1:
                raise Unavailable('coding_clock_namespace_unknown')
            after=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            if before!=after or Path(f'/proc/{pid}/cgroup').read_text().strip()!='0::'+group.relative:
                raise Unavailable('coding_clock_namespace_unknown')
            child.namespace_fds.extend(acquired);acquired=[]
            ident['clock_domain']=identities
            self.clock_verified=True
            return True
        except (OSError,ValueError,KeyError,IndexError):
            raise Unavailable('coding_clock_namespace_unknown') from None
        finally:
            close_unknown=False
            for fd in acquired:
                try:
                    os.close(fd)
                except OSError:
                    close_unknown=True
            if close_unknown:
                child.acquisition_observations.append('worker_descriptor_close_unknown')
                raise Unavailable('coding_clock_descriptor_unknown') from None

    def gate_capture(self,input_bytes):
        # Parent's ordering/input checks run before acquiring additional FDs.
        if not self.ready or self.gated or type(input_bytes) is not bytes or len(input_bytes)>8500:
            raise Unavailable('worker_gate_order_or_input_bounds')
        if not self._verify_clock():
            return False
        return super().gate_capture(input_bytes)

    def _startup(self):
        if self.start_ready:
            return
        child=self.domain.children['capture'];data=child.buffers['stderr']
        if b'\n' not in data and 'stderr' not in child.eof:
            return
        self.start_ready=True
        try:
            line=bytes(data).split(b'\n',1)[0]
            self.start_record=validate_start(parse(line),**self.binding)
            self.start_ns=self.start_record['start_monotonic_ns']
        except (FrameError,ProtocolError):
            self.coding_unknown.add('capture_timing_unknown')

    def _read(self,role,name,block):
        super()._read(role,name,block)
        if role=='capture' and name=='stderr':
            self._startup()

    def _capture_capacity(self):
        if not self.start_ready:
            return 0
        return min(65536,max(0,128*1024-len(self.queue)-T_LIMIT-10))

    def _capture_receive(self,fd,capacity):
        block=os.read(fd,capacity)
        self.read_ns=time.monotonic_ns()
        return block

    def _data_packets(self,admitted):
        if self.start_ns is not None and self.read_ns is not None and self.start_ns>self.read_ns:
            # Keep the one origin bound to every T. A contradictory read cannot
            # become an invented elapsed interval or change that origin.
            self.read_ns=None;self.coding_unknown.add('capture_timing_unknown')
        if self.start_ns is None or self.read_ns is None:
            self.coding_unknown.add('capture_timing_unknown')
        timing=dict(schema='cairn.worker-capture-timing/1',**self.binding,
            start_monotonic_ns=self.start_ns,received_monotonic_ns=self.read_ns,
            offset=self.stream_bytes,bytes=len(admitted),sha256=hashlib.sha256(admitted).hexdigest(),
            observations=['capture_timing_unknown'] if self.coding_unknown else [])
        try:
            timing=validate_timing(timing,**self.binding)
            framed=coding_packet(b'T',encoded(timing))+coding_packet(b'D',admitted)
        except ProtocolError as exc:
            raise Unavailable(str(exc)) from None
        self.wire.update(framed);self.wire_bytes+=len(framed);self.timings+=1
        return framed

    def capture_report(self):
        child=self.domain.children['capture']
        if 'stderr' not in child.eof:
            raise Unavailable('worker_capture_metadata_not_drained')
        lines=bytes(child.buffers['stderr']).split(b'\n')
        # Malformed startup may retain data with unknown timing; it must not be
        # mistaken for a final report. There are exactly two control records.
        if len(lines)!=3 or lines[-1]!=b'':
            raise Unavailable('worker_capture_metadata_unknown')
        try:
            final=parse(lines[1])
        except FrameError:
            raise Unavailable('worker_capture_metadata_unknown') from None
        if (type(final) is not dict or set(final)!={'kind','run_id','actor_wait','input_bytes','input_bytes_written','observations'} or
            final['kind']!='ACTOR_WAIT' or final['run_id']!=self.run_id):
            raise Unavailable('worker_capture_metadata_unknown')
        return dict(start=self.start_record,final=final)

    def end_capture(self,actor_wait,*,input_bytes,input_bytes_written,observations=(),hook_measurements):
        try:
            self.hook_snapshot=validate_hooks(hook_measurements,**self.binding)
        except ProtocolError as exc:
            raise Unavailable(str(exc)) from None
        try:
            super().end_capture(actor_wait,input_bytes=input_bytes,input_bytes_written=input_bytes_written,observations=observations)
        except Unavailable:
            self._pending_end=None
            raise
        self.coding_end=self._pending_end;self._pending_end=None
        return self.coding_end

    def _terminal_packets(self,value):
        hook=coding_packet(b'H',encoded(self.hook_snapshot))
        digest=self.wire.copy();digest.update(hook)
        unknown=self.coding_unknown|set(self.hook_snapshot['observations'])
        end={**value,'schema':'cairn.worker-coding-end-observation/1','wire_sha256':digest.hexdigest(),
            'wire_bytes':self.wire_bytes+len(hook),'timing_packets':self.timings,
            'hook_packet_sha256':hashlib.sha256(hook).hexdigest(),'coding_observations':sorted(unknown)}
        try:
            end=validate_coding_end(end,**self.binding)
        except ProtocolError as exc:
            raise Unavailable(str(exc)) from None
        self._pending_end=end
        return hook+coding_packet(b'E',encoded(end))

    def forget_transient(self):
        super().forget_transient()
        self.hook_snapshot=None
        self._pending_end=None
