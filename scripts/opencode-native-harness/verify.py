"""Verify the actual namespace and dropped credentials before any workload exec."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ENV_NAMES = {'PATH', 'LANG', 'HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
             'XDG_STATE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR', 'HARNESS_FIXTURE_ROOT'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def credentials(status, metadata):
    require([int(v) for v in status['Uid'].split()] == [metadata['uid']] * 4 and metadata['uid'] != 0,
            'UID was not fully dropped')
    require([int(v) for v in status['Gid'].split()] == [metadata['gid']] * 4, 'GID was not fully dropped')
    require(sorted(int(v) for v in status['Groups'].split()) == metadata['groups'], 'groups changed')
    require(all(int(status[key], 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')),
            'capabilities remain')
    require(status['NoNewPrivs'].strip() == '1', 'no_new_privs is absent')


def verify(root, expected_netns):
    root = Path(root)
    metadata = json.loads((root / 'launch.json').read_text())
    require(root.is_absolute() and root.stat().st_uid == metadata['uid'] and root.stat().st_mode & 0o077 == 0,
            'fixture root identity or permissions differ')
    current = os.readlink('/proc/self/ns/net')
    require(re.fullmatch(r'net:\[\d+\]', metadata['host_netns']) is not None, 'host namespace identity missing')
    require(current == expected_netns and current != metadata['host_netns'], 'namespace identity differs or is host')
    parent = metadata['parent_pid']
    start = Path(f'/proc/{parent}/stat').read_text().rsplit(')', 1)[1].split()[19]
    require(start == metadata['parent_start'] and os.readlink(f'/proc/{parent}/ns/net') == metadata['host_netns'],
            'original host process identity is unavailable')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    credentials(status, metadata)
    require(set(os.environ) == ENV_NAMES, 'environment contains unexpected or missing entries')
    expected = dict(PATH='/usr/bin:/bin:/usr/sbin', LANG='C.UTF-8', HOME=str(root/'home'),
                    XDG_CONFIG_HOME=str(root/'config'), XDG_DATA_HOME=str(root/'data'),
                    XDG_CACHE_HOME=str(root/'cache'), XDG_STATE_HOME=str(root/'state'),
                    XDG_RUNTIME_DIR=str(root/'runtime'), TMPDIR=str(root/'tmp'), HARNESS_FIXTURE_ROOT=str(root))
    require(dict(os.environ) == expected, 'environment paths or values differ')
    def ip(*args):
        return json.loads(subprocess.run(['/usr/sbin/ip', '-j', *args], check=True,
                                        capture_output=True, text=True, timeout=3).stdout)
    links = ip('link', 'show')
    require(len(links) == 1 and links[0]['ifname'] == 'lo' and 'UP' in links[0]['flags'],
            'namespace must contain exactly one active loopback interface')
    for family in ('-4', '-6'):
        require(ip(family, 'route', 'show', 'table', 'main') == [], 'main routing table is not empty')
        for route in ip(family, 'route', 'show', 'table', 'all'):
            require(route.get('dev') == 'lo' and ipaddress.ip_network(route['dst'], strict=False).is_loopback,
                    'non-loopback route exists')
    return dict(netns=current, host_netns=metadata['host_netns'], uid=metadata['uid'], gid=metadata['gid'],
                groups=metadata['groups'], capabilities='all-zero', no_new_privs=True,
                interfaces=['lo'], main_routes=[], env_names=sorted(os.environ))


def verify_current():
    root = Path(os.environ['HARNESS_FIXTURE_ROOT'])
    proof = json.loads((root / 'verified.json').read_text())
    return verify(root, proof['netns'])


if __name__ == '__main__':
    try:
        root, namespace, *command = sys.argv[1:]
        require(command and Path(command[0]).is_absolute(), 'absolute workload executable required')
        evidence = verify(root, namespace)
        (Path(root)/'verified.json').write_text(json.dumps(evidence, indent=2))
        os.chdir(Path(root)/'workspace')
        os.execv(command[0], command)
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'FAIL-CLOSED: {exc}', file=sys.stderr)
        raise SystemExit(77)
