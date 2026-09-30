"""Prospective native permission rules and redacted observations; no execution."""
import re
import shlex

# Keep the former read/edit and narrow shell routes; add only authorized local
# toolchain operations. Native safety checks still apply independently.
ALLOW = [
    'Read', 'Edit', 'Write', 'Glob', 'Grep',
    'Bash(git status *)', 'Bash(git diff *)', 'Bash(git log *)', 'Bash(git show *)',
    'Bash(rg *)', 'Bash(python3 *)', 'Bash(go test *)',
    'Bash(go version)', 'Bash(go build *)', 'Bash(go vet *)',
    'Bash(gofmt -l *)', 'Bash(gofmt -w *)', 'Bash(pg_config --version)',
    'Bash(make check)', 'Bash(make test-integration)',
    *['mcp__cairn__'+name for name in ('cairn_search', 'cairn_pull', 'cairn_history',
       'cairn_client_info', 'cairn_pull_evidence', 'cairn_assessments')],
]
DENY = ['mcp__cairn__'+name for name in ('cairn_remember','cairn_edit','cairn_assess','cairn_prepare_note')]


def settings_permissions():
    return dict(allow=list(ALLOW), deny=list(DENY))


def command_metadata(value):
    """Classify, never authorize or retain command/path/env text."""
    if not isinstance(value,str) or len(value)>65536:
        return dict(command_category='unknown',shell_form='invalid_or_oversized')
    # Conservative telemetry only: even metacharacters inside quoted data earn
    # this label. This is not a replacement for the native permission matcher.
    form='compound_or_redirect' if re.search(r'[;&|<>`\n$()]',value) else 'standalone'
    try: words=shlex.split(value)
    except ValueError:return dict(command_category='unknown',shell_form='malformed')
    category='other'
    if len(words)>=2 and words[0]=='go' and words[1] in ('version','build','test','vet'):
        category='go_'+words[1]
    elif len(words)>=2 and words[0]=='make' and words[1] in ('check','test-integration'):
        category='make_'+words[1].replace('-','_')
    elif words[:2]==['pg_config','--version']: category='postgres_version'
    elif len(words)>=2 and words[0]=='gofmt' and words[1] in ('-l','-w'):
        category='gofmt_check' if words[1]=='-l' else 'gofmt_write'
    elif len(words)>=2 and words[0]=='git' and words[1] in ('status','diff','log','show','add'):
        category='git_'+words[1]
    elif words and words[0] in ('python3','rg','which'):
        category={'python3':'scripted_shell','rg':'source_search','which':'executable_probe'}[words[0]]
    return dict(command_category=category,shell_form=form)


def error_metadata(content):
    """Known error-language signals, not an inferred authorization verdict."""
    if isinstance(content,str): text=content[:8192].lower()
    elif isinstance(content,list):
        text=' '.join(x.get('text','')[:2048] for x in content[:4]
                      if isinstance(x,dict) and isinstance(x.get('text'),str)).lower()
    else: text=''
    if 'requires approval' in text or 'permission prompt' in text: reason='approval_required'
    elif 'safety' in text or 'security check' in text: reason='safety_check_reported'
    elif 'permission' in text and any(x in text for x in ('denied','not allowed','not permitted')): reason='permission_denied_reported'
    else: reason='unclassified_tool_error'
    return dict(error_reason=reason,reason_source='tool_result_text_signal')


def denial_metadata(value, calls):
    """Native terminal denial is authoritative; free-form reason/input is redacted."""
    if not isinstance(value,dict): return dict(tool='unknown',reason='native_permission_denial',matched_call=False)
    ident=value.get('tool_use_id')
    ident=ident if isinstance(ident,str) and re.fullmatch(r'toolu_[A-Za-z0-9_-]{1,128}',ident) else None
    row=calls.get(ident,{}) if ident else {}
    name=value.get('tool_name')
    known={'Bash','Read','Edit','Write','Glob','Grep'} | {x for x in ALLOW+DENY if x.startswith('mcp__')}
    out=dict(tool=name if isinstance(name,str) and name in known else 'unknown',reason='native_permission_denial',matched_call=bool(row))
    if ident:out['tool_use_id']=ident
    if out['tool']=='Bash':
        args=value.get('tool_input')
        out.update(command_metadata(args.get('command') if isinstance(args,dict) else None))
        if row:
            out.update({key:row[key] for key in ('command_category','shell_form','error_reason','reason_source') if key in row})
    return out


def background_result(value):
    """Recognize pending Bash acknowledgments; never count them as completion."""
    if isinstance(value,dict):
        if any(value.get(k) for k in ('run_in_background','background','isAsync','is_async','task_id','background_task_id','backgroundTaskId')):
            return True
        if value.get('status') in ('running','pending','background'):return True
        return any(background_result(value[k]) for k in ('content','text','tool_use_result') if k in value)
    if isinstance(value,list):return any(background_result(v) for v in value[:16])
    if isinstance(value,str):
        text=value[:8192].lower()
        return any(s in text for s in ('running in background','running in the background','background task id','command running with id','moved to the background'))
    return False
