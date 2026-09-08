# Native Striatum context: implementation direction

Status: proposed Cairn-side integration direction, 2026-09-08. This document
does not amend or accept a Striatum contract. U1 remains partial. The opening
request is RQ-408328 on Striatum's compiler graph, subject
`striatum-next/passes/cairn-native-context-input-20260908`. The existing Driver
produced intent-capture run 408357 and accepted intent head 408364. A read-only
planner inspection found no unmet capture steps, and subsequent live status
reports the `captured` request satisfied. That is an opening bracket,
not native memory delivery or implementation acceptance.

The desired outcome is a real Striatum build that receives useful Cairn context
through a declared, sealed input, with its retrieval receipt linked to the actual
host execution and separately assessed task outcome. The
[admission assessment](verification/native-admission-assessment-2026-09-08.md)
remains the historical evidence baseline.

## Choose the input according to its lifetime

| Material | Existing home | Integration consequence |
| --- | --- | --- |
| Fixed pass instructions or a deliberately fixed doctrine excerpt | Content-addressed catalog prompt asset, D0004/D0007 | Implement declaration loading, exact materialization, rendering and admission together. The asset belongs to that pass implementation. |
| Cairn's response to a repository/task/run query | Declared run input with exact context and source pins, D0004.C9 | Preserve the selected query result as a task-specific input. A global pass preamble cannot establish its scope, freshness or destination. |
| Binding compiler architecture | Accepted generated Decision Record | Ordinary Cairn notes cannot substitute for this authority. |

The selected direction for dynamic Cairn memory is an explicitly resolved input
for the consuming packet/run. The existing Work Packet v2 `inputs` field names
artifact/data identities, but names alone do not establish a current exact pin or
authorize the build pass to consume a new kind. The owning implementation must
resolve the input through its accepted producer/consumer contracts and pin the
result before dispatch. It must not infer a memory input from an arbitrary file
in the workspace or repurpose the optional Review Ledger.

The proposed producer is the existing **observation** pass, extended with a
declared Cairn watch target. Its Exogenous Change Record records the authenticated
API observation and retains the exact returned context bytes and their digest.
The proposed consumer is **build**, with an explicitly declared optional
Exogenous Change Record input selected by the packet's input identity and
resolved to an exact admitted version. The ECR attests what Cairn returned; it
does not certify the truth of the enclosed notes. This uses the existing Evidence
role without adding a Product kind or a second transformation merely to copy
the bytes.

The watch target must declare its repository/task/run tuple, query, applicability
context, destination and available memory room. Credentials stay in the trusted
host configuration. The retained record must distinguish the requested tuple,
authenticated Cairn response, semantic seal and receipt, selected versions,
capture time and prior/observed content hashes. An arbitrary ECR or a caller's
unverified claim that some bytes came from Cairn is insufficient. These are
proposed contract requirements; the current observation executor does not
implement a Cairn watch target.

Prompt-asset support is useful shared infrastructure, but implementing static
assets alone will not close native Cairn U1. Do not spend another iteration
rendering a fixed test paragraph and report that as dynamic memory integration.

## Required flow

1. **Resolve the host context.** Establish the canonical repository, packet/task,
   run identity, revision/workspace declarations, destination and actual host
   binding. A future binding that changes destination or applicability must
   trigger a new compatible retrieval before launch. These are declared labels;
   physical workspace verification remains the host's responsibility.
2. **Compile through Cairn's authenticated interface.** Obtain a full bounded
   context package with receipt, semantic seal and exact selected versions.
   Preserve mandatory-context handling and normal refusal codes. Valid empty
   selection and a failed connection are different results. Neither a missing
   store nor refused mandatory context becomes a silent empty-memory success.
3. **Retain the exact permitted input through the producing contract.** The
   observation pass admits the externally obtained bytes under the proposed
   declared Cairn watch target. D0002's observation path owns world-authored
   change; simply writing a Graph Store object or attaching a claimed receipt
   does not produce an artifact. Consume the admitted observation directly,
   preserving its byte/receipt correspondence through the build input.
4. **Bind input identity before dispatch.** The accepted consuming contract must
   name the input and its applicability. Its exact body hash, source version and
   relevant context pins enter the sealed Run Manifest. Pass, packet or input
   changes follow the existing invalidation algebra. A delivery recheck must
   reject a Cairn receipt that is no longer valid for fresh use; historical
   recompilation is not fresh delivery authorization.
5. **Materialize and render that same input.** The dispatch bundle must contain
   precisely the pinned bytes. Every actual invocation's rendering/source map
   must account for them, including supported spill and relaunch behavior.
   Admission must reject missing, substituted, duplicated or undeclared input
   entries. A manifest reference or reported asset hash alone does not prove
   that the invocation carried the context.
6. **Observe the actual host.** Keep the Cairn observer identity in the parent
   host. Persist the correspondence among Cairn receipt, Striatum run/dispatch,
   host attempt and result reference. Spawn and terminal facts must come from
   that host, with internal relaunches represented consistently. A lane receives
   neither observer credentials nor operator database access.
7. **Assess the task separately.** Preserve process completion, task acceptance
   and memory exposure/use as separate observations. A source citation and an
   exit-zero process cannot stand in for a successful build.

The first execution can use a package containing ordinary A notes. This is a
bounded admission case, not a reduced final requirement. It must not strip B/C
selections or required context to manufacture an A-only package: if the chosen
native path cannot honor the returned package, refuse that case. Full class,
authority and mandatory-runtime behavior stays on the roadmap.

