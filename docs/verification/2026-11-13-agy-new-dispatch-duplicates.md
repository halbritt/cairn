# New selected-layer Agy dispatch duplicates, 2026-11-13

`PublishAgyLayer` now refuses a newly introduced managed event/command pair
when that same pair already appears in another hook group of the selected
layer. For example, an existing `audit-hooks.Stop` command `/pinned/cairn hook`
prevents adding that command to `cairn-coordination.Stop`. The other group is
preserved; detecting a duplicate grants no deletion authority.

This child starts at frozen producer commit
`6249f82cce29501c13846c1648db649462a83db0`, tree
`83965ceb9da5460a721aa54992f9084b26370002`. Its lease changes only
`agy_layer_json_linux.go`, a dedicated Go test file, and this verification
document. The frozen producer and recovery worktrees and evidence stay intact.

## Check consumed by the publisher

The existing merge path counts literal dispatch pairs in the old managed
group and the validated prepared group. Only a positive count difference is
an introduced dispatch. It compares those additions with command hooks in
other top-level groups of the current selected-layer settings.

The check uses the existing strict JSON/member parsing. A compared entry must
be an actual event-array object with the exact lowercase fields `type` equal
to `command` and a string `command`. Opaque values and nested command metadata
are outside this check. It does not recursively classify settings, search for
Cairn substrings, normalize whitespace, identify aliases, or change
`agyCairnCommand`.

The public publisher consumes the check in `agyMergeSettings`, before opening
the selected publication parent, staging bytes, writing recovery correspondence,
or renaming settings. The existing raw splices and exact `replace_commands`
preservation rules still control the merge.

For a command `C` already in another group's `Stop` event:

| Old managed dispatches | Prepared managed dispatches | Result |
| --- | --- | --- |
| None | `Stop: C` | Refuse the new duplicate |
| One `Stop: C` | One `Stop: C` | Preserve the carried dispatch |
| One `Stop: C` | Two `Stop: C` | Refuse the added copy |
| None | `PreInvocation: C` | Allow the different event |
| None | `Stop: D` | Allow the distinct command |

Pre-existing managed duplicates retain their multiplicity and original bytes.
Listing an outside command in `replace_commands` does not authorize removing
that outside hook or bypass this refusal. The existing positive
`printf "cairn docs"` case remains a preservation control.

## Local evidence and gates

The development invocation passed two top-level tests and 28 named subtests,
with zero failures or skips. Both account and project layers use actual
`PublishAgyLayer` calls. Refusal snapshots compare every fixture path, raw
file bytes, mode, device and inode, including pre-existing recovery records,
backups and an unreadable synthetic credential selector. No new publication
or record may appear on refusal.

Positive cases cover distinct commands, different events, literal whitespace,
carried unrelated hooks, carried duplicate multiplicity, opaque metadata,
case-distinct field names and repeat publication. Repeating an allowed plan
must preserve the resulting settings inode and complete record inventory.

The final frozen-delta gate selects these tests and the maintained Agy layer
controls, including the unchanged preservation assertions:

```sh
mise run test:unit -- -race -count=1 -p=1 -json ./internal/lifecycle/install -run '^TestAgy(Layer|NewDispatch)'
mise run check
mise run host:exec -- python3 -B scripts/verify_docs.py
```

The captured final command also uses the existing Go `-exec` image capture to
copy and hash the actual test image, record build information and timestamp
it before execution. Full argv, module/task/source pins, image metadata, raw
test output, fixture observations and final exits are retained in
`/home/halbritt/.cache/cairn301-agy-new-duplicates-1467-20261113`. Tests keep the
normal host HOME/tools and use mise admission and disposable application state.

## Limits

This guard covers new exact event/command duplication against other groups
in the selected layer. Opaque or pre-existing duplicates, duplicates wholly
inside the managed group without an outside match, semantic aliases and
legacy writers remain outside its claim. The existing other-layer checks and
all deletion and authority boundaries are unchanged.

There is no native client, model, API, PostgreSQL or additional SIGKILL series.
The both-active concurrency interval, 20 immediate NEW-active recovery
snapshots, strict install3/5, whole-unit/common-image, CAIRN-305 AC4 and HC05
remain open. The earlier producer's test results remain evidence for that
frozen producer, not qualification of this changed Go image. Release owns
exact review, composition, landing and cleanup; production grant remains NONE.
