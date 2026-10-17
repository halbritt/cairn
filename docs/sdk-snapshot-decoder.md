# Snapshot decoder and full phase-table disposition

_utf8_parts borrows the read block through memoryview. Only a carry of at most
three bytes plus at most three new bytes is concatenated. Large remaining input
is decoded through a view. Snapshot._readback still hashes every original byte,
drains to exact EOF and checks original file identity/digest/length before
reporting invalid UTF-8. Bad middle input clears text fragments and sets a sticky
invalid flag; subsequent reads continue authentication. Truncated final carry
still raises the original final UnicodeDecodeError after authentication. No
fail-fast BodyDecoder is substituted. Normal output and refusal precedence remain.

Seven new real-snapshot tests cover widths1..16 and all2/3/4-byte scalar split
positions,8192 boundaries, malformed/overlong/surrogate/out-of-range sequences,
truncated final errors, digest/identity/content mutation after invalid bytes,
real FD cleanup and retained interrupted decoder frames. Borrowed views can pin
the underlying bytes after an interruption; finally/local clearing elsewhere is
not proof those aliases ended. No standing allocation is released by this change.

The healthy explicit length envelope is conservatively8210:8192 borrowed read
plus18 for small carry/head/tail/transient slots. It is not a physical allocator
bound. Installed Python3.12.3 observations show invalid input through a view can
create a distinct8192-byte UnicodeDecodeError.object alongside the8192-byte input:
16384 named live bytes, before other internals. That failure observation is in the
phase table and prohibits replacing an all-path quote with the healthy envelope.

sdk-owner-phase-table.json is the single source-resolved phase inventory. It pins
18 authored files and records18 backing groups and50 phase/state rows. Original
ticket paths refer to actual production object relationships; amounts are the
reviewed full-standing fixture profile, not observed native allocations or a
complete physical quote. Cumulative command working is parameterized k=0..4;
reply/interruption states retain k, transfer proposals carry k+1, and uncertain
before_status/parent retirement/return transitions retain old/proposed references.
Command5 is a denied attempt, not an admitted fifth command. In fact full initial
callback admission already refuses on raw capacity, so downstream rows are
conditional source paths, not claims those states were reached with the full
profile. Prior callback history0..127 preserves the controller row limit and the
unproved retained-owner-shell cost.

check_sdk_owner_phase_table.py checks pins, source locators, unique original
ticket charging, arithmetic, cumulative-state coverage and denied/unknown status.
Its success means TABLE_VALID_BOUNDS_INCOMPLETE. Every physical_upper is null;
unknown is never zero. These checks cannot turn table declarations into proof.
Full callback136353/command153325 vs131072 and cumulative working refusals remain.
No generation-bound transition is selected.

The residual decision is to stop isolated buffer optimizations. Complete closure
now needs: (1) implementation-bound Python/decoder/compiler/allocator and graph
storage bounds, including failure objects; (2) the actual supported native launch
and OS custody contract covering all native/backend/producer/kernel memory under
one original attempt; (3) upstream/export/error alias containment and a bounded
terminal evidence consumer. The source-only SDK stream minimum is not a quote for
the native process. load_engine's Unicode/compile path is not bounded by its8192
read loop. No source boolean, generic adapter, discarded terminal result or
reclassified working bytes can satisfy these missing interfaces.

No vendor/provider/native/namespace acquisition/execution is selected. Preserve
all consumed SDK operations, all caps and tickets. The table supplies exact
source anchors and needed evidence for each unknown rather than authorizing a
new speculative fetch. Main and earlier candidates remain unchanged; this is
not complete CAIRN-107 or runtime adoption.
