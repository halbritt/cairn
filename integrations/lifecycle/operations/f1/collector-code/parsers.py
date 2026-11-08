"""Strict, non-echoing projections for fixed F1 metadata reads."""
import json
import re
import uuid
import unicodedata

ORIGIN = 'proximal.tail0ecc2e.ts.net:8789'
BACKEND = 'http://127.0.0.1:18889'
API_SOCKET = '/home/halbritt/.local/share/cairn/api.sock'
TS_SOCKET = '/var/run/tailscale/tailscaled.sock'
PG_PATHS = ('/var/run/postgresql/.s.PGSQL.5432', '/run/postgresql/.s.PGSQL.5432')
MAX_JSON = 65536

class Unknown(Exception):
    """Only fixed code literals are exposed by the collector."""


def need(ok, code):
    if not ok:
        raise Unknown(code)


def _pairs(rows):
    out = {}
    for key, value in rows:
        need(key not in out, 'DUPLICATE_JSON_KEY')
        out[key] = value
    return out


def _constant(_):
    raise Unknown('NONFINITE_JSON')


def decode(raw, limit=MAX_JSON):
    need(type(raw) is bytes and len(raw) <= limit, 'OUTPUT_BOUND')
    try:
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            need(depth <= 24, 'JSON_DEPTH')
            if isinstance(item, str):
                need(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), 'INVALID_UNICODE')
            elif type(item) is dict:
                pending.extend((v, depth + 1) for v in item.values())
                pending.extend((k, depth + 1) for k in item)
            elif type(item) is list:
                pending.extend((v, depth + 1) for v in item)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise Unknown('INVALID_JSON') from None


def mapping(value, code):
    need(type(value) is dict, code)
    return value


def tailscale(raw):
    value = mapping(decode(raw), 'STATUS_NOT_OBJECT')
    need(set(value) <= {'TCP','Web','Services','AllowFunnel','Foreground'}, 'STATUS_UNKNOWN_FIELD')
    for name in ('Foreground','Services'):
        need(name not in value or value[name] == {}, 'STATUS_AMBIGUOUS_SCOPE')
    tcp = mapping(value.get('TCP'), 'TCP_MISSING')
    selected = mapping(tcp.get('8789'), 'TCP_SELECTED_MISSING')
    need(set(selected)=={'HTTPS'} and selected['HTTPS'] is True, 'TCP_MODE_CHANGED')
    web = mapping(value.get('Web'), 'WEB_MISSING')
    # Other host/port handlers cannot be safely interpreted as this selected route.
    need(all(type(k) is str for k in web), 'WEB_KEY')
    need(sum(k.endswith(':8789') for k in web) == 1 and ORIGIN in web, 'ROUTE_AMBIGUOUS')
    target = mapping(web[ORIGIN], 'WEB_SELECTED')
    need(set(target) == {'Handlers'}, 'WEB_SELECTED_FIELDS')
    handlers = mapping(target['Handlers'], 'HANDLERS_MISSING')
    need(set(handlers) == {'/'}, 'MOUNT_AMBIGUOUS')
    need(handlers['/'] == {'Proxy': BACKEND}, 'HANDLER_CHANGED')
    funnel = mapping(value.get('AllowFunnel', {}), 'FUNNEL_TYPE')
    need(ORIGIN not in funnel or funnel[ORIGIN] is False, 'SELECTED_FUNNEL_ENABLED')
    return {'origin': 'https://' + ORIGIN, 'mount':'/', 'backend': BACKEND,
            'https':True, 'selected_funnel':False,
            'assurance':'configured forwarding route only; no request causality'}


def unit(raw, expected):
    need(type(raw) is bytes and len(raw) <= 4096, 'UNIT_BOUND')
    try: lines = raw.decode('utf-8', 'strict').splitlines()
    except UnicodeError: raise Unknown('UNIT_ENCODING') from None
    values = {}
    for line in lines:
        key, sep, value = line.partition('=')
        need(sep == '=' and key not in values, 'UNIT_SYNTAX')
        values[key] = value
    need(set(values) == {'Id','MainPID','NRestarts','ActiveState','SubState'}, 'UNIT_FIELDS')
    need(values['Id'] == expected and values['ActiveState'] == 'active' and values['SubState'] == 'running', 'UNIT_NOT_SELECTED_ACTIVE')
    need(re.fullmatch(r'[1-9][0-9]{0,9}',values['MainPID']) is not None and
         re.fullmatch(r'0|[1-9][0-9]{0,9}',values['NRestarts']) is not None, 'UNIT_IDENTITY')
    return {'unit':expected,'pid':int(values['MainPID']),'restarts':int(values['NRestarts'])}


