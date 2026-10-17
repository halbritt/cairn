"""Fixed ordinary protocol surrogate; never the native CLI or a provider."""
import json
import os
import sys
import select


def send(value):
    body = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
    while body:
        count = os.write(1, body[:7])
        body = body[count:]


def receive():
    body = bytearray()
    while not body.endswith(b'\n'):
        part = os.read(0, 1)
        if not part or len(body) >= 8500:
            raise ValueError('surrogate_input')
        body.extend(part)
    return json.loads(body)


initial = receive()
assert initial['type'] == 'control_request'
hooks = initial['request']['hooks']
send(dict(type='control_response', response=dict(subtype='success',
    request_id=initial['request_id'], response={})))
mode = sys.argv[1] if len(sys.argv) == 2 else 'normal'
if mode == 'flood':
    size = 0
    while not select.select([0], [], [], 0)[0]:
        body = b'{"type":"surrogate_flood","content":"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}\n'
        size += len(body)
        if size > 4 * 1024 * 1024:
            raise ValueError('surrogate_flood_bound')
        os.write(1, body)
user = receive()
assert user['type'] == 'user'
session = '00000000-0000-4000-8000-000000000004'
if mode == 'exit_document':
    send(dict(type='surrogate_document', content='after actual wait 😀'))
    raise SystemExit(0)
if mode == 'partial_exit':
    os.write(1, b'{"type":')
    raise SystemExit(0)
if mode == 'eof_alive':
    os.close(1)
    os.close(2)
    while os.read(0, 512):
        pass
    raise SystemExit(0)
if mode == 'batch':
    batch = [dict(type='control_request', request_id=ident, request=dict(
        subtype='hook_callback', callback_id=hooks['SessionStart'][0]['hookCallbackIds'][0],
        input=dict(hook_event_name='SessionStart', session_id=session))) for ident in ('a', 'b')]
    batch.append(dict(type='tail', content='unchanged😀'))
    body = b''.join(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'
                    for value in batch)
    assert len(body) <= 512
    assert os.write(1, body) == len(body)
    assert receive()['response']['request_id'] == 'a'
    assert os.write(2, b'Z' * 512) == 512
    assert receive()['response']['request_id'] == 'b'
for index, event in enumerate(('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure')):
    callback = hooks[event][0]['hookCallbackIds'][0]
    request_id = 'surrogate-' + str(index)
    send(dict(type='control_request', request_id=request_id, request=dict(
        subtype='hook_callback', callback_id=callback,
        input=dict(hook_event_name=event, session_id=session, extra='café-😀', nested=[{'full': True}]),
        issued_at=0, deadline_ms=999999999)))
    if mode == 'exit_callback':
        raise SystemExit(0)
    reply = receive()
    assert reply['type'] == 'control_response'
    assert reply['response']['request_id'] == request_id
    assert reply['response']['response']['hookSpecificOutput']['additionalContext'] == 'surrogate context: café'
    os.write(2, b'observed-reply\n')
send(dict(type='surrogate_document', content='strict UTF-8 😀'))
# A direct-child exit is deliberately independent of parent stdin closure.
