# Direct canonical size/hash sink

The size/hash path consumes the same complete _pieces traversal as the transport
encoder, but hashes each encoded piece directly. It no longer creates a digest-only
bytearray or yielded bytes. Encoded scratch is cleared in finally before the next
encode, including on hash/count failure. canonical_chunks, _pieces, _scalar and
_string_pieces are unchanged. No earlier digest is published on late refusal.

For supported values, a string slice is at most1365 Unicode scalars, each costing
at most6 escaped UTF-8 bytes:8190. Other pieces (punctuation, finite float spelling,
booleans/null and signed1024-bit integers) are smaller. One encoded piece is live
in the sink. Previously the hash consumer could overlap bytearray contents,
encoded piece and yielded bytes, or buffer plus old/new encoded reassignment:
the conservative named logical envelope was3*8192=24576. The new explicit encoded
payload envelope is8190, a16386 difference in these envelopes, not an observed
process-memory saving or permission to refund that amount.

Unicode pieces, source graph/traversal, digest internals and allocator overhead
remain. A callee traceback may retain the offered encoded bytes even after the
sink finally clears its own reference. Caller failures still freeze original
charges; this change does not prove every error alias ended. A second interruption
of cleanup does not establish release. No ticket/cap/quote changes occur.

The verifier's next larger named path is Snapshot.read_text: read block plus the
incremental decoder's possible concatenated carry/block and carry copies, with a
conservative logical envelope16393. Adding first-SessionStart user<=8500 and
permission705 gives25598 named bytes, versus33781 before. This is a selected
verifier/controller overlap calculation, not a whole-process bound. Suspended
producer transport still uses unchanged canonical_chunks and stays under its
callback allocation; all other queued/working/compiler/kernel backing remains
separately accountable. Terminal graph still overlaps recurrent verification.

The shared incoming module is a child source dependency. Its closed SOURCE_PINS
entry is updated; every other child digest and the exact engine pin are unchanged.
Synthetic parity tests compare independent json.dumps bytes/hash and actual
transport, cover nesting/escapes/maximum boundaries and late shape/Unicode/number
refusals, and retain failure tracebacks to distinguish caller clearing from callee
retention. No native/provider execution or admission is established.

Current full-bank reservations still refuse136353/153325 against131072. The next
concrete materialization correction is to replace full carry+read concatenation
in snapshot UTF-8 decoding with the existing bounded-view/small-carry decoder
pattern. It must still hash/read to exact EOF and validate original identity even
on invalid UTF-8 before reporting failure; an early decoder exception must not
short-circuit that source authentication. Unicode join/JSON/compiler working
storage is a separate proof obligation. No reduced row is selected until the
complete phase/alias/physical inventory and original-ticket transition support it.