def directory(raw):
    envelope = mapping(decode(raw), 'DIRECTORY_NOT_OBJECT')
    # cairn command output is the cairn.response/1 ok/status/data envelope.
    need(envelope.get('schema') == 'cairn.response/1' and envelope.get('ok') is True and envelope.get('status') == 'OK' and type(envelope.get('data')) is dict, 'DIRECTORY_ENVELOPE')
    page = envelope['data']
    need(type(page.get('more')) is bool, 'DIRECTORY_PAGE')
    rows = page.get('agents')
    need(type(rows) is list and len(rows) == 1, 'DIRECTORY_SAMPLE_ABSENT')
    row = mapping(rows[0], 'DIRECTORY_ROW')
    need(row.get('machine_id') == 'archon', 'DIRECTORY_MACHINE_MISMATCH')
    out = {'machine_id':'archon'}
    for key in ('agent_id','execution_id'):
        value = row.get(key)
        try: parsed = uuid.UUID(value) if type(value) is str else None
        except ValueError: parsed = None
        need(parsed is not None and str(parsed) == value, 'DIRECTORY_IDENTITY')
        out[key] = value
    generation = row.get('database_generation')
    need(type(generation) is int and 0 <= generation <= (1 << 63) - 1, 'DIRECTORY_GENERATION')
    out['database_generation'] = generation
    out['scope'] = 'one visible configured-owner sample; not exhaustive or physical-machine attestation'
    return out


def proc_stat(raw, expected_pid):
    need(type(raw) is bytes and len(raw) <= 8192, 'PROC_BOUND')
    try:
        text = raw.decode('ascii','strict'); end=text.rfind(')')
        need(end > 0 and text.split(' ',1)[0] == str(expected_pid), 'PROC_IDENTITY')
        fields=text[end+2:].split()
        need(len(fields) >= 20 and fields[0] not in ('Z','X','x'), 'PROC_NOT_LIVE')
        ppid,start=int(fields[1]),int(fields[19])
        need(start > 0 and ppid >= 0, 'PROC_IDENTITY')
        return {'pid':expected_pid,'ppid':ppid,'start_ticks':start}
    except (ValueError,UnicodeError): raise Unknown('PROC_SYNTAX') from None


def unix_rows(raw):
    need(type(raw) is bytes and len(raw)<=262144, 'SOCKET_TABLE_BOUND')
    try: text=raw.decode('utf-8','strict')
    except UnicodeError: raise Unknown('SOCKET_ENCODING') from None
    rows=[]
    for line in text.splitlines():
        f=line.split()
        need(len(f)>=8 and f[0] in ('u_str','u_dgr','u_seq'), 'SOCKET_SYNTAX')
        need(f[5].isdigit() and f[7].isdigit(), 'SOCKET_INODE')
        rows.append({'state':f[1], 'address':f[4], 'inode':int(f[5]),
                     'peer_address':f[6], 'peer_inode':int(f[7]),
                     'pids':[int(v) for v in re.findall(r'pid=(\d+)',line)]})
    return rows


def tcp_listener(raw, api_pid):
    need(type(raw) is bytes and len(raw)<=262144, 'SOCKET_TABLE_BOUND')
    try: text=raw.decode('utf-8','strict')
    except UnicodeError: raise Unknown('SOCKET_ENCODING') from None
    matching=[]
    for line in text.splitlines():
        f=line.split()
        need(len(f)>=5, 'TCP_SYNTAX')
        if f[3] == '127.0.0.1:18889':
            matching.append((f[0],[int(v) for v in re.findall(r'pid=(\d+)',line)]))
    need(matching == [('LISTEN',[api_pid])], 'API_LOOPBACK_LISTENER_UNKNOWN')
    return '127.0.0.1:18889'


