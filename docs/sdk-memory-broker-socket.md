# Memory broker socket foundation

This source supplies a Linux named record transport and Python framing profile.
It does not assemble requests, dispatch the memory API, run the engine, or prove
native delivery. Production use remains unavailable until the broker, source and
configuration custody, shared resource reservations and coordinator exist.

`BrokerListener` binds one fresh `memory.sock` in an already private directory.
The server accepts only an actual live `NativeProcessOwner` child, borrowing its
held pidfd for readiness without duplicating or adopting it. The client captures
its actual parent once with `getppid`, opens that parent's pidfd and never
refreshes it or reconnects. Both endpoints require dumpable zero, a fixed deadline
and the selected same-UID/GID process contract. These checks bind processes and
lifetimes, not executable identity, source or callback cause.

`SO_PEERCRED` checks the connection. Every received record separately requires
exactly one matching `SCM_CREDENTIALS` item. A forked process or recipient of a
transferred connection must not gain the original sender's identity. Unexpected
control items, truncation and rights-bearing messages refuse. Every visible
received right receives one close attempt even if another close raises; an
uncertain close remains recorded and is never retried by raw descriptor number.
An arbitrary interruption outside an observed cleanup boundary remains unknown;
these Python routines do not claim universal asynchronous resource recovery.

The transport lends caller-owned views for records of 32 through 544 bytes. It
does not parse their headers or privileged command arguments. The caller must
use the codec and operation-specific broker state machine. A known would-block
returns `-1` (or `None` for accept); ambiguous send, receive or acceptance is
terminal. Accepted bytes are not an API result. Successful acceptance consumes
the listener's one connection even when setup subsequently refuses the peer.

`snapshot.last_transfer` is a bounded immutable scalar tuple containing a fresh
per-call sequence, direction, offered size and actual observed syscall count (or
`None`). Known EAGAIN records zero; no positive count is inferred from an error.
The receipt survives a later lifetime, ancillary or cleanup refusal. A parent
must match its fresh generation/direction to the exact held transfer promise
before preserving that prefix; an older receipt after pre-effect refusal cannot
count again. Consumed malformed/uncredentialed bytes are traffic, never usable
protocol. This receipt does not complete an uncertain record, authenticate its
payload or refund any unobserved remainder.

Cleanup premarks each owned handle before relinquishing it. A missing handle
alone does not prove closure: cleanup uncertainty stays separate from the
original operation failure. The listener retains the accepted endpoint through
connection construction and promotes nested endpoint close uncertainty. Closing
a listener before start consumes it. Neither terminal object may acquire a new
connection later.

`receive_terminal_into` is a separate terminal observation for a caller whose
actual protocol has already sent BYE. It leaves ordinary `receive_into` behavior
unchanged. It returns0 only after an actual nonblocking receive of zero bytes,
no ancillary data, no semantic message flags, observed HUP/RDHUP, a still-live
original peer and known auxiliary cleanup. It returns-1 for known would-block.
Positive bytes, a credentialed zero-length packet, rights, missing shutdown
readiness or uncertain cleanup refuse; actual consumed counts remain in
`last_transfer`. A successful EOF observation cannot be replayed. The method
accepts no permit flag and does not certify that a caller really sent BYE.

The only allowed returned option flag is MSG_CMSG_CLOEXEC: Linux documents this
as reflecting the requested receive option. The option stays enabled so any
unexpected received rights remain close-on-exec. [recvmsg(2)](https://man7.org/linux/man-pages/man2/recvmsg.2.html)
also distinguishes zero-size messages from stream EOF. The selected check uses
SO_PASSCRED's per-message credentials and shutdown readiness in addition to the
actual zero receive; neither an empty queue nor a HUP bit alone is EOF.
[unix(7)](https://man7.org/linux/man-pages/man7/unix.7.html),
[poll(2)](https://man7.org/linux/man-pages/man2/poll.2.html).
These are Linux source-contract assumptions for this selected SEQPACKET path;
the pure boundary tests do not prove installed-kernel behavior. The service
runtime separately enforces known-sent BYE and distinguishes expected channel
termination from premature parent death. It does not infer the parent's actual
descriptor-table cleanup merely from read shutdown.

The directory and filesystem socket entry are held through cleanup. Unlink
requires the current leaf to match the held entry, but stat followed by unlink
is not an atomic conditional operation. Ancestor checks and private mode bits
also do not prevent same-UID mutation. The future capture builder must exclude
actor access and concurrent pathname replacement; no hostile filesystem or
actor containment claim follows from this module.

## Python wire profile 3

The header remains the existing 32-byte big-endian `>IBBHQQII` format, magic
`CIPC`, explicit version 3, zero flags and reserved fields, monotonically
increasing per-direction sequence and a nonzero correlated operation ID.
Profiles 1 and 2 retain their existing kinds and meanings. Node and the native
addon implement only those earlier profiles; they do not implement profile 3.
`Channel` defaults to profile 2 and requires an explicit `profile=3` for this
protocol.

| Kind | Direction | Body | Framing meaning |
|---|---|---|---|
| 33 | C2B | 1–512 bytes | Request data |
| 34 | C2B | empty | Request end |
| 65 | B2C | 1–512 bytes | Command stdout data |
| 66 | B2C | 1–512 bytes | Command stderr data |
| 67 | B2C | 4 bytes | Actual downstream return code, once implemented |
| 68 | B2C | 1 byte | Terminal refusal |

The codec checks framing, sequence, direction and loans. It does not prove that
a request ended, a result corresponds to a completed owned command, or an
operation is authorized. In particular, the ordinary socket fixture's literal
kind-67 reply is synthetic transport data, not an observed command wait or API
result. A complete request/result state machine remains to be implemented.

## Resources and validation scope

The transport receives auxiliary reserve/release callbacks from its owner. It
creates no additional raw pool or stream allowance. Actual deployment must bind
all producer, broker, codec and counterparty storage to the original attempt's
reservations and retain them through actual waits and release. Chunking does
not account decoded copies, command output, kernel queues or physical memory.
The original cgroup controls remain required; this source adds no universal RAM
guarantee or new admission condition.

`test_trial_sdk_memory_broker_wire.py` checks literal headers, closed direction
and kind sets, body and operation bounds, sequence replay, legacy compatibility
and shared backing loans. `test_trial_sdk_memory_broker_socket.py` injects faults
using fake endpoints, including a trace interruption after an accepted send.
Those tests attest source transitions, not kernel credentials or FD recovery.

The explicit `trial_sdk_memory_broker_socket_host.py` fixture is intended only
for a fresh, source-pinned, bounded ordinary owned-child run under the existing
subreaper supervisor. Its four cases cover a fixed exchange, a wrong connecting
child, unexpected received rights and an inherited socket used by a forked
sender. It retains actual direct-child waits, child-reported fork wait,
descriptor inventories and parent/child cleanup states. Test-only auxiliary
counters and no-op codec hooks do not establish shared aggregate budget fit.
No host result is implied by the existence or pure tests of that fixture.

The full broker must still enforce closed typed requests, fixed parent scope,
current-attempt receipt/handle membership and the shared pre-dispatch gate. It
must own and cooperatively drain downstream commands without blocking the
original reply deadline. Full CAIRN-107 evaluation, account and provider
capacity, native origin, model-request delivery and usefulness remain unproved.
