# Native Striatum context: implementation direction

Updated 2026-09-10. The native path is [merged to Striatum main](verification/native-integration-2026-09-10.md)
at `5848b23`, including observation production,
build input admission and host execution. The accepted catalog still specifies
`observation@1` and `build@3`; the proposed native contracts are not enabled in
production. U1 remains partial.

Two real model comparisons received native context but produced no admitted
repair. The [corrected comparison](verification/native-corrected-recurrence-2026-09-08.md)
retired that unchanged task and binding. Native delivery is observed; useful
application in a completed build remains unestablished.

A [live follow-up review](verification/retained-intent-review-2026-09-10.md)
confirmed that the opening request remains satisfied at `captured`, main's catalog
remains `observation@1`/`build@3`, and the installed Driver is clean `a6b1ae7`.
Use `striatum --json status` for a machine-readable status read; `--json` is a
global flag before the verb. The review did not advance native contract acceptance.

## Current implementation and next work

### Shared RFC adoption check — 2026-09-10

The live graph has an unfinished amendment on the same RFC 0004 identity that
Cairn needs. Request 408565 targets `rfc-staged` for
`review-conclusions-are-evidence-bound`. Its captured note matches request
408483 and identifies that separate feature. Escalations 409639 and 409640
report nine exhausted dispatch attempts and the resulting planning dead end.
Those failures are not Cairn build trials or evidence against memory usefulness.

The status projection alone names the shared RFC, so it is insufficient to
attribute its work to Cairn. Read the retained request intent before acting.
The [adoption check metadata](verification/native-adoption-2026-09-10.json)
pins the relevant records without copying their private request bodies.
Cairn's own opening request 408328 remains satisfied only at `captured`.
No relevant acceptance candidate was queued in this observation; RFC 0007's
Decision Record was listed as stale.

Advance native adoption by first reconciling amendment sequencing on the shared
RFC identity. Preserve the existing feature's intent and accepted semantics;
its eventual stage acceptance cannot establish acceptance of Cairn's separate
draft. The owning RFC 0004 and RFC 0007 amendments then need native stage
production, review and personal acceptance, followed by generated successor
Decisions. D0016.C1/C3/C13 exclude these RFC and Decision acceptances, and
escalation resolution, from the standing agent delegation. The current Cairn
task does not resolve or retry the other feature's escalations.

After those Decisions are accepted, adopt the corresponding catalog/schema
changes, verify the integrated tree, deploy through Striatum's existing
transaction and exercise a meaningful native build. Both the source and deployed
catalog inspected here retain `observation@1`/`build@3`; installing the merged
code alone would leave native context disabled. The Driver is still clean
`a6b1ae7`. Ordinary Codex/OpenCode task-value work can continue independently of
this shared-RFC sequence.

This check changed adoption guidance, not the graph, runtime or implementation.
It did not diagnose the unrelated dispatch failures or establish a new task-value
result. The next observation must refresh these graph facts before mutation.

### Implemented boundaries

| Boundary | Implemented evidence | Remaining work |
| --- | --- | --- |
| Acquisition | [Host capture](verification/native-capture-2026-09-08.md), merged to Striatum main at `5ea87ca`, compiles and confirms the exact observer-owned receipt. | The watch supports the existing body request described below; newer retrieval options need an explicit interface extension when a task needs them. |
| Observation and build | [Request/producer](verification/native-observation-2026-09-08.md), [build input](verification/native-build-input-2026-09-08.md) and [complete Driver chain](verification/native-chain-2026-09-08.md) are merged into main with proposed contracts. | Owning contract acceptance and production adoption. Do not implement another producer or duplicate the input path. |
| Host execution | [Supervisor integration](verification/native-host-2026-09-08.md) checks actual context, claims current delivery and records process correspondence. | Verify any new task's actual binding and outcome through this existing supervisor. Do not wrap native Striatum in `cairn run`. |
| Task value | [Executor](verification/native-executor-2026-09-08.md) and two retained model comparisons exercised the path; neither comparison completed its repair. | Use a meaningful task with a justified execution condition. Preserve qualitative and cumulative observations as well as completed artifacts; do not repeat the retired comparison unchanged. |

The original opening request was RQ-408328, subject
`striatum-next/passes/cairn-native-context-input-20260908`, with intent-capture run
408357 and accepted intent head 408364. Those historical records establish the
opening capture, not native contract acceptance. Current source and the evidence
above supersede earlier reports' descriptions of unimplemented components.

### Current watch compatibility

