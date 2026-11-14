# Agy checked publisher concurrency evidence

The original verification cut added process tests for the checked Agy publisher on base
`31a554ae4f503ac1cada270d8bea32680bf034c4` (tree
`d1bba6c748ac0d79851eee5f305b1da233f52058`). That cut did not change the publisher.
This document retains its historical results and describes the test adaptation
for the separately reviewed recovery publisher below. Historical results do not
qualify the composed source.

The earlier component review accepted the paired-layer checked path and identified
that concurrent checked publishers had source reasoning without a process test.
The test cut addresses that gap with separate owned processes, explicit account
and project roots, pinned prepared settings, and private observer handshakes.
It uses the actual checked publisher rather than a substitute writer.

## Observation boundaries

The original private observer exposes before and after `settings-sync`,
`publication`, `readback`, and `directory-sync`. The recovery composition also
exposes `recovery-sync`; the controller forwards that actual boundary without
inventing an observer inside publication. Publication performs its paired
recheck and atomic rename inside one action; no observer separates that final
recheck from rename. The final other-layer recheck follows the directory-sync
observer. Schedules must state these boundaries accurately. Reaching a barrier
establishes that boundary, not an observation inside an unexposed action.

Each schedule retains the initial settings, prepared inputs, process image pin,
actual process IDs, released barriers, intermediate settings, stdout, stderr,
exits, and terminal settings after both processes join. Configuration parsing and
unrelated literal bytes and modes are checked at controlled observations. Actual
transient double-active, both-error, foreign-inode and retained recovery outcomes
are recorded when encountered, including failures.

## Finite schedules

The dedicated [Go test](../../internal/lifecycle/install/agy_layer_concurrency_linux_test.go)
selects eight schedules. Both processes first reach the existing
`before:publication` observer while retaining the same original settings facts.
The fixtures create both parent chains before either publisher starts.

| Schedule | Controlled release |
| --- | --- |
| Opposite first wins | Complete the first publisher before releasing the second. |
| Opposite second wins | Complete the second publisher before releasing the first. |
| Opposite contended publication | Release both publication barriers, then collect their actual outcomes. |
| Opposite after readback | Hold the first after readback while the second attempts publication. |
| Opposite after directory sync | Hold the first after directory sync while the second attempts publication. |
| Same layer first wins | Complete the first publisher before releasing the second on that layer. |
| Same layer second wins | Complete the second publisher before releasing the first on that layer. |
| Same layer contended publication | Release both publication barriers on the same layer. |

The controller's barrier release policy is fixed. It does not control OS
interleaving inside an unexposed action. In contended cases, receipts must retain
the actual order and outcome rather than infer which process crossed an internal
check first. Ordered winner cases need successful publication controls; terminal
exclusivity alone would also accept an implementation that always refused.

## Qualification limits

Finite competing checked-publisher schedules are component evidence. They do not
prove exclusion of legacy or uncooperating writers, absence of every transient
window, crash recovery, installed or native Agy behavior, or a complete cutover.
Hard-kill recovery belongs to a separate implementation and test cut. This cut
adds no runtime lock, authority mechanism, production hook or configuration change.
Existing release install requirements are unchanged.

## Existing installer compatibility receipt

The prior component's 26 installer methods cannot be carried merely from the
older component head: their builds include coordination code changed on this
base. Release instead ran all 26 methods on the exact base and tree above. The
recorded command was `mise run test:unit -- test_r06_agy_layers test_r04_hook_settings -v`;
it exited zero with no failures or skips. The wrapper elapsed time was 27.076
seconds and unittest reported 26.779 seconds. The raw log SHA-256 is
`f290f7f812e1170f80ea601b11891f52cedac7cacbe2e42397a8eb1e54fed4f6`.

This receipt is from the base, not a new run in this test cut. It carries only
while the relevant production, Python and build inputs remain unchanged. The
final diff must verify that condition. It is not a production-image or installed
qualification claim. Release's landing/custody record is
`271af74e-f0b8-4847-8c7d-4ab268ebf2a4` version 1.

## Collected results

The author local race run exercised all eight schedules successfully on the
committed test bytes (`ba27a1ff9d8a097a99f547771f7478ddabb8e2b6`, test blob
`3f42655f9d3fb2f2360e9adc9a73269cdb9c72b3`). It ran one asserting suite with
eight schedule subtests and 16 real helper processes, with zero failures or
skips. The separate inactive helper entrypoint is not additional coverage.

The command was `mise run test:unit -- -race ./internal/lifecycle/install -run '^TestAgyLayerConcurrency' -count=1 -v`.
It exited zero in 11.894 seconds. Its log SHA-256 is
`63a502473e5e8b1209eb1760f405d96e85b5458290473deaec6f7112846cdd88`.
The actual helper image was copied and pinned before use as
`fed33f1936cd6c3126d26165cb1a03767ef661b3683c53101f5f9054e9cd5ef6`
(6,005,028 bytes, Go 1.25.0, race instrumentation enabled).

