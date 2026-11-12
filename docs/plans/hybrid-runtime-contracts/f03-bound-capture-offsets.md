# Bound Codex transcript offsets

The two methods in [test_f03_bound_capture_offsets.py](../../../scripts/test_f03_bound_capture_offsets.py)
carry the original transcript-offset oracles through the receipt-selected bound
memory caller. They use the installed checked Source launcher and Go
`IMAGE memory` entry at H150 with the same selected bound profiles and event
bytes. The original methods also have separate checked unbound coverage.
This leaf covers the existing Codex PreCompact and Stop cells.

## Original corpus

The test methods are unchanged function aliases from
[test_f03_capture_boundary.py](../../../scripts/test_f03_capture_boundary.py):

- `test_short_stop_and_repeated_append_truncate_keep_exact_offsets`, lines
  172–189: six complete pair sites and ten explicit assertion expressions;
- `test_append_after_snapshot_remains_for_later_stop`, lines 291–304: two
  complete pair sites and five explicit assertion expressions.

The original `configure`, `rollout`, `courtesy_messages`, `outcome`, `scheduled`
and `state` helpers are aliases too. The rollout writer retains the default
`json.dumps` spaces and one LF per record. Alternating user `thanks` and
assistant `no problem` messages keep their roles, order, `response_item` message
shape and input/output text-part types. Two, four, six and eight rows from this
writer occupy 268, 536, 804 and 1,072 bytes. The same actual transcript path is
reused throughout each method.

The six-pair sequence starts with a two-row Stop that has too little dialogue
and no capture marker. PreCompact commits the two-row offset. Four-row Stop
has only two new messages and preserves that offset. Eight-row Stop reaches
the six-new-message threshold and advances to its exact snapshot length.
After truncation to two rows, Stop remains too short; PreCompact recovers the
shorter exact offset. Repeated text is counted by absolute message positions.

The two-pair snapshot sequence saves the original six-row bytes, then schedules
a rewrite with eight rows around the final shared-lock open. The committed
marker must remain at the original 804-byte snapshot while the file grows to
1,072 bytes. Before Go runs, the original callback restores those exact six-row
bytes, and the same schedule runs independently. A later Stop sees only two
new messages and preserves the original offset. The rewrite preserves the
six-row prefix; this is not an `O_APPEND` system-call test.

## Bound fixture and comparison

The class inherits the unchanged `BoundCaptureTests` and `BoundMemoryCase`
fixture. It uses real R02 installation, the complete retained Source closure,
independently selected receipt and profiles, actual checked launchers and an
authenticated synthetic Unix API. The selected profiles retain their bound
`/3` configuration. Source activation exclusion may remove the binding from
an internal private config copy; it does not rewrite that selected generation.

The Go launcher retains `memory`, receipt and digest, role, entrypoint, config,
H150, persistent content-addressed launch root and shell selection. Source uses
the installed memory/H150 launcher. Each consumes the original event bytes at
the fixture cwd. The separate `memory-capture` route is outside this leaf.
Codex Stop's asynchronous native delivery is a separate installed behavior.

The leaf's only local launch adapter runs on Go `before_run`. After the parent
resets state, it invokes the original `before_candidate` callback and maps the
original `candidate_prefix` to the bound runner's `go_prefix`. It copies the
empty tuple too, clearing the schedule before the later Stop. The parent then
arms the original inotify scheduler. Source prefixes, the 200 ms flock delay,
scheduler join and error propagation remain unchanged.

The parent pair checker compares full exit codes, stdout, stderr, persisted
state keys and values, ordered API operations and complete normalized bodies.
It validates independent UUIDs and measured timing before its existing
normalization, and checks authentication, reported origins and post-lock report
locks. The original `state` alias removes only validated `last_capture.at`;
path, marker, offset and capture content remain compared. The existing
`scalar_tree` is unchanged. Bound Source capture reports its origin without an
implementation ID; Go reports the selected binary digest. Each expected origin
is asserted separately rather than erased.

The bound pair resets its complete fixture memory-state directory between
frontends. Both start with the same original state bytes; the transcript lies
outside that directory and is restored by the original callback. No
cross-frontend lock-inode identity is claimed. `load_tests` selects exactly the
two leaf methods, leaving inherited capture/provider/deadline tests out of this
run. No source, production implementation, old helper, oracle, normalizer,
process-control mechanism or observer is changed by the leaf.

## Snapshot and qualification limits

The original scheduler fires on the second `IN_OPEN` for the shared session
lock, from any process. The API fixture can also open that lock while checking
post-lock reports. Source's initial and final shared-lock opens back the
intended append window in its code. For Go, exact append-before-commit timing
is inferred from the inherited trigger and paired outcomes, rather than
separately observed. Offset equality alone cannot distinguish an append just
before commit from one just after commit. Temporary strace logs are not
automatically retained. The corpus adds no window instrumentation.

The base bound fixture has no selector stub and retains the normal caller
environment within the approved execution fixture. The exact short/courtesy
inputs return before selection by Source structure and the corresponding Go
comparisons. This is not a provider-isolation guarantee or a newly measured
zero-call count. Keep these inputs unchanged; this setup must not be reused for
substantive dialogue or real provider/model execution.

Both methods are straight-line and contain six plus two `pair()` sites. A
successful two-method run with no skips supports **eight complete pairs and
sixteen hook frontends as inferred counts**. The leaf adds no separately logged
pair counter or raw process packets. Those counts exclude build, installer,
wrapper, strace and SDK subprocesses. A skip leaves qualification incomplete.

The frozen leaf retains its historical preparation-only module docstring.
Actual execution evidence comes from the test outcome, exact tested head and
receipt. A guide-only child commit can reuse a focused result only after proving
that the leaf and all original source/helper/oracle/code bytes and modes are
unchanged; keep the tested and final heads distinct.

## Run the leaf

From a clean committed candidate, use the existing shared executor and approved
private fixture with a fresh absolute receipt path:

```sh
mise run host:exec -- --timeout 15m --weight 1 -- make test-isolated \
  RECEIPT=/absolute/evidence/bound-offset-focused-isolation.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -I -B -m unittest discover -s scripts -p test_f03_bound_capture_offsets.py -v'
```

Require both named methods, zero failures/errors/skips, actual outer and inner
exit zero, a clean exact-head receipt and a passing fixture selftest. Retain the
actual command, terminal result and independent unittest/outer durations.
Project check, docs tiers 1 and 3, and manual source/command/scope review qualify
the final guide candidate separately.

The Source snapshot and offset behavior is in
[memory.py](../../../integrations/lifecycle/memory.py):936–987, 3048–3087,
3144–3157 and 3223–3305. The corresponding Go code is in
[capture_dialogue.go](../../../internal/lifecycle/memoryhook/capture_dialogue.go)
and [capture_body.go](../../../internal/lifecycle/memoryhook/capture_body.go),
reached through the checked bound memory host. These implementations remain
unchanged by this corpus.

A focused pass covers these constructed producer-shaped inputs and caller
comparisons. Other harnesses, substantive selection, retry/CAS/shutdown
expansion, native asynchronous delivery, installed qualification, full-gate
acceptance and measured usefulness remain separate claims.
