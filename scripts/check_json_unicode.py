"""Check serialized request Unicode at both CLI entry points in the disposable store."""
import hashlib
import json
import subprocess
import uuid


def check(binary, root, env, call):
    agent = ['agent', '--token-file', str(root / 'hosted-agent.token'), 'evidence']
    client_env = dict(env, CAIRN_DATABASE_URL='host=/nonexistent-json-client dbname=denied')
    for args in [agent, ['capture-evidence']]:
        selected_env = client_env if args == agent else env
        for encoded in [b'"\xff"', b'"\\ud800"', b'"\\udc00"']:
            request = dict(request_id=str(uuid.uuid4()), repo='fixture:socket',
                           source='Selected Unicode transport source', body='MARKER')
            raw = json.dumps(request).encode().replace(b'"MARKER"', encoded)
            response = subprocess.run([binary, *args], input=raw, env=selected_env,
                                      capture_output=True, timeout=10)
            assert response.returncode != 0 and json.loads(response.stdout)['status'] == 'INVALID_REQUEST'
            request['body'] = 'Valid selected source: é 😀 �\r\n'
            saved = call(args, request, selected_env)
            assert saved['sha256'] == hashlib.sha256(request['body'].encode()).hexdigest()
        for encoded, decoded in [(b'"\\ud83d\\ude00"', '😀'),
                                 (b'"\\\\ud800"', '\\ud800'),
                                 (b'"\\ufffd"', '�')]:
            request = dict(request_id=str(uuid.uuid4()), repo='fixture:socket',
                           source='Selected valid escaped source', body='MARKER')
            raw = json.dumps(request).encode().replace(b'"MARKER"', encoded)
            response = subprocess.run([binary, *args], input=raw, env=selected_env,
                                      capture_output=True, check=True, timeout=10)
            saved = json.loads(response.stdout)['data']
            assert saved['sha256'] == hashlib.sha256(decoded.encode()).hexdigest()
            assert call(['evidence', saved['evidence_id']], {})['body'] == decoded
    print('Both CLIs reject lossy serialized Unicode without reserving capture IDs and preserve valid escapes and literal replacement characters')
