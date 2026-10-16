# SDK whole-record IPC codec

The implementations are `scripts/trial_sdk_ipc.py` and
`scripts/trial_sdk_ipc.cjs`.

This source component defines one shared Python/CommonJS whole
record. It does not open sockets, authenticate senders, clean ancillary rights,
select SOCK_SEQPACKET, implement stream reassembly, signal a child, acknowledge
native writes, launch a process, parse SDK JSON or authorize a provider operation.
The future owner supplies transport, authentication and shared accounting.

## Wire

Big endian fixed 32-byte header, followed by exactly body_length bytes:

| Offset | Width | Field |
|---|---:|---|
| 0 | 4 | ASCII CIPC (43 49 50 43) |
| 4 | 1 | version 1 |
| 5 | 1 | closed kind below |
| 6 | 2 | flags, zero |
| 8 | 8 | unsigned sequence, 1..2^64-1 |
| 16 | 8 | operation, 0..2^53-1 |
| 24 | 4 | body_length, 0..512 and kind-specific |
| 28 | 4 | reserved, zero |

Direction is configured locally and checked against kind; it is not authenticated
by that check. The direction names include channel role. No sequence gaps/replays,
no wrap. A fresh endpoint begins at 1. The first_sequence/firstSequence argument
permits boundary tests or an independently authorized checkpoint; it supplies no
resume authority. Reinstantiating a codec never makes a replay safe. Each direction
uses a separate instance and a separate sequence space. Do not share an instance
between independent send and receive streams.

| Kind | Name | Direction | Body bytes | Operation |
|---:|---|---|---:|---|
| 1 | LAUNCH_BEGIN | N2P_DATA | 8 | positive |
| 2 | LAUNCH_FIELD | N2P_DATA | 1..512 | positive |
| 3 | LAUNCH_END | N2P_DATA | 0 | positive |
| 4 | STDIN_WRITE | N2P_DATA | 1..512 | positive |
| 5 | STDIN_END | N2P_DATA | 0 | positive |
| 6 | STDOUT_CREDIT | N2P_DATA | 2 | zero |
| 7 | ABORT | N2P_DATA | 0 | zero |
| 8 | RELEASE | N2P_DATA | 0 | positive |
| 129 | LAUNCH_PART_ACK | P2N_DATA | 0 | positive |
| 130 | LAUNCH_RESULT | P2N_DATA | 1 | positive |
| 131 | WRITE_RESULT | P2N_DATA | 2 | positive |
| 132 | WRITABLE | P2N_DATA | 0 | zero |
| 133 | STDIN_END_RESULT | P2N_DATA | 1 | positive |
| 134 | STDOUT | P2N_DATA | 1..512 | zero |
| 135 | STDOUT_EOF | P2N_DATA | 0 | zero |
| 136 | STDERR | P2N_DATA | 1..512 | zero |
| 137 | WAIT | P2N_DATA | 8 | zero |
| 138 | ERROR | P2N_DATA | 2 | zero |
| 139 | RELEASE_RESULT | P2N_DATA | 1 | positive |
| 201 | SIGNAL | N2P_CONTROL | 1 | positive |
| 202 | SIGNAL_RESULT | P2N_CONTROL | 1 | positive |

These are framing envelopes. Body contents are opaque in this slice: even a
correctly sized CREDIT can contain zero or an excessive allowance and MUST be
rejected by the future semantic adapter before effects. WAIT's eight bytes do not
prove a wait. Result status values, launch field schema, correlation to outstanding
operations and lifecycle order require separately closed semantic validators.
Nothing maps ACK to launched:true. No operational receiver may dispatch directly
from codec success without authentication and semantic admission.

## API and borrowed lifetime

Python: Codec(direction, reserve, release, first_sequence=1).
CommonJS: new Codec(direction, reserve, release, firstSequence=1n).

- encode_into(destination, kind, operation, body) / encodeInto returns a Frame.
  Destination has exactly 32+body length bytes and is writable. Body and destination
  must not share a backing allocation, even if the ranges are disjoint.
- decode(record) returns a Frame after all header, kind, length, operation and
  expected-sequence checks. A record is exactly one whole transport record.
- Frame exposes kind, sequence, operation, record, body and release(). Metadata
  stays readable after release; record/body access and another release refuse.
