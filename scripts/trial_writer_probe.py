"""Inside the attempt sandbox, before the actor: report launch evidence, then exec unchanged.

argv: EVIDENCE_FD CGROUP_DEV -- COMMAND...

This runs as the exact process that becomes the actor, after bwrap has built
the sandbox. It reads, without changing anything:
- its capability sets and NoNewPrivs;
- the filesystem types in its mount table;
- whether any open descriptor is on the host cgroupfs device CGROUP_DEV;
- its namespace pid;
- its /proc/self/cgroup line;
- user.max_user_namespaces.
It writes one JSON line to EVIDENCE_FD, closes that descriptor and execs
COMMAND. If it cannot report, it exits 125 without exec.
"""
import json
import os
import sys

REFUSED = 125


def evidence(evidence_fd, cgroup_dev):
    status = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines() if ':' in line)
    caps = {k: status[k].strip() for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')}
    types = sorted({line.split(' - ', 1)[1].split()[0] for line in open('/proc/self/mountinfo') if ' - ' in line})
    on_cgroupfs = []
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        if fd == evidence_fd:
            continue
        try:
            info = os.fstat(fd)
        except OSError:
            continue  # the listing descriptor itself, already closed
        if '%d:%d' % (os.major(info.st_dev), os.minor(info.st_dev)) == cgroup_dev:
            on_cgroupfs.append(fd)
    try:
        userns = int(open('/proc/sys/user/max_user_namespaces').read().strip())
    except (OSError, ValueError):
        userns = None
    return dict(schema='cairn.writer-launch-evidence/1', pid=os.getpid(), caps=caps,
                no_new_privs=status.get('NoNewPrivs', '').strip(), mount_types=types,
                cgroupfs_descriptors=on_cgroupfs, cgroup=open('/proc/self/cgroup').read().strip(),
                max_user_namespaces=userns)


def main(argv):
    try:
        evidence_fd, cgroup_dev, separator, command = int(argv[1]), argv[2], argv[3], argv[4:]
        if separator != '--' or not command:
            return REFUSED
        os.write(evidence_fd, (json.dumps(evidence(evidence_fd, cgroup_dev), sort_keys=True) + '\n').encode())
        os.close(evidence_fd)
    except (OSError, ValueError, IndexError, KeyError):
        return REFUSED
    os.execvp(command[0], command)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
