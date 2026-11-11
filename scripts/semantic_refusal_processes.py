"""Bounded Go-supervisor process-double observations for S-01b."""
import json
import re
import shlex
import shutil
from pathlib import Path

import check_semantic_reference as reference


# Each item lists every probe required to cover that particular subcase.
PROBES = {
    'rank-output-boundary': (['F-PROCESS/output-caps/rank'], ['']),
    'rank-output-overflow': (['F-PROCESS/output-caps/rank'], ['exceeds limit']),
    'rank-early-exit': (['F-PROCESS/early-exit/boundary'], ['worker failed']),
    'stream-early-exit': (['F-PROCESS/early-exit/boundary'], ['framing']),
    'rank-descendant': (['F-PROCESS/descendants-holding-pipes/boundary','F-PROCESS/group-termination-join/boundary'], ['worker failed']),
    'stream-descendant': (['F-PROCESS/descendants-holding-pipes/boundary','F-PROCESS/group-termination-join/boundary'], ['deadline']),
    'rank-blocked-input': (['F-PROCESS/blocked-input/boundary','F-PROCESS/earlier-deadline/boundary'], ['worker failed']),
    'rank-blocked-output': (['F-PROCESS/blocked-output/boundary','F-PROCESS/earlier-deadline/boundary'], ['worker failed']),
    'stream-blocked-input': (['F-PROCESS/blocked-input/boundary'], ['deadline']),
    'stream-blocked-output': (['F-PROCESS/blocked-output/boundary','F-PROCESS/earlier-deadline/boundary'], ['deadline']),
    'stream-wrong-id': (['F-STREAM/wrong-response-id/boundary'], ['ID mismatch']),
    'stream-missing-id': (['F-STREAM/absent-response-id/boundary'], ['ID mismatch']),
    'stream-missing-result': (['F-STREAM/missing-result/omitted'], ['omitted result']),
    'stream-null-result': (['F-STREAM/missing-result/null'], ['omitted result']),
    'stream-missing-newline': (['F-STREAM/incomplete-line/Go-output-without-newline'], ['framing']),
    'stream-output-overflow': (['F-STREAM/oversize-line/Go-ordinary-output','F-PROCESS/output-caps/stream-ordinary'], ['exceeds limit']),
    'stream-cache-overflow': (['F-STREAM/oversize-line/Go-cache-output','F-PROCESS/output-caps/stream-cache'], ['cache exceeds limit']),
    'stream-trailing-json': (['F-STREAM/trailing-output/same-line-json'], ['one JSON']),
    'stream-trailing-line': (['F-STREAM/trailing-output/unsolicited-next-line'], ['', 'ID mismatch']),
    'stream-identity-model': (['F-STREAM/identity-change/same-child-model'], ['', 'changed model']),
    'stream-identity-algorithm': (['F-STREAM/identity-change/same-child-algorithm'], ['', 'changed model']),
    'stream-new-child-identity': (['F-STREAM/identity-change/new-idle-child'], ['', '']),
    'stream-cache-omission': (['F-STREAM/cache-omission/absent'], ['', '', '']),
    'stream-cache-null': (['F-STREAM/cache-omission/null'], ['', '', '']),
    'stream-failure-restart': (['F-STREAM/failure-close-discard/failure'], ['', 'framing', '']),
    'stream-close-reopen': (['F-STREAM/failure-close-discard/owner-close'], ['', '']),
    'stream-busy': (['F-PROCESS/busy-refusal/stream-lane'], ['busy', 'deadline']),
    'stream-owner-close': (['F-PROCESS/owner-shutdown/stream'], ['canceled']),
    'rank-start-busy-retry': (['F-PROCESS/busy-refusal/ETXTBSY-startup-distinct'], ['']),
    'rank-start-busy-persistent': (['F-PROCESS/busy-refusal/ETXTBSY-startup-distinct'], ['busy']),
    'embed-concurrency': (['F-PROCESS/query-document-concurrency/boundary'], ['', 'canceled']),
    'embed-busy': (['F-PROCESS/busy-refusal/document-lane'], ['busy', 'canceled']),
    'embed-query-busy': (['F-PROCESS/busy-refusal/query-lane','F-PROCESS/owner-shutdown/query'], ['busy', 'canceled']),
    'embed-owner-close': (['F-PROCESS/owner-shutdown/document'], ['canceled']),
    'embed-earlier-deadline': (['F-PROCESS/earlier-deadline/boundary'], ['deadline']),
    'embed-output-overflow': (['F-PROCESS/output-caps/embedding'], ['buffer full']),
    'embed-restart-identity': (['F-EMBED/identity-after-restart/boundary'], ['', 'deadline', 'changed model']),
    'embed-legacy-projection': (['F-EMBED/legacy-omitted-projection/boundary'], ['']),
    **{'embed-projection-'+name: (['F-EMBED/bad-query-projection/'+('projection-on-document' if name=='on-document' else name)], ['projection'])
       for name in ('method','hash','byte-length','mid-codepoint','token-counts','on-document')},
}


