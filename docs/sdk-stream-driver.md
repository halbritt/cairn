# Direct SDK stream pump

`scripts/trial_sdk_stream_driver.py` provides the local readiness and demux
component for the selected direct Python route. It is not the complete
TrialSessionDriver or a runtime factory. Construct it with the original pristine
`SDKControl` and a fresh `NativeProcessOwner` before the separately authorized
fixed builder starts that owner. The session retains its `SDKSessionCustody`,
original source handles, work deadline and cleanup responsibility in the same
process. There is one protocol writer; do not also run a Node SDK writer or
write directly to native stdin.

The source accepts exact owner types through `SDKDirectWriter`. Those types and
Python references provide a trusted, serialized ownership convention, not
authentication against hostile in-process mutation. The pump does not create,
consume or recheck an `SDKSessionCustody` itself.
[`SDKSessionDriver`](sdk-session-driver.md) binds that custody to this exact
controller and native owner and adds the separate terminal input policy. No
declaration or caller boolean makes the native interface complete.

## Reads, writes and handoff

`step()` polls native stdout, stderr, stdin readiness and the held pidfd. It
returns at most one selected event. Its stdout iterator holds one 512-byte slab
until completely exhausted, including after a callback handoff. A separate
512-byte slab drains stderr without overwriting that tail. `LineAssembler`
provides strict UTF-8 and a 128-KiB per-document bound. `OwnedJSON` transfers
control documents once to `SDKControl.receive_owned`; ordinary documents
transfer once to the external evidence consumer. Unknown `control_` types take
the refusing control path, rather than the ordinary event-label path.

`step(timeout=.05)` accepts a finite poll cap from zero to 50 ms. The existing
callback deadline can shorten it further. Zero supports nonblocking service
beside the independent [producer transport](sdk-producer-driver.md); it does
not change callback timing or supply a runtime owner.

The external consumer must not accumulate returned documents or callback graphs
without its own bounded storage. Decoded line text and parsed graphs are working
representations, not raw-buffer credits. A suspended generator retains its
current decoded line until it advances or is closed. The local slabs are 1024
bytes; this is not the complete composed working set. The direct writer can
allocate a full offered suffix up to the original 8500-byte wire allowance.
Producer canonical chunks, capture forwarding, parser working memory, selected
retention and all original 24576+3480/128-KiB/512-MiB controls still require their
composed accounting. No physical-memory or runtime-fit claim follows here.

Positive read returns update exact consumed-prefix counts and hashes before
clock checks or parsing can refuse. The stdout stream is capped at 3 MiB and
combined native stdout/stderr at 4 MiB. Provider/capture/producer channels remain
outside these two local counters; the full composition must enforce the original
aggregate stream allowance. Hashes on failure are consumed prefixes, not claims
that EOF was observed. The native owner separately retains actual EOF facts.

The pump drains a bounded stdout batch before accepting another batch. When a
new readiness poll reports both input writable and stdout readable, it services
one actual write before reading the next stdout batch. Continuous ordinary
stdout therefore cannot indefinitely starve the protocol writer. The existing
writer reacquires its suffix and immediately reports each actual positive
return. `SDKControl.write_pending` observes the actual current queue without
allocating an offer; cumulative `queued_input_bytes` is unsuitable because it
also includes an unstarted user temporarily deferred by a callback.

## Callback deadline and ownership

The source deliberately supports one active callback at a time. It returns the
full `Callback`, then pauses stdout advancement while continuing stderr, input
write and wait service. An independently owned producer must receive the full
`iter_input_json()` stream, with observed input progress, stdout/stderr, actual
wait, memory-call counters and cleanup. None of those observations is supplied
by this pump or inferred from `reply(request_id, context)`.

