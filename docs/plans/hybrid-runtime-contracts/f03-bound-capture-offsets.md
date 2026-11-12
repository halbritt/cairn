# Bound Codex transcript offsets

This proposed corpus carries exactly two existing `CaptureBoundaryTests`
function objects into the receipt-selected bound memory fixture:

- `test_short_stop_and_repeated_append_truncate_keep_exact_offsets`, from
  `scripts/test_f03_capture_boundary.py:172-189` at `62d4f863`;
- `test_append_after_snapshot_remains_for_later_stop`, from the same file,
  lines 291-304 at that pin.

Those methods already have checked **unbound** coverage. The proposed new leaf
is `scripts/test_f03_bound_capture_offsets.py`. It selects the actual bound
Source launcher and Go `IMAGE memory` caller at H150, preserving the binding
in both selected profiles. It does not substitute `memory-capture`, an unbound
configuration, a direct ordinary body, or a replacement Source implementation.
No other harness or new supported caller cell is added.

## Original inputs and byte offsets

Both original method bodies remain aliases, including all their assertions.
Their `configure`, `rollout`, `courtesy_messages`, `outcome`, `scheduled` and
`state` helpers are the original function objects too. In particular, the rollout
writer retains `json.dumps`'s original default spaces and one LF per record;
the compact JSONL writer in `BoundCaptureTests` is not substituted. Alternating
user `thanks` and assistant `no problem` messages retain their original order,
roles, `response_item`/message shape and input/output text-part types. The same
actual transcript path is reused through each sequence.

The first method has six pairs. Two-row Stop is too short and creates no
`codex_capture_marker`. Two-row PreCompact commits its exact end offset.
Four-row Stop has only two new messages and preserves that offset. Eight-row
Stop reaches the six-new-message threshold and advances to that snapshot's
exact byte length. After truncation to two rows, Stop remains too short;
PreCompact recovers the shorter exact offset. Equal repeated text is counted
by absolute transcript positions rather than text identity.

The second method has two pairs. Its six-row original byte snapshot is saved
before invoking either frontend. At the final state-lock open, the unchanged
inotify/strace scheduler rewrites the same transcript with eight rows. The
committed offset must still equal the original six-row byte length and remain
less than the actual file size. Before Go, the original `before_candidate`
callback restores the exact saved six-row bytes; the same scheduled append is
then applied independently. A later Stop sees only two appended messages and
keeps the original snapshot offset. The test does not advance to the later
file size merely because those bytes exist at commit time.

These are existing synthetic producer-shaped Codex inputs: fixed UUID-shaped
session, actual fixture cwd, Stop/PreCompact and a transcript path. They are
not recorded native input or evidence that every transcript shape is supported.

## Bound fixture adaptation requiring admission

The new class inherits `BoundCaptureTests`, whose unchanged `BoundMemoryCase`
uses the real R02 installed fixture, complete selected Source closure and
profiles, checked Source launcher, and Go `memory --receipt ...` vector with
H150. Both configurations retain the bound `/3` selection. The original
`configure` republishes the selected Codex generation. The parent capture
setup enables its existing synthetic authenticated capture-observation API;
it does not configure or execute a selector. These courtesy/short cases return
before checkpoint lookup or provider selection. `BoundProviderCaptureTests`
was inspected but is not needed for these unchanged inputs.

There is one explicit new launch adapter. On the Go `before_run`, invoke the
original `before_candidate` callback, if present, after the parent's state
reset and before the inherited scheduler starts. Map the exact original
`candidate_prefix` to the bound runner's existing `go_prefix` on every Go
invocation, including the cleared tuple before the later Stop. The original
Source prefix and both original strace vectors keep their 200 ms flock delay.
The unchanged bound `before_run`/`after_run` call the original inotify start
and finish functions. No helper or scheduler implementation is copied or
modified, and no additional process-control or observation mechanism is added.

