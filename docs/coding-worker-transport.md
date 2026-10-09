# Internal coding timing transport

`CodingTransport(domain, run_id, source_manifest_sha256, intent_sha256)` is a
separate internal branch of the accepted owned worker transport. It adds no
launcher, actor argv factory, clock callback, profile selection, authentication
or native admission route. Only separately reviewed fixed internal builders may
bind capture/composition workers to the actual `AttemptDomain`. The default
byte-only `WorkerTransport` D/E branch retains its behavior and budget.

The parent must install and pin the fixed capture, fixed composition worker,
pure coding codec and their transitive dependencies before READY. Composition
creates the exclusive held output intent before READY; only after that READY and
independent worker namespace/cgroup observations may `gate_capture(frozen_bytes)`
send G and at most 8,500 input bytes. Existing actual owned wait/release and
INTENT/READY ordering remain in the base transport and domain.

Before G, coding transport also opens/holds controller and actual capture-init
`time` and `time_for_children` namespace FDs. All four inode identities must
match, and the actual host init PID/start ticks and leaf membership are checked
before and after acquisition. Unavailable or changed observations refuse G.
Refusal closes every acquired descriptor, retains any close uncertainty on the
owned worker and never reconstructs PID signal permission. Successful FDs remain
owned by the worker until its ordinary guarded close.

The fixed capture must emit exactly two newline-delimited stderr control records:
first bounded CAPTURE_START, emitted immediately before actual actor Popen,
then ACTOR_WAIT after its actual owned wait and input accounting. START binds
run/source and actual `start_monotonic_ns`; ACTOR_WAIT retains the accepted common
actor PID/start/boot/raw wait/returncode and input facts. `capture_report()` returns
`{'start': validated_start_or_None, 'final': declared_actor_wait_record}` only
after control EOF and exact two-record framing. These declarations do not
authenticate runtime; the parent owns the fixed capture builder and admission.

Until startup control is available, native stdout is backpressured without
reading it. A missing origin plus a blocked capture therefore requires the
parent's existing wall/failure stop, not a manufactured elapsed interval. On
startup/control EOF, malformed or absent START permits bounded selected stream
with explicit timing unknown. Missing or malformed final framing refuses the
actor report. The parent may still close failure evidence with null actor/input
facts and fixed common unknown observations after independently stopping,
draining, waiting and releasing capture. It must not turn these unknowns into
actor FAIL or a valid success.

Each actual controller `os.read` of native stdout is immediately timestamped by
controller `monotonic_ns`. One bounded T plus unchanged D is queued as a pair;
origin stays constant and read-before-start has null read timing with fixed
`capture_timing_unknown`. Once timing is unknown, later intervals remain unknown.
Gate time and sink arrival never substitute for capture origin or read time.
T reserves up to 1KiB and both headers within the unchanged 128KiB queue, preserving
backpressure. The base D native hash/count and separately observed raw hash/count
are unchanged. Known output excess can bind the selected prefix and fixed stop
observation; actual transport loss stays unknown. No packet history is retained.

After actual capture closure, `end_capture(actor_wait, *, input_bytes,
input_bytes_written, observations=(), hook_measurements=selected_H)` validates a
root-supplied bounded selected hook snapshot, queues H then full coding E and
returns that full coding E only after admission to the bounded queue. Hook
measurement provenance remains the parent's responsibility. Unknown rows are
explicit null, distinct from empty rows. The pure `CodingDecoder` verifies T/D/H
and full E source/native/wire binding in the fixed sink. The parent must retain
full coding E in selected success/failure evidence, not strip it into common E.
`closed_sink()` still requires actual externally observed composition wait,
namespace identity, release, drained transport and exact CLOSED declaration.
CLOSED itself is a declaration, not independent persistence or validity proof.

Default raw native allowance stays 4MiB (the base internal ceiling is 64MiB),
queue stays 128KiB, packet bounds remain T 1KiB and D/H/E 64KiB, frozen fixture wall
stays four seconds, attempt aggregate stays 512MiB/no swap/32 PID/one core and
owned outer fixture service stays 1GiB/64 tasks. One-byte fragmentation has the
documented large derived wire/CPU cost in `coding-worker-protocol.md`; no lower
chunk-count limit, increased resource allowance or packet history is added.

Provider-free real worker tests exercise actual nonzero actor wait, capture and
sink waits/releases, separately delayed sink timing, fragmented D, malformed,
future, missing and late contradictory origin, selected H unknown, namespace FD
refusal/close uncertainty, known output-cap prefix closure, startup pipe
backpressure/owned stop, lost final metadata and hung sink without persistence.
The clock/descriptor fault cases inject real OS observation faults around owned
processes; they do not attest a successfully created alternate time namespace.
The fixture's forked disposable actor and selected-file sink are fixed test
peers, not the root coding composer/retention implementation or a native provider.
Actual profile/hook measurement, success Retention.prepare and selected timing
policy remain the separate fixed-worker integration owner's work.
