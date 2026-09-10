"""Exercise retained versions through either ordinary native tool interface."""
import hashlib
import uuid


def check(invoke, large_record_id=None):
    marker = 'history' + uuid.uuid4().hex
    original = marker + ': preserve earlier guidance 日本語'
    saved = invoke('remember', dict(request_id=str(uuid.uuid4()), body=original, shareable=True))
    record_id = saved['record_id']
    invoke('edit', dict(request_id=str(uuid.uuid4()), record_id=record_id,
                        expected_version=1, body=marker + ': updated guidance'))
    first = invoke('history', dict(record_id=record_id, limit=1))
    assert first['historical'] and first['current_version'] == 2 and first['current_class'] == 'A'
    assert first['more'] and first['next_before_version'] == 2
    assert [v['version'] for v in first['versions']] == [2] and 'body' not in first['versions'][0]
    invoke('edit', dict(request_id=str(uuid.uuid4()), record_id=record_id,
                        expected_version=2, body=marker + ': third wording'))
    next_page = invoke('history', dict(record_id=record_id, before_version=first['next_before_version']))
    assert next_page['current_version'] == 3 and not next_page['more']
    assert [v['version'] for v in next_page['versions']] == [1]
    exact = invoke('history', dict(record_id=record_id, version=1))
    assert exact['historical'] and exact['current_version'] == 3
    assert exact['versions'][0]['body'] == original
    assert exact['versions'][0]['body_sha256'] == hashlib.sha256(original.encode()).hexdigest()
    assert exact['versions'][0]['body_bytes'] == len(original.encode())
    empty = invoke('history', dict(record_id=record_id, before_version=1))
    assert not empty['more'] and not empty['versions']
    invoke('history', dict(record_id=record_id, version=4), 'NOT_FOUND')
    for fields in ({'version': -1}, {'limit': 101}, {'version': 1, 'limit': 1},
                   {'version': 1, 'before_version': 2}):
        invoke('history', dict(record_id=record_id, **fields), 'INVALID_REQUEST')
    invoke('edit', dict(request_id=str(uuid.uuid4()), record_id=record_id,
                        expected_version=1, body='Historical version cannot overwrite current wording'), 'VERSION_CONFLICT')
    private = invoke('remember', dict(request_id=str(uuid.uuid4()), body=marker + ': private'))
    for fields in ({}, {'version': 1}):
        invoke('history', dict(record_id=private['record_id'], **fields), 'NOT_FOUND')
    if large_record_id is None:
        large_record_id = invoke('remember', dict(request_id=str(uuid.uuid4()), body='z' * 65536, shareable=True))['record_id']
    assert invoke('history', dict(record_id=large_record_id))['versions'][0]['body_bytes'] == 65536
    invoke('history', dict(record_id=large_record_id, version=1), 'BUDGET_REFUSED')
    print('Native history preserves exact old bodies, stable metadata paging, privacy, stale-edit refusal and output budgets')
