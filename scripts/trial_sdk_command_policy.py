"""Inert fixed engine-dialect plans. No dispatch, receipt authority or admission.

The trusted parent supplies route/scope/context. A returned plan only validates
shape and routing; executing it still requires the parent's current-attempt held
receipt/evidence authorization, sole gate debit, reservations and source custody.
"""
from dataclasses import dataclass
import json
import posixpath
import re
import unicodedata
import uuid

from trial_sdk_engine_adapter import _text_utf8_size
from trial_sdk_incoming import (IncomingError, MAX_DOCUMENT_BYTES,
                                canonical_size_sha256, parse_document)


class CommandPolicyError(ValueError):
    pass


_CONTEXT=(('revision','--revision'),('workspace_sha256','--workspace-sha256'),
          ('task_class','--task-class'),('task_phase','--task-phase'),
          ('binding','--binding'),('capability','--capability'))
_KINDS=frozenset(('note','observation','claim','lesson','procedure','decision',
                  'preference','instruction'))


def _refuse(code='command_policy_shape'):
    raise CommandPolicyError(code)


def _string(value):
    if type(value) is not str or '\0' in value:
        _refuse()
    try:
        return _text_utf8_size(value)
    except ValueError:
        _refuse()


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        _refuse()
    return value


def _number(value, low, high):
    if not re.fullmatch(r'0|[1-9][0-9]*',value) or len(value)>10:
        _refuse()
    return _integer(int(value),low,high)


def _id(value):
    if type(value) is not str:
        _refuse()
    try:
        parsed=uuid.UUID(value)
    except (ValueError,AttributeError):
        _refuse()
    if parsed.int==0 or str(parsed)!=value:
        _refuse()
    return value


def _span(value, ceiling):
    if value is None:
        return None
    if type(value) is not dict or set(value)!={'offset','length'}:
        _refuse()
    return {'offset':_integer(value['offset'],0,ceiling-1),
            'length':_integer(value['length'],1,ceiling)}


@dataclass(frozen=True,slots=True)
class CommandPlan:
    """Immutable data only; callers cannot treat construction as read authority."""
    operation: str
    argv: tuple
    body: str | None


