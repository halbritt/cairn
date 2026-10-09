# Live synthetic coding pipeline fixture

`sh scripts/test-coding-pipeline.sh` runs seven provider-free tests in one fresh
owned delegated user service. The test imports the existing sink source-closure
helper, snapshots the entire Python worker import closure into private readonly
files, and mounts those files into fresh sandbox namespaces. The capture has no
mount of the sink's writable output directory. Both workers run under the
existing `AttemptDomain` and `CodingTransport`; the composition entrypoint is
the existing root `CodingSink`, not a replacement test sink.

The only actor is fixed Python code forked inside the capture. It must receive
the exact frozen 69-byte one-user-message input before it writes a small fixed
SDK-shaped T1 format sample. Its success final contains null values solely to
exercise the pinned final-object parser; it performs no task work. Capture
records its monotonic origin before fork and flushes CAPTURE_START before
actor output. Capture owns that direct child, samples its actual namespace PID,
start ticks and host boot ID while the child is blocked on input, and obtains
raw status with `waitpid`. Actor PID is local to the independently observed
capture PID namespace; capture and composition waits separately carry actual
controller-owned host process identity. No actor argv, binary, SDK, provider,
native-bootstrap, caller clock or admission interface is added.

The real transport checks held capture/controller time and time-for-children
namespace identities before gate delivery. It timestamps actual capture pipe
reads and sends paired T/D, selected H and full coding E to the real sink.
The sink consumes the exclusive intent and acquires both writers before READY;
the controller observes READY before creating capture. Successful retention
uses the actual capture-owned actor ExitSnapshot before composer observations.
Selected success/failure files are independently opened with NOFOLLOW and
checked for type, owner, 0600 mode, one link, bounded length and receipt hash.
Returned CLOSED declarations require actual sink wait, namespace observation
and kernel containment release; schema and hashes alone authenticate nothing.
Writes are exclusive and fsynced, not an atomic batch publication.

The seven cases cover actor zero and actor 17, a future-origin fault, lost final
actor metadata, unknown H, sink 29 after persistence, and killed sink after
READY. The known nonzero actor closes failure evidence with its real raw wait.
Timing/metadata/H uncertainty never enables PREPARED. Sink nonzero or loss
withholds accepted closure even if a returned declaration or selected file
exists. Pending transport after sink loss is erased only by ordinary cleanup;
the test never empties a queue to manufacture EOF or successful persistence.

Each attempt keeps the original four-second deadline, 4 MiB native-byte bound,
128 KiB queue and 8,500-byte input allowance. Capture and composition leaves
share the same parent 512 MiB, no-swap, 32-PID, one-core ceiling. The owned outer
service keeps 90 seconds, 1 GiB, no swap and 64 tasks. The existing bounded
one-second final cleanup observation is retained separately. No budget changes,
permission changes or default fixture source changes are made. These small
samples do not demonstrate saturation behavior; the existing transport fixtures
cover its bounds. T/H framing still has the documented fragmentation overhead.

SDK init/result/model/account claims, profile matching and reported counters
are constructed fixture records. H rows, their process durations and memory
call counts are constructed, or explicitly unknown; no hook runs. Reported
synthetic cost is not an actual native/provider/hook cost or account proof.
Actual synthetic process wait/input/live timing and sink custody do not attest
native behavior, hook provenance, permissions, cell validity, task correctness,
runtime admission or measured usefulness. Selected result logs retain only
identities, waits, clocks, fixed uncertainty codes, bounds and file hashes;
native-byte packets and selected file contents remain transient and are removed
with the disposable fixture directories. Main and frozen prior trees remain
unchanged until independent root review and integration.