Striatum `internal/cairn/capture.go` at `ee8a463` accepts only
`cairn.semantic/3` body packages. Its watch declares repository/task/run,
revision/workspace, task class, binding/capability, query, destination and budget.
It does not expose semantic discovery, kind filters, task phase, failure signatures
or index/pull delivery. Unknown watch fields and unsupported package schemas
refuse; dropping those fields is not a compatible way to request their behavior.

Cairn `a848e3c` still emits schema 3 for that existing request. Its compiler emits
schema 9 for kind filtering, 10 for task phase and 12 for failure signatures.
Those newer options are available through Cairn's ordinary interfaces, but their
presence does not establish native Striatum support. There is no observed need
to widen the native adapter merely to keep its existing watch working.

### Current-build verification (2026-09-10)

The existing `TestCairnService` passed against installed Cairn `a848e3c` and
Striatum integration branch `ee8a463`. It creates its own PostgreSQL cluster,
API identities and graph. Local/hosted capture, exact child rendering, observer
ownership refusal, request acquisition, offline replay and offline observation
production passed. With a delegated cgroup, the actual supervised shell also
ran and recorded its outcome; a changed selected note refused before launch.

Reproduce from the Striatum integration checkout with a built Cairn CLI:

```sh
systemd-run --user --scope --quiet -p Delegate=yes env \
  STRIATUM_REQUIRE_CGROUP=1 \
  STRIATUM_CAIRN_TEST_BINARY=/absolute/path/to/cairn \
  go test -race ./tools/cairn-capture -run '^TestCairnService$' -count=1 -v
```