@dataclass(frozen=True,slots=True)
class FixedCommandPolicy:
    executable: str
    socket: str
    token_file: str
    repo: str
    task: str
    run: str
    context: tuple=()

    def __post_init__(self):
        for value in (self.executable,self.socket,self.token_file):
            _string(value)
            if not value.startswith('/') or len(value)>4096:
                _refuse('command_policy_binding')
        for value in (self.repo,self.task,self.run):
            if not 1 <= _string(value) <= 256 or not value.strip() or value=='*':
                _refuse('command_policy_binding')
        if type(self.context) is not tuple:
            _refuse('command_policy_binding')
        seen=set(); normalized=[]
        for pair in self.context:
            if (type(pair) is not tuple or len(pair)!=2 or type(pair[0]) is not str
                    or pair[0] not in dict(_CONTEXT) or pair[0] in seen):
                _refuse('command_policy_binding')
            key,value=pair
            size=_string(value)
            seen.add(key)
            if not size:
                continue  # The actual engine omits explicitly empty strings.
            if key=='revision':
                valid=re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})',value)
            elif key=='workspace_sha256':
                valid=re.fullmatch(r'[0-9a-fA-F]{64}',value)
            else:
                valid=size<=256 and value!='*'
            if not valid:
                _refuse('command_policy_binding')
            normalized.append(pair)
        object.__setattr__(self,'context',tuple(normalized))

    def plan(self,operation,args=(),body=None):
        if operation in ('native-search', 'native-pull'):
            from trial_sdk_native_request import native_plan
            if args:
                _refuse()
            return native_plan(self, operation, body)
        if (type(operation) is not str or operation not in ('search','pull','pull-evidence','history')
                or type(args) is not tuple or any(type(arg) is not str for arg in args)
                or body is not None and type(body) is not str):
            _refuse()
        # Existing gate's bound, not a new command-size grant.
        try:
            size,_=canonical_size_sha256({'operation':operation,'args':list(args),'body':body})
        except (ValueError,UnicodeError,RecursionError):
            _refuse()
        if size>MAX_DOCUMENT_BYTES:
            _refuse('command_policy_bound')
        prefix=(self.executable,'agent','--socket',self.socket,'--token-file',self.token_file)
        if operation=='search':
            if body is not None:
                _refuse()
            return CommandPlan(operation,prefix+(operation,)+self._search(args),None)
        if args or body is None:
            _refuse()
        try:
            graph=parse_document(body,_string(body))
        except IncomingError:
            _refuse()
        graph=self._json(operation,graph)
        text=json.dumps(graph,ensure_ascii=False,separators=(',',':'),sort_keys=True)
        if _text_utf8_size(text)>MAX_DOCUMENT_BYTES:
            _refuse('command_policy_bound')
        return CommandPlan(operation,prefix+(operation,),text)

    def _search(self,args):
        singles={}; entities=[]; kinds=[]; semantic=False; query=None
        permitted={'--repo','--task','--run','--tokens','--offset',*(flag for _,flag in _CONTEXT)}
        i=0
        while i<len(args):
            flag=args[i]; i+=1
            if flag=='--':
                if i!=len(args)-1:
                    _refuse()
                query=args[i]
                break
            if flag=='--semantic':
                if semantic:
                    _refuse()
                semantic=True
                continue
            if flag not in permitted|{'--entity-file','--kind'} or i==len(args):
                _refuse()
            value=args[i]; i+=1
            _string(value)
            if flag=='--entity-file':
                if (not 1<=_text_utf8_size(value)<=512 or value.strip()!=value
                        or any(unicodedata.category(c)=='Cc' for c in value)
                        or value.startswith('/') or posixpath.normpath(value)!=value
                        or value in ('.','..') or value.startswith('../')
                        or '\\' in value or ':' in value):
                    _refuse()
                entities.append(value)
            elif flag=='--kind':
                if value not in _KINDS:
                    _refuse()
                kinds.append(value)
            else:
                if flag in singles:
                    _refuse()
                singles[flag]=value
        if query is None or _string(query)>4096 or not query.strip() and not entities:
            _refuse()
        if len(set(entities))>16 or len(kinds)>8:
            _refuse()
        fixed={'--repo':self.repo,'--task':self.task,'--run':self.run}
        configured=dict(self.context)
        fixed.update({flag:configured[key] for key,flag in _CONTEXT if key in configured})
        if any(singles.get(flag)!=value for flag,value in fixed.items()):
            _refuse('command_policy_scope')
        if set(singles)-set(fixed)-{'--tokens','--offset'}:
            _refuse('command_policy_scope')
        if '--tokens' not in singles:
            _refuse()
        tokens=_number(singles['--tokens'],256,1000000)
        result=['--repo',self.repo,'--task',self.task,'--run',self.run,'--tokens',str(tokens)]
        if semantic:
            result.append('--semantic')
        if '--offset' in singles:
            result.extend(('--offset',str(_number(singles['--offset'],0,10000))))
        # Core normalizes identical entity hints; avoid redundant argv slots.
        for value in dict.fromkeys(entities):
            result.extend(('--entity-file',value))
        for value in kinds:
            result.extend(('--kind',value))
        for key,flag in _CONTEXT:
            if key in configured:
                result.extend((flag,configured[key]))
        result.extend(('--',query))
        return tuple(result)

    def _json(self,operation,value):
        if operation=='history':
            allowed={'record_id','repo','version','before_version','limit','span'}
            if set(value)-allowed or 'record_id' not in value:
                _refuse()
            if 'repo' in value and value['repo']!=self.repo:
                _refuse('command_policy_scope')
            result={'record_id':_id(value['record_id']),'repo':self.repo}
            for key,high in (('version',2147483647),('before_version',2147483647),('limit',100)):
                if key in value:
                    result[key]=_integer(value[key],0,high)
            if result.get('version',0)>0 and (result.get('limit',0) or result.get('before_version',0)):
                _refuse()
            span=_span(value.get('span'),65536)
            if span is not None:
                if result.get('version',0)==0:
                    _refuse()
                result['span']=span
            return result
        required={'request_id','receipt_id','handle'}
        if operation=='pull-evidence':
            required|={'evidence_id','expected_sha256'}
        allowed=required|{'span'}|({'opening_bytes'} if operation=='pull' else set())
        if not required<=set(value) or set(value)-allowed:
            _refuse()
        result={key:_id(value[key]) for key in sorted(required-{'expected_sha256'})}
        if operation=='pull-evidence':
            digest=value['expected_sha256']
            if type(digest) is not str or not re.fullmatch('[0-9a-fA-F]{64}',digest):
                _refuse()
            result['expected_sha256']=digest
        span=_span(value.get('span'),65536 if operation=='pull' else 1048576)
        if span is not None:
            result['span']=span
        if 'opening_bytes' in value:
            opening=_integer(value['opening_bytes'],0,768)
            if opening and span is None:
                _refuse()
            result['opening_bytes']=opening
        return result
