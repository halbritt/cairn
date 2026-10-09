"""Pure coding T/H/E projection; matching hashes are not runtime authentication."""
import hashlib
import math
import struct
from selected_native.frames import FrameError,parse
from trial_worker_protocol import (ProtocolError,encoded,integer,hash_value,uuid_value,
                                   codes,validate_end)
T_LIMIT=1024
H_LIMIT=65536
QUEUE_LIMIT=128*1024
TIME_LIMIT=2**63-1
CODING_OBSERVATIONS=frozenset({'capture_timing_unknown','hook_measurements_unknown'})


def binding(value,run_id,source_manifest_sha256):
    if (not uuid_value(run_id) or not hash_value(source_manifest_sha256) or
        value.get('run_id')!=run_id or value.get('source_manifest_sha256')!=source_manifest_sha256):
        raise ProtocolError('coding_packet_binding')


def parsed(payload,limit):
    if type(payload) is not bytes or len(payload)>limit:
        raise ProtocolError('coding_packet_bounds')
    try:
        return parse(payload)
    except FrameError:
        raise ProtocolError('coding_packet_malformed') from None


def coding_packet(tag,payload):
    limit=T_LIMIT if tag==b'T' else H_LIMIT
    if tag not in (b'T',b'D',b'H',b'E') or type(payload) is not bytes or len(payload)>limit:
        raise ProtocolError('coding_packet_bounds')
    return tag+struct.pack('!I',len(payload))+payload


def validate_start(value,*,run_id,source_manifest_sha256):
    if (type(value) is not dict or set(value)!={'kind','schema','run_id','source_manifest_sha256','start_monotonic_ns'} or
        value['kind']!='CAPTURE_START' or value['schema']!='cairn.worker-capture-start/1' or
        not integer(value['start_monotonic_ns'],TIME_LIMIT)):
        raise ProtocolError('capture_start_unknown')
    binding(value,run_id,source_manifest_sha256)
    if len(encoded(value))>T_LIMIT:
        raise ProtocolError('coding_packet_bounds')
    return value.copy()


def validate_timing(value,*,run_id,source_manifest_sha256):
    fields={'schema','run_id','source_manifest_sha256','start_monotonic_ns','received_monotonic_ns',
            'offset','bytes','sha256','observations'}
    if type(value) is not dict or set(value)!=fields or value['schema']!='cairn.worker-capture-timing/1':
        raise ProtocolError('coding_timing_shape')
    binding(value,run_id,source_manifest_sha256)
    start,received=value['start_monotonic_ns'],value['received_monotonic_ns']
    if any(v is not None and not integer(v,TIME_LIMIT) for v in (start,received)) or (
        start is not None and received is not None and received<start):
        raise ProtocolError('capture_timing_unknown')
    observations=codes(value['observations'],{'capture_timing_unknown'})
    if (start is None or received is None) and 'capture_timing_unknown' not in observations:
        raise ProtocolError('capture_timing_unknown')
    if (not integer(value['offset'],64*1024*1024) or not integer(value['bytes'],65536,1) or
        value['offset']+value['bytes']>64*1024*1024 or not hash_value(value['sha256']) or len(encoded(value))>T_LIMIT):
        raise ProtocolError('coding_packet_bounds')
    return {**value,'observations':observations}


def validate_hooks(value,*,run_id,source_manifest_sha256):
    if type(value) is not dict or set(value)!={'schema','run_id','source_manifest_sha256','rows','observations'} or value['schema']!='cairn.worker-hook-measurements/1':
        raise ProtocolError('coding_hook_shape')
    binding(value,run_id,source_manifest_sha256)
    observations=codes(value['observations'],{'hook_measurements_unknown'})
    rows=value['rows'];selected=None
    if rows is None:
        if 'hook_measurements_unknown' not in observations:
            raise ProtocolError('hook_measurements_unknown')
    else:
        if type(rows) is not list or len(rows)>128:
            raise ProtocolError('coding_hook_bounds')
        selected=[]
        keys={'event','stdout_sha256','stdout_bytes','stderr_bytes','process_seconds','exit_code','memory_call_attempts'}
        counts={'search','pull','pull-evidence','history','other'}
        for row in rows:
            if type(row) is not dict or set(row)!=keys:
                raise ProtocolError('coding_hook_shape')
            if (row['event'] not in ('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure') or
                not hash_value(row['stdout_sha256']) or any(not integer(row[k],10**9) for k in ('stdout_bytes','stderr_bytes')) or
                type(row['process_seconds']) not in (int,float) or not 0<=row['process_seconds']<=1e12 or not math.isfinite(row['process_seconds']) or
                not integer(row['exit_code'],255,-255) or type(row['memory_call_attempts']) is not dict or
                set(row['memory_call_attempts'])!=counts or any(not integer(n,10**9) for n in row['memory_call_attempts'].values())):
                raise ProtocolError('coding_hook_bounds')
            selected.append({**row,'memory_call_attempts':row['memory_call_attempts'].copy()})
    result={**value,'rows':selected,'observations':observations}
    if len(encoded(result))>H_LIMIT:
        raise ProtocolError('coding_packet_bounds')
    return result


