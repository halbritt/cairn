# Native Striatum admission assessment — 2026-09-08

Cairn U1 remains partial. Striatum has a declared knowledge-promotion path and a
historical fixture proof, but the inspected evidence does not establish current
native Cairn admission. This assessment corrects the earlier inference from a
parked RFC and empty prompt assets alone. It adds no runtime behavior.

## Scope and evidence

Inspected Striatum checkout: `a6b1ae71d95cdf99200c10d8c6ce855d9da70a69`.
It was clean before and after the assessment. The fleet-knowledge checkout was
at `dc70cf0` with pre-existing untracked `.striatum/` and `policy/`; those paths
were preserved. Its graph was read using the official `striatum ledger cat`
reader. Running the CLI from the Striatum checkout with `-repo` pointing to
fleet-knowledge resolves the compiler catalog and backend declarations; running
from the registration-only fleet checkout with defaults does not.

The [metadata and probe results](native-admission-assessment-2026-09-08.json)
retain exact identities without private artifact bodies. Raw graph snapshots
and decompressed historical bodies remain local under `/tmp`.

| Observation | What it establishes |
| --- | --- |
| `catalog/passes/knowledge-promotion.yaml` is marked accepted and consumes Exogenous Change Records to produce candidate Products. | A declared promotion contract exists despite RFC0015's parked index entry. |
| `internal/backend/local/executors.go` implements promotion; driver producer resolution, admission and verification contain fleet-knowledge branches. | There is current source implementation, not merely a target description. |
| Request 10055 is recorded satisfied at Verified. | The graph records that claim; its meaning needs the underlying evidence. |
| Accepted ChangeSet 42451's audit explicitly identifies fixture mode and defers live delivery behind named dependencies. | The historical proof does not claim live recall service delivery. |
| Accepted ChangeSets contain `internal/store/recall_source.go` and an end-to-end recall test, but those files are absent from the inspected checkout. | Historical artifact acceptance is insufficient evidence that this checkout implements that path. The cause of the discrepancy was not established. |
| The registered fleet-knowledge graph contains one Product admission, seq 230; head movement 234 accepts it. | A real admitted Product exists. It is about Python verification checks, contains no hippo mention, and is not the historical recall fixture element. |
| That Product's request 1 currently reports `migration_required`; initial verification runs 237/240 closed abandoned. | Neither its accepted head nor those runs establishes a currently Verified live recall element. No migration was performed. |

## Reproduced checker limits

Built the unchanged `tools/knowledge-bindings-independent` source into a temporary
binary. Each invocation used an isolated temporary directory containing only its
input `body`; no synthetic input was admitted to an operational graph or Cairn.
The Product body was decompressed from its content-addressed object, with header,
length and SHA256 independently checked before the probe.

| Input | Current-source checker result |
| --- | --- |
| Exact Product 230 body, SHA256 `27aba2ea4450e56926795272aa5640eab9f15590241e1d3d98fbfa5d902f8cda` | Exit 1: no knowledge elements found. Its headings do not match the checker's element format. |
| An explicit fictional element with two distinct repository strings and two non-hash citation strings | Exit 0: reports two attestations across two independent sources. |

The checker counts distinct cited repository/hash strings. Its source explicitly
states that resolving cited hashes against admitted evidence is a later
strengthening. The probe also shows that the strings need not be valid hashes.
This does **not** show counterfeit admission through the complete live gate
path, nor does it identify which checker binary historical runs executed.
It shows why a passing standalone check cannot establish provenance closure or
execution independence for Cairn knowledge.

## Dispatch boundary remains separate

Fresh `internal/driver/session_dispatch.go` manifests have empty PromptAssets.
Manifest and bundle code represent and hash-check assets, and recovery can decode
them, but `internal/backend/llm/prompt.go` renders Environment and Inputs rather
than PromptAssets. Build contract v3 has no arbitrary Cairn input. These findings
leave the [declared input boundary](../local-api.md#striatum-boundary) in place.
Knowledge promotion and pre-dispatch context delivery are different paths; one
cannot stand in as proof of the other.

## Evidence needed to close native U1

1. Identify the current accepted producer and consumer contract for Cairn context,
   reconcile the historical recall artifacts with the running implementation,
   and establish the corresponding declared input path. A file copied into a
   lane is insufficient.
2. Compile and seal the exact permitted context before dispatch. Verify its
   identity/hash in the actual bundle and its presence in the actual lane input.
   A populated manifest field alone is insufficient.
3. Associate the receipt with the real authenticated host attempt and observe
   terminal/recovery behavior through that host. Preserve task gate evidence
   separately from process completion, as required by
   [host attempt linkage](../host-attempt-link.md).
4. If knowledge promotion supplies the context, resolve supporting evidence and
   its provenance under the owning contract. Cited strings and the historical
   fixture label cannot establish that evidence.
5. Observe useful behavior on a real task with bounded comparison and uncertainty.
   A successful build, status label, or synthetic adapter test cannot establish
   the complete multi-harness outcome.

These are remaining U1 proof obligations, not a new accepted Striatum design.
The assessment leaves both codebases' runtime semantics and all live lanes,
graphs, timers, and services unchanged. The next implementation must select a
concrete host/input contract; silently reviving historical ChangeSets would skip
that decision.

## Assessment provenance

Validated doctrine release: `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`.
Packet `pkt-2670e9cc3288ff3d` informed evidence-before-intervention and repository
contract precedence. This is a bounded assessment; it does not recommend a
Striatum architecture or decide its acceptance. The local decision receipt
retains evidence hashes, alternatives and obligation classifications.
