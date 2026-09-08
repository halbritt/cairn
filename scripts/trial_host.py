"""Observed wrapper attempts for the opt-in OpenCode trial controller."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def invoke(binary, environment, command, request=None, arguments=()):
    result = subprocess.run([str(binary), command, *map(str, arguments)],
                            input=None if request is None else json.dumps(request).encode(),
                            env=environment, capture_output=True, check=False, timeout=40)
    response = json.loads(result.stdout)
    if result.returncode or not response['ok']:
        raise RuntimeError(command + ': ' + response['status'] + ': ' + response.get('message', ''))
    return response.get('data')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


class TrialHost:
    def __init__(self, root, client, environment, scope):
        self.root = Path(root)
        self.root.mkdir(mode=0o700)
        sync_directory(self.root.parent)
        self.client = list(map(str, client))
        self.environment = dict(environment)
        self.scope = dict(scope)
        self.attempt_id = str(uuid.uuid4())
        self.request_id = str(uuid.uuid4())
        self.result = None
        write_json(self.root / 'identity.json', dict(attempt_id=self.attempt_id, request_id=self.request_id, scope=self.scope))

    def call(self, operation, request):
        return invoke(self.client[0], self.environment, self.client[1], request,
                      [*self.client[2:], operation])

    def observation(self, operation, request):
        pending = self.root / (operation + '.pending.json')
        write_json(pending, request)
        response = self.call(operation, request)
        expected_state = 'running' if operation == 'spawn' else request['state']
        if response != dict(attempt_id=self.attempt_id, state=expected_state):
            raise RuntimeError('host observation response does not match its attempt and state')
        write_json(self.root / (operation + '.response.json'), response)
        pending.rename(self.root / (operation + '.confirmed.json'))
        sync_directory(self.root)
        return response

    def run(self, arguments, timeout):
        command = [*self.client, 'run', '--repo', self.scope['repo'],
                   '--task', self.scope['task_id'], '--run', self.scope['run_id'],
                   '--attempt-id', self.attempt_id, '--request-id', self.request_id, *arguments]
        write_json(self.root / 'intent.json', dict(
            attempt_id=self.attempt_id, request_id=self.request_id, scope=self.scope,
            command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest()))
        read_fd, write_fd = os.pipe()
        process = None
        # The observed PID becomes the Cairn wrapper by exec, only after the
        # host confirms spawn. EOF after host failure never releases the child.
        launcher = ('import os,sys; fd=int(sys.argv[1]); ready=os.read(fd,1); os.close(fd); '
                    'sys.exit(125) if ready!=b"1" else os.execv(sys.argv[2],sys.argv[2:])')
        try:
            process = subprocess.Popen([sys.executable, '-c', launcher, str(read_fd), *command],
                                       env=self.environment, pass_fds=(read_fd,),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.close(read_fd)
            read_fd = None
            write_json(self.root / 'process-started.json', dict(pid=process.pid))
            self.observation('spawn', dict(request_id=str(uuid.uuid4()), attempt_id=self.attempt_id,
                                          dispatcher='cairn:opencode-trial-host',
                                          delegate='cairn:opencode-trial-wrapper', scope=self.scope))
            os.write(write_fd, b'1')
            os.close(write_fd)
            write_fd = None
            stdout, stderr = process.communicate(timeout=timeout)
            self.result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            self.record_process(process.pid)
            return self.result
        except BaseException:
            if write_fd is not None:
                os.close(write_fd)
                write_fd = None
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate(timeout=5)
                self.result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                if not (self.root / 'process.json').exists():
                    self.record_process(process.pid)
                self.retain_terminal()
            raise
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)

    def record_process(self, pid):
        write_json(self.root / 'process.json', dict(
            pid=pid, returncode=self.result.returncode,
            stdout_sha256=hashlib.sha256(self.result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(self.result.stderr).hexdigest()))

    def terminal_request(self, result_ref):
        code = self.result.returncode
        state = {124: 'timeout', 130: 'killed'}.get(code, 'killed' if code < 0 else 'failed')
        if code == 0:
            state = 'completed' if result_ref else 'no_result'
        return dict(request_id=str(uuid.uuid4()), attempt_id=self.attempt_id,
                    state=state, result_ref=result_ref)

    def retain_terminal(self):
        if self.result is None:
            return
        if any((self.root / name).exists() for name in ('terminal.pending.json', 'terminal.confirmed.json')):
            return
        if any((self.root / name).exists() for name in ('spawn.pending.json', 'spawn.confirmed.json')):
            # Recovery confirms the original spawn before the terminal. No
            # candidate correspondence is inferred from failed postprocessing.
            write_json(self.root / 'terminal.pending.json', self.terminal_request(''))

    def finish(self, result_ref):
        if self.result is None:
            raise RuntimeError('cannot finalize an unobserved process')
        return self.observation('terminal', self.terminal_request(result_ref))
