# Coding stream composition

`scripts/trial_coding_stream.py` feeds one bounded transient native stream to
the success-only reader, the independent failed-action projector, terminal
metadata and selected-input accounting. It exposes no process launcher,
provider admission, persistent raw-stream ingestion, cell-validity decision or
task grade.

The profile declares the six implementation tools, two native memory tools,
`dontAsk`, the current read policies and projected hook events. The seven
eligible task templates require their common final JSON fields and task-specific
field. T6 remains withdrawn in the planned eight-case analysis outside this
module. These declarations do not freeze a campaign or implement the separate
weekly instrument. Transport stays within the original 4 MiB total, 1 MiB line
and 2,048-frame capture limits; selected final JSON stays within 8,192 bytes.

Each accepted transport chunk contributes to the whole-stream digest even after
the success reader refuses a frame. Its consumed-prefix digest is retained
separately. An invalid transport chunk prevents a complete-digest claim. Bounds
stop parsing, discard transient transport buffers and mark uncertainty; later
bounded chunks can still contribute hashes. Failed terminal counters and cost
are reported values or explicit unknowns. They do not attest billing or enforce
a spending limit.

Every transport stop poisons and closes the success reader before returning,
including invalid chunks, total/line/frame bounds and reader interruptions.
Required capture timing uncertainty also poisons it while the independent
failure-action and terminal channels continue. An already accepted ledger prefix
remains diagnostic evidence; direct success retention cannot turn that prefix
into successful whole-transport evidence. `whole_stream_consumed` additionally
requires a complete digest and no composer stop. Post-stop hashing is bounded
by each accepted chunk's size and the external owner's total stream/deadline
contract; the composer alone does not bound all later hashing work.

Hook measurements enter through a bounded projection containing only the event,
stdout digest and byte count, stderr byte count, elapsed time, exit code and five
memory-call counters. Extra payloads are not traversed or passed to accounting.
Malformed fields stop accounting with a fixed uncertainty code. The projected
measurements are caller declarations until the external owner binds their
actual process and transport origin. Accounting failure labels, native denials
and missing actor work do not by themselves decide cell validity.

Reported native hook success requires an explicit integer zero exit code.
Missing or malformed exits remain unknown and produce no successful delivery
projection; they do not invent a hook failure. Hook byte and call charges remain.

The caller declares a frozen input charge. Impossible charges below the encoded
single-message wire size plus the existing 512-byte reserve are refused, and at
least 1,000 bytes remain for hook context. The owner must additionally bind the
memory instruction, actual complete input delivery, settings, request and source
manifest; the composer cannot authenticate those facts.

A successful composition worker must call `Retention.prepare(reader,
actual_exit_snapshot)` before `observations(hook_rows)`. The composer never
calls `finish(0)` or manufactures an exit snapshot. Its observation call closes
the composer once and clears transient stream, terminal and parser buffers.
Failure observations can instead feed a separately owned failure-evidence
writer. This module does not implement that writer or establish its closure.
The controller must still observe the worker's actual return and containment
release, independently of any READY or CLOSED declaration.

Constructed tests cover complete hook/accounting correlation, failed-result
rollback with later whole-stream data, failed terminal counters, missing tails,
malformed timing, transport bounds, invalid chunks, private extra hook payloads,
measurement faults, interrupts and task/input declarations. They launch no
provider and establish no runtime admission or measured usefulness.

`trial_native_observation.measure_lines` accepts single-pass iterables of complete
text lines and corresponding timing rows. It performs the same accounting as
the existing batch `measure` entry point, including repeated error-result bytes,
missing or surplus timing rows, and the original 9,500-byte and 30-second checks.
An input iterator failure propagates without a partial success report.

This entry point avoids requiring a complete stream string. It does not bound
the caller's line buffers or the evaluator's correlation state, authenticate
timing rows, or classify SDK control frames. The current coding composer still
uses the batch entry point. Connecting retained SDK streams requires those
separate framing, resource, provenance and control-frame contracts.

`LineMeasurement` provides the same evaluator as a single-use incremental owner.
Call `consume(line, timing)` for each complete original text line, then
`finish(hook_rows)` once final hook observations are available. Each consume ends
its borrowed line and timing references before returning. An exception consumes
the observer; `close()` is idempotent and also ends an unfinished observation.
The batch `measure` and `measure_lines` interfaces delegate to this owner and
retain their existing charges, unknown states, and limits.

This interface does not yet connect SDK stream custody to qualification. It
retains the evaluator's existing correlation state, whose bounds still need
review for the SDK allocation. The live integration must supply complete-line
receipt time separately from the first-byte callback deadline, explicitly
validate and classify SDK control frames, and bind final measurements to the
retained stream. Calling `finish` alone does not establish that provenance.