The callback's five-second bound starts at the conservative monotonic sample
immediately before the actual read interval containing its first byte. It is
not an exact kernel-arrival timestamp. Assembly, parse, external producer work,
reply serialization and the final actual write use that same bound. Payload
`issued_at` and `deadline_ms` are not clock authority. Every step, idle poll and
reply checks time; polling is at most 50 ms and shortened to the active deadline.
Completing a reply after its deadline still records the actual write and marks
the callback timed out. It never reports successful local completion or replays
the bytes. Reply-written clears this local slot only; it does not prove native
consumer settlement, producer success or context placement in a model request.

A later callback already in the held read batch keeps that batch's original
time. It can expire while an earlier producer runs. A fragmented unclassified
line has no callback identity until parsing completes: this component rejects
an already-expired callback once identified, but does not proactively enforce a
five-second shutdown while its type is still unknown. The separately owned
120-second native/140-second outer deadline remains necessary. Native concurrent
callback behavior and stdout pressure while awaiting replies remain unproved;
the one-active restriction is not compatibility acceptance.

## Exit and failure

Pidfd readiness causes an actual owned wait check before subsequent effects.
A known wait with pending input or an active callback refuses. A new callback
parsed from the unread tail after a known wait also refuses; ordinary final
documents can still be drained. Wait, stdout EOF, stderr EOF and input close
remain distinct facts. Normal `step()` never closes stdin, even when the write
queue is empty or a result document appears.

`close_input_after_wait()` is a cleanup operation after the actual child wait.
It is deliberately not a native result/end policy. A CLI that waits for stdin
EOF after its result needs the separate terminal transition supplied by
[`SDKSessionDriver`](sdk-session-driver.md); this pump alone cannot finish that
session. Ordinary surrogate checks cover the local transition, while actual
native compatibility remains unproved. The outer deadline must retain an
unsupported or unresolved outcome. Producer failure, native signal/wait and
source cleanup also remain the external owner's responsibility.

An operation error poisons the pump and controller, closes its suspended
generator once and preserves the original exception. `cancel()` drops that read
loan without signalling, reaping, closing native stdin or asserting successful
release. Close uncertainty remains sticky. The native owner retains its own
actual waits and independently attempted cleanup. There is no success-retention
or task-acceptance API here.

## Local checks

Pure queue-readiness tests run with the normal Python test suite. The optional
ordinary-process suite is explicit:

```sh
python3 -B scripts/check_trial_sdk_stream_driver.py --output /absolute/fresh/directory
```

It snapshots seventeen explicit guarded public sources into exclusive read-only
files and uses the existing fixed subreaper/group supervisor, ten seconds of
work, twelve seconds total and 64 KiB combined output. The host body is explicitly
aliased to that supervisor's fixed leader filename; this does not replay the old
owner suite. Source/failure retention reuses the direct-writer checker's guarded
finalizer, with the exact source root and names supplied explicitly. Default
direct-writer behavior is unchanged. No selectable command, actual SDK/native
CLI, provider, account, namespace, corpus or oracle is opened.

The normal surrogate passes fragmented strict Unicode, four synthetic hooks,
full-input inspection, exact SDK/native write totals, separate stderr hash,
ordinary document handoff, actual child wait and known release. Thirteen additional
cases cover post-read clock failure with exact counts/hashes; callback after
actual wait; conservative read-interval overrun; idle and enqueue timeout; late
final write; shortened idle poll; writable fairness under ordinary stdout flood;
buffered-tail deadline preservation; separate-slab stderr isolation; ordinary
output drained after an actual wait; partial-line EOF refusal; and EOF while
the child remains alive with stdin open. Time
and post-syscall faults are controlled system-boundary injections, not observed
installed failures or native timing measurements. Diagnostic children retain
their actual waits and detached handles; unobserved EOFs remain explicitly
incomplete release. The checker being green means those expected diagnostic
conditions held, not that every diagnostic child succeeded or released cleanly.

CAIRN-107 remains active/HOLD. The distinct SDK evidence/retention profile,
actual capture clock/T/D observations, native terminal-policy compatibility,
independent producers, native interface/origin/witness proof, authentic capacity
and funding, frozen task/oracle, blind grading and both full weekly campaigns
remain required. CAIRN-149/150 dispositions are unaffected.
