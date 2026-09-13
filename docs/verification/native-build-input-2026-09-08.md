# Native build input and prompt correspondence

The Striatum integration branch now resolves a packet's explicitly selected
Cairn observation into the build's sealed input and renders its complete captured
context. Admission checks that context against the retained launched prompt.
This is implementation evidence for the proposed native path. The accepted
catalog remains `observation@1` and `build@3`; native execution is not enabled.

[Source checkpoint](https://github.com/halbritt/striatum-next/commit/5d9e0b9aabff49067b8f4f681b17a7517a299dc8).

## Behavior

A packet may name one complete
`cairn-context/<capture-sha256>/exogenous-change-record` identity. A packet with
no such input keeps its ordinary behavior. Once named, the memory must resolve:
missing evidence withholds that build with a reason. File paths and ordinary
packet references retain their existing meaning.

The resolver requires an admitted ECR version from an intact exact
`observation@2` implementation 1 run. It checks the producing capture pin,
reproduces the ECR from that source, and checks the canonical repository,
`<work-graph-identity>#<packet-id>` consumer identity and `build` task class.
The complete ECR body and exact version/hash enter ordinary input pinning and
materialization. The execution labels in the capture still need to be matched
to actual placement and host facts before launch.

The renderer includes the captured readable context once. It preserves all
selected memory and class/mandatory labels, while avoiding the redundant JSON
envelope containing the encoded package and escaped rendering. The visible
label and source map identify the full ECR. Rendering does not replace its
artifact hash with the context's digest.

Native context stays inline. Argument-based delivery may spill other context
through the existing path mechanism, but refuses when the complete native
context cannot fit. Stdin delivery retains it. Admission checks the exact
labelled block in the retained prompt and requires one corresponding inline
source-map entry. An intact source hash cannot excuse missing or altered text.
Historical build rendering keeps its existing interpretation.

## Verification scope

Focused race checks cover the parser, complete renderer, source mapping,
argument spill/refusal, Driver preparation and admission against the actual
renderer. Negative cases include wrong producer contracts, source pins,
repositories and packets; unavailable evidence; duplicate packages; and omitted,
altered, duplicated or path-only prompt context.

The Driver preparation fixture constructs folded artifact facts. It proves
resolution, sealed pins, materialization and step withholding; it does not itself
prove producer admission. The [producer and request checks](native-observation-2026-09-08.md)
cover that earlier boundary. The admission fixture calls the real renderer and
then changes its returned prompt or source map. These complementary checks are
not a real-service-to-provider build trial.

Validation used Go 1.23.4. The full `make check` passed before the final store
prompt-admission check was added. After that addition, formatting, build and
accepted Decision source-pin lint passed again, along with the full store suite
and focused race checks for the affected paths. The [metadata](native-build-input-2026-09-08.json)
records that sequence and exact log hashes. No CI result or production deployment
is claimed.

The implementation review used Pincite packet `pkt-2d94afb3acda9f4b`; repository
precedence, ownership, preservation and contextual errors informed the change.
Nine residual generic obligations remain outside the bounded verification claim;
no broader design or deployment recommendation is inferred from the checks.

## Later host checkpoint

The [native host implementation](native-host-2026-09-08.md) now connects the
existing supervisor to binding, claim, delivery and outcome operations and
checks retained host correspondence. Its service fixture is separate from a
real build trial. The following list records what remained at this earlier
source checkpoint.

## Remaining work at this checkpoint

The existing supervisor must consume the retained observer-owned receipt, check
actual binding/destination/workspace correspondence, claim current eligibility,
observe the host launch, and record delivery and terminal outcome. Each relaunch
needs its own supported authorization or an explicit refusal. The owning
contracts and generated decisions still require acceptance before enablement.
A real build with prospective task checks must then establish whether the memory
helps. No new model-task benefit is claimed by this checkpoint.
