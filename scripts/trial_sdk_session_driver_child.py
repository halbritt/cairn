"""Fixed ordinary EOF-dependent session surrogate; no native CLI or provider."""
import json
import os
import sys

SESSION = '00000000-0000-4000-8000-000000000003'
MODE = sys.argv[1]
assert MODE in ('normal', 'error', 'premature', 'causal', 'close_unknown',
                'source_drift', 'duplicate', 'post_control')


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode() + b'\n'


def send(value):
    body = wire(value)
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


result = dict(type='result', session_id=SESSION,
    subtype='error_max_turns' if MODE == 'error' else 'success', is_error=MODE == 'error',
    result='Surrogate output only; task not graded.')
initial = receive()
assert initial['request']['subtype'] == 'initialize'
if MODE == 'premature':
    send(result)
else:
    send(dict(type='control_response', response=dict(subtype='success',
        request_id=initial['request_id'], response={})))
    assert receive()['type'] == 'user'
    for index, event in enumerate(('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure')):
        ident = 'session-' + str(index)
        request = dict(type='control_request', request_id=ident, request=dict(
            subtype='hook_callback', callback_id=initial['request']['hooks'][event][0]['hookCallbackIds'][0],
            input=dict(hook_event_name=event, session_id=SESSION, extra='full canonical input: café')))
        if MODE == 'causal' and index == 3:
            batch = wire(request) + wire(result)
            assert len(batch) <= 512 and os.write(1, batch) == len(batch)
        else:
            send(request)
        reply = receive()
        assert reply['response']['request_id'] == ident
        assert reply['response']['response']['hookSpecificOutput']['additionalContext'] == 'ordinary context: café'
    if MODE != 'causal':
        send(result)
    if MODE == 'duplicate':
        send(result)
    if MODE == 'post_control':
        send(dict(type='control_cancel_request', request_id='session-3'))
# This process cannot normally exit until the parent actually ends native input.
assert os.read(0, 512) == b''
os.write(2, b'input-eof-observed\n')