def joined_calls(text):
    pending = ''
    for line in text.splitlines():
        # -xx escapes descriptor annotations as well as quoted syscall buffers.
        line=re.sub(r'<((?:\\x[0-9a-f]{2})+)>',
                    lambda match:'<'+bytes.fromhex(match[1].replace('\\x','')).decode('utf-8','backslashreplace')+'>',line)
        if '<unfinished ...>' in line:
            pending = line.split('<unfinished ...>',1)[0]
            yield line
        elif '<... ' in line and ' resumed>' in line:
            if pending:
                yield pending + line.split(' resumed>',1)[1]
                pending = ''
        else:
            yield line


def process_observation(folder, name):
    result = reference.load(folder/'driver.stdout')
    reference.require(set(result) == {'case','calls'} and result['case'] == name, 'wrong Go process case')
    wanted = PROBES[name][1]
    reference.require(len(result['calls']) == len(wanted), 'missing Go result/error call')
    for call, error in zip(result['calls'], wanted, strict=True):
        reference.require(set(call) == {'error','result'} and isinstance(call['error'],str), 'missing Go result/error fields')
        reference.require((call['error']=='' if error=='' else bool(call['error']) if error is None else error in call['error']),
                          'Go process disposition changed: '+name+': '+call['error'])
    events_path = folder/'events.jsonl'
    events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
    starts = [r for r in events if r['event']=='start']
    members = [r for r in events if r['event'] in ('start','descendant')]
    reference.require(len(members)==len({(r['pid'],r['start_ticks']) for r in members}),
                      'duplicate process identities')
    reference.require(all(r['pgrp']==r['pid'] and r['parent']>1 for r in starts), 'worker process group differs')
    traces = {int(p.name.rsplit('.',1)[1]): list(joined_calls(p.read_text())) for p in folder.glob('trace.*')}
    workers=[]
    for member in members:
        pid=member['pid']
        reference.require(type(pid) is int and pid>1 and pid in traces, 'invalid/missing owned process trace')
        lines=traces[pid]
        dispositions=[line for line in lines if '+++ exited with ' in line or '+++ killed by ' in line]
        reference.require(len(dispositions)==1, 'missing exact process exit/kill')
        pipe_ids=set()
        if member['event']=='start':
            channel=re.fullmatch(r'pipe:\[(\d+)\]',member.get('stdout_pipe',''))
            reference.require(channel is not None, 'missing worker stdout channel')
            pipe_ids.add(channel[1])
        writes=[]
        for line in lines:
            match=re.search(r'write\(1<pipe:\[(\d+)\]>',line)
            if match:
                pipe_ids.add(match.group(1));writes.append(line)
        prefix=None
        if not pipe_ids:
            prefix=''
        else:
            reads=[]
            for other in traces.values():
                for line in other:
                    match=re.search(r'read\(\d+<pipe:\[('+ '|'.join(pipe_ids)+r')\]>, "((?:\\x[0-9a-f]{2})+)"(?:\.\.\.)?, \d+\)\s+= ([1-9]\d*)',line)
                    if match:
                        reads.append((float(line.split()[0]),match.group(2).replace('\\x','')[:128]))
            if reads: prefix=min(reads)[1]
            elif not writes: prefix=''
        workers.append(dict(pid=pid,role=member['event'],exit_or_kill=dispositions[0],stdout_prefix_hex=prefix,
                            stdout_prefix_state='observed' if prefix is not None else 'unknown'))
    if name not in ('rank-start-busy-persistent',):
        reference.require(starts, 'fixture process did not initialize')
    requests_raw=[e for e in events if e['event']=='request']
    if name.startswith('stream-') and name not in ('stream-blocked-input','stream-early-exit','stream-descendant'):
        reference.require(requests_raw,'stream request was not observed')
    if name.startswith('embed-'):
        reference.require(requests_raw,'embedding request was not observed')
    if name=='embed-projection-mid-codepoint':
        reference.require(requests_raw[0]['request']['text']=='é' and 'prefix' in result['calls'][0]['error'],
                          'UTF-8 projection fixture did not reach its intended boundary')
    if name in ('stream-cache-omission','stream-cache-null','stream-failure-restart','stream-close-reopen'):
        requests=[e for e in events if e['event']=='request']
        reference.require(len(starts)==2 and requests[-1]['cache'] is False, 'discarded cache reached replacement')
    if name == 'stream-new-child-identity': reference.require(len(starts)==2, 'new identity did not use a fresh child')
    failed_execs=[line for lines in traces.values() for line in lines if 'execve(' in line and 'ETXTBSY' in line]
    if name.startswith('rank-start-busy'): reference.require(failed_execs, 'startup busy path not observed')
    failed_processes=[dict(pid=pid,exit_or_kill=[line for line in lines if '+++ exited with ' in line or '+++ killed by ' in line])
                      for pid,lines in traces.items() if any('execve(' in line and 'ETXTBSY' in line for line in lines)]
    reference.require(all(row['pid']>1 and len(row['exit_or_kill'])==1 for row in failed_processes),
                      'failed executable process did not exit')
    requests=[dict(pid=e['pid'],frame=e['frame'],cache=e['cache']) for e in events if e['event']=='request']
    kills=[line for lines in traces.values() for line in lines
           if any(re.search(r'\bkill\(-'+str(s['pid'])+r', SIGKILL\)',line) for s in starts)]
    waits=[line for lines in traces.values() for line in lines if 'wait' in line and
           any(('si_pid='+str(s['pid'])+',') in line or re.search(r'wait4\('+str(s['pid'])+r',',line) for s in starts)]
    if any(word in name for word in ('blocked','descendant','owner-close','busy')) and not name.startswith('rank-start-busy'):
        reference.require(kills and waits,'process-group termination or direct-child join not observed')
    replacement_case=name in ('stream-cache-omission','stream-cache-null','stream-failure-restart','stream-new-child-identity','embed-restart-identity')
    return dict(id=name,scope='Go-supervisor/process-double',freshness='reused',
        capture_origin='new S01b disposable process-double capture; audit reuses its pinned report',
        disposition=dict(go_error_or_result=dict(state='observed',calls=result['calls']),
            discovery_coverage=reference.unobserved('no discovery call'),
            process_exit_kill=dict(state='observed',workers=workers,failed_execs=failed_execs,failed_processes=failed_processes,
                                  process_group_signals=kills,direct_child_waits=waits),
            stdout_prefix=dict(state='observed' if all(w['stdout_prefix_state']=='observed' for w in workers) else 'partial',
                               prefixes=[dict(pid=w['pid'],hex=w['stdout_prefix_hex']) for w in workers]),
            cache=(dict(state='observed',incoming_requests=requests) if requests and name.startswith('stream-') else
                   reference.unobserved('this fixture did not exchange a rank-cache frame')),
            restarts=dict(state='observed',successful_starts=len(starts),
                          replacement_count=max(0,len(starts)-1) if replacement_case else 0,
                          owner_generations=2 if name=='stream-close-reopen' else 1)))


