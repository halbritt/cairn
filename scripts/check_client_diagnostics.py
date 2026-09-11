"""Check client failure messages through the actual CLI and MCP entry points."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from check_mcp import session


def check(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='cairn-client-') as directory:
        root = Path(directory)
        token = root / 'hosted-agent.token'
        socket = root / 'api.sock'
        secret = 'private-client-token-canary'
        environment = dict(os.environ, HOME=directory, CAIRN_HOME=directory,
                           CAIRN_DATABASE_URL='private-database-diagnosis-canary')

        def invoke(command):
            result = subprocess.run([binary, *command], env=environment, cwd=directory,
                                    input='', text=True, capture_output=True, timeout=5)
            for private in (directory, secret, environment['CAIRN_DATABASE_URL']):
                assert private not in result.stdout + result.stderr, result
            return result

        agent = ['agent', '--socket', str(socket), '--token-file', str(token), 'version']
        missing = invoke(agent)
        body = json.loads(missing.stdout)
        assert missing.returncode == 7 and not body['ok'], missing
        assert body['status'] == 'CLIENT_SETUP_FAILED' and 'token file' in body['message'], body
        assert 'configured token-file path and access' in body['message'], body

        startup = invoke(['mcp', '--socket', str(socket), '--token-file', str(token),
                          '--repo', 'fixture:socket', '--task', 'diagnosis', '--run', 'stdio'])
        assert startup.returncode == 1 and not startup.stdout, startup
        assert 'CLIENT_SETUP_FAILED' in startup.stderr and 'token file' in startup.stderr, startup

        token.write_text(secret)
        token.chmod(0o600)
        disconnected = invoke(agent)
        body = json.loads(disconnected.stdout)
        assert disconnected.returncode == 7 and not body['ok'], disconnected
        assert body['status'] == 'API_CONNECTION_FAILED' and 'socket and service' in body['message'], body
        with session(binary, root, environment) as tool:
            message = tool('cairn_search', dict(query='diagnosis'), error=True)
            assert 'API_CONNECTION_FAILED' in message and 'socket and service' in message, message
            assert directory not in message and secret not in message, message

        token.chmod(0o644)
        unsafe = invoke(agent)
        body = json.loads(unsafe.stdout)
        assert unsafe.returncode == 2 and body['status'] == 'INVALID_REQUEST', unsafe

        # Unknown errors must still be masked, including malformed operator DSNs.
        unknown = invoke(['list'])
        body = json.loads(unknown.stdout)
        assert unknown.returncode == 7 and body['status'] == 'STORE_ERROR', unknown
        assert body['message'] == 'operation failed; inspect the local store and task runtime', body
    print('CLI and MCP identify token/socket failures without private paths or credentials; unknown errors stay masked')


if __name__ == '__main__':
    check(sys.argv[1])
