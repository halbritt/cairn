# Direct SDK session input termination

`scripts/trial_sdk_session_driver.py` composes original source custody,
`SDKControl`, `SDKStreamDriver` and a fresh `NativeProcessOwner`. It adds an
explicit result-to-input-EOF transition. It does not implement a runtime
factory, independent hook producers, capture forwarding or task grading.
`require_complete()` still refuses with `hook_interface_unavailable` after its
source recheck. No real native CLI is admitted by these objects.

## Original owners

Construct `SDKSessionDriver` from the exact `SDKReviewPreparation`. Construction
consumes it once into its original `SDKSessionCustody`, retains its original
launch/controller objects, creates one fresh native owner and constructs the
one protocol writer before any child starts. Metadata, a raw controller or an
already consumed review cannot substitute for that ownership transfer. Closing
the old review cannot close the moved sources. Constructor failures clean up
newly acquired ownership without replacing the primary or erasing inherited
cleanup uncertainty; optional exception annotations remain best effort.

The externally authorized fixed builder can borrow `launch` and `native_owner`.
Neither is a launch permit, and the declaration does not prove that the actual
child executes that binary/configuration. This ownership convention requires
serialized access and no alternate protocol writes, FD replacement or foreign
waiter. It does not resist hostile Python mutation or authenticate an account,
endpoint, process, namespace, loaded settings or model request.

The external owner must retain these original objects in the actual capture
process, enforce the native/outer work and cleanup deadlines, service independent
producers, and collect actual native, producer and capture exits separately.
There is no additional Node writer or fictional Node actor/quota release in this
direct route. The finite outer capture bootstrap gate remains a different
descriptor from continuing native SDK input.

The separate [producer transport](sdk-producer-driver.md) provides bounded
full-input I/O and actual direct-child wait/release for independently owned
producers. `step(timeout=.05)` forwards a poll cap from zero to 50 ms to the
stream pump, allowing nonblocking interleaving. Selecting the real engine,
validating its output and composing its counters/evidence remain external.

## Terminal input policy

Ordinary documents still transfer to the separate evidence consumer. When the
pump returns an actual owned top-level `result`, the wrapper consumes that
`OwnedJSON` once. `TerminalDocument` checks the original session ID, a closed
lowercase subtype scalar and an exact Boolean `is_error`. `success` requires
`is_error=False`; other closed subtype names require `is_error=True`. This is a
minimal, explicit input-terminal policy, not the full installed native result
schema or a claim that every accepted error subtype exists in that interface.
Nested tool-result text and a caller-supplied success flag cannot trigger it.

The parsed graph transfers once in a new terminal document without a second
raw encoding or whole-graph copy. Its metadata retains the original wire
length/hash and first-read interval. Work product, tool balance, native model
usage, provider failure classification, costs, oracle correctness and grading
remain the independent evidence path's responsibility. An error result can end
input while remaining an error; input closure never turns it into task success.

Before closing input, the wrapper checks direct-writer/controller/native known
write counts agree and no write uncertainty is reported. The pump retains the
monotonic sample after each successful positive local write. A terminal line's
original first-read interval must not predate that last sample. Thus a result
already read into the held batch while a reply was pending is refused, even if
parsing resumes after the reply finishes. This checks observed local read/write
ordering only. Bytes buffered elsewhere can first be read later; it does not
prove native emission order, reply consumption, model delivery or causality.

The wrapper rechecks held sources, calls `SDKControl.seal_input()` and makes one
actual `NativeProcessOwner.close_stdin()` attempt. The seal requires successful
initialization, the full user write, an empty queue, fully written observed
callbacks and resolved mandatory SessionStart/UserPromptSubmit reservations.
See [SDK input seal](sdk-input-seal.md). The seal is sticky even if the physical
close is uncertain. The close attempt is recorded before it happens. An error
poisons the wrapper/controller, preserves original terminal metadata and actual
byte/close uncertainty, and cannot authorize replay or another close attempt.

After a known close, the pump drains ordinary output and observes actual wait
and EOF independently. A duplicate result or later control traffic fails the
session. Normal queue exhaustion still means idle. A terminal document returned
by `step()` means this local input transition completed; it is not a CLOSED
evidence declaration, successful process exit or task acceptance.

## Source failure and exit

`SDKSessionCustody.held_source_bindings()` provides the nonclosing recheck used
by the driver. A refusal or interruption makes source verification sticky but
retains the original file descriptors. Driver source checks, including
`require_complete`, poison input on failure without closing these files while
the child remains live. This corrects the unsafe composition of a live child
with the older offline `source_bindings()` close-on-error behavior. The older
method keeps that behavior for its existing offline callers.

`close_sources()` consumes an unlaunched native owner, or requires an actual
native wait plus detached native handles before closing source ownership once.
An attempted launch with no known child identity refuses as launch-unknown.
The method never signals or reaps, retries an uncertain close, or converts
incomplete EOF cleanup into known release. Its source-cleanup tuple is separate
from the native owner's release result. Native signal/wait/cleanup remain
accessible through `native_owner` after session failure.

This is a direct-child lifetime guard, not proof of descendant containment or
the selected external-exit contract. The real builder must enforce the complete
capture/cgroup/descendant release and selected-retention custody requirements
before using source closure in an admitted attempt. Actual capture exit and
source/evidence closure cannot be inferred from the direct child's wait. No
runtime builder or completion gate implementing those requirements is added.

## Ordinary checks and remaining work

Pure tests use actual disposable source files/controllers and strict owned
documents. Reported controller writes in those tests are model inputs, not
syscall evidence. They cover ownership transfer, one graph handoff, terminal
shape/session refusal, retained source handles on drift/interruption, and
inherited cleanup uncertainty after failed constructor assignment. The two
custody defects were reproduced before correction using real FDs and OS read/
close boundary faults. No internal owner class is mocked.

The optional fixed suite is explicit and consumes a fresh directory:

```sh
python3 -B scripts/check_trial_sdk_session_driver.py --output /absolute/fresh/directory
```

It snapshots twenty-five guarded authored sources and uses the existing fixed
owned-group supervisor: ten seconds work, twelve total and 64 KiB combined
output. The explicit host alias does not replay an earlier owner suite. All
setup/settings/binary files in this suite are disposable inert fixtures; the
actual child is one fixed ordinary Python surrogate. No native/SDK, provider,
account, namespace, corpus or oracle is opened.

The surrogate emits four synthetic callbacks and cannot normally exit until it
actually receives stdin EOF. Normal and error-result cases require that EOF,
actual zero wait, separate stderr evidence and known release. Six diagnostic
cases check premature results, an already-read result predating the final reply,
actual close followed by an injected exception, live source drift, duplicate
results and post-terminal controls. Expected killed/incomplete-release cases
remain explicit. The live drift case checks the original source FDs remain
open until actual native cleanup. Every case collects its actual wait, detaches
handles and checks descriptor restoration; a diagnostic PASS is not a successful
native task outcome.

The original budgets remain unchanged. Composed raw/stream/decoded working
storage, producer/capture buffering and 24576+3480 accounting remain unfinished;
there is no physical-memory-fit claim. Actual native terminal/concurrent hook
compatibility and callback-to-model-request delivery remain unproved. Independent
full-input producers and their five-second progress/drain/wait/cost evidence,
the distinct SDK H/E/retention profile, actual capture clock/start/T/D and
external exit proof, authentic funded reserved capacity with debit before each
forward, fresh frozen task/oracle, blind grading and both full weekly campaigns
remain required. CAIRN-107 stays active/HOLD; CAIRN-149/150 remain Done.