## Concrete implementation boundaries

The inspected Striatum source is commit
`a6b1ae71d95cdf99200c10d8c6ce855d9da70a69`.

| Boundary | Current source | Required change or decision |
| --- | --- | --- |
| Pass declarations | `internal/driver/catalog.go: PassSpec, LoadCatalog` | Prompt-asset declarations are currently omitted from the driver projection. The dynamic memory consumer also needs an accepted declared-input contract. |
| Fresh materialization | `internal/driver/session_dispatch.go` | Fresh Run/Dispatch Manifests hard-code empty prompt assets. Resolve and retain only the chosen contract's exact input before opening dispatch. |
| Bundle integrity | `internal/backend/bundle.go: WriteDispatchBundle` | Existing code checks declared asset bytes. Validate identity/path uniqueness and namespace ownership when enabling nonempty assets. |
| Invocation rendering | `internal/backend/llm/prompt.go: renderPrompt` | Current rendering/source maps cover environment and inputs. Add only the accepted additional context category, with its transport bound intact. |
| Admission | `internal/store/admission_v2.go: runSemanticSourceMultisetV2, runPromptAssetHashesV2` | The source-map multiset covers environment/inputs; asset hashes have a separate comparison. Preserve historical contracts while making the new invocation requirement checkable. |
| Retained dispatch | `internal/driver/run_open.go`, `dispatch_recovery.go` | Existing asset decoding is part of reconstructing the exact dispatch. It must preserve the same new input identity; do not add a second recovery mechanism. |
| Cairn host connection | `localapi`, `runner`, `core/run_retrieval.go` | Reuse existing authenticated compilation and host observations. Settle the native correspondence and recheck point before adding another adapter API. |

## Acceptance evidence for this direction

Before calling the native path implemented, retain a real admitted input's
producer and consumer pins, the Cairn package/receipt correspondence, the actual
dispatch bundle, and the invocation's admitted rendering evidence. Demonstrate
wrong repository/destination/context, missing bytes, digest substitution, stale
delivery, unsupported mandatory content and changed packet inputs being refused
at their owning boundary. Existing unrelated requests, graphs and timers must
remain outside the experiment's mutations.

Then perform an authorized real build with relevant previously captured memory,
prospective task checks and a complete workspace. Compare the same task/binding/
budget with no optional memory and with the same material supplied directly when
that comparison is valid. Record actual retrieval contact, accepted/rejected/
unknown task outcome, repeated failures, elapsed time, context/pull cost and
review burden. Preserve failures and inconclusive controls.

The selected producer/consumer proposal must enter the owning Striatum contract
process before the dynamic bridge gains runtime force. Changes to Striatum
semantics follow the owning RFC and generated decision process. The capture
request does not grant new acceptance authority, and this Cairn document is not
a hand-authored Striatum Decision Record.

## Evidence and next contract work

[Recorded metadata](verification/native-input-proposal-2026-09-08.json) separates
the completed opening capture from the unimplemented proposal. The source and
read-only diagnostic records remain under `/tmp/cairn-native-input-` on the
development host. No Cairn binary, API profile, Striatum source or live lane was
changed in this assessment.

The owning proposed amendments now exist in Striatum commit
`0d9245f2954c088a03661463ac52ec15254eddff`:

- [RFC 0004: observation and build inputs](https://github.com/halbritt/striatum-next/blob/0d9245f2954c088a03661463ac52ec15254eddff/rfcs/0004-compiler-passes/cairn-context-amendment.md).
- [RFC 0007: delivery and host correspondence](https://github.com/halbritt/striatum-next/blob/0d9245f2954c088a03661463ac52ec15254eddff/rfcs/0007-agent-runtimes-and-execution-backends/cairn-context-amendment.md).

These are separate draft files under their owning RFCs. The accepted source
files, generated decisions and runtime remain unchanged; Striatum's RFC lint
and all accepted decision-source pin checks passed. The drafts settle the
proposed execution-scope label without a future-manifest hash cycle, D0 replay
from a retained authenticated capture, and use of the ordinary input rendering
path. Static prompt-asset support is not a prerequisite.

Two source constraints sharpen the implementation work. Pre-launch compilation
must use the observing host's receipt identity: `BindRun` and `ClaimRun` enforce
ownership, while `LinkRunRetrieval` only joins retrievals created during an
already observed host run. Also, current `ClaimRun` checks restore generation,
policy and payload availability but does not fully re-evaluate selected-record
eligibility at the inspected base. The subsequent [launch freshness check](launch-freshness.md)
now reuses compiler eligibility in a serializable snapshot while preserving the
exact package and single-launch rules. [Retained execution](retained-execution.md)
now supplies the Cairn-side staged consumption path: it loads the observer-owned
receipt and expected seal, verifies run declarations and executes without
recompiling. Native acquisition, admitted ECR resolution and adapter/host
correspondence still need implementation under accepted Striatum contracts.

Advance those prerequisites and the accepted contract path under the existing
captured subject. Do not create another opening request, rewrite these drafts
as accepted decisions, or repeat the prompt-asset inventory. The eventual
closing observation must record actual resulting changes and evidence; it has
not been issued yet. [Amendment metadata](verification/native-amendments-2026-09-08.json)
records the draft-only verification boundary.

Validated Pincite packet `pkt-7e9933beb3fab6e1` informed repository precedence,
evidence before intervention and behavior preservation. The private decision
classifies 15 obligations as nonmaterial to this proposal; the implementation
and real-task obligations above remain open.
