"""Exact bounded v4 request/outcome wire; no command, proof or grant fields."""
import json
from backend_binding import BackendUnknown, safe_id
from effects import Action, SCHEMA, canonical, digest, unique_object

MAX_REQUEST=4096
FIELDS={'schema','run_id','effect_id','action','binding_sha256','request_sha256'}


def request(value,binding):
    if type(value) is not dict or set(value)!=FIELDS or value['schema']!=SCHEMA:raise BackendUnknown('request_schema')
    safe_id(value['run_id']);safe_id(value['effect_id'])
    if value['run_id']!=binding.run_id or value['binding_sha256']!=binding.digest():raise BackendUnknown('request_binding')
    try:Action(value['action'])
    except (ValueError,TypeError):raise BackendUnknown('fixed_action_required') from None
    body={k:v for k,v in value.items() if k!='request_sha256'}
    if value['request_sha256']!=digest(canonical(body)):raise BackendUnknown('request_digest')
    if len(canonical(value))+1>MAX_REQUEST:raise BackendUnknown('request_size')
    return dict(value)


def decode(data,binding):
    if type(data) is not bytes or not 0<len(data)<=MAX_REQUEST or not data.endswith(b'\n'):raise BackendUnknown('wire_framing')
    try:value=json.loads(data,object_pairs_hook=unique_object,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError):raise BackendUnknown('wire_JSON') from None
    value=request(value,binding)
    if data!=canonical(value)+b'\n':raise BackendUnknown('wire_noncanonical')
    return value


def outcome(value):
    return {'schema':'cairn.remote-effect-outcome/1',
            **{k:value[k] for k in ('run_id','effect_id','action','binding_sha256','request_sha256')},
            'outcome':'APPLIED','executor_exit':0}
