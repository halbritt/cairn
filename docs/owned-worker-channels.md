# OwnedWorker channel profiles

Trusted callers can select protected local IPC explicitly:

```python
worker = OwnedWorker(group, argv, channels='unix-stream/1')
```

The default remains `pipes/1`. Unknown profiles refuse with
`owned_worker_channel_profile`. The legacy `Child` implementation, fixed launcher
command, domain ownership and aggregate resource controls are unchanged. No
fixture selects the new profile in this change.

## Transport contract

`unix-stream/1` creates four unnamed Linux `AF_UNIX`, `SOCK_STREAM` socket pairs
with `SOCK_CLOEXEC`: stdin, stdout, stderr and namespace status. It shuts down each
unused direction. The parent writes stdin and reads the other three channels.
Status endpoints are nonblocking; parent stdio endpoints become nonblocking
after process creation, as in the pipe profile.

Popen receives the child stdio endpoints as descriptors. Its parent `stdin`,
`stdout` and `stderr` attributes expose binary buffered streams, including
`fileno`, `write`/`flush`/`close`, `read` and `read1` where applicable. Existing
descriptor reads/writes, EOF handling and `OwnedWorker.observe_wait()` continue
to work. Closing stdin flushes buffered input before closing its endpoint.
Callers must continue to use the existing owned wait observer to preserve raw
wait status; independently reaping with Popen or `waitpid` consumes that evidence.

Only the existing cgroup membership descriptor and selected status writer are
in `pass_fds`; selected stdio is mapped onto 0/1/2. `close_fds=True` excludes
other inherited descriptors. The parent closes every child endpoint before
successful construction returns. Four parent channel descriptors and the
existing launcher pidfd remain; no extra guard descriptor is inherited. EOF
requires all legitimate inherited writers to close, as with pipes.

On Linux, opening a socket through `/proc/PID/fd/N` fails with `ENXIO`. Ordinary
child tests demonstrate this for all four child channels, alongside a positive
control that reopens pipe stdout and injects a canary. This closes that specific
pipe-reopening path. It does not prevent writes by legitimate descriptor
holders, descriptor transfer, ptrace/pidfd access where permitted, or compromised
trusted launchers. Namespace, privilege and producer contracts still matter.

## Construction and uncertain close

The protected profile retains `group.bootstrap_child` before channel allocation.
Every acquired endpoint is registered before later setup. Once Popen returns,
the actual process is reachable there before stream installation, pidfd
acquisition or nonblocking setup. Successful construction clears the bootstrap
reference; failed construction leaves it for the existing domain cleanup.
An existing bootstrap owner refuses replacement with `owned_worker_bootstrap_held`.
Callers must finish cleanup of that owner rather than clear it and retry.

Socket peers have **different inodes**. The old pipe reader/writer inode guard
therefore cannot guard the status socket writer. Each new endpoint instead
retains its own `(device, inode, SO_COOKIE)` before use. Linux `SO_COOKIE` (57)
identifies that endpoint; its value is used only to guard cleanup, never as
producer authentication. Missing cookie support refuses construction; there is
no pipe or inode-only fallback.

Endpoint custody owns descriptor closure. Stream wrappers use `FileIO` with
`closefd=False`, so a later wrapper destructor cannot blindly close an unrelated
descriptor after a close fault. Before retrying a close, custody checks that the
descriptor still identifies the original endpoint. A changed identity or
`EBADF` retires the number while retaining `worker_descriptor_close_unknown`;
an unrelated replacement is left open. An unreadable identity retains the
endpoint record for later guarded cleanup. A newly acquired endpoint whose
initial identity observation fails gets one immediate close attempt; if that
close is uncertain, its raw number is retained as unknown and is never blindly
retried. Repeated cleanup preserves selected faults even after release.

The owner must serialize channel I/O, closure and descriptor-table mutation.
Callers borrow descriptors and must not independently close or replace them.
Identity-check-then-close is not atomic against concurrent foreign mutation of
the parent's descriptor table. This profile does not claim protection against
such mutation or arbitrary asynchronous interruption inside interpreter resource
acquisition. Popen remains responsible for its own internal failures before it
returns a process handle.

OSError setup failures report `owned_worker_setup_unknown`; pidfd acquisition
also retains `owned_launcher_handle_unknown`. Unexpected exceptions propagate
with the bootstrap reference retained. Pre-spawn cleanup creates no synthetic
wait fact. Existing domain cleanup may report `outer_wait_unknown` for a retained
failed construction with no process; that is not a successful execution.
The existing pidfd, namespace and cgroup cleanup contracts are unchanged.

## Verification and scope

Run the ordinary subprocess suite from the repository root:

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s scripts -p test_trial_owned_worker_channels.py
make check
```

Tests substitute only the launcher command at the Popen boundary with an
ordinary Python child. They exercise real socket/pipe I/O, proc-fd reopening,
EOF, raw wait observation, inheritance, allocation and Popen failures, late
setup faults, close uncertainty, descriptor reuse and held bootstrap cleanup.
They do not launch bwrap, create namespaces/cgroups or execute native/provider
work. The namespace boundary suite is a separate validation obligation before
fixture adoption.

Protected channels are one integrity condition, not native invocation origin,
consumer settlement, account dedication, capacity, admission or usefulness
proof. Root owns fixture selection and the remaining native witness conditions.
No 8500/9500/8192/32000, 4 MiB, 128 KiB, 512 MiB, 32-PID, CPU, campaign or other
original evaluation limit changes here.
