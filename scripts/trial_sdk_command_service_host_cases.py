"""Selected ordinary composition; run only through the frozen outer supervisor.

One parent, one actual service and one finite authored fixture CLI. No Cairn API,
SDK/native CLI, credentials, provider, namespace or evaluation operation occurs.
"""
import hashlib
import json
import os
from pathlib import Path
import select
import sys
import tempfile
import time

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_operation import CommandOperation, OperationQuote
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_peer import Peer
import trial_sdk_command_service_wire as wire


def main():
    before = len(os.listdir('/proc/self/fd'))
    source = Path(__file__).resolve().parent
    first = time.monotonic_ns()
    deadline = first + 8_000_000_000
    resources = AttemptResources()
    # This is the combined already-held parent+child raw subquote, including
    # the one parent's512-byte service diagnostic slab. Not physical-fit proof.
    link = ServiceLink(resources, deadline_ns=deadline,
                       standing_raw=5184, standing_working=128*1024*1024)
    diagnostics = resources.grant(resources.local, 'data', 512)
    if diagnostics is None:
        raise ValueError('fixture_diagnostic_credit')
    slab = bytearray(512)
    counts = dict(stdout=0, stderr=0)
    hashes = {name: hashlib.sha256() for name in counts}
    operation = None
    shutdown = None
    gate = None
    released = False
    directory = None
    primary = None

    def remaining():
        now = time.monotonic_ns()
        if now >= deadline:
            raise TimeoutError('fixture_original_wall')
        return (deadline-now)/1_000_000_000

    def drain(name):
        # The fixed service emits no console output. Even an unexpected observed
        # prefix remains traffic and will make this selected fixture fail.
        with memoryview(slab) as view:
            count = link.owner.read_into(name, view)
            if count > 0:
                counts[name] += count
                if sum(counts.values()) > 512:
                    raise ValueError('fixture_diagnostic_limit')
                with view[:count] as body:
                    hashes[name].update(body)
                raise ValueError('fixture_unexpected_service_diagnostic')

    def poll_once(record=True):
        poll = select.poll()
        if record:
            for fd, flags in link.interests():
                poll.register(fd, flags)
        state = link.owner.snapshot()
        stdio = {}
        for name in ('stdout', 'stderr'):
            if state['launched'] and not state[name+'_eof']:
                fd = link.owner.fileno(name)
                stdio[fd] = name
                poll.register(fd, select.POLLIN)
        if state['launched'] and state['wait'] is None:
            poll.register(link.owner.fileno('pidfd'), select.POLLIN)
        ready = dict(poll.poll(min(50, remaining()*1000)))
        for fd, flags in ready.items():
            if flags & select.POLLNVAL:
                raise ValueError('fixture_readiness_unknown')
            if fd in stdio:
                drain(stdio[fd])
        if state['launched']:
            link.owner.observe_wait()
        return ready

    try:
        Peer.protect()
        directory = Path(tempfile.mkdtemp(prefix='csr-', dir='/tmp'))
        ready_credit = link.credit(32)
        link.arm_receive(ready_credit, 32)
        link.prestart(str(directory),
                      ('/usr/bin/python3', '-B',
                       str(source/'trial_sdk_command_service_fixture_child.py'),
                       str(directory), str(deadline)), str(source),
                      {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        while not link.snapshot()['connected']:
            poll_once()
            link.accept()
        while link.transcript.snapshot()['phase'] == 'boot':
            poll_once()
            link.receive_step(lambda frame: None)
        ready_credit.finish()

        operation = CommandOperation(link,
            OperationQuote(40000,64*1024*1024,65536,64*1024*1024,
                           40000,32*1024*1024,0),
            operation=1, lease=10, deadline_ns=time.monotonic_ns()+5_000_000_000)
        def unavailable_native(request, original_deadline):
            raise ValueError('fixture_native_unavailable')
        gate = MemoryDispatchGate(unavailable_native, operation.forward_hook,
                                  first+30_000_000_000)
        operation.bind_gate(gate)
        operation.prepare_hook('search',
            ('--repo','fixture-repo','--task','fixture-task','--run','fixture-run',
             '--tokens','1024','--','ordinary synthetic query'))
        while True:
            poll_once()
            operation.step()
            if operation.snapshot()['done']:
                break
        outcome = operation.result()
        if (outcome.returncode != 17 or outcome.stdout != 'ordinary service: é💡\n'
                or outcome.stderr != 'fixture diagnostic: �\n'):
            raise ValueError('fixture_command_outcome')
        reported_cli = link.transcript.snapshot()
        if reported_cli['cli_wait_status'] != 17 << 8:
            raise ValueError('fixture_cli_wait')
        outcome = None  # End this caller's text alias before local retirement.
        operation.retire()

        shutdown = link.credit(64)
        link.arm_receive(shutdown, 32)
        link.queue(wire.SHUTDOWN, 0, shutdown)
        while link.snapshot()['send_pending']:
            poll_once()
            link.send_step()
        while link.transcript.snapshot()['phase'] != 'closed':
            poll_once()
            link.receive_step(lambda frame: None)
        shutdown.finish()
        link.close_channels()
        while True:
            state = link.owner.snapshot()
            if state['wait'] is not None and state['stdout_eof'] and state['stderr_eof']:
                break
            poll_once(record=False)
        service = link.owner.snapshot()
        if service['wait']['returncode'] != 0:
            raise ValueError('fixture_service_wait')
        if not link.owner.release()['released']:
            raise ValueError('fixture_service_release')
        resources.settle(diagnostics, sum(counts.values()))
        diagnostics = None
        slab = None
        link.retire()
        resources.close()
        released = True
        directory.rmdir()
        directory = None
        facts = dict(service=link.owner.snapshot(), reported_cli=reported_cli,
                     resources=resources.snapshot(), gate=gate.snapshot(),
                     operation=operation.snapshot(), diagnostics={
                         name: dict(bytes=counts[name],sha256=hashes[name].hexdigest())
                         for name in counts},
                     fd_counts=[before,len(os.listdir('/proc/self/fd'))],
                     scope='ordinary synthetic command only; no API/native/provider admission')
        if facts['fd_counts'][0] != facts['fd_counts'][1]:
            raise ValueError('fixture_fd_inventory')
        print(json.dumps(facts, sort_keys=True))
        return 0
    except BaseException as exc:
        primary = type(exc).__name__
        raise
    finally:
        if not released:
            # Never infer a missing CLI wait or refund on failure. The frozen
            # outer supervisor contains this inherited group before reaping.
            resources.freeze()
            cleanup = []
            try: link.close_channels()
            except BaseException: cleanup.append('channel_close_unknown')
            if diagnostics is not None:
                try: resources.quarantine(diagnostics, sum(counts.values()))
                except BaseException: cleanup.append('diagnostic_receipt_unknown')
            state = link.owner.snapshot()
            if state['launched'] and state['wait'] is None:
                try: link.owner.signal(1, 9)
                except BaseException: cleanup.append('service_signal_unknown')
            # No recursive-delete cleanup while a possibly live service retains
            # the path; the outer owner preserves a partial fixture directory.
            print(json.dumps(dict(primary=primary,service=link.owner.snapshot(),
                resources=resources.snapshot(),diagnostic_counts=counts,
                cleanup=cleanup,
                retained_directory=None if directory is None else str(directory))), file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
