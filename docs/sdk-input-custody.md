# Decoded SDK input custody

`scripts/trial_sdk_input_custody.py` provides decoded fragment staging, whole-line
validation and bounded counted writes. It creates no process, closes no descriptor,
opens no IPC and imports no SDK. It does not implement an authenticated receiver
or native ownership. The [input profile2](sdk-input-profile.md) and separately named
[Node custody factory](sdk-spawn-proxy.md) define its intended upstream contract;
the [receiver assembly and descriptor owner](sdk-input-receiver.md) compose those
source contracts. The live authenticated transport/reactor adapter remains
unavailable.

`InputCustody(validate,reserve,release,max_bytes=128*1024)` borrows exact bytes or a
readonly contiguous byte memoryview of1..512 bytes in `offer(fragment,first_byte_ns)`.
The timestamp must be a numeric exact nonnegative integer from the trusted
receiver's clock; it cannot carry a second raw object or a peer's timestamp claim.
The reply is exactly `{custodied:n}` through at most the first LF. Any suffix stays
caller-owned. While a validated line awaits drain, custody0 consumes nothing.

Collection releases the raw ingress loan and retains original spelling as decoded
working text, including the original escapes and whitespace. At LF the entire line
is strictly parsed once; a trusted validator receives the exclusive graph and
metadata and must return exactly True. Its result is policy testimony, not identity
or authority. Invalid completion can fail despite earlier successful fragment
custody. It forwards no bytes from that invalid line. Graph mutation cannot change
the immutable original spelling that the native writer later receives.

`write_to(writer)` attempts at most512 bytes of that validated line. The synchronous
writer returns a truthful actual accepted count and retains no borrowed view or
derived alias. Partial counts preserve the suffix; zero or truthful no-progress
BlockingIOError means backpressure. Credit denial also means no progress. The
outer driver must observe native readiness and shared credit changes instead of
spinning. Unknown effects or malformed counts terminate without replay.

`end_input()` rejects a partial line and prevents further offers. State drained
means that all staged validated bytes have known acceptance; it does not mean
native stdin closure, process wait, stdout EOF or model delivery. The owner must
observe those separately before producing the profile2 end receipt. `cancel()`
discards staged state and returns known credits once, preserving a failed state
and known/unknown native counts. Cleanup failure never replaces a primary write
failure or licenses a retry of an uncertain release.

The snapshot separates custodied_bytes, validated_bytes, known_native_written_bytes,
native_outcome_unknown and cleanup_unknown. A known count is only a confirmed lower
bound when native_outcome_unknown is true. Dependency snapshots are selected pure,
stable and nonthrowing observations; injected snapshot faults and allocation failure
are outside the tested preservation claim.

Calls and accounting/validation/writer callbacks are trusted, serialized,
synchronous and nonretaining. Busy rejection does not discard or clear an outer
call's guard. Cancellation and final-drain cleanup retain that guard through release
callbacks. Decoder errors export only their fixed code after leaving the decoder
exception context. Public early-error paths clear their local borrowed raw argument;
normal dependency view release must complete. Arbitrary external traceback aliases
are not sanitized by this module.

The raw scratch reservation16 precedes assembler creation and comes from the same
original ledger. OriginalLine separately reserves its encoding, at most512 bytes,
before allocation. Reserve must return exactly True for acquired credit or False
for no debit. A throwing/non-boolean reserve has uncertain outer-ledger outcome;
do not guess a refund. Release has a void-success contract; exceptions remain
unknown and are not retried. Ingress/frame backing and overlapping transport copies
retain their own credits. The128KiB LF-excluded line ceiling is unchanged; decoded
text/graphs and runtime overhead remain working allocations under the original
aggregate control. Logical reservations do not prove physical memory fit.

Sixteen authored tests cover fragment progress, UTF8 splits, exact128KiB/overflow,
validation, original spelling/hash, multiple lines, actual short/zero/unknown counts,
shared credit, primary/cleanup separation, cancellation, retained-error raw lifetime
and reentry. The CPython lifetime checks keep exceptions without garbage collection.
Scoped independent review resolved the earlier entry-retention and cleanup-guard
findings under the dependency contracts above. This is source verification, not
whole-package/runtime qualification. Full107, original limits and capture HOLD
remain unchanged.
