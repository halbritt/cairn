"""Consume-once ordinary source/result/EOF suite; no native launch option."""
import argparse
import json
from pathlib import Path
import uuid

from check_trial_sdk_native_process_owner import read, digest, write, supervise
from check_trial_sdk_direct_writer import finalize_check
from check_trial_sdk_stream_driver import SOURCES as STREAM_SOURCES

HERE = Path(__file__).parent
SOURCES = tuple(name for name in STREAM_SOURCES if name not in (
    'trial_sdk_stream_driver_host.py', 'trial_sdk_stream_driver_child.py')) + (
    'trial_sdk_session_driver.py', 'trial_sdk_session_driver_host.py',
    'trial_sdk_session_driver_child.py', 'check_trial_sdk_session_driver.py',
    'trial_native_bootstrap.py', 'trial_bootstrap_snapshot.py', 'trial_paired_request.py',
    'trial_task_input.py', 'trial_task_arms.py', 'test_trial_sdk_bootstrap_review.py')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    out = parser.parse_args().output
    if not out.is_absolute() or '..' in out.parts:
        raise ValueError('fresh_absolute_output')
    for part in out.parents:
        if part.is_symlink():
            raise ValueError('output_ancestor_symlink')
    bodies = {name: read(HERE / name) for name in SOURCES}
    pins = {name: digest(body) for name, body in bodies.items()}
    out.mkdir(mode=0o700)
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for name, body in bodies.items():
        target = ('trial_sdk_native_process_owner_host_cases.py'
                  if name == 'trial_sdk_session_driver_host.py' else name)
        path = snapshot / target
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(body)
        path.chmod(0o400)
    write(out / 'INTENT.json', dict(request_id=str(uuid.uuid4()), sources=pins,
        leader_alias={'trial_sdk_native_process_owner_host_cases.py': 'trial_sdk_session_driver_host.py'},
        scope='fixed ordinary EOF-dependent Python surrogate and source-custody/close faults only',
        actual_sdk=False, actual_native=False, provider=False, namespace=False,
        work_seconds=10, total_seconds=12, output_bytes=65536))
    outer, logs = supervise(snapshot)
    result = finalize_check(out, pins, outer, logs, source_root=HERE, source_names=SOURCES)
    print(json.dumps(dict(output=str(out), ok=result['ok'], test_wait=outer['test_wait'],
        waits=outer['waits'], pipes=outer['pipes'], errors=outer['errors'],
        group_children_empty=outer['group_children_empty'], selected=result['selected'])))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