def wire_ceiling(stream_bytes,timing_packets):
    if not integer(stream_bytes,64*1024*1024) or not integer(timing_packets,stream_bytes) or (stream_bytes>0 and timing_packets==0):
        raise ProtocolError('coding_wire_bounds')
    # Per nonempty D: <=1024 T payload +two5-byte headers. One H <=65536+5.
    return stream_bytes+timing_packets*(T_LIMIT+10)+H_LIMIT+5


def validate_coding_end(value,*,run_id,source_manifest_sha256):
    extras={'wire_sha256','wire_bytes','timing_packets','hook_packet_sha256','coding_observations'}
    if type(value) is not dict or value.get('schema')!='cairn.worker-coding-end-observation/1' or not extras<=set(value):
        raise ProtocolError('coding_end_shape')
    common={k:v for k,v in value.items() if k not in extras};common['schema']='cairn.worker-end-observation/1'
    common=validate_end(common,run_id=run_id,source_manifest_sha256=source_manifest_sha256)
    observations=codes(value['coding_observations'],CODING_OBSERVATIONS)
    count=value['timing_packets'];size=value['wire_bytes']
    maximum=wire_ceiling(common['stream_bytes'],count)
    if (not integer(size,maximum) or size<common['stream_bytes']+count*10+5 or
        not hash_value(value['wire_sha256']) or not hash_value(value['hook_packet_sha256']) or len(encoded(value))>H_LIMIT):
        raise ProtocolError('coding_wire_bounds')
    return {**common,'schema':value['schema'],**{k:value[k] for k in extras if k!='coding_observations'},'coding_observations':observations}


def decode_timing(payload,**binding_args):
    return validate_timing(parsed(payload,T_LIMIT),**binding_args)


def decode_hooks(payload,**binding_args):
    return validate_hooks(parsed(payload,H_LIMIT),**binding_args)


def decode_coding_end(payload,**binding_args):
    return validate_coding_end(parsed(payload,H_LIMIT),**binding_args)


class CodingDecoder:
    """One pending T, running digests and selected H; no packet history/clock."""
    def __init__(self,run_id,source_manifest_sha256):
        self.binding=dict(run_id=run_id,source_manifest_sha256=source_manifest_sha256)
        self.pending,self.hooks,self.end=None,None,None
        self.wire,self.stream=hashlib.sha256(),hashlib.sha256()
        self.wire_bytes,self.stream_bytes,self.timings=0,0,0
        self.origin,self.last=None,None
        self.have_origin=False
        self.observations=set()
        self.poisoned=False

    def feed(self,tag,payload):
        if self.poisoned:
            raise ProtocolError('coding_decoder_poisoned')
        try:
            return self._feed(tag,payload)
        except ProtocolError:
            self.poisoned=True
            self.pending=None
            raise

    def _feed(self,tag,payload):
        if self.end is not None:
            raise ProtocolError('coding_packet_order')
        framed=coding_packet(tag,payload)
        if tag==b'T':
            if self.pending is not None or self.hooks is not None:
                raise ProtocolError('coding_packet_order')
            value=decode_timing(payload,**self.binding)
            if value['offset']!=self.stream_bytes or (self.have_origin and value['start_monotonic_ns']!=self.origin) or (
                value['received_monotonic_ns'] is not None and self.last is not None and value['received_monotonic_ns']<self.last):
                raise ProtocolError('capture_timing_unknown')
            self.origin=value['start_monotonic_ns'];self.have_origin=True
            if value['received_monotonic_ns'] is not None:
                self.last=value['received_monotonic_ns']
            self.observations.update(value['observations']);self.pending=value
        elif tag==b'D':
            if self.pending is None or self.hooks is not None or len(payload)!=self.pending['bytes'] or hashlib.sha256(payload).hexdigest()!=self.pending['sha256']:
                raise ProtocolError('coding_data_binding')
            value=self.pending;self.pending=None
            self.stream.update(payload);self.stream_bytes+=len(payload);self.timings+=1
            elapsed=None if value['observations'] or value['start_monotonic_ns'] is None or value['received_monotonic_ns'] is None else (value['received_monotonic_ns']-value['start_monotonic_ns'])/1e9
        elif tag==b'H':
            if self.pending is not None or self.hooks is not None:
                raise ProtocolError('coding_packet_order')
            self.hooks=decode_hooks(payload,**self.binding)
            self.hook_sha=hashlib.sha256(framed).hexdigest();self.observations.update(self.hooks['observations'])
        elif tag==b'E':
            if self.pending is not None or self.hooks is None:
                raise ProtocolError('coding_packet_order')
            value=decode_coding_end(payload,**self.binding)
            if (value['stream_bytes']!=self.stream_bytes or value['stream_sha256']!=self.stream.hexdigest() or value['wire_bytes']!=self.wire_bytes or
                value['wire_sha256']!=self.wire.hexdigest() or value['timing_packets']!=self.timings or value['hook_packet_sha256']!=self.hook_sha or
                not self.observations<=set(value['coding_observations'])):
                raise ProtocolError('coding_end_binding')
            self.end=value
            return ('end',value)
        self.wire.update(framed);self.wire_bytes+=len(framed)
        if self.wire_bytes>wire_ceiling(self.stream_bytes+(self.pending['bytes'] if self.pending else 0),self.timings+(1 if self.pending else 0)):
            raise ProtocolError('coding_wire_bounds')
        if tag==b'D':
            return ('data',payload,elapsed)
        return ('timing',None) if tag==b'T' else ('hooks',self.hooks)