Each observed case ended with one successful publisher and one checked refusal.
All 16 children joined without forced stops. No terminal or sampled transient
double-active state, both-error result, foreign-inode outcome, terminal recovery
residue, torn JSON, or unrelated literal-byte or mode damage was observed. These
finite observations do not exclude other outcomes in unobserved interleavings.

The first admitted development attempt failed before publication competition:
an unsafe shared evidence ancestor was correctly refused. The fixture was moved
to a new private user-cache runtime root without changing shared permissions or
product code. Both failed and passing source, image and raw receipt histories
are retained. This is not a product counterexample or a waived assertion.

The local result and packet are Cairn record
`51955e2b-7d66-4a62-9b89-07087e5cc9d2` version 1. Final candidate gates and
independent review are recorded separately; this local run establishes neither
final acceptance nor the broader qualification claims above.

## Composition with durable recovery records

The later final run of `d44c5dfc` and its independent review both observed a
transient both-active state in the opposite-layer contended schedule, followed
by both publishers returning errors and rolling back. Those results are recorded
in `84364f01-c7a5-4908-9b99-b1518275213e` version 1 and
`d7066677-a8b4-4d76-a32f-8d951cd9a53a` version 1. They differ from the earlier
local run above; no absence of a transient window is inferred.

The recovery publisher at `0c67d838b89000a7f6604899e29ddc4de22d8450` retains
`.cairn-recovery-agy-<token>.json` correspondence records with mode `0400`, in
addition to `.cairn-agy-<token>` staged or backup settings with mode `0640` in
these fixtures. The original concurrency collector captured only the latter
prefix. The composed test captures both families and keeps their actual bytes,
mode, owner, link count and inode in every snapshot.

Journal checks distinguish canonical records from settings: exact schema,
record name/token, selected and other roots, layer and relative paths, plus the
observed old and new inode/digest correspondence. Inodes may move between
selection and temporary names during an exchange; the test matches their actual
observed bytes instead of assuming a name still selects an inode. A successful
publisher must return a recovery pin that selects the captured record. Settings
retain the original literal-byte, mode, dispatch and ordered winner/refusal
assertions. All eight schedules and ownership/join checks remain.

Additional tests publish through the real checked path on both layers, then
mutate copies of the captured observations. Wrong record mode, owner, links,
schema, layer, root, temporary name, new inode or bytes, noncanonical encoding,
missing old bytes and missing successful recovery pins must refuse. These
negative cases do not edit retained files or change the publisher.

Retained records are evidence under the explicit recovery contract, not proof
of cleanup or a quiescence barrier. Strict install-3/install-5, legacy writer
exclusion, immediate old-or-disabled state, power loss, native/installed unit
and full R06 remain open. Exact composed gates and independent review are still
required; this section makes no new completed-run claim.

## Commit and durable-intent ordering

The checked publisher now uses its existing recovery record to coordinate
opposite layers. Preflight may refuse already visible conflicts without a
write. Before any rename, the publishing path durably writes its own selected
record and staged bytes, then checks the other layer for a Cairn group or an
in-progress record. Old selection plus staged new inode means in progress;
new selection plus recorded old backup, or prior absence, means committed.
Unknown names, metadata, roots or file correspondence refuse. A precommit
refusal withdraws only unchanged owned members and syncs that directory.

The selected settings rename is the commit. Postcommit errors and SIGKILL retain
the complete new selection and its exact old backup/record. The next explicit
checked recovery validates the new inode/bytes, syncs the parent and rechecks
without renaming or deleting correspondence. It does not reactivate old settings.
The existing schema and literal commands are unchanged; no lock, runtime disable,
semantic command classifier or new authority is introduced.

All eight schedules above remain. Ordered cases first hold both children before
`recovery-sync`, preserving actual successful-winner/refusal controls. Contended
cases then hold both at `before:publication` with both records durable before
releasing them. The original snapshots, record/inode checks, literal bytes,
exit and child-join assertions remain, and any sampled both-active state is now
a failure. Both publishers refusing is safe in contended cases. The collector's
definition of active is unchanged.

Owned process tests cover both sides of all five publication actions on both
layers with both initial old settings and initial absence: 40 owned SIGKILL
cases. Precommit cases require old settings/absence; postcommit cases require
the exact committed NEW inode and forward recovery with retained correspondence.
Malformed/foreign/pin/backup negatives run on both layers. These required cases
cannot skip. Exact final-candidate image/compiler/input pins, numeric process
receipts and results belong to its sealed packet, not the historical results
above. The previous failures and 20 immediate NEW-active observations remain
retained with their original interpretation and source identities.

Cooperating-publisher schedules do not close uncooperating legacy Python writers,
power loss or semantic attribution of opaque/renamed commands. R-07 excludes
legacy installer activity and applies the existing final D6 duplicate/unknown
user refusal. Native Agy positive/refresh, whole-unit qualification and production
admission are separate. This is the bounded implementation of coordinator
`bfaf5bca-4472-483f-8073-7492b0ec0888` version 1, not a criteria rewrite or an
independent acceptance claim.