def collect(args, value):
    if args.reuse_report:
        return reseal(args,value)
    ledger=reference.failure_inventory(value)
    allowed={s['id'] for r in ledger['cases'] for s in r['subcases']}
    reference.require({s for targets,_ in PROBES.values() for s in targets} <= allowed,'process fixture outside ledger')
    reference.require(args.driver.is_absolute(), 'absolute native driver path required')
    reference.native_executable(args.driver)
    output=args.output.absolute()
    reference.require(not output.exists(),'process output must be fresh')
    report=dict(schema='cairn.semantic-process-refusals/1',ledger_sha256=value['failure_ledger']['sha256'],
        sources=ledger['source_sha256'],tool_sha256=reference.tool_pins(),driver_sha256=reference.digest(args.driver),
        fixture_sha256=reference.digest(reference.PROJECT/'scripts/semantic_refusal_fixture.py'),
        go_source_sha256=reference.digest(reference.PROJECT/'scripts/testdata/semantic-refusal-driver/main.go'),
        project_path=str(reference.PROJECT),cases=[],failed=[],model_execution=False,
        capture_provenance=dict(mode='new isolated execution'))
    with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,args.driver],output_dir=output) as isolated:
        shutil.copy2(args.driver,output/'driver')
        (output/'driver').chmod(0o555)
        for name in PROBES:
            folder=output/name;folder.mkdir()
            launcher=folder/'worker'
            launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 -I -S -B '+
                ' '.join(shlex.quote(str(x)) for x in [reference.PROJECT/'scripts/semantic_refusal_fixture.py',name,folder/'events.jsonl'])+'\n')
            launcher.chmod(0o700)
            before=reference.digest(launcher)
            result=isolated.run(['/usr/bin/strace','-ff','-ttt','-xx','-yy','-s','128','-e','trace=process,kill,read,write',
                '-o',str(folder/'trace'),str(output/'driver'),name,str(launcher),str(folder/'events.jsonl')],
                timeout=12,stdout_limit=4194304,role='S01b-Go-process-double',mode=name)
            (folder/'driver.stdout').write_bytes(result.stdout);(folder/'driver.stderr').write_bytes(result.stderr)
            try:
                reference.require(result.returncode==0,'Go fixture driver failed: '+name)
                observation=process_observation(folder,name)
            except ValueError as error:
                report['failed'].append(dict(id=name,error=str(error),driver_exit_code=result.returncode,
                    files_sha256={str(p.relative_to(output)):reference.digest(p) for p in folder.iterdir() if p.is_file()}))
                continue
            reference.require(reference.digest(launcher)==before,'fixture launcher changed')
            report['cases'].append(dict(id=name,invocation=result.invocation,observation=observation,
                files_sha256={str(p.relative_to(output)):reference.digest(p) for p in folder.iterdir() if p.is_file()}))
    report['receipts_sha256']={name:reference.digest(output/name) for name in ('cleanup.json','isolation-proof.json')}
    reference.require(report['driver_sha256']==reference.digest(output/'driver'),'native driver changed during execution')
    reference.require(report['tool_sha256']==reference.tool_pins(),'process collector changed during run')
    reference.save(output/'process-report.json',report)
    return report


