# Output EOF at exhausted traffic credit

`NativeProcessOwner.probe_output_eof(name, scratch)` lets a coordinator distinguish
queued output, a live empty stream and actual EOF after its payload allowance is
exhausted. `name` is `stdout` or `stderr`. `scratch` is the caller's existing
writable contiguous byte memoryview, 4 through 512 bytes. No additional application payload buffer
or payload allowance is allocated; interpreter and kernel overhead remain separate.

The method polls the owner's original Linux Unix-domain stream endpoint without
waiting and uses the first four scratch bytes for the native-int `FIONREAD` result.
Positive queued bytes return `data` without calling `readv`; at an exhausted limit
the coordinator must refuse overflow rather than consume them. An empty stream
without `POLLRDHUP` or `POLLHUP` returns `pending`. The outer coordinator must keep
servicing other owners and enforce the original deadline.

Only observed read-side hangup plus zero queued bytes permits a one-byte read into
the same reserved scratch. Only an actual zero return establishes EOF through the
existing `read_into` path and consumes the normal output-descriptor close attempt.
A known interrupted/would-block read remains `pending`. Poll, queue state and
process wait never substitute for that actual zero return.

This relies on the selected Linux `AF_UNIX`/`SOCK_STREAM` shutdown semantics and
the owner's existing exclusive-reader, unchanged-descriptor custody: after peer
send-side closure, no future bytes can enter that receive queue. A zero queue
observation then permits an EOF read without consuming a new payload byte.
This is conditional source reasoning, not proof of the running kernel or hostile
same-UID containment. A foreign reader, replacement FD or unverified transport
invalidates the argument. The API does not adopt a supplied FD or select another
transport.

An unexpected positive probe read violates that contract. Its byte count is
retained in the copied `output_probe_positive_bytes` snapshot, the stream becomes
terminal, and EOF is not invented. The coordinator must preserve the overrun or
unknown accounting and refuse, never treat it as permission to exceed the limit.
Metadata/read/cleanup exceptions likewise prevent replay through that stream.
An actual zero read remains an observed EOF even if its subsequent close is
uncertain; the original close-unknown/release facts remain separate. No raw close
number is retried.

`scripts/test_trial_sdk_output_eof.py` uses controlled OS boundaries and the real
owner state machine. It tests drained hangup/read-zero, pending empty streams,
queued-data refusal without reads, interrupted probes, unexpected positive reads,
metadata failures, close uncertainty and final-loan-cleanup interruption. These
tests create no real sockets, pidfds or subject processes. A separately authorized
ordinary Linux socket run is still required before claiming actual EOF behavior
at the exact aggregate limit. The full command job, broker and runtime remain
separate integrations.

The source rationale uses [poll(2)](https://man7.org/linux/man-pages/man2/poll.2.html)
for hangup meaning and [unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html)
for `FIONREAD`/`SIOCINQ` unread-byte counts. In the fixed
[Linux v6.8 Unix socket source](https://raw.githubusercontent.com/torvalds/linux/v6.8/net/unix/af_unix.c),
`unix_shutdown` only adds shutdown bits; `unix_stream_sendmsg` rejects the peer's
receive shutdown before queueing; `unix_poll` reports that shutdown;
`unix_inq_len` sums remaining queued bytes; and `unix_stream_read_generic` returns
zero when the queue is empty, shutdown is set and no socket error intervenes.
Together these support the no-future-payload inference under the custody
assumptions above. These references were consulted on 2026-10-16; Linux v6.8 is a
source reference, not an identification or verification of the installed kernel.
