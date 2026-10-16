# Python SDK IPC peer

`scripts/trial_sdk_peer.py` supplies a Linux record endpoint for a future parent
reactor. It authenticates record credentials against independently supplied
PID/UID/GID and a held pidfd. It does not launch a process, dispatch an SDK frame,
write native stdin or establish hook delivery. The reactor is not implemented.

`Peer.protect()` explicitly sets and checks process dumpability zero. Construction
requires that protection, a future absolute monotonic deadline within 140 seconds,
and a fresh unnamed AF_UNIX SOCK_SEQPACKET endpoint with SO_PASSCRED enabled.
Both endpoints must finish admission before traffic can autobind their addresses.
The selected PID must be meaningful in the receiver's namespace, with a genuine
kernel proc mount. Names and payload flags are not authentication.

The constructor takes `endpoint, pidfd, pid, uid, gid, deadline, scratch, reserve,
release`. It owns CLOEXEC duplicates; originals remain the caller's responsibility.
Use fresh instances and serialized calls. Constructor callbacks must not expose or
reenter the partially constructed instance. External aliases must not replace,
close or reconfigure the endpoint during use.

The caller precharges stable writable 512-byte construction scratch and 544-byte
receive storage. `send()` borrows immutable bytes or a stable readonly contiguous
byte view of 32–544 bytes. `receive_into()` borrows a writable contiguous byte view
of exactly 544 bytes. It requires exactly one matching SCM_CREDENTIALS record;
unexpected, missing, duplicate or truncated ancillary traffic refuses. Installed
SCM_RIGHTS descriptors are closed once. UID/GID retain the full unsigned 32-bit
range; an explicit Linux representation check guards the selected credential ABI.
High-ID tests use synthetic records, not privileged identity changes.

Setup and each receive reserve `AUXILIARY` from the same original ledger. Exact
`True` means acquired, `False` means no debit; exceptions or other values mean
uncertainty. The current LP64 Linux value is 480 bytes: two control stores and two
address stores. This is a stock CPython 3.12.3 marshalling model, not verification
of the installed build, allocator, kernel charges or universal physical memory.
The original aggregate controls remain required. Release uncertainty is sticky
and never permits a guessed refund. Trusted callbacks must not retain raw loans.

Known would-block/interrupted operations return `-1`. Positive send counts mean
socket transmission only. Every post-I/O liveness-check exception makes the peer
terminal and uncertain because I/O may already have occurred. The caller must
ignore receive storage on any exception and must never replay an uncertain send.
Snapshots retain first failure, outcome uncertainty and separate cleanup/close
uncertainty. Descriptor close detaches owned numbers before one attempt each;
an uncertain close cannot later close a reused number. Closing duplicates proves
neither endpoint EOF nor native stdin closure, child wait or context delivery.

Pure credential tests run with the normal Python suite. Explicit ordinary-host
checks also exercise actual sockets/pidfds, rights cleanup, credit denial,
reentry, deadline crossing, injected post-I/O faults and descriptor reuse:

```sh
make test-sdk-peer SDK_PEER_TEST_OUTPUT=/absolute/fresh/absent-directory
```

The parent directory must exist. The command refuses output reuse, including a
dangling symlink, and retains intent, source copies, bounded logs, source pins and
the actual owned child wait. It has a 10-second/64-KiB outer bound. Its audit guard
is an effect regression check for trusted tests, not hostile-code isolation.
Neither this check nor the separate [Node binding checks](sdk-native-ipc.md)
qualifies the actual SDK/native sandbox, independent control progress, callback
dispatch, cancellation, model-request witness, provider capacity or usefulness.
