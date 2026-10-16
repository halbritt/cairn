"""Explicit bounded Linux owned-endpoint check with retained exact source snapshots."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid

def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        a = os.fstat(fd)
        assert stat.S_ISREG(a.st_mode) and a.st_size < 65536
        assert not (a.st_dev == 64512 and a.st_ino in {22838820,22946591,22946592,22981266,22981268})
        body = os.read(fd, a.st_size + 1)
        z = os.fstat(fd)
        assert len(body) == a.st_size and (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns,a.st_ctime_ns) == (z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns,z.st_ctime_ns)
        return body
    finally:
        os.close(fd)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    options = parser.parse_args()
    root = Path(__file__).resolve().parent
    out = Path(options.output)
    if not out.is_absolute() or '..' in out.parts or not out.parent.is_dir():
        raise ValueError('absolute_fresh_output_required')
    if os.path.lexists(out):
        raise ValueError('consumed_output')
    names = ('trial_sdk_peer.py','trial_sdk_peer_host_cases.py','guard_trial_sdk_peer.py','check_trial_sdk_peer.py')
    def pins():
        return {name: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
                for name in names for body in [read(root / name)]}


    before = pins()
    out.mkdir(mode=0o700)
    command = ['/usr/bin/python3', '-B', '-c',
               "import guard_trial_sdk_peer,runpy;runpy.run_path('trial_sdk_peer_host_cases.py',run_name='__main__')"]
    with (out / 'INTENT.json').open('x') as f:
        json.dump({'request_id': str(uuid.uuid4()), 'command': command, 'pins': before,
                   'scope': 'ordinary owned AF_UNIX endpoint tests only; no SDK/native CLI/provider effects'}, f, indent=2)
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for name in names:
        body = read(root / name)
        if {'bytes':len(body), 'sha256':hashlib.sha256(body).hexdigest()} != before[name]:
            raise ValueError('source_drift_before_launch')
        with (snapshot / name).open('xb') as f:
            f.write(body)
    start = time.monotonic()
    child = subprocess.Popen(command, cwd=snapshot, env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    selector = selectors.DefaultSelector()
    logs = {'stdout': bytearray(), 'stderr': bytearray()}
    for name in logs:
        pipe = getattr(child, name)
        os.set_blocking(pipe.fileno(), False)
        selector.register(pipe, selectors.EVENT_READ, name)
    reason = None
    while selector.get_map():
        if time.monotonic() - start >= 10 and reason is None:
            os.killpg(child.pid, signal.SIGKILL)
            reason = 'timeout'
        for key, _ in selector.select(.01):
            block = os.read(key.fd, 4096)
            if not block:
                selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            room = 65536 - sum(map(len, logs.values()))
            logs[key.data].extend(block[:room])
            if len(block) > room and reason is None:
                os.killpg(child.pid, signal.SIGKILL)
                reason = 'output_bound'
    try:
        code = child.wait(timeout=max(.001, 10 - (time.monotonic() - start)))
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        reason = 'timeout'
        code = child.wait(timeout=2)
    selector.close()
    stable = before == pins()
    for name, body in logs.items():
        with (out / (name + '.log')).open('xb') as f:
            f.write(body)
    result = {'owned_pid':child.pid, 'actual_wait':code, 'seconds':time.monotonic()-start,
              'kill_reason':reason, 'pins_stable':stable,
              'logs':{name:{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
                      for name,body in logs.items()}}
    with (out / 'RESULT.json').open('x') as f:
        json.dump(result, f, indent=2)
    print(logs['stderr'].decode())
    print(json.dumps(result))
    raise SystemExit(code if code else int(reason is not None or not stable))

if __name__ == '__main__':
    main()