PRIOR_FIELDS={'schema','ledger_sha256','sources','tool_sha256','driver_sha256','fixture_sha256',
              'go_source_sha256','project_path','cases','failed','model_execution','receipts_sha256'}
ACTOR_SOURCES=('semantic/command.go','semantic/stream.go','semantic/embedding_command.go',
               'core/query_projection.go','scripts/semantic_refusal_fixture.py',
               'scripts/testdata/semantic-refusal-driver/main.go')


def prior_report(value,path,sha):
    reference.require(sha and reference.digest(path)==sha,'pinned prior process report required')
    report=reference.load(path)
    reference.require(set(report)==PRIOR_FIELDS and report['schema']=='cairn.semantic-process-refusals/1' and
                      report['model_execution'] is False and report['failed']==[],
                      'unsupported prior process scope/projection claim')
    ledger=reference.failure_inventory(value)
    reference.require(all(report['sources'].get(name)==ledger['source_sha256'][name] for name in ACTOR_SOURCES) and
        report['fixture_sha256']==reference.digest(reference.PROJECT/'scripts/semantic_refusal_fixture.py') and
        report['go_source_sha256']==reference.digest(reference.PROJECT/'scripts/testdata/semantic-refusal-driver/main.go'),
        'retained process actor binding changed')
    ids=[row['id'] for row in report['cases']]
    reference.require(len(ids)==len(set(ids)) and set(ids)==set(PROBES),'missing/duplicate retained process cases')
    return report


def reseal(args,value):
    prior=prior_report(value,args.reuse_report,args.reuse_report_sha256)
    source=args.reuse_report.parent;output=args.output.absolute()
    reference.require(not output.exists(),'requalified process output must be fresh');output.mkdir(mode=0o700)
    for name in ('driver','cleanup.json','isolation-proof.json'):
        pin=prior['driver_sha256'] if name=='driver' else prior['receipts_sha256'][name]
        shutil.copy2(reference.pinned(source,name,pin),output/name)
    shutil.copy2(args.reuse_report,output/'prior-process-report.json')
    report=dict(prior,ledger_sha256=value['failure_ledger']['sha256'],
        sources=reference.failure_inventory(value)['source_sha256'],tool_sha256=reference.tool_pins(),cases=[],
        capture_provenance=dict(mode='requalified retained traces',prior_report_sha256=args.reuse_report_sha256,
            original_tool_sha256=prior['tool_sha256'],
            prior_projection='withdrawn: escaped descriptor and unfinished write prefixes were not decoded'))
    for row in prior['cases']:
        reference.require(set(row)=={'id','invocation','observation','files_sha256'},'retained process case fields changed')
        reference.pinned_case_files(source,row['id'],row['files_sha256'],{'worker','driver.stdout','driver.stderr'})
        (output/row['id']).mkdir()
        for relative,pin in row['files_sha256'].items():
            shutil.copy2(reference.pinned(source,relative,pin),output/relative)
        report['cases'].append(dict(row,observation=process_observation(output/row['id'],row['id'])))
    reference.require(report['tool_sha256']==reference.tool_pins(),'trace sealer changed during qualification')
    reference.save(output/'process-report.json',report)
    return report


