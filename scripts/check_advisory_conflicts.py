"""Qualified competing positions through the installed ordinary tool protocols."""
import json
import uuid

from check_note_transport import operator


def check_harness(invoke, binary, environment):
    marker = 'disputedstrategy' + uuid.uuid4().hex
    first = marker + ': reuse a connection. ' + 'First position rationale. ' * 12
    second = 'Create a fresh connection for isolation. ' + 'Competing rationale. ' * 12
    a = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), body=first, kind='decision', shareable=True))
    b = invoke('cairn_remember', dict(request_id=str(uuid.uuid4()), body=second, shareable=True))
    group = operator(binary, environment, 'dispute', dict(request_id=str(uuid.uuid4()),
        record_ids=[a['record_id'], b['record_id']], reason='PRIVATE-OPENING-DETAIL'))
    assert not invoke('cairn_search', dict(query=marker))['index']
    view = invoke('cairn_search', dict(query=marker, advisory_conflicts=True, kinds=['decision']))
    assert view['source_schema'] == 'cairn.semantic/14' and view['advisory_conflicts'] is True
    assert {e['record_id'] for e in view['index']} == {a['record_id'], b['record_id']}
    assert 'PRIVATE-OPENING-DETAIL' not in json.dumps(view)
    for entry in view['index']:
        assert entry['conflicts'][0]['conflict_id'] == group['conflict_id']
        assert 'summary_span' not in entry
        pulled = invoke('cairn_pull', entry['pull_arguments'])
        assert pulled == invoke('cairn_pull', entry['pull_arguments'])
        assert pulled['selection']['record']['record_id'] == entry['record_id']
        positions = [pulled['selection'], *pulled['competing']]
        assert {p['record']['record_id']: p['record']['body'] for p in positions} == {
            a['record_id']: first, b['record_id']: second}
        assert all(p['conflicts'] == entry['conflicts'] for p in positions)
        assert 'PRIVATE-OPENING-DETAIL' not in json.dumps(pulled)
    print('Ordinary tools omit disputes by default; explicit search and either pull preserve both complete positions and private opening context')
