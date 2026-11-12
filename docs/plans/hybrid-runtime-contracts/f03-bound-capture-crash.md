# Bound capture crash corpus

The four tests in `scripts/test_f03_bound_capture_crash.py` cover Codex
PreCompact/Stop and Claude PreCompact/SessionEnd through the existing
receipt-selected bound memory caller. The separate `memory-capture` unbound
consumer is excluded from this corpus.

The new test inherits the unchanged BoundProviderCaptureTests/BoundMemoryCase
installed fixture, selected receipt/profiles, checked Source launcher, Go `memory`
launcher, local synthetic selector and Unix API. It leaves all original source,
helpers, tests, normalizers and Go code unchanged. `load_tests` selects exactly
four new leaf methods so inherited provider/deadline tests are not rerun.

Each cell has two independent seeds: no state; or an exact JSON state carrying
retained binding, recall/unknown Unicode/large-integer fields, prior capture
status/digest, and Codex-only offset-zero markers. Six substantive alternating
native transcript records reach selection even for Codex Stop. The event carries
hook_event_name, fixed synthetic session_id, actual fixture cwd and transcript
path. Producer JSONL shapes follow the existing producer-cell corpus; they are
not the raw checked-wrapper command vector. Both profiles are republished for
the selected harness by the unchanged fixture. Source and Go consume identical
original event bytes and the same selected receipt.

For each seed, Source and Go are reset independently. The unchanged local
selector is delayed after it has recorded its actual input. The test requires
one real selector call through each actual checked frontend, exact H150/vector,
content-addressed mode0500 launcher and selected receipt. It verifies the actual
kernel hook argv, selector executable argv, cwd/device/inode/owner0700, empty
scratch directory, CHILD=1 and removal of inherited CLAUDECODE. No real provider,
model, production database or native session is involved.

The test pins the own Popen child and selector with pidfds; ancestry, UID, boot
ID and start time establish ownership. A numeric PID from a log is only a hint.
PID values <=1 and non-integer targets fail before process control. SIGSTOP
stabilizes the owned parent/descendant tree before the intentional hook SIGKILL.
Only pidfd_send_signal is used. No process group or numeric-PID kill is used.
The test becomes a temporary subreaper, freezes/pins any observed owned child
subtree while its parents cannot fork/reap, and kills/reaps retained descendants
parent-first after hook death. The pinned selector program forks no children;
that fact is asserted. This does not claim exercised selector-grandchild or
production descendant cleanup. An unproved orphan is reported as failure and is
never signaled. The existing private process namespace remains the outer fixture
boundary. Selector workdir cleanup uses only the kernel-observed empty owned
inode and `rmdir`, never a log-selected recursive path. All waits and output
collection are bounded; cleanup errors retain the primary diagnostic.

Popen-to-pidfd custody also has an explicit failure path. If constructing the
owned process reference fails, no event bytes have been sent and no signal is
permitted. The test closes unwritten stdin, sets it to None for bounded
`communicate(timeout=3)`, closes both output pipes and re-raises the primary
exception together with cleanup failures. A still-running unowned hook adds an
explicit failure; the outer fixture owns namespace cleanup. Failures before
kernel cwd proof can leave scratch artifacts: those are reported as remaining,
never as cleaned.

At the actual selector boundary, the capture lock is held and the shared state
lock is already free. The test requires exactly those observations, exact
pre-kill nonlock state bytes/modes, then hook return -SIGKILL with empty stdout
and stderr. After hook death it opens the already existing real lock files
without creating substitutes, verifies unchanged device/inode/mode, and acquires
both nonblocking. Every nonlock state byte/mode remains equal to the seed;
there is no create, revise, capture observation or write. Raw per-side input,
receipt, vector, identity, provider/API and lock observations are retained in
BOUND_CAPTURE_CRASH_RAW records.

Crash Source/Go outer outcomes, exact byte state, ordered API bodies and provider
input compare equal. Only independently validated nonzero request UUIDs in
lookup calls are removed. The synthetic bearer and per-side diagnostic origin
are asserted, not erased: bound Source capture is reported without implementation
ID; Go reports the actual selected binary SHA. No crash timing/state/marker/ID
normalization is added. Host PIDs, cwd and receipt are verified/retained separately
rather than pretending their volatile values are equal between processes.

After each crash pair a fresh reset runs the unchanged ordinary Source/Go pair
with no selector delay and one selected decision. All existing stream/state/API/
selector checks remain; additionally one confirmed decision with the exact
project-prefixed body, saved record identity, six-message digest and complete
Codex marker are required. Unknown/recall seed fields survive. This is the
ordinary commit control on reset state, not a transient automatic retry.
It does not demonstrate retry on the exact crashed directory. Append after
snapshot and repeated truncate offset cases remain a separate next piece.

Run only this leaf through the existing host task and isolated fixture. Choose
an existing absolute output directory outside the checkout for the receipt:

```sh
mise run host:exec -- --timeout 15m --weight 1 -- make test-isolated \
  RECEIPT=/absolute/output/focused-isolation.json ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -I -B -m unittest discover -s scripts -p test_f03_bound_capture_crash.py -v'
```

Qualification requires four leaf methods, eight seed subtests, zero failures,
errors or skips, sixteen `BOUND_CAPTURE_CRASH_RAW` records and
`BOUND_PROVIDER_PAIRS=8`. The eight unchanged ordinary helper pairs imply
sixteen control frontend runs; they do not emit separate raw crash packets.
Inherited fixture skips count as missing qualification, even if unittest exits
zero. There is no fabricated RED for this test-only evidence gap.
Producer-shaped success/fault injection is kept separate from malformed refusal,
interpreter/resource classes, prior full-gate failures and campaign/native/
installed acceptance. Integration and acceptance remain separate from a focused
corpus pass; existing authority and runtime holds remain.
