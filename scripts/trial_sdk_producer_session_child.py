"""Fixed SDK-shaped surrogate requiring four actual independent producer replies."""
import json
import os

SESSION = '00000000-0000-4000-8000-000000000003'


def receive(prefix=b''):
    body = bytearray(prefix)
    while not body.endswith(b'\n'):
        block = os.read(0,1)
        if not block or len(body) >= 8500:
            raise ValueError('surrogate_input')
        body.extend(block)
    return json.loads(body)


def send(value):
    wire = json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
    with memoryview(wire) as view:
        while view:
            count = os.write(1,view[:512])
            assert count > 0
            view = view[count:]


def main():
    initial = receive()
    send(dict(type='control_response',response=dict(subtype='success',
        request_id=initial['request_id'],response={})))
    prefix = bytearray()
    while len(prefix) < 512:
        part = os.read(0,512-len(prefix))
        assert part
        prefix.extend(part)
    for index,event in enumerate(('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure')):
        extra = 'Full callback content: café 😀' + ('é'*35000 if index else '')
        send(dict(type='control_request',request_id='combined-'+str(index),request=dict(
            subtype='hook_callback',callback_id=initial['request']['hooks'][event][0]['hookCallbackIds'][0],
            input=dict(hook_event_name=event,session_id=SESSION,extra=extra))))
        if not index:
            assert receive(prefix)['type'] == 'user'
        remaining = memoryview(b'native stderr during producer\n'*2048)
        while remaining:
            count = os.write(2,remaining[:512])
            assert count > 0
            remaining = remaining[count:]
        reply = receive()
        assert reply['response']['request_id'] == 'combined-'+str(index)
        assert reply['response']['response']['hookSpecificOutput']['additionalContext'] == 'ordinary producer context: café 😀'
    send(dict(type='result',session_id=SESSION,subtype='success',is_error=False,
              result='ordinary combined mechanism only'))
    assert os.read(0,512) == b''
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
