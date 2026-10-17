# Owned memory-command job candidate

This candidate supplies `CommandJob` and a waited-text adapter seam. It is not a
running command service, operation policy, memory broker or runtime admission.
The fixed service must own the selected executable, arguments, credentials,
environment, source/configuration custody and original absolute deadline.
Coordinator integration remains unavailable. In particular, `start` still uses
synchronous `Popen`; the callback coordinator must not run that launch itself.
A prestarted trusted service is the selected design direction. Its actual CLI
waits and the coordinator's actual service wait must remain separate facts.

Construction is inert. The service retains the job before preparation and gate
dispatch. `prepare(body)` takes separately owned immutable Unicode stdin text or
`None`, validates its UTF-8 size against the existing total stream ceiling and
requests the original owner's queue/working reservations. A refused reservation
never launches. The parent forward then consumes `start(argv,cwd,env)` once and
may return the retained job; the gate's returned counter means that actual
forward return, not command/API completion. Any later failure must also poison
the enclosing broker attempt; this class cannot silently refund gate credit.

## Progress and actual outcomes

`step()` performs bounded nonblocking I/O: at most one 512-byte stdin offer and
one read per output, plus actual wait/close/release observations. The caller
interleaves SDK, producer, broker and command service turns. `interests()` lends
the owner's actual descriptors for readiness only. An idle return is not a
reason to spin or invent readiness. All callbacks and calls require trusted
serialization and no retained raw aliases.

Immutable body slices encode at most 128 Unicode scalars at once, then copy into
the owned 512-byte pending slot. No full raw stdin body is assembled. Positive
write counts, prefix hashes and cumulative traffic are recorded before time or
policy checks. Known EPIPE preserves only the accepted prefix and stops stdin;
it does not call the suffix consumed. An actual peer wait can also end input
without inventing delivery. The command can still have a valid nonzero outcome.
The command-input opt-in and its final independent review are prerequisites.

Outputs use strict stdout and replacement stderr decoding, with no raw copy
larger than the input read block or six-byte UTF-8 head. At most three pending
raw bytes remain per decoder. Unicode fragments coalesce into 256-character
blocks. Each original CLI stream retains its existing 1-MiB raw-byte limit;
this is separate from the original shared 4-MiB cumulative stream budget.
Raw counts/hashes describe the CLI bytes, not a later re-encoding of text.
Replacement stderr can require three times as many UTF-8 bytes in transport.
Every such transport byte still needs its own original aggregate charge.

Malformed stdout is latched while bounded capture continues. It does not
override an earlier output-limit, communication or deadline failure. Only after
actual wait, both EOFs and known owner release does `take_outcome()` materialize
text and return one `CommandTextOutcome`; invalid stdout then raises UnicodeError.
Actual nonzero return codes and JSON error text remain available to the engine's
real `run_json`/`Memory.call` decoder, including BUDGET_REFUSED. No synthetic zero
or API-success flag replaces these observations. Typed mapping of other service
failures back across the future broker remains to be implemented and verified.

## Reservations and lifetime

The supplied budgets object binds `reserve(queue,working)`, `release`,
`stream_room` and `charge_stream` to the ORIGINAL attempt reservations. No
per-command allowance, spill file, new raw pool or extra process-memory budget
is created. The queue quote is 1548 bytes: two owned 512-byte slots, bounded
decode/encoding/error scratch and UTF-8 head/carry overlap. The working quote
covers both streams' Unicode fragments and joined text, bounded references and
scratch, plus the transferred stdin text. It uses selected CPython object-size
observations conservatively; it is not whole-process or allocator-fit proof.
All native, backend, service and producer allocations remain inside the original
512-MiB aggregate controls. Actual shared-owner binding and peak validation are
still required; these callback interfaces do not establish that binding.

Taking text does not refund its reservation. Before `retire`, the trusted owner
must end every returned text alias or transfer its actual charge to the receiving
owner. Another process's acknowledgement is not its actual wait. The future
broker must bind sender/receiver reservations, decoder/join overlap and actual
producer cleanup; this class supplies no cross-process release proof.
Uncertain reserve/release stays recorded and is not replayed. Cleanup retains
primary, wait, signal and release facts independently. Expired callbacks cannot
receive late results; outer containment still owns unresolved processes and the
original final wall deadline.

## Validation ceiling

The candidate's model tests exercise the actual job and decoder with controlled
I/O/wait ports, including full stream ceilings, nonzero outcomes, known partial
stdin rejection, unread tails after a modeled wait and late positive writes.
Those waits and budgets are models, not OS observations or physical admission.
Adapter tests use the actual authored engine decoder with inert text outcomes.
Source review and fresh bounded ordinary-child checks are still required.
Original full CAIRN-107 criteria, source budget, account capacity, native origin,
model delivery and usefulness remain unproved.