def verify(value, path, expected_hash):
    reference.require(expected_hash and reference.digest(path)==expected_hash,'pinned process report required')
    report=reference.load(path);ledger=reference.failure_inventory(value)
    reference.require(set(report)==PRIOR_FIELDS|{'capture_provenance'} and
        report['schema']=='cairn.semantic-process-refusals/1' and report['model_execution'] is False,
        'unsupported process observed/accepted claim')
    reference.require(report['failed']==[],'process report retains failed probes; no complete tuple claim')
    reference.require(report['ledger_sha256']==value['failure_ledger']['sha256'] and report['sources']==ledger['source_sha256'] and
        report['tool_sha256']==reference.tool_pins() and report['fixture_sha256']==reference.digest(reference.PROJECT/'scripts/semantic_refusal_fixture.py') and
        report['go_source_sha256']==reference.digest(reference.PROJECT/'scripts/testdata/semantic-refusal-driver/main.go'),
        'process source/input binding changed')
    provenance=report['capture_provenance']
    if provenance==dict(mode='new isolated execution'):
        prior=None
    else:
        reference.require(set(provenance)=={'mode','prior_report_sha256','original_tool_sha256','prior_projection'} and
            provenance['mode']=='requalified retained traces' and
            provenance['prior_projection']=='withdrawn: escaped descriptor and unfinished write prefixes were not decoded',
            'unsupported process capture provenance')
        prior=prior_report(value,path.parent/'prior-process-report.json',provenance['prior_report_sha256'])
        reference.require(provenance['original_tool_sha256']==prior['tool_sha256'] and
            all(report[key]==prior[key] for key in ('driver_sha256','fixture_sha256','go_source_sha256','receipts_sha256','project_path')),
            'retained process provenance changed')
    ids=[r['id'] for r in report['cases']]
    reference.require(len(ids)==len(set(ids)) and set(ids)==set(PROBES),'missing/duplicate process cases')
    reference.pinned(path.parent,'driver',report['driver_sha256'])
    cleanup=reference.load(reference.pinned(path.parent,'cleanup.json',report['receipts_sha256']['cleanup.json']))
    proof=reference.load(reference.pinned(path.parent,'isolation-proof.json',report['receipts_sha256']['isolation-proof.json']))
    reference.require(cleanup['all_observed_processes_exited'] is True and cleanup['errors']==[] and
        cleanup['backing_removed'] is True and cleanup['launcher_exit_codes']==[0]*(len(ids)+1) and
        proof['wrapper_sha256']==value['source_sha256']['scripts/semantic_acceptance_isolation.py'] and
        proof['profile']=='default' and all(m['read_only'] for m in proof['read_only_mounts']) and
        cleanup['isolation_proof_sha256']==report['receipts_sha256']['isolation-proof.json'],
        'process cleanup/isolation changed')
    observed={}
    for row in report['cases']:
        reference.require(set(row)=={'id','invocation','observation','files_sha256'},'missing process disposition fields')
        folder=reference.pinned_case_files(path.parent,row['id'],row['files_sha256'],
            {'worker','driver.stdout','driver.stderr'})
        reference.require(list(folder.glob('trace.*')), 'unsealed process trace')
        if prior is not None:
            original=next(r for r in prior['cases'] if r['id']==row['id'])
            reference.require(row['invocation']==original['invocation'] and row['files_sha256']==original['files_sha256'],
                              'retained process raw capture changed')
        actual=process_observation(folder,row['id'])
        reference.require(actual==row['observation'] and row['invocation'] in cleanup['invocations'] and
            row['invocation']['role']=='S01b-Go-process-double' and row['invocation']['mode']==row['id'],
            'process observation disagrees with raw tuples')
        observed[row['id']]=actual
    result={}
    for subcase in {s for targets,_ in PROBES.values() for s in targets}:
        names=[name for name,(targets,_) in PROBES.items() if subcase in targets]
        result[subcase]=dict(id=subcase,scope='Go-supervisor/process-double',freshness='reused',
                             capture_origin=report['capture_provenance'],
                             complete=all(observed[name]['disposition']['stdout_prefix']['state']=='observed' for name in names),
                             probes=[observed[name] for name in names])
    return result
