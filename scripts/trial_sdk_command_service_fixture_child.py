"""Fixed ordinary-test service builder. Source existence authorizes no run."""
import os
from pathlib import Path
import sys

from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_command_service_runtime import RuntimeBinding, RAW_STORAGE, serve


def main():
    # Only the fresh owned fixture parent selects this builder's private path
    # and original clock. This is test configuration, never native admission.
    if len(sys.argv) != 3:
        raise ValueError('fixture_binding')
    directory = Path(sys.argv[1])
    deadline = int(sys.argv[2])
    if not directory.is_absolute() or '..' in directory.parts or deadline <= 0:
        raise ValueError('fixture_binding')
    source = Path(__file__).resolve().parent
    policy = FixedCommandPolicy(str(source/'trial_sdk_command_service_fixture_cli.py'),
                                '/synthetic/api', '/synthetic/token',
                                'fixture-repo', 'fixture-task', 'fixture-run')
    binding = RuntimeBinding(policy, str(source),
        {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'},
        str(directory/'memory.sock'), deadline,
        RAW_STORAGE, 32*1024*1024, 40000, 32*1024*1024)
    result = serve(binding)
    if (result['unknown'] or not result['done'] or not result['closed']
            or not result['standing_released'] or result['cleanup']):
        raise ValueError('fixture_service_incomplete')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
