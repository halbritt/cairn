# Native capture process observations

`scripts/trial_native_observation.capture` remains the supported import and
returns the existing `(stdout, stderr_tail, classification_code, timings)` tuple.
The implementation is in `trial_native_capture.py`. The prospective
`trial_task_eval.run_agent` caller supplies a separate `custody_path`, retaining
`native-process-custody.json` before parsing or grading and copying it into the
successful aggregate record as `process_custody`. A grading exception cannot
remove that separate process report. Legacy callers need not supply the path.

The child starts in a new OS session. Stdin writes and stdout/stderr reads use
nonblocking descriptors in one selector loop. The loop charges stdin delivery
and output collection to the same monotonic deadline, including when a child
never reads stdin or its leader exits while descendants hold pipes open. It
does not retry, select a provider, or reset a consumed target. Existing 8 MiB
pending-line, 64 MiB stdout, and 20,000-byte stderr-tail bounds remain; timing
metadata now has a 65,536-row limit. Crossing a stream or timing bound returns
classification code 125. Stderr is drained, with its full observed byte count
reported separately from the bounded tail returned to the caller.

`classification` records completed, timeout, output_limit, timing_limit, or an
observer interruption/I/O exception. `legacy_exit_code` preserves the old
caller's numeric classification: 124 on timeout, 125 on capture limits, otherwise
the child's return code. `observed_returncode` is independently obtained by
the capture owner's `waitpid` observation: a normal exit value or a negative
signal number.
It is null when the leader could not be reaped; `observed_wait_status` separately
retains the actual kernel wait-status integer. The return code uses Python's
`waitstatus_to_exitcode` encoding. A real exit 124 therefore
has `classification: completed` and `observed_returncode: 124`; a timeout can
have `legacy_exit_code: 124` and `observed_returncode: -15`. Neither process exit
nor a native final event establishes task acceptance.

On Linux, cleanup samples `/proc` identities as PID plus start ticks, session,
process group, parent and state. `waitid(WNOWAIT)` observes leader exit without
reaping it, preserving its session/PID anchor through the final cleanup sample.
Only after all signalling and session scans does `waitpid(WNOHANG)` reap the
leader and assign the observed return code to the Popen handle. Missing wait
status remains unknown; no fallback infers exit zero.
Each signal target has a pidfd bound while its membership in the owned session
or its relationship to a still-bound parent is checked. A first-observed escape
that already reparented cannot gain ownership from a PID or later start time.
Cleanup runs even if the leader already exited and even when pipes reached EOF. It sends TERM, permits up to two seconds,
then sends KILL with another two-second observation window if necessary. All
signals use stored identity-verified Linux pidfds; no reconstructed process group
ID or PID is signalled. Previously bound escaped descendants remain owned.
Missing or changed identity refuses the effect and reports unknown.
Unavailable pidfd/waitid support, scan/identity failure, signal failure, a live
observed member remaining, or inability to reap the leader produces fixed public errors
and `cleanup.status: unknown`. A prospective grade is then marked harness_error
while preserving the independent technical check outcome and legacy exit value.
Successful execution also requires every byte of the frozen native input to be
written. A child that closes stdin early and emits a valid-looking final result
is marked `input_delivery_incomplete`; its actual exit and independent technical
check remain available.
`input_delivery_complete` describes bytes written to the pipe; it does not prove
that the native process read them or accepted the prompt.

Capture and cleanup exceptions, absent/malformed custody metadata, and Python
interrupts produce selected typed unknowns in the prospective caller. The
aggregate retains every observed execution failure, with unknown actual wait
status left null. An observer-integrity stop prevents further planned calls while
already admitted cells retain their evidence. That stop is distinct from the
confirmed provider 401/429 admission stop; both can appear in one report.
The outer future exception handler also retains aggregate harness_error records
and recovers available custody even when later grading raises.

`observed_clear` means no live member remained in the final bounded sample and
the direct leader was reaped without a reported cleanup fault. Zombies retain
their state in the report. It does not attest their other parents' reaping, or
the existence and cleanup of descendants that escaped and reparented between
samples. Process scans cap at 16,384 directory entries and tracking caps at 256
identities; hitting either cap remains unknown. No raw stream, command, prompt,
credential, process name, or exception text enters the process metadata.

## Remaining supervision and retention seam

`hard_io_deadline_attested` and `cleanup.complete_descendant_custody` are always
false. Selector deadlines bound pipe readiness waits; they do not bound a
stalled `/proc` read, allocation/JSON conversion, metadata write, `fsync`, cleanup
interruption, or a kernel operation that stops returning. The selected-retention
V2 prototype's source acceptance and 64 MiB serialized disk ceiling are unchanged.
This correction neither imports that sink nor establishes its peak-memory
reservation, hard I/O wall clock, external terminal closure, or crash/power-loss
durability. The current runner still holds a bounded raw stream in memory for
its existing parsers; it does not become the selected-retention V2 runtime.
Unhandled SIGTERM, SIGHUP or SIGKILL sent to the observer skips its Python cleanup
and can leave the separate child session alive. Main-thread SIGINT can also
interrupt the overall controller. Only an external supervisor can detect those
terminations and close the whole containment boundary; this module provides no
such signal guarantee.

Before a runtime can claim those contracts, a separate owner must supervise
capture, parsing, and retention in a disposable process boundary with an
externally enforced wall clock and resource reservation. It must own a
containment mechanism covering descendant escape, record the worker's actual
OS termination independently from prepared/failure markers, confirm containment
release, and retain a fixed selected failure when the worker/sink cannot close.
That owner must write an exclusive attempt intent before the provider launch and
return a typed attempt result binding the consumed intent, process termination,
selected retention state and independent technical grade. This process metadata
addition does not implement that owner or exclusive-intent/result protocol.
Missing evidence must remain unknown; a retained marker cannot prove the worker
returned. No source-only review or passing disposable-child test admits a new
provider operation or relaxes the original memory, tool, final-answer, actor, or
consumed-target budgets.

## Local verification

`python3 -B -m unittest discover -s scripts -p 'test_trial_native_capture.py'`
exercises real disposable children for oversized never-read stdin, an 8,500-byte
input with the pipe reduced to 4,096 bytes before writing, concurrent
large input/output, timeout, actual nonzero/124 exits, surviving descendants
with open and closed pipes, and an observed setsid escape. Real-child refusal
tests cover changed/reused identity, missing pidfd support, and an escape whose
parent exited before its first observation. Fault injection checks signal failure
and missing process identity; the non-reaping exit test inspects the live zombie
anchor before the final wait. The prospective-run tests execute the current caller with a disposable native fixture
and verify retained/aggregate custody, unknown-cleanup failure, and custody
survival across grading failure. These tests contact no model/provider or store.
