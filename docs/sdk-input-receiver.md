# SDK input receiver and descriptor owner

`trial_sdk_input_receiver.py` composes decoded input custody with the explicit
[profile2 protocol](sdk-input-profile.md). `trial_sdk_native_input.py` supplies a
synchronous, unbuffered descriptor owner. Neither launches a process, authenticates
IPC, loads the SDK or establishes that a descriptor belongs to the selected native
child. The live transport/reactor adapter remains unavailable.

`InputReceiver(validate,reserve,release,writer,closer,maximum=3145728)` accepts
profile2 input kinds9 and5 after independent frame authentication and parsing.
Operations must increase, with gaps allowed. `first_byte_ns` is an exact
nonnegative integer from the trusted receiver clock. Kind9 borrows1..512 immutable
bytes through at most the first LF; any suffix stays caller-owned. The complete
offer must fit the remaining cumulative maximum, which cannot exceed3MiB. Strict
whole-line validation precedes native writes through the
[custody component](sdk-input-custody.md). No allowance is added.

The scalar `reply` is either None or `(kind,operation,value)`. The driver encodes
it into its reserved frame storage. Kind140 reports custody, kind132 reports
readiness after a zero-custody pause drains, and kind133 reports the final known
native total after validated drain and known close. `reply_sent()` acknowledges
the exact current frame only after its known complete send. It has no correlation
argument: the serialized driver must reject stale or duplicate completions itself.
Encoded frame and transport loans retain their own ownership and accounting.
Unknown send outcome requires `transport_unknown()`, never a guessed acknowledgment
or replay.

While a reply is pending, `advance()` performs no write. Otherwise it attempts at
most one bounded write. Short writes, zero, EAGAIN and denied shared credit retain
the exact suffix. The reactor must independently service transport sends, native
write readiness and shared-credit changes; a zero return is not a readiness event.
End success requires all validated/custodied bytes to have known native acceptance,
no custody cleanup uncertainty, and known closure of the unique owned descriptor.
Only a confirmed end-reply send marks the receiver closed.

Cancellation and transport failure preserve the first primary error, attempt
staging cleanup and one close, and retain cleanup uncertainty separately. Reentry
is refused without clearing an outer operation's guard. Snapshots expose both
dependency `cleanup_unknown` and `receiver_cleanup_unknown`; inspect both. Raw
exceptions remain local diagnostics. Wire errors must use bounded fixed codes.

`NativeInput(fd)` borrows the descriptor during configuration checks and takes
exclusive custody only on successful construction. Refusal leaves custody and
flags with the caller. The descriptor must already be writable, nonblocking,
noninheritable and a FIFO/socket. The caller independently establishes that it is
the selected native stdin stream, including socket stream type, provenance and
absence of aliases or another file-object owner. `fileno()` is borrowed for
readiness observation; it grants no close, duplicate, rebind or write authority.

`write(body)` borrows1..512 bytes and returns the actual unbuffered syscall count.
EAGAIN is known no progress; other syscall failures conservatively make write
outcome unknown and forbid further writes. `close()` detaches the descriptor number
before its one syscall. Known success returns None; an exception is sticky unknown
and subsequent close calls refuse without another syscall, even after number reuse.
If the syscall failed before release, outer worker teardown must retain unresolved
cleanup. No destructor guesses release. Closing this descriptor does not prove
absence of aliases, child EOF, wait or model delivery. Its CLOEXEC flag says nothing
about the native child's stdin flag.

Calls and accounting/validation callbacks are trusted, serialized and nonretaining.
Snapshots and local standard view release complete normally. Original raw, working,
stream and aggregate controls remain unchanged; logical credit is not a physical
memory measurement. All snapshots remain unqualified.

Twelve receiver methods cover framing/client flow, reply ordering, retained-error
lifetime, bounded progress, cancellation, uncertain close and primary preservation.
Six descriptor-owner methods use disposable local pipes to check actual bytes and
EOF, EAGAIN/resumption, refusal before custody transfer, raw-error lifetime,
invalid-line refusal and no close retry across descriptor-number reuse. A simulated
uncertain close is injected only in its test. These are local source/ordinary-pipe
checks, not protected-channel, actual SDK/native, capacity, delivery, grading or
CAIRN-107 admission evidence. Original budgets and capture HOLD remain unchanged.
