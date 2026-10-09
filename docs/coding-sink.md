# Fixed coding composition sink

`trial_coding_sink` composes the reviewed coding stream reader, coding T/D/H/E
decoder, success retention and separate attempt-observation writer. It launches
no actor or provider. Its internal typed factory consumes a fresh run through
`Retention.create`, checks the expected intent digest and acquires the separate
attempt writer before making READY available. Configuration, profile and source
declarations remain the external owner's responsibility.

The fixed pipe reader bounds each packet before reading its payload. Exactly
one T binds each nonempty D's native bytes and capture-relative elapsed time;
one selected H precedes the full coding E. The decoder independently verifies
the framed-wire hash/count, native-byte hash/count, timing count and framed H
digest. Missing, malformed or extra packets poison the attempt. The pipe reader
requires EOF after E before persistence or CLOSED. Blocking reads and filesystem
calls remain subject to the external controller's absolute I/O deadline.

On a known zero actor and capture wait, complete input delivery matching the
fixed user-message encoding of the composer's frozen prompt, capture release
and no declared end/timing/hook or composer uncertainty, the sink may call the
success retention writer with the exact E actor identity and exit snapshot.
It never finishes a failed reader with a synthetic zero exit. A success-retention
refusal keeps that writer consumed and its diagnostic/partial files intact;
the distinct attempt writer may retain selected failure observations. This is
not a retry of the original persistence operation.

`AttemptRetention.prepare_coding` requires an exact completed, unpoisoned
`CodingDecoder` and the separately held `ConsumedHooks` snapshot acquired from
the actual H payload. The snapshot keeps a bounded canonical selected projection
and the original framed H digest; valid noncanonical JSON retains its original
wire digest. Before either success or observation persistence, the sink compares
the current decoded H with this snapshot. The attempt writer repeats the check
after consuming its preparation, rejects row/source/observation drift, and uses
the frozen rows. These local state checks confer no runtime authority.
It retains the entire coding E, verifies its current running wire/native hashes and counts, and
checks the coding stream's native digest. Success observations additionally
bind the original SELECTED/PREPARED receipt and exit snapshot. Common E APIs
retain their earlier behavior; coding facts are never stripped into a seemingly
complete common report. Typed local objects and matching declarations are shape
and binding checks, not authenticated process or account identity.

The sink closes both independently owned writers and clears transient parser
input before returning CLOSED. An observation write/readback/fsync or close
failure withholds CLOSED and leaves consumed files intact. An interrupted or
broken pipe may preserve prior selected channels without an end observation;
that diagnostic does not close the attempt. Cleanup preserves the original
pipe or interruption error. No raw transport, private command or prompt is
written by this module.

CLOSED distinguishes `success_retention` and `failure_evidence`. Neither outcome
decides cell validity or task acceptance. The external owner must still observe
the actual sink return, namespace/process identity, complete channel drain and
released containment, and independently inspect exact selected files. A returned
receipt alone cannot establish any of those observations. Native launch/profile
acceptance, independently measured hook provenance, actual capture timing,
provider-account separation/capacity, cost enforcement and campaign admission
are separate unfinished requirements.

The disposable fixed worker runs this sink inside the existing owned composition
leaf. Its actor/T/H/end facts are constructed; it has no provider entry.
`test-coding-sink-boundary.sh` owns a fresh delegated service with the existing
outer 1GiB/no-swap/64-task ceiling and unchanged aggregate 512MiB/no-swap/32-PID/
one-core controls. Four actual worker tests observe zero/nonzero sink wait,
namespace identity, containment release, selected-file hashes and a killed worker
after READY. Runtime sources are copied to private readonly fixture snapshots
and their digest manifest supplies the declared source hash. These tests do not
observe a real actor, native hook, live capture clock or loaded provider profile.

Seventeen additional local tests exercise disposable selected persistence and
constructed packets: success/failure composition, unknown timing, missing/extra/
drifted end, consumed decoder refusal, observation I/O fault, close failure and
interrupted diagnostic cleanup, consumed-H state drift and valid noncanonical
H wire. A declared complete input count for a different
message remains control uncertainty, not successful delivery of the frozen
input. They make no campaign or usefulness claim.
