# Native observation producer and request frontend

The proposed native path now acquires Cairn context before issuing an observation
request, pins the retained capture, produces its ECR offline, and validates the
submitted body against that source. The source is on Striatum's integration
branch. A later [build-input checkpoint](native-build-input-2026-09-08.md) adds
resolution, rendering and prompt correspondence. Accepted native contracts,
launch correspondence and model-task benefit remain unfinished.

Source checkpoints:

- [Producer and admission](https://github.com/halbritt/striatum-next/commit/88b2c4f85f843e71a0017412309c82a884ce5509).
- [Trusted request frontend](https://github.com/halbritt/striatum-next/commit/6937b12361738e95547113b19918db6a48120927).
- [Command and boundaries](https://github.com/halbritt/striatum-next/blob/6937b12361738e95547113b19918db6a48120927/docs/how-to/request-cairn-observation.md).

The request frontend calls `Host.Acquire` itself. Ordinary request issuance
refuses the reserved `cairn-context/` prefix; native issuance accepts a watch and
trusted host configuration, rather than a supplied capture file or an
authentication flag. The watch repository must match the target graph. Native
issuance also requires an accepted `observation@2` declaration, which exists only
in the test catalogs for these checks. The operational catalog remains unchanged.

## Observed checks

Both source checkpoints passed Striatum's full `make check` under Go 1.23.4:
formatting, build, accepted Decision source-pin lint, and the default test suite.
Focused race tests cover complete capture preservation, wrong or missing source
refusals, altered ECR submissions, request validation, bounded CLI input, and
historical observation rendering.

The Driver test acquires through a configured synthetic client, issues the real
request, removes that client, then drives planning, materialization, execution
and admission to the `observed` target. It confirms exactly one admitted ECR
containing the original captured context. The synthetic seal is explicitly a
fixture; this test alone does not prove Cairn service authentication.

The separate service test starts its own PostgreSQL cluster and Cairn Unix API,
seeds an ordinary shareable fixture procedure, and invokes the public native
request CLI with a provisioned observer. It checks the retained scoped memory
and refusal of an ordinary request claiming the same native identity. After the
API is stopped, the local observation executor produces identical ECR bytes on
replay. Existing local/hosted capture and Cairn child-rendering comparisons also
pass in that test.

These are complementary tests, not a claim that one real-service test exercises
the entire Driver and provider execution chain. No model was called, no
operational memory fixture was inserted, and no native runtime was deployed.

## Remaining work

The [build-input checkpoint](native-build-input-2026-09-08.md) implements the
packet-named resolver, source-pinned rendering and prompt correspondence checks.
The existing supervisor must still perform the actual Cairn package,
binding, claim, delivery and outcome operations. Relaunch behavior and acceptance
of the owning contracts remain explicit requirements before native enablement.

[Verification metadata](native-observation-2026-09-08.json) retains source commits,
client identity, check-log hashes and the limits of these claims.
