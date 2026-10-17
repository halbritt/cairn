# Node custody owner

`scripts/trial_sdk_node_owner.cjs` supplies the owner interface used by the
[spawn proxy](sdk-spawn-proxy.md). `trial_sdk_node_launch.cjs` counts and streams
the selected launch tuple as JSON. The owner uses the repository's protocol-2
[codec](sdk-ipc-codec.md) and the [Node transport](sdk-native-ipc.md) API. Loading
these modules creates no socket, launches no child and imports no SDK. The trial
runtime does not yet bind this owner to an authenticated builder.

The source preserves the selected owner and serializer, with only two relative
import paths changed. Public method names and hashes are not authentication of a
quota, addon, Peer, process or native observation.

## Builder and storage

The trusted parent must reserve the whole 22,386-byte remote quota before Node
creation. `allocateOwnerStorage(quota)` then subdivides genuine named allocation
debits: data TX/RX 544 each, control TX/RX 33 each, scalar 8 and C auxiliary 128.
Each synchronous `quota.claim(name, size)` must return exactly true before its
allocation. The five actual backing arrays total 1,162 bytes; the separate 128 is
a selected C standing charge. Failed allocation/setup does not refund this quota.
An always-true callback without the actual preassignment is not a valid builder.

`borrowRXsetup(syncUse)` lends RX[0:512] once for sequential Peer setup and zeroes
it afterward. The builder owns all partial Peer acquisitions until successful
`bind({addon, dataPeer, controlPeer, deadline, stderrSink})`. It must establish
authentic distinct channels, roles, protection, the selected addon image and a
common absolute monotonic deadline. Bind checks cannot establish these facts.
The stderr sink is bounded, synchronous and non-retaining. Trusted callbacks must
respect serialization and view lifetimes.

Codec frames borrow these exact slots; frame return does not refund backing
credit. Raw arrays and retiring C metadata stay charged until the actual owned
Node OS wait and aggregate cleanup. This accounting model does not prove all
V8, allocator, Promise, kernel or interpreter memory fits 24,576/3,480 bytes.

## Protocol and lifecycle

One consumed launch checks plain tuple data, at most 64 arguments/128 environment
keys, 4,096 UTF-16 units per string and serialized wire at most 8,500 bytes.
The serializer freezes the tuple graphs, omits undefined environment/cwd entries
and emits scalar bytes into existing storage without a complete JSON/UTF-8 token.
These bounds do not establish compatibility with an actual SDK tuple.

Launch kinds 1/2/3 share one operation; only the matching 129 resolves launch.
Input kind 9 uses a new operation and one pending at-most-512-byte borrow; its
matching 140 reports custody rather than native writes. Kind 133 reports known
input closure and actual native-written total. A zero custody result pauses input
until 132. One unused/pending 1..512 stdout credit is allowed. The parent's
credited-read invariant remains required, including EOF reads under unused credit.

One pump owns data RX/TX and one combined READ/WRITE readiness arm. A new TX intent
cancels an existing READ-only arm and consumes that exact Promise/generation before
same-Peer I/O. An EAGAIN-held frame is retained unchanged. A 64-action progress
quantum permits scheduling; it does not authorize an EAGAIN retry loop. Control
201/202 uses the independent Peer with a matching operation and accepted Boolean;
acceptance is not an actual native wait or model delivery.

EOF flags, both-EOF sentinel and actual wait remain distinct observations.
Unsupported signal/status values fail as unknown. Release kind 8 waits for known
input closure, actual wait, both EOFs and prior operations. Matching 139 must be
observed before both operational Peer closes and the release callback succeed.
Close attempts are independent and once-only. Unknown operations retain their
first failure and unresolved loans; no retry, guessed completion or refund follows.

The [parent reactor](sdk-parent-reactor.md) separately establishes target cleanup,
`ENGINE_LOCAL_RELEASED` and the actual Node wait/cleanup before `RELEASED` and
remote refund. Node's operational release callback is neither a V8 deallocation
nor an actor wait. The base reactor's model release is not a production fallback.

## Optional ordinary host check

```sh
make test-sdk-node-owner SDK_NODE_OWNER_TEST_OUTPUT=/absolute/fresh/absent/output
```

This optional target is outside default discovery. It snapshots 24 exact authored
inputs, builds one addon from the five current C sources, then runs one fixed
combined case with the repository owner, serializer, proxy, codec, Python Peer,
OwnedEngine and direct-process owner. No historical addon or artifact module is
loaded. The fixture port adapters are specific to this check, not trial wiring.

The fixed child reads one 33-byte UTF-8 JSON line through actual stdin EOF, echoes
it and writes bounded `A` bytes until actual EAGAIN. While proxy readable storage
is 2,048 bytes and actual data READ is armed, one independent SIGKILL exchange is
selected. The observer checks exact echoed bytes/output count, input custody and
native-written total, multi-fragment launch JSON, actual target/Node waits,
correlated 139 and the three release/accounting milestones. It records pressure
at the child and READ state at control selection separately; it cannot prove the
child socket is still unwritable at that later instant.

Each of the two fixed stages has a ten-second work interval, two-second cleanup
reserve and at most 64 KiB retained stdout/stderr combined. A verified subreaper
holds the unreaped leader through group containment before any direct/adopted
wait. Selected helpers inherit that group and must not escape it. Unknown cleanup
fails. Actual outer facts are persisted before later source/output verification;
partial receipts are not atomic publication or permission to replay the attempt.
Fresh output ownership and unchanged trusted toolchains are caller obligations;
this is not hostile same-UID custody or a hard real-time scheduling guarantee.

These ordinary checks do not establish exact SDK/native executable binding,
namespace custody, authenticated origin, hook/model delivery, physical fit,
provider/account capacity, fresh grading or full CAIRN-107 admission. Original
profile caps, verified aggregate controls, subscription routing and capture HOLD
remain in force. The selected source freezes and consumed attempts stay historical.
