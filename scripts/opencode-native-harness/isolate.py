"""Create a unique, credential-free loopback namespace; never fall back to host."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
PATH = '/usr/bin:/bin:/usr/sbin'
# This constant is the only program run with privilege. User code runs only
# after setpriv drops UID, groups/capabilities and privilege acquisition.
SETUP = r'''
set -eu
uid=$1; gid=$2; groups=$3; root=$4; verifier=$5
shift 5
/usr/sbin/ip link set dev lo up
namespace=$(/usr/bin/readlink /proc/self/ns/net)
exec /usr/bin/setpriv --reuid "$uid" --regid "$gid" --groups "$groups" \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all --no-new-privs \
  /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin LANG=C.UTF-8 \
  HOME="$root/home" XDG_CONFIG_HOME="$root/config" XDG_DATA_HOME="$root/data" \
  XDG_CACHE_HOME="$root/cache" XDG_STATE_HOME="$root/state" XDG_RUNTIME_DIR="$root/runtime" \
  TMPDIR="$root/tmp" HARNESS_FIXTURE_ROOT="$root" \
  /usr/bin/python3 -I "$verifier" "$root" "$namespace" "$@"
'''


def start_time(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def launch(command):
    if not command or not Path(command[0]).is_absolute():
        raise ValueError('an absolute workload executable is required')
    uid, gid = os.getuid(), os.getgid()
    if uid == 0 or os.geteuid() != uid or os.getegid() != gid:
        raise ValueError('run as the original unprivileged user')
    for fd in (0, 1, 2):
        if stat.S_ISSOCK(os.fstat(fd).st_mode):
            raise ValueError('inherited socket stdio is not supported')
    groups = sorted(set(os.getgroups()))
    root = Path(tempfile.mkdtemp(prefix='cairn-opencode-harness-', dir='/tmp'))
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'tmp', 'workspace'):
        (root / name).mkdir(mode=0o700)
    metadata = dict(uid=uid, gid=gid, groups=groups, host_netns=os.readlink('/proc/self/ns/net'),
                    parent_pid=os.getpid(), parent_start=start_time(os.getpid()))
    (root / 'launch.json').write_text(json.dumps(metadata))
    print(json.dumps(dict(harness_root=str(root))), file=sys.stderr, flush=True)
    args = ['/usr/bin/sudo', '-n', '--', '/usr/bin/env', '-i', f'PATH={PATH}', 'LANG=C.UTF-8',
            '/usr/bin/unshare', '--net', '--', '/bin/sh', '-c', SETUP, 'harness-setup',
            str(uid), str(gid), ','.join(map(str, groups)), str(root), str(HERE / 'verify.py'), *command]
    # close_fds prevents inherited host-connected descriptors from entering the namespace.
    result = subprocess.run(args, env={'PATH': PATH, 'LANG': 'C.UTF-8'}, close_fds=True)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(launch(sys.argv[1:]))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'FAIL-CLOSED: {exc}', file=sys.stderr)
        raise SystemExit(77)
