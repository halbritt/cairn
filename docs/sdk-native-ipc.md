# Native SDK IPC transport source

`integrations/sdk-ipc` contains a Linux Node-API transport binding. It implements
credential-aware records, asynchronous readiness and a bounded synchronous control
exchange. It imports
no SDK, launches no native child, and defines no message-body authority. It is
not wired into the trial runtime. Use the [framing codec](sdk-ipc-codec.md) and
separate semantic gates after authenticating each record.

The [Python peer](sdk-python-peer.md) supplies the corresponding credential-aware
record boundary for a future parent reactor. The reactor is still unwired.

`protect()` sets and verifies dumpable0 in the calling Node process; `now()` reads
monotonic nanoseconds. `openPeer(fd,pidfd,pid,uid,gid,deadline,scratch512)` creates
owned CLOEXEC duplicates and binds an unnamed credential-enabled Unix SEQPACKET
endpoint to a held live PID. The kernel proc mount, selected peer identity, socket
creation and descriptor custody must be established independently. Numeric IDs or
payload fields do not authenticate that selection. Both peers open before traffic:
SO_PASSCRED can give an initially unnamed endpoint an automatic abstract address.
That address is never identity; subsequent receives still check socket options
and per-record credentials against the held peer.

`sendRecord` accepts 32–544 bytes and returns socket acceptance, never native stdin
acceptance. `receive` accepts a caller-owned output view of 32–544 bytes and returns
one record with exactly one matching PID/UID/GID credential. Truncated,
unknown, missing or extra ancillary data refuses. Installed SCM_RIGHTS descriptors
are closed once; uncertain cleanup remains terminal. `-1` means known would-block;
terminal exceptions never authorize replay. Socket EOF is transport failure,
not native stdout EOF or child wait.

The output view's length is the actual receive capacity. A 34-byte record into a
33-byte view is terminal truncation, not a valid shorter record. A refused record
zeros exactly that view; bytes outside the view remain untouched. `exchange`
also accepts a 32–544-byte reply view, including a 33-byte control buffer, and
requires distinct request/reply backing ArrayBuffers even for disjoint views.
Its return value is the authenticated record length. The higher-level codec must
still check the record kind, operation and body. Early failure before receive does
not promise to clear untouched caller storage.

`exchange` sends one record, then receives one authenticated reply on a dedicated
control pair with its selected deadline checked at readiness, operation boundaries
and before reporting success. It calls no JavaScript callback while waiting.
Successful send is never repeated. A post-send deadline failure remains unknown:
prechecks cannot make a clock reading and syscall atomic. Parse direction, sequence,
kind, operation and status before interpreting any reply as a signal result. The
parent must service this control pair independently of blocked data/native I/O.

`waitPeer(peer, mask, absoluteDeadlineBigInt)` returns a Promise for readiness:
READ is 1, WRITE is 2, and their combination is 3. Its selected deadline must be in
the future and no later than the Peer deadline. The wait performs no record I/O;
after resolution, the next nonblocking operation can still return would-block.
One wait may be armed per Peer. Send, receive and exchange on that same Peer
refuse while armed; a separate control Peer remains usable. The caller must
serialize access and consume the wait outcome before starting the next operation.

`cancelWait(peer)` reports whether it cancelled an armed wait and rejects that
Promise with `ipc_wait_cancelled`. `closePeer` rejects an armed wait with
`ipc_wait_closed`. Cancellation alone leaves the Peer usable; timeout or an
uncertain readiness/liveness result makes it terminal. The implementation stops
the poll before closing the watched descriptor. A cancelled or completed wait
retains its C state and strong Peer reference through both libuv close callbacks,
so Promise settlement does not establish metadata release. Environment teardown
also stops waits and closes descriptors, without manufacturing a JavaScript result
or an OS process wait.

One loaded binding image permits at most eight retained Peer wrappers and sixteen
armed or closing waits across its Node environments, including Workers. Closed
wrappers still count until finalization; settled waits still count until their
close callbacks finish. Contended reservations refuse without spinning. These
limits do not cover separately loaded binding images or all V8/kernel memory.

Buffers are caller-reserved, fixed, nonshared stable Uint8Array/Buffer storage.
No payload pointer is retained across calls. A readiness wait retains the Peer
wrapper, not a record-buffer pointer. Exchange request/reply backing must differ.
The caller includes scratch, records, ancillary/stack storage and working
metadata in the existing ledgers and aggregate controls. The wrapper does not
explicitly reject resizable backing; the fixed-backing precondition is essential.
Explicit close invalidates descriptor slots before closing, never retries uncertain
numbers, and leaves allocation disposal to the finalizer. Finalization is best
effort, not release evidence. Exceptions after protect, send, receive or close
may follow real effects; do not infer rollback.

The reviewed wrapper is conditional on these helper and caller contracts. Ordinary
host checks exercise fresh owned processes, credentials, pidfds, records, refusal,
descriptor cleanup, API bounds, exchange deadlines and backpressure. The 38 cases
include the original sixteen transport cases, twelve readiness/lifetime cases,
and ten caller-sized control cases. Readiness checks cover real event-loop progress
before peer I/O, actual send saturation and writable wakeup, cancellation/rearming,
close with FD reuse, GC retention, Worker teardown and the shared-image cap.
Control checks cover capacities, pre-effect argument refusal, canaries, truncation,
credentials, received-rights closure and deadline failure. They do not
establish actual sandbox/native ownership, privileged-actor exclusion, aggregate
physical fit, SDK compatibility, hooks, model context delivery, provider capacity,
grading or full CAIRN-107 admission. Capture HOLD and all trial budgets remain.

Run the optional Linux check with Node/libuv headers, Node and a C compiler installed:

```sh
make test-sdk-native-ipc SDK_IPC_TEST_OUTPUT=/absolute/fresh/output-directory
```

The output directory must be absent and its parent must exist. The harness hashes
all five C/header inputs and ten harness files and copies them to a read-only
snapshot. It loads only the fixed case functions from that snapshot and records
an intent before compiling and running the cases against the same addon image.
It retains its serialized source ownership assumption and checks input hashes
again after the run. Ordinary Node/helper children have fresh process groups;
the harness retains actual owned waits and bounded output and refuses reuse.
Compile and host-case work each have a ten-second deadline; timeout cleanup retains
the actual wait separately. These bounds do not establish a physical memory quota.
Build output and receipts stay outside tracked
source. This target runs separately from the default tests and never invokes an
SDK, native CLI, namespace, provider or evaluation.