- reserve(n) must synchronously return the exact Boolean true/false. True owns a
  loan of n=32+body bytes; false owns none. No partial grant, pending promise or
  post-commit throw. Any other result/throw fails the codec with fixed error.
- release(n) must synchronously return the exact loan, without failure. An exception
  records cleanup_unknown/cleanupUnknown and permanently fails the codec. It is
  never retried. A callback must not signal failure merely with a false return;
  release has a void-success contract.

Python requires exact memoryview objects, contiguous one-dimensional unsigned-byte
format B; destination must be writable. Frame returns fresh readonly memoryview aliases; releasing a returned alias does not release the frame loan or invalidate its internal view.
JS accepts ordinary Uint8Array/Buffer views with fixed, non-shared ArrayBuffer
backing; SharedArrayBuffer and resizable backing refuse. JS typed-array aliases
cannot be made readonly here. Trusted caller objects must not override view
methods/properties. Python cannot establish absence of another process mapping the
same memory; the owner must ensure exclusive storage discipline.

The caller must hold all record/body backing storage stable through the operation
and every outstanding Frame. Hooks cannot modify, detach, resize or concurrently
access it. Do not retain/use aliases after release; existing aliases cannot be
revoked by either language. A readonly Python alias does not stop mutation through
another alias. A transport borrowing the encoded record must finish borrowing
before Frame.release. Release acknowledges only end of this local loan, never
native acceptance, delivery, EOF, launch, signal acceptance or actual wait.

One codec has one frame or operation in progress. Calls during reserve/release or
an outstanding Frame refuse with frame_busy without changing that loan. There is
no internal queue or hidden retry. A valid issued Frame consumes its sequence
immediately; release does not rewind it. If send outcome is unknown, preserve that
uncertainty and terminate the transport rather than reset/retry sequence. Invalid
input or unknown reservation permanently fails the codec. Ordinary credit denial
is recoverable: no destination mutation, no body returned, no sequence consumed;
the caller may retain that same already-accounted record and retry when credit is
available. The maximum uint64 sequence is used once, then the codec is exhausted.

## Reservation and overlap requirements

No codec path allocates a raw body buffer. encode_into writes into caller storage
only after successful reserve. decode only borrows an existing view; it never
copies the supplied record. Small metadata objects, integer/header parsing and
view wrappers are ordinary interpreter overhead, not a physical-memory theorem.

The caller MUST reserve actual backing capacity before allocating its preallocated
receive/send buffers; codec reserve is later admission of a logical frame loan.
Returning that loan does not free or refund still-live backing capacity. The ledger
must either model the explicit transfer/alias relationship or conservatively charge
both: it must not count a loan return as deallocation of a retained buffer. Body
source storage remains charged while encoding; destination backing, any transport
copy, outstanding borrowed send, incoming frame and synchronous-control scratch
can overlap. Separate codecs for separate directions all use the SAME owner ledger.
Four local one-frame limits do not jointly prove any aggregate bound. Headers/body
loan charges use the existing IPC2048/framing1432 shares only after the root's
allocation partition is reviewed; this module introduces no separate allowance.
Kernel queues, ancillary allocations, decoder staging and native binding scratch
remain outside this source-only accounting claim. No new sockets or kernel tuning.

Invalid framing is rejected before invoking reserve and before handing out a body;
this does not mean the supplied transport record was allocated for free. Future
transport must enforce length/admission before allocation and authenticate every
span/record. Sender credentials and payload operation/sequence IDs are different
facts. No API here establishes either peer's identity.

## Evidence

Run `make test-sdk-ipc` for the Python and guarded CommonJS suites; `make test`
includes both. The 16 Python methods and 15 CommonJS groups share 21 synthetic
wire vectors in `scripts/trial_sdk_ipc_vectors.json` plus an explicit literal
vector. They check encoding/decoding, malformed headers, sequence/operation/body
bounds, view lifetimes and credit failures. These are local source checks, not
sender authentication or a deployed IPC protocol. The CommonJS preload is an
effect regression guard, not hostile-code isolation.

The conditional complete-line admission versus single-flight512 native-write
problem remains unsolved by framing. All original full126/1900/two-week campaigns,
fresh independent blind grading, capacity/custody, publication30275/32768 and
capture HOLD remain unchanged. No SDK/vendor/model/provider/native/build/addon,
namespace or socket effects are part of this component.
