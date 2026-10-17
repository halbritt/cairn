"""Consume-once fixed ordinary check; no SDK/native/provider launch options."""
import argparse
import json
import os
from pathlib import Path
import uuid

from check_trial_sdk_native_process_owner import read, digest, write, supervise

HERE = Path(__file__).parent
SOURCES = ('trial_sdk_direct_writer.py', 'trial_sdk_direct_writer_host.py',
    'trial_sdk_native_process_owner.py', 'trial_sdk_native_input.py',
    'trial_sdk_native_process_owner_child.py', 'trial_sdk_control.py',
    'trial_sdk_incoming.py', 'trial_coding_launch.py', 'trial_task_permissions.py',
    'selected_native/__init__.py', 'selected_native/frames.py',
    'selected_native/evidence.py', 'check_trial_sdk_native_process_owner.py',
    'check_trial_sdk_direct_writer.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    out = parser.parse_args().output
    if not out.is_absolute() or '..' in out.parts:
        raise ValueError('fresh_absolute_output')
    for part in (out.parent, *out.parent.parents):
        if part.is_symlink():
            raise ValueError('output_ancestor_symlink')
    bodies = {name: read(HERE / name) for name in SOURCES}
    pins = {name: digest(body) for name, body in bodies.items()}
    out.mkdir(mode=0o700)
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for name, body in bodies.items():
        # The accepted supervisor has one fixed leader name. This alias is an
        # explicit source mapping, not a replay of its original host suite.
        target = ('trial_sdk_native_process_owner_host_cases.py'
                  if name == 'trial_sdk_direct_writer_host.py' else name)
        path = snapshot / target
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(body)
        path.chmod(0o400)
    write(out / 'INTENT.json', {'request_id': str(uuid.uuid4()), 'sources': pins,
        'leader_alias': {'trial_sdk_native_process_owner_host_cases.py': 'trial_sdk_direct_writer_host.py'},
        'scope': 'fixed ordinary Python echo child; no actual SDK/native/provider/account/namespace',
        'work_seconds': 10, 'total_seconds': 12, 'output_bytes': 65536})
    outer, logs = supervise(snapshot)
    result = finalize_check(out, pins, outer, logs)
    print(json.dumps({'output': str(out), 'ok': result['ok'], 'test_wait': outer['test_wait'],
                      'waits': outer['waits'], 'pipes': outer['pipes'],
                      'group_children_empty': outer['group_children_empty'],
                      'selected': result['selected'], 'errors': outer['errors']}))
    return 0 if result['ok'] else 1


def finalize_check(out, pins, outer, logs, *, source_root=HERE, source_names=SOURCES):
    """Retain collected facts independently from later persistence/check faults."""
    # Retain actual outer observations before any later diagnostic/pin I/O.
    try:
        write(out / 'OUTER-OBSERVATION.json', outer)
    except BaseException as error:
        outer['errors'].append('observation_write:' + type(error).__name__)
    for name, body in logs.items():
        try:
            with (out / (name + '.log')).open('xb') as stream:
                stream.write(body)
        except BaseException as error:
            outer['errors'].append('log_write_' + name + ':' + type(error).__name__)
    outer['logs'] = {name: digest(body) for name, body in logs.items()}
    try:
        stable = pins == {name: digest(read(source_root / name)) for name in source_names}
    except BaseException as error:
        stable = False
        outer['errors'].append('pins:' + type(error).__name__)
    try:
        selected = json.loads(logs['stdout'])
    except (ValueError, UnicodeError):
        selected = None
    ok = (stable and not outer['errors'] and not outer['live_deadline_exceeded']
        and outer['test_wait'] is not None and outer['test_wait']['returncode'] == 0
        and outer['group_children_empty'] and not logs['stderr']
        and set(outer['pipes']) == {'stdout', 'stderr'}
        and all(row == {'close_attempts': 1, 'closed_known': True}
                for row in outer['pipes'].values())
        and type(selected) is dict and selected.get('ok') is True)
    result = {'ok': ok, 'outer': outer, 'selected': selected, 'source_pins_stable': stable}
    try:
        write(out / 'RESULT.json', result)
    except BaseException as error:
        outer['errors'].append('result_write:' + type(error).__name__)
        result['ok'] = False
    return result


if __name__ == '__main__':
    raise SystemExit(main())
