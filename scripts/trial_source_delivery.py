"""Bounded source identities from emitted Cairn payloads; never retain bodies.

This is evidence extraction, not applicability, currentness or authority checking.
Unknown representations stay unknown. Source strings are UTF-8 only where the
known Cairn schema declares text; no stringification or normalization is used.
"""
import base64
import binascii
import copy
import hashlib
import json
import re

UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
DIGEST = re.compile(r'[0-9a-f]{64}')
MAX_BYTES = 1024 * 1024
MAX_ITEMS = 128


def _digest(value):
    if not isinstance(value,str) or not DIGEST.fullmatch(value):
        raise ValueError('digest')
    return value


def _text(value):
    if not isinstance(value,str):
        raise ValueError('text')
    data=value.encode('utf-8')
    if len(data)>65536:
        raise ValueError('body size')
    return data


def _integer(value,minimum=0):
    if type(value) is not int or not minimum<=value<=2147483647:
        raise ValueError('integer')
    return value


def _identity(record):
    ident=record.get('record_id')
    if not isinstance(ident,str) or not UUID.fullmatch(ident):
        raise ValueError('record identity')
    return dict(record_id=ident,version=_integer(record.get('version'),1))


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(raw):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>MAX_BYTES:
        raise ValueError('envelope size')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)


class _Extract:
    def __init__(self):
        self.items=[]

    def add(self,item):
        if len(self.items)>=MAX_ITEMS:
            raise ValueError('item bound')
        self.items.append(item)

    def body(self,record,*,span=None,historical=False,declared=None,mandatory=None):
        item=dict(_identity(record),historical=historical)
        if type(mandatory) is bool:
            item['mandatory']=mandatory
        if span is not None:
            if record.get('body') not in (None, ''):
                raise ValueError('ambiguous full body and span')
            if not isinstance(span,dict):
                raise ValueError('span')
            offset,end,total=(_integer(span.get(k)) for k in ('offset','end','total_bytes'))
            if not 0<=offset<=end<=total<=65536:
                raise ValueError('span extent')
            if 'body_base64' in span:
                if 'body' in span and span['body']:
                    raise ValueError('ambiguous span')
                value=span['body_base64']
                if not isinstance(value,str) or len(value)>90000:
                    raise ValueError('span base64')
                data=base64.b64decode(value,validate=True)
            else:
                data=_text(span.get('body',''))
            if len(data)!=end-offset or _hash(data)!=_digest(span.get('sha256')):
                raise ValueError('span identity')
            source=_digest(declared if historical else span.get('source_sha256'))
            item.update(extent='partial_span',source_sha256=source,
                        span=dict(offset=offset,end=end,total_bytes=total),
                        delivered_sha256=_hash(data),delivered_bytes=len(data))
        else:
            data=_text(record.get('body'))
            digest=_hash(data)
            if declared is not None and digest!=_digest(declared):
                raise ValueError('body identity')
            item.update(extent='full_body',source_sha256=digest,
                        delivered_sha256=digest,delivered_bytes=len(data))
        self.add(item)

    def selection(self,selected,span=None):
        if not isinstance(selected,dict) or not isinstance(selected.get('record'),dict):
            raise ValueError('selection')
        self.body(selected['record'],span=span,mandatory=selected.get('mandatory'))

    def expansion(self,value):
        if not isinstance(value,dict) or 'selection' not in value:
            raise ValueError('expansion')
        self.selection(value['selection'],value.get('span'))
        for selected in self.list(value,'competing'):
            self.selection(selected)

    @staticmethod
    def list(value,key):
        items=value.get(key,[])
        if not isinstance(items,list) or len(items)>MAX_ITEMS:
            raise ValueError('list')
        return items

    def view(self,value):
        if not isinstance(value,dict) or not any(k in value for k in ('selected','index','expanded','candidate_bodies')):
            raise ValueError('view')
        for selected in self.list(value,'selected'):
            self.selection(selected)
        for entry in self.list(value,'index'):
            if not isinstance(entry,dict):
                raise ValueError('preview')
            data=_text(entry.get('summary'))
            item=dict(_identity(entry),extent='preview',historical=False,
                      delivered_sha256=_hash(data),delivered_bytes=len(data))
            if entry.get('body_sha256') is not None:
                item['source_sha256']=_digest(entry['body_sha256'])
            # A summary may contain omission marks: its location is a hint,
            # not a claim that displayed bytes are a canonical source slice.
            hint=entry.get('summary_span')
            if hint is not None:
                if not isinstance(hint,dict):raise ValueError('preview location')
                item['summary_span']=dict(offset=_integer(hint.get('offset')),length=_integer(hint.get('length')))
            self.add(item)
        if value.get('expanded') is not None:
            self.expansion(value['expanded'])
        for candidate in self.list(value,'candidate_bodies'):
            if not isinstance(candidate,dict):raise ValueError('candidate')
            self.expansion(candidate.get('response'))

    def history(self,value):
        if not isinstance(value,dict) or value.get('historical') is not True or 'versions' not in value:
            raise ValueError('history')
        for version in self.list(value,'versions'):
            if not isinstance(version,dict):raise ValueError('history version')
            record=dict(version,record_id=value.get('record_id'))
            if 'body' in version or 'span' in version:
                self.body(record,span=version.get('span'),historical=True,declared=version.get('body_sha256'))
            else:
                item=dict(_identity(record),extent='history_metadata',historical=True)
                if version.get('body_sha256') is not None:
                    item['source_sha256']=_digest(version['body_sha256'])
                self.add(item)


