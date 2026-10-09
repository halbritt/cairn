"""Bounded worker packets and selected E projection. No runtime authority.

Matching identities/hashes here proves shape and declared binding only. Actual
wait, namespace, persistence and kernel release remain external observations.
"""
import json
import os
import struct
import uuid

from selected_native.frames import FrameError, parse

OBSERVATIONS=frozenset({'actor_wait_unknown','capture_wait_unknown','capture_release_unknown',
    'capture_metadata_missing','stream_transport_unknown','input_delivery_incomplete',
    'capture_not_completed','observer_interrupted','stream_budget_exhausted'})
CLEANUP_OBSERVATIONS=frozenset({'owned_launcher_handle_unknown','owned_launcher_stop_unknown',
    'containment_kill_unknown','containment_release_unknown','outer_wait_unknown',
    'worker_close_unknown','worker_stream_close_unknown','worker_descriptor_close_unknown',
    'resource_events_unknown','cgroup_close_unknown','cgroup_descriptor_close_unknown',
    'cgroup_directory_close_unknown','cgroup_directory_removal_unknown','cgroup_parent_close_unknown'})


class ProtocolError(Exception):
    pass


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def packet(tag,payload):
    if type(payload) is not bytes or len(payload)>65536 or tag not in (b'D',b'E'):
        raise ProtocolError('worker_packet_bounds')
    return tag+struct.pack('!I',len(payload))+payload


def hash_value(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def uuid_value(value):
    try:
        return type(value) is str and str(uuid.UUID(value))==value
    except ValueError:
        return False


def integer(value,maximum,minimum=0):
    return type(value) is int and minimum<=value<=maximum


def wait_pair(value):
    raw,code=value['raw_status'],value['returncode']
    if raw is not None and (not integer(raw,65535) or not (
        (os.WIFEXITED(raw) and raw==os.WEXITSTATUS(raw)<<8) or (os.WIFSIGNALED(raw) and raw<=255))):
        raise ProtocolError('worker_wait_inconsistent')
    if code is not None and not integer(code,255,-127):
        raise ProtocolError('worker_wait_inconsistent')
    if raw is not None and code is not None and os.waitstatus_to_exitcode(raw)!=code:
        raise ProtocolError('worker_wait_inconsistent')


def wait_fact(value):
    if value is None:
        return None
    fields={'pid','start_ticks','raw_status','returncode','boot_id'}
    if type(value) is not dict or set(value)!=fields:
        raise ProtocolError('worker_wait_shape')
    for key,maximum in [('pid',2**31-1),('start_ticks',2**64-1)]:
        if value[key] is not None and not integer(value[key],maximum,1):
            raise ProtocolError('worker_wait_identity')
    if value['boot_id'] is not None and not uuid_value(value['boot_id']):
        raise ProtocolError('worker_wait_identity')
    wait_pair(value)
    return value.copy()


def codes(value,allowed):
    if (type(value) is not list or len(value)>len(allowed) or
        any(type(item) is not str or item not in allowed for item in value) or value!=sorted(set(value))):
        raise ProtocolError('worker_observation_bounds')
    return value.copy()


def validate_end(value,*,run_id,source_manifest_sha256):
    fields={'schema','run_id','source_manifest_sha256','stream_sha256','stream_bytes',
        'observed_stream_sha256','observed_stream_bytes','actor_wait','capture_wait','capture_released',
        'input_bytes','input_bytes_written','observations','capture_cleanup_observations'}
    if type(value) is not dict or set(value)!=fields:
        raise ProtocolError('worker_end_shape')
    if (value['schema']!='cairn.worker-end-observation/1' or not uuid_value(run_id) or
        not hash_value(source_manifest_sha256) or value['run_id']!=run_id or
        value['source_manifest_sha256']!=source_manifest_sha256):
        raise ProtocolError('worker_end_binding')
    forwarded,observed=value['stream_bytes'],value['observed_stream_bytes']
    if (not integer(forwarded,64*1024*1024) or not integer(observed,64*1024*1024+128*1024) or
        observed<forwarded or not hash_value(value['stream_sha256']) or not hash_value(value['observed_stream_sha256']) or
        (observed==forwarded and value['stream_sha256']!=value['observed_stream_sha256'])):
        raise ProtocolError('worker_stream_observation')
    actor=wait_fact(value['actor_wait']);capture=wait_fact(value['capture_wait'])
    if actor is not None and capture is not None and actor['boot_id'] is not None and capture['boot_id'] is not None and actor['boot_id']!=capture['boot_id']:
        raise ProtocolError('worker_boot_identity_mismatch')
    observations=codes(value['observations'],OBSERVATIONS)
    cleanup=codes(value['capture_cleanup_observations'],CLEANUP_OBSERVATIONS)
    if value['capture_released'] is not True and value['capture_released'] is not None:
        raise ProtocolError('worker_release_observation')
    full,written=value['input_bytes'],value['input_bytes_written']
    if (full is not None and not integer(full,8500)) or (written is not None and (
        full is None or not integer(written,full))):
        raise ProtocolError('worker_input_observation')
    required=set()
    if actor is None or any(item is None for item in actor.values()):
        required.add('actor_wait_unknown')
    if capture is None or any(item is None for item in capture.values()):
        required.add('capture_wait_unknown')
    if value['capture_released'] is not True or cleanup:
        required.add('capture_release_unknown')
    if full is None or written!=full:
        required.add('input_delivery_incomplete')
    if not required<=set(observations) or (observed>forwarded and not {
        'stream_budget_exhausted','stream_transport_unknown'}&set(observations)):
        raise ProtocolError('worker_unknown_observation_missing')
    result={**value,'actor_wait':actor,'capture_wait':capture,
            'observations':observations,'capture_cleanup_observations':cleanup}
    if len(encoded(result))>65536:
        raise ProtocolError('worker_packet_bounds')
    return result


def decode_end(payload,*,run_id,source_manifest_sha256):
    if type(payload) is not bytes or len(payload)>65536:
        raise ProtocolError('worker_packet_bounds')
    try:
        value=parse(payload)
    except FrameError:
        raise ProtocolError('worker_end_malformed') from None
    return validate_end(value,run_id=run_id,source_manifest_sha256=source_manifest_sha256)
