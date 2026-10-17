#!/usr/bin/python3
"""Finite ordinary fixture command; never invokes Cairn or any provider."""
import os
import sys


def main():
    # The fixed policy's synthetic route is part of this authored fixture. No
    # real socket, token or other credential is opened, and no args are executed.
    if sys.argv[1:6] != ['agent', '--socket', '/synthetic/api', '--token-file', '/synthetic/token']:
        raise ValueError('fixture_route')
    if sys.argv[6:7] != ['search']:
        raise ValueError('fixture_operation')
    if sys.stdin.buffer.read(1):
        raise ValueError('fixture_unexpected_stdin')
    os.write(1, 'ordinary service: é💡\n'.encode('utf-8'))
    os.write(2, b'fixture diagnostic: \xff\n')
    return 17


if __name__ == '__main__':
    raise SystemExit(main())