def _extract(action):
    parsed=_Extract()
    try:
        action(parsed)
    except (ValueError,TypeError,AttributeError,KeyError,UnicodeError,binascii.Error,RecursionError):
        # Never return a partially validated envelope as a delivery claim.
        return dict(schema='cairn.source-delivery/1',status='unknown',items=[],reason='unsupported_or_invalid_payload')
    return dict(schema='cairn.source-delivery/1',status='observed',items=parsed.items)


def hook_delivery(raw):
    def parse(extractor):
        if not raw.strip():return
        value=_json(raw)
        if not isinstance(value,dict):raise ValueError('hook')
        output=value.get('hookSpecificOutput')
        if output is None:return
        if not isinstance(output,dict):raise ValueError('hook output')
        text=output.get('additionalContext')
        if text is None or text=='':return
        if not isinstance(text,str):raise ValueError('context')
        extractor.view(_json(text[text.index('{'):]))
    return _extract(parse)


def native_delivery(tool,result):
    if not isinstance(result,dict) or ('is_error' in result and type(result['is_error']) is not bool):
        return dict(schema='cairn.source-delivery/1',status='unknown',items=[],reason='invalid_native_error_flag')
    if result.get('is_error') is True:
        return dict(schema='cairn.source-delivery/1',status='error',items=[],reason='native_tool_error')
    def parse(extractor):
        parts=result.get('content')
        if isinstance(parts,str):
            value=_json(parts)
        elif isinstance(parts,list) and len(parts)==1 and isinstance(parts[0],dict) and parts[0].get('type')=='text':
            value=_json(parts[0].get('text'))
        else:raise ValueError('native content')
        if tool=='mcp__cairn__cairn_search':extractor.view(value)
        elif tool=='mcp__cairn__cairn_pull':extractor.expansion(value)
        elif tool=='mcp__cairn__cairn_history':extractor.history(value)
        elif tool in ('mcp__cairn__cairn_client_info','mcp__cairn__cairn_assessments'):
            pass  # Metadata only; never claim a source body.
        else:raise ValueError('unsupported tool payload')
    return _extract(parse)


def bind_origins(delivery,origins,*,bodies=None):
    """Join only the frozen import map; never copy arbitrary provenance/body text."""
    result=copy.deepcopy(delivery)
    rows={(r['imported_record_id'],r['imported_version']):r for r in origins}
    for item in result['items']:
        item.pop('input_id',None)
        row=rows.get((item['record_id'],item['version']))
        source=item.get('source_sha256')
        span_matches = True
        if item['extent'] == 'partial_span' and bodies is not None and row is not None:
            body = bodies.get(row['input_id'])
            if not isinstance(body,str):
                span_matches = False
            else:
                raw = body.encode('utf-8')
                span = item['span']
                span_matches = (len(raw) == span['total_bytes'] and _hash(raw) == source
                                and _hash(raw[span['offset']:span['end']]) == item['delivered_sha256'])
        if row is None or not span_matches or (source is not None and source!=row['body_sha256']):
            item['origin_match']='unmatched'
            result['status']='unknown'
        else:
            item['input_id']=row['input_id']
            item['origin_match']='body_hash' if source is not None else 'id_version_only'
    return result