The parent pair checker remains `BoundMemoryCase.pair`, not a modified copy of
the unbound pair method. It compares complete exit/stdout/stderr, persisted
state and ordered API bodies; validates independent UUIDs and measured timing
before its existing normalization; and checks synthetic authentication,
reported implementation origin and post-lock report observations. Bound Source
capture retains its expected reported origin without an implementation ID;
Go retains the actual selected binary SHA. No origin field is erased. The
original `scalar_tree` and `CaptureBoundaryTests.state` timing rules remain
unchanged. The latter removes only the original validated `last_capture.at`;
the bound fixture's broader state timing normalizer is not substituted.

The existing bound pair resets its full fixture memory-state directory between
frontends, whereas the old unbound pair preserves lock files during reset.
Both start from the same original state bytes. The transcript is outside that
directory and is not reset by the parent; the original callback handles its
six-row snapshot. No cross-frontend lock-inode identity claim is made. No
marker/path/offset/request-body or refusal normalization is introduced.

`load_tests` selects only the two named new leaf entries. The inherited old
capture/provider/deadline cases are not rerun by this leaf. No new pair/raw
packet counter is added; eight pairs and sixteen frontend invocations are
static expected counts from the original method bodies, not observations.

## Source and caller mapping

At the pinned Source, `memory.py:936-987` reads one bounded transcript snapshot
and its absolute message-end offsets. `capture` at 3048-3087 takes the snapshot
marker before courtesy or duplicate return. `codex_new_messages` at 3144-3157
uses exact resolved path and byte positions, treating a shorter file as new.
`capture_event` at 3223-3305 preserves the initial/final shared-state lock split,
Stop threshold, capture-owned-field CAS and successful-replace marker update.
The capture report follows lock release. Source capture exclusion from inbox
activation does not remove the selected binding or turn this into unbound
installation evidence.

The Go path is `cmd/cairn-lifecycle/main.go`'s checked `memory` branch,
`memoryhost.RunChecked`, `binding.VerifyMemoryEntry` and its resolved bound
invocation, `memoryhost.runCheckedBound`, `memoryhook.RunBound`, then the
existing `runMemory`/`runCapture` body. `capture_dialogue.go` keeps absolute
offsets for its bounded read; `capture_body.go` owns the corresponding short
Stop, snapshot marker, final capture CAS and save. The new leaf changes none
of those implementations or their helper/oracle files.

## Proposed execution and limits

Preparation is static only: no project import, application, build, hook, test,
provider or model has run for this piece. SAME1279 frozen criteria review and
then separate exact Foundation execution admission are required before using
the proposed command. There is no fabricated RED for this evidence-only gap.
After admission, from an appropriate clean committed candidate, use the
existing host executor and approved fixture with a fresh absolute receipt:

```sh
mise run host:exec -- --timeout 15m --weight 1 -- make test-isolated \
  RECEIPT=/absolute/evidence/bound-offset-focused-isolation.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -I -B -m unittest discover -s scripts -p test_f03_bound_capture_offsets.py -v'
```

Expected membership is two leaf methods, eight complete pairs and sixteen
frontends, all H150. Qualification must inspect actual unittest membership,
outcomes, outer/inner exits, receipt head/clean status and fixture selftest;
a skip is missing evidence. No pass, measured duration or observed count is
claimed by this proposal. Existing bound/unbound baselines and crash artifacts
remain evidence at their actual tested heads, with their dirty/failed receipts
and limits retained. None establishes the new offset corpus's result.

The newly landed crash leaf/guide are existing unowned files and remain pinned
unchanged. Other harnesses, new callers, substantive selection, retry/CAS or
shutdown expansion, observer refinement and runtime construction stay outside
this piece. All campaign holds, the CAIRN-318/319 owner closure bar, 53 frozen
historical diagnostics and the interrupted full run remain open. Independent
review, later qualification/integration, installed/native behavior, full-gate
acceptance and measured usefulness remain separate claims.
