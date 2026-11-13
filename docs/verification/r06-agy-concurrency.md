# Agy checked publisher concurrency evidence

This verification cut adds process tests for the checked Agy publisher on base
`31a554ae4f503ac1cada270d8bea32680bf034c4` (tree
`d1bba6c748ac0d79851eee5f305b1da233f52058`). The publisher implementation is
unchanged by this cut. Test results and finite schedules are recorded below when
collected; this document does not claim a completed run during implementation.

The earlier component review accepted the paired-layer checked path and identified
that concurrent checked publishers had source reasoning without a process test.
The test cut addresses that gap with separate owned processes, explicit account
and project roots, pinned prepared settings, and private observer handshakes.
It uses the actual checked publisher rather than a substitute writer.

## Observation boundaries

The existing private observer exposes before and after `settings-sync`,
`publication`, `readback`, and `directory-sync`. Publication performs its paired
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
