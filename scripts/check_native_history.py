"""Exercise retained versions through either ordinary native tool interface."""
import hashlib
import base64
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
    fragment_offset = len(original.encode()) - 2
    fragment = invoke('history', dict(record_id=record_id, version=1,
                                     span=dict(offset=fragment_offset, length=1)))['versions'][0]
    assert 'body' not in fragment and 'body' not in fragment['span']
    assert base64.b64decode(fragment['span']['body_base64']) == original.encode()[fragment_offset:fragment_offset + 1]
    assert fragment['body_sha256'] == exact['versions'][0]['body_sha256']
    empty = invoke('history', dict(record_id=record_id, before_version=1))
    assert not empty['more'] and not empty['versions']
    invoke('history', dict(record_id=record_id, version=4), 'NOT_FOUND')
    for fields in ({'version': -1}, {'limit': 101}, {'version': 1, 'limit': 1},
                   {'version': 1, 'before_version': 2}, {'span': {'offset': 0, 'length': 1}},
                   {'version': 1, 'span': {'offset': len(original.encode()), 'length': 1}},
                   {'version': 1, 'span': {'offset': 0, 'length': 0}}):
        invoke('history', dict(record_id=record_id, **fields), 'INVALID_REQUEST')
    invoke('edit', dict(request_id=str(uuid.uuid4()), record_id=record_id,
                        expected_version=1, body='Historical version cannot overwrite current wording'), 'VERSION_CONFLICT')
    private = invoke('remember', dict(request_id=str(uuid.uuid4()), body=marker + ': private'))
    for fields in ({}, {'version': 1}, {'version': 1, 'span': {'offset': 0, 'length': 1}}):
        invoke('history', dict(record_id=private['record_id'], **fields), 'NOT_FOUND')
    if large_record_id is None:
        large_record_id = invoke('remember', dict(request_id=str(uuid.uuid4()), body='z' * 65536, shareable=True))['record_id']
    assert invoke('history', dict(record_id=large_record_id))['versions'][0]['body_bytes'] == 65536
    invoke('history', dict(record_id=large_record_id, version=1), 'BUDGET_REFUSED')
    excerpt = invoke('history', dict(record_id=large_record_id, version=1,
                                    span=dict(offset=65500, length=128)))
    version = excerpt['versions'][0]
    assert excerpt['historical'] and version['version'] == 1 and 'body' not in version
    assert version['body_bytes'] == 65536
    assert version['body_sha256'] == hashlib.sha256(b'z' * 65536).hexdigest()
    assert version['span'] == dict(offset=65500, end=65536, total_bytes=65536,
                                   sha256=hashlib.sha256(b'z' * 36).hexdigest(), body='z' * 36)
    print('Native history preserves exact old bodies, bounded excerpts, stable metadata paging, privacy, stale-edit refusal and output budgets')