def tcp_connections(raw, api_pid):
    need(type(raw) is bytes and len(raw)<=262144, 'SOCKET_TABLE_BOUND')
    try: text=raw.decode('utf-8','strict')
    except UnicodeError: raise Unknown('SOCKET_ENCODING') from None
    count=0
    for line in text.splitlines():
        pids=[int(v) for v in re.findall(r'pid=(\d+)',line)]
        if api_pid not in pids:continue
        f=line.split();need(len(f)>=5, 'TCP_SYNTAX')
        need(f[3]=='127.0.0.1:18889' and re.fullmatch(r'127\.0\.0\.1:[0-9]+',f[4]), 'API_TCP_PEER_UNKNOWN')
        count+=1
    return count


def local_map(raw):
    envelope=mapping(decode(raw),'CLIENTS_ENVELOPE')
    need(envelope.get('schema')=='cairn.response/1' and envelope.get('ok') is True and envelope.get('status')=='OK','CLIENTS_ENVELOPE')
    value=mapping(envelope.get('data'),'CLIENTS_DATA')
    required={'schema','storage','observation_epoch','started_at','observed_at','retention_seconds','server','exhaustive','partial','truncated','wall_clock_regressed','returned','eligible_rows','counters','rows'}
    need(required<=set(value) and all(value[k] is not None for k in required),'CLIENTS_REQUIRED_FIELDS')
    need(value['schema']=='cairn.clients/1' and value['storage']=='volatile' and value['exhaustive'] is False,'CLIENTS_SCHEMA')
    need(type(value['retention_seconds']) is int and value['retention_seconds']==86400,'CLIENTS_RETENTION')
    need(type(value['server']) is dict and type(value['started_at']) is str and value['started_at'] and type(value['observed_at']) is str and value['observed_at'],'CLIENTS_REQUIRED_FIELDS')
    epoch=value['observation_epoch']
    try:valid=type(epoch) is str and str(uuid.UUID(epoch))==epoch
    except ValueError:valid=False
    need(valid,'CLIENTS_EPOCH')
    rows=value['rows'];counts=mapping(value['counters'],'CLIENTS_COUNTERS')
    need(type(rows) is list and len(rows)==1 and type(value['returned']) is int and value['returned']==1,'CLIENTS_EMPTY_OR_COUNT')
    need(type(value['eligible_rows']) is int and 1<=value['eligible_rows']<=128,'CLIENTS_ACCOUNTING')
    need(all(type(value[k]) is bool for k in ('partial','truncated','wall_clock_regressed')),'CLIENTS_ACCOUNTING')
    need(all(type(counts.get(k)) is int and 0<=counts[k]<=2**62 for k in ('evicted_cohorts','invalid_declarations')),'CLIENTS_COUNTERS')
    need(value['truncated']==(value['eligible_rows']>1) and value['partial']==(value['truncated'] or counts['evicted_cohorts']>0),'CLIENTS_ACCOUNTING')
    row=mapping(rows[0],'CLIENTS_ROW');principal=row.get('principal');machine=row.get('machine_id')
    need(type(principal) is str and principal.strip() and len(principal.encode('utf-8'))<=256 and not any(unicodedata.category(c)=='Cc' for c in principal),'CLIENTS_PRINCIPAL')
    need(not principal.startswith('machine:'),'CLIENTS_NOT_LOCAL_PRINCIPAL')
    need(type(machine) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,62}',machine) and machine!='archon','CLIENTS_LOCAL_MAPPING_UNKNOWN')
    # The pinned Cairn CLI already runs ParseClientsResponse, including build,
    # timestamp and reported-descriptor validation. This projection adds no
    # authority from reported data and does not reproduce that facade parser.
    return {'principal_class':'local_non_machine','local_machine':machine,'observation_epoch':epoch,
            'partial':value['partial'],'truncated':value['truncated'],'wall_clock_regressed':value['wall_clock_regressed'],
            'scope':'own authenticated cohort mapping only; no census or writer/renewal assertion'}
