"""Join the owned writer leaf, verify membership, then exec the unchanged legacy actor command.

argv: PROCS_FD EXPECTED_CGROUP STATUS_FD COMMAND...

The process writes its own pid to the held cgroup.procs descriptor, then reads
/proc/self/cgroup and requires exactly '0::EXPECTED_CGROUP'. Only after that
does it report 'member PID' on the status descriptor and exec. If any step
fails, it exits 125 without exec. Unlike the fixture launcher, it sets no
rlimits and changes no environment: the legacy actor's limits and environment
pass through unchanged.
"""
import os
import sys

REFUSED = 125


def main(argv):
    try:
        procs, expected, status = int(argv[1]), argv[2], int(argv[3])
        command = argv[4:]
        if not command or not expected.startswith('/'):
            return REFUSED
        os.write(procs, str(os.getpid()).encode())
        os.close(procs)
        with open('/proc/self/cgroup') as membership:
            if membership.read().strip() != '0::' + expected:
                return REFUSED
        os.write(status, b'member %d\n' % os.getpid())
        os.close(status)
    except (OSError, ValueError, IndexError):
        return REFUSED
    os.execvp(command[0], command)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