The initial nondelegated run passed capture but skipped the native-host subtest.
The command above passed all subtests without skips. This is a current component
compatibility check, not a rerun of full Driver build admission or a model task.
The dated [implementation history](implementation-status.md#implementation-history)
retains the source/binary pins and both log hashes. No production configuration,
contract or runtime changed.

The requirements below remain the design direction. The implementation table
above identifies which parts now exist; acceptance and task value remain
separate.

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

The producer implemented on the integration branch is the existing **observation**
pass, extended with a declared Cairn watch target. Its Exogenous Change Record records the authenticated
API observation and retains the exact returned context bytes and their digest.
The implemented consumer is **build**, with an explicitly declared optional
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
proposed contract requirements implemented on the integration branch; the accepted
observation contract does not yet enable a Cairn watch target.

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

## Historical implementation boundaries

The following table records the initial assessment, before the implementation
checkpoints above. Its missing-work descriptions are historical, not the current
backlog. That assessment inspected Striatum commit
`a6b1ae71d95cdf99200c10d8c6ce855d9da70a69`.

| Boundary | Source at the initial assessment | Required change at that time |
| --- | --- | --- |
| Pass declarations | `internal/driver/catalog.go: PassSpec, LoadCatalog` | Prompt-asset declarations are currently omitted from the driver projection. The dynamic memory consumer also needs an accepted declared-input contract. |
| Fresh materialization | `internal/driver/session_dispatch.go` | Fresh Run/Dispatch Manifests hard-code empty prompt assets. Resolve and retain only the chosen contract's exact input before opening dispatch. |
| Bundle integrity | `internal/backend/bundle.go: WriteDispatchBundle` | Existing code checks declared asset bytes. Validate identity/path uniqueness and namespace ownership when enabling nonempty assets. |
| Invocation rendering | `internal/backend/llm/prompt.go: renderPrompt` | Current rendering/source maps cover environment and inputs. Add only the accepted additional context category, with its transport bound intact. |
| Admission | `internal/store/admission_v2.go: runSemanticSourceMultisetV2, runPromptAssetHashesV2` | The source-map multiset covers environment/inputs; asset hashes have a separate comparison. Preserve historical contracts while making the new invocation requirement checkable. |
| Retained dispatch | `internal/driver/run_open.go`, `dispatch_recovery.go` | Existing asset decoding is part of reconstructing the exact dispatch. It must preserve the same new input identity; do not add a second recovery mechanism. |
| Cairn host connection | `localapi`, `runner`, `core/run_retrieval.go` | Reuse existing authenticated compilation and host observations. Settle the native correspondence and recheck point before adding another adapter API. |

## Acceptance evidence for this direction

The native path must retain a real admitted input's
producer and consumer pins, the Cairn package/receipt correspondence, the actual
dispatch bundle, and the invocation's admitted rendering evidence. Demonstrate
wrong repository/destination/context, missing bytes, digest substitution, stale
delivery, unsupported mandatory content and changed packet inputs being refused
at their owning boundary. Existing unrelated requests, graphs and timers must
remain outside the experiment's mutations.

For further task-value work, use an authorized real build with relevant previously
captured memory, prospective task checks and a complete workspace. Compare the same task/binding/
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

[Original proposal metadata](verification/native-input-proposal-2026-09-08.json)
records the opening assessment, when the proposal was unimplemented. Its source
and diagnostic records remain under `/tmp/cairn-native-input-` on the development
host. The later branch implementation and model comparisons are linked above.
The owning RFC 0004/0007 amendments remain proposals. Acceptance must follow
Striatum's existing contract and generated-decision process before production
enablement; the opening intent capture and fixture admission do not grant it.

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

### Historical amendment and implementation notes (2026-09-08)

The following paragraphs retain the sequence of earlier checkpoints. Statements
that work was unfinished describe those checkpoints; use the current table above
for the remaining work. The doctrine packet at the end belongs to the original
proposal, not the 2026-09-10 compatibility check.

Two source constraints sharpened the implementation work. Pre-launch compilation
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

Striatum's [host acquisition tool](https://github.com/halbritt/striatum-next/blob/5ea87ca65c1bd25f228c0447110d991a3f0de8c8/docs/how-to/capture-cairn-context.md)
now compiles through the observer profile, confirms the exact retained package,
and preserves a complete capture that can be read offline using its independently
pinned hash. Its [real-service verification](verification/native-capture-2026-09-08.md)
covers local/hosted profiles and correspondence with Cairn's child rendering.
This is host preparation, not an admitted ECR. The trusted producing path must
still acquire and pin that data before sealing the observation, and accepted
producer/consumer contracts, input resolution and actual host correspondence
remain open. The tool adds no runtime force to the proposed amendments.

A [draft producer implementation](https://github.com/halbritt/striatum-next/commit/88b2c4f85f843e71a0017412309c82a884ce5509)
is now on Striatum's `cairn-native-observation` branch. It pins the original
capture as a declared observed-world environment entry, produces a typed ECR
without contacting Cairn, and checks the submitted body against that same source
at admission. Focused race tests, a real disposable-service replay after API
shutdown, and the full Striatum check passed for that producer checkpoint.
The [trusted request frontend](verification/native-observation-2026-09-08.md)
is also implemented and checked on that branch. It acquires the capture itself
before issuing the request and refuses ordinary requests claiming a native
capture identity. The [build-input checkpoint](verification/native-build-input-2026-09-08.md)
now resolves and pins a packet-named ECR, renders its captured context, and checks
that exact context at prompt admission. The accepted catalog remains
`observation@1`/`build@3`; actual launch correspondence remains unfinished. This is implementation
progress, not native enablement or new task-benefit evidence.

The retained runner is useful for hosts that need Cairn to own their child
process. Striatum already owns that process in
`internal/backend/llm/supervisor.go:Supervisor.execute` and renders declared
inputs in `prompt.go:renderPrompt`. Its adapter should call the authenticated
`RunPackage`, binding, claim, delivery and outcome operations at that existing
boundary. Wrapping the supervisor with `cairn run` would append another context
copy outside Striatum's recorded input rendering and add a second process
supervisor. Preserve the ordinary ECR input rendering, and perform the final
Cairn check before the actual confined provider invocation. This requires no
second runner or recovery subsystem. The Cairn interface is now
[checked and installed](verification/retained-execution-2026-09-08.md).

Advance those prerequisites and the accepted contract path under the existing
captured subject. Do not create another opening request, rewrite these drafts
as accepted decisions, or repeat the prompt-asset inventory. The eventual
closing observation must record actual resulting changes and evidence; it has
not been issued yet. Preparatory source integration at `5848b23` does not close
the wider native contract and adoption work. [Amendment metadata](verification/native-amendments-2026-09-08.json)
records the draft-only verification boundary.

Validated Pincite packet `pkt-7e9933beb3fab6e1` informed repository precedence,
evidence before intervention and behavior preservation. The private decision
classifies 15 obligations as nonmaterial to this proposal; the implementation
and real-task obligations above remain open.

## Historical native host implementation checkpoint

The [host verification](verification/native-host-2026-09-08.md) extends the
[build input checkpoint](verification/native-build-input-2026-09-08.md) through
Striatum's existing confined supervisor. It binds the exact retained receipt to
the selected backend/runtime and staged context, claims once, records delivery
and process outcomes, and checks retained host correspondence at admission.
The real-service fixture demonstrates one confined shell invocation and refusal
after a selected note changes; it does not demonstrate an accepted model build.

The integration branch includes proposed host configuration and observation
schemas. The accepted catalog, generated Decisions and installed runtime remain
unchanged. The [complete-chain test](verification/native-chain-2026-09-08.md)
now joins producer and consumer admission through the Driver using a real Cairn
service and mechanical fixture planning/build output. Later real model comparisons are
linked in the current table above. They established delivery but no completed
repair, and the unchanged comparison is retired.
