"""Validate source-table integrity/arithmetic. Success does NOT admit a run."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess


def _source_blob(root, revision, rel):
    """Read one regular source file from the table's immutable Git snapshot."""
    assert isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{40}', revision)
    resolved = subprocess.run(['git', 'rev-parse', '--verify', revision + '^{commit}'],
                              cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert resolved.returncode == 0 and resolved.stdout.decode().strip() == revision
    listing = subprocess.run(['git', 'ls-tree', '-z', '--full-tree', revision, '--', rel],
                             cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert listing.returncode == 0
    entries = listing.stdout.split(b'\0')
    entries = [entry for entry in entries if entry]
    assert len(entries) == 1
    metadata, path = entries[0].split(b'\t', 1)
    mode, kind, object_id = metadata.decode('ascii').split()
    assert path.decode('utf-8') == rel and mode in ('100644', '100755') and kind == 'blob'
    source = subprocess.run(['git', 'cat-file', 'blob', object_id],
                            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert source.returncode == 0
    return source.stdout


def validate(table, root):
    assert table['schema']=='cairn.owner-phase-table/1'
    assert table['caps']=={'raw':131072,'combined':536870912}
    assert table['decision']=='RETAIN_FULL_STANDING_REFUSAL_NO_TRANSITION'
    revision = table.get('source_snapshot')
    assert revision is not None
    for rel,pin in table['sources'].items():
        assert rel.startswith('scripts/') and '..' not in Path(rel).parts
        data = _source_blob(root, revision, rel)
        assert len(data)<=65536 and len(data)==pin['bytes']
        assert hashlib.sha256(data).hexdigest()==pin['sha256']
        units={}
        for node in ast.parse(data).body:
            if isinstance(node,(ast.FunctionDef,ast.ClassDef)):
                units[node.name]=[node.lineno,node.end_lineno]
                if isinstance(node,ast.ClassDef):
                    for method in node.body:
                        if isinstance(method,ast.FunctionDef):units[node.name+'.'+method.name]=[method.lineno,method.end_lineno]
        assert all(units.get(name)==span for name,span in pin['units'].items())
    owners=table['ticket_rows'];backings={b['id']:b for b in table['backings']}
    assert len(backings)==len(table['backings'])==21
    for backing in backings.values():
        assert backing['physical_status']=='unproved' and backing['missing_evidence']
        assert set(backing['tickets'])<=owners.keys()
        ref=backing['source'];assert table['sources'][ref['path']]['units'][ref['unit']]==ref['lines']
    phases={p['id']:p for p in table['phases']};assert len(phases)==len(table['phases'])==56
    for phase in phases.values():
        assert phase['physical_upper'] is None and phase['transition_allowed'] is False
        assert phase['backings'] and set(phase['backings'])<=backings.keys()
        if phase['id'] != 'preparation':
            assert {'private_stream','paired_selection'} <= set(phase['backings'])
        if phase['id'].startswith('private_evidence_'):assert 'evidence_publication' in phase['backings']
        tickets=[r['ticket'] for r in phase['reservations']]
        assert len(tickets)==len(set(tickets))
        assert phase['raw_total']==sum(r['raw'] for r in phase['reservations'])
        assert phase['working_total']==sum(r['working'] for r in phase['reservations'])
        assert phase['combined_total']==phase['raw_total']+phase['working_total']
        assert phase['raw_cap_fits']==(phase['raw_total']<=131072)
        assert phase['combined_cap_fits']==(phase['combined_total']<=536870912)
        denied=not phase['raw_cap_fits'] or not phase['combined_cap_fits']
        assert phase['admission']==('denied_by_current_reservations' if denied else 'unproved_native_and_physical')
        for row in phase['reservations']:
            original=owners[row['ticket']]
            if row['ticket']=='C':
                assert row['raw'] in (63067,5723,38491)
                assert row['working']==original['working']+phase['prior_completed_commands']*64*1048576
            else:assert (row['raw'],row['working'])==(original['raw'],original['working'])
        for key in ('uncertain_publication','uncertain_parent_release'):
            if key in phase:
                record=phase[key];assert record['old'] in phases and record['proposed']==phase['id'] and record['rule']
    expected={'preparation','service_authentication','first_hook_before_user','suspended_producer','later_callback_after_user'}
    expected.update('command_'+str(k+1) for k in range(5))
    expected.update(name+'_'+str(k) for k in range(5) for name in ('reply_detach','interrupted_command','before_status_old','before_status_proposed','parent_retired','request_return','terminal_recheck','cleanup_retained'))
    expected.add('paired_preparation')
    expected.update('private_evidence_'+str(k) for k in range(5))
    assert set(phases)==expected
    for entry in table['profile_evidence']:
        ref=entry['source'];assert table['sources'][ref['path']]['units'][ref['unit']]==ref['lines']
    for k in range(5):
        for target,field,old in [('before_status_proposed','uncertain_publication','before_status_old'),('parent_retired','uncertain_parent_release','before_status_proposed'),('request_return','uncertain_publication','parent_retired')]:
            assert phases[target+'_'+str(k)][field]['old']==old+'_'+str(k)
        assert phases['command_'+str(k+1)]['prior_completed_commands']==k
        for name in ('reply_detach','interrupted_command','before_status_old'):
            assert phases[name+'_'+str(k)]['prior_completed_commands']==k
        for name in ('before_status_proposed','parent_retired','request_return'):
            assert phases[name+'_'+str(k)]['prior_completed_commands']==k+1
        for name in ('terminal_recheck','cleanup_retained'):
            assert phases[name+'_'+str(k)]['completed_commands_in_retired_callback']==k
    assert phases['first_hook_before_user']['raw_total']==136353
    assert phases['command_1']['raw_total']==153325
    assert not phases['command_5']['combined_cap_fits']
    assert phases['command_5']['admission']=='denied_by_current_reservations'
    return dict(status='TABLE_VALID_BOUNDS_INCOMPLETE',phase_count=len(phases),backing_count=len(backings),source_count=len(table['sources']),transition_allowed=False,unknown_backings=sorted(backings))


if __name__=='__main__':
    root=Path(__file__).resolve().parent.parent
    path=root/'docs/sdk-owner-phase-table.json'
    assert path.stat().st_size<=65536
    print(json.dumps(validate(json.loads(path.read_text()),root),indent=2))
