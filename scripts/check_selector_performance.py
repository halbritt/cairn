#!/usr/bin/env python3
"""Compare real installed selectors on synthetic dialogue; no database access."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

from test_claude_lifecycle import hook


def scenarios(project):
    decision = dict(record_id='existing-decision', kind='decision', version=1,
                    body=project+': Validation database\n\nUse PostgreSQL 16 for disposable transaction tests.')
    return [
        ('decision', ['Use PostgreSQL instead of SQLite because concurrent workers require transactional updates. The implementation and transaction tests remain unfinished.',
                      'The decision is PostgreSQL; implementation and transaction tests are next.'], []),
        ('correction', ['Correction: use PostgreSQL 17, not PostgreSQL 16, for the disposable transaction tests. This replaces the previous database version decision.',
                        'The database version decision is now PostgreSQL 17.'], [decision]),
        ('lookup', ['What is 2+2?', '4.'], []),
        ('excluded', ['Do not save this conversation or its decisions in memory. For this exercise choose PostgreSQL.', 'Understood.'], []),
        ('checkpoint', ['Continue the migration. The implementation is done, but transaction tests and deployment remain outstanding.',
                        'Implementation is complete. Next: run transaction tests, then deploy.'], []),
        ('acknowledgment', ['Thanks!', "You're welcome."], []),
    ]


def validate(name, selected, writes):
    if name in ('lookup','excluded','acknowledgment'):
        assert not writes, name+' produced a write'
    elif name=='correction':
        revised=[w for w in writes if w[0]=='revise']
        assert len(revised)==1 and revised[0][1]['record_id']=='existing-decision', 'correction lost existing identity'
        assert 'PostgreSQL 17' in revised[0][1]['body'], 'correction lost version'
    elif name=='decision':
        kinds=[w[1]['draft']['kind'] for w in writes if w[0]=='create']
        assert 'decision' in kinds and 'note' in kinds, 'decision/checkpoint not separated'
    else:
        assert selected['checkpoint'] and 'test' in selected['checkpoint'].lower(), 'unfinished checkpoint lost'


def measure(claude, model, repeat, effort=None):
    observations=[]
    with tempfile.TemporaryDirectory(prefix='cairn-selector-benchmark-') as tmp:
        config=dict(cairn='unused',claude=claude,socket='unused',token_file='unused',repo='fixture:selector',model=model)
        memory=hook.Memory(config,'selector-benchmark')
        for iteration in range(repeat):
            for name, texts, candidates in scenarios(Path(tmp).name):
                writes=[]
                selection={}
                real_run=hook.run_json
                def select(command, **kwargs):
                    started=time.monotonic()
                    if effort:
                        command = [*command, '--effort', effort]
                    result=real_run(command,**kwargs)
                    selection.update(result.get('structured_output') or {})
                    observation['selector_seconds']=time.monotonic()-started
                    observation['input_bytes']=len(kwargs['body'].encode())
                    return result
                def save(operation, args=(), payload=None):
                    writes.append((operation,payload))
                    return dict(record_id=payload.get('record_id','fixture-saved'))
                observation=dict(model=model,effort=effort,case=name,iteration=iteration)
                event=dict(cwd=tmp,session_id='selector-benchmark',hook_event_name='SessionEnd',
                           messages=[dict(role=role,text=text) for role,text in zip(('user','assistant'),texts)])
                started=time.monotonic()
                with patch.object(memory,'checkpoint',return_value=None), \
                     patch.object(hook,'durable_candidates',return_value=candidates), \
                     patch.object(hook,'handoff_candidates',return_value=[]), \
                     patch.object(memory,'call',side_effect=save), patch.object(hook,'run_json',side_effect=select):
                    hook.capture(memory,event)
                validate(name,selection,writes)
                observation.update(total_seconds=time.monotonic()-started,passed=True)
                observations.append(observation)
                print(json.dumps(observation),flush=True)
    return observations


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude',required=True)
    parser.add_argument('--model',action='append',required=True)
    parser.add_argument('--repeat',type=int,default=2)
    parser.add_argument('--effort')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    observations=[]
    for model in args.model:
        observations.extend(measure(args.claude,model,args.repeat,args.effort))
        timings=[r['total_seconds'] for r in observations if r['model']==model]
        print(model,'median',statistics.median(timings),'max',max(timings),flush=True)
    args.output.write_text(json.dumps(observations,indent=2)+'\n')


if __name__=='__main__':
    main()
