# Validated original-line forwarding

`trial_sdk_forwarding.OriginalLine` is a pure, one-line egress component. It
validates a `DecodedLine` with the existing incoming parser, retains its immutable
text reference, and transfers its exclusive `OwnedJSON` token through
`take_document()` once. Mutating that graph cannot change forwarded spelling.
The line metadata is trusted assembler provenance, not caller authentication.

`write_to(writer)` makes one synchronous write attempt using a borrowed read-only
view. The writer returns its actual accepted byte count. Zero or an EAGAIN-style
`BlockingIOError` without partial progress leaves the pending chunk intact. Other
errors or invalid counts terminate the line without replay. A partial-progress
exception is unknown outcome, never a reason to resend. The method does not open
an endpoint, poll, manufacture a success or interpret the control protocol.

The supplied `reserve(n)` must atomically return True with n bytes of credit or
False with none; it must not fail after committing credit. `release(n)` returns
that exact credit once. Both are trusted synchronous, non-reentrant accounting
boundaries. Release failure is retained as `cleanup_unknown`; it is not retried
and must not be treated as returned credit. A primary writer failure remains the
raised exception even if release also fails. `acknowledged_bytes` counts only
reported writes, not recipient parsing or delivery. The final LF must drain
before state becomes `complete`. `cancel()` abandons the remainder and drops any
unclaimed graph, without invalidating a graph already transferred to its owner.

The caller transfers its immutable line reference by convention and stops using
it; Python does not enforce exclusive immutable aliases. Each instance owns at
most one line and one encoded chunk. The driver must admit only one in-flight
line per selected lane, with no implicit document queue. Completion releases the
text reference; an unclaimed graph remains available until taken or cancelled.

At most 128 code points are encoded per chunk (512 UTF-8 bytes), with original
whitespace, escapes, numeric spelling and CR preserved; LF is a separately
reserved one-byte chunk. Credit is acquired before encoding, retained over partial
writes, and returned only after the chunk and borrowed view are dropped. No
whole-line byte representation or reserialization exists. Incoming validation's
UTF-8 length check now counts scalar widths instead of allocating encoded scratch;
its accepted values, errors, graph limits and public API remain unchanged.

The writer must not retain the view or derived aliases after returning. Writer
copies, downstream queues, IPC framing, receive buffers and concurrent lines need
separate reservations in the owner's unchanged 128KiB aggregate. Decoded text,
parser graphs, text slices and decoder parts/join are working representations under
the existing aggregate process-memory limits. Dropping references is not proof of
physical collection, peak RSS or kernel accounting.

`LineAssembler.feed` is independently borrowed: exhaust or explicitly close its
generator before releasing the raw input reservation/backing view, even when a
forwarded line has completed. Coalesced input may keep the original raw chunk
alive while forwarding pauses. Do not queue all yielded documents to evade the
single-line rule. No iterator over encoded output hides that lifetime here.

This component grants no callback, producer, context, native origin or delivery
authority. It is not a second SDKControl writer or an SDK/native bridge. Original
8500/9500 budgets, first-byte callback deadlines, tools/events and resource caps
are unchanged; the future driver still owns their enforcement and real waits.

Pure checks: `python3 -B -m unittest discover -s scripts -p
 'test_trial_sdk_forwarding.py'`, plus the existing incoming tests. Tests use
strict real parsing, synthetic credit and synchronous writer boundaries only.
