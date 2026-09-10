# Historical recompilation requirement audit — 2026-09-10

E3 remains partial. Its retained-receipt recompilation is implemented; the missing
original-incident evidence should not be mistaken for an absent replay engine.
The accepted design also requires real-history evaluation to exclude advice
written after the incident being replayed.

The audit inspected the accepted design, current source, named tests and earlier
native-history report at `64659de`. It added one direct cutoff regression and
ran the relevant checks against disposable PostgreSQL. It changes no runtime
code, storage schema, operational note or installed service.

## Requirement and evidence

| Requirement | Current evidence | Finding |
| --- | --- | --- |
| Preserve a durable cutoff without treating a PostgreSQL transaction ID as time travel | Design §7.5; `core/compiler.go` captures one repeatable-read snapshot and commits its candidate details; `core/recompile.go` loads candidates for one exact receipt. | Implemented for captured read sets. A timestamp alone cannot identify an unrecorded snapshot. |
| Recompute selection under original scope, revision/context, policy, eligibility, ranking and budgets | `recompileTx`, frozen `CandidateFacts`, retained `SemanticPackage`; phase, policy, entity, semantic and conflict tests reconstruct matching seals. | Implemented for supported retained compiler formats. Query and explicit entity intent must match; other pins come from the receipt. |
| Exclude later-created claims and policy | New `TestHistoricalRecompileExcludesLaterNotesAndInstructions`: a later note and mandatory instruction appear in a fresh recurrence, while the original read set retains only its original note and seal. | Directly verified. |
| Prevent later corrections, evidence changes and revocations from rewriting historical selection | `TestHistoricalRecompileUsesFrozenEligibilityAndVersions`, `TestHistoricalRecompileFreezesEvidenceAndDetectsMissingInputs`, policy revision/revocation and scope-authorization tests. Fresh delivery changes or refuses while old seals reproduce. | Implemented and tested. Historical inspection does not revive present authority. |
| Pin optional model results | `TestSemanticDiscoveryUsesEligibleNotesAndFrozenScores` forbids a model invocation during recompilation and rejects altered frozen scores. | Implemented. Recompilation does not rerun a nondeterministic model. |
| Retain advisory conflict positions at the original cutoff | `TestAdvisoryConflictFreshnessBeforeCachedPull` preserves historical seals after edits, resolution or another conflict, while current pull/launch checks refuse. | Implemented. |
| Preserve missing-history and deletion limits | `historicalRecord` reports missing versions or changed bytes; `recompileTx` rejects explanations without frozen facts. `TestForgetExcludesCopiesBeforePurgeAndKeepsUseHistory` refuses replay/recompilation after forgetting. | Implemented refusal paths; hashes cannot restore purged payloads. |
| Inspect an authenticated host's historical receipt | `core/observations.go:receiptAccess` requires the original channel. The local CLI refuses the recorded host receipt; `localapi/server.go` has no recompile route. | Direct host-receipt reconstruction is unavailable through the existing authenticated interface. Current-execution `run-package` is a different operation. |
| Replay a real incident using only pre-cutoff memory/evidence and the relevant historical repository revision | Design §15.5; the [native-history trial](history-coverage-2026-09-07.md) explicitly imported its notes later and labelled all scenarios as recurrence. The recorded-run check below reconstructs a recurrence preflight using its actual earlier note. | Original Striatum incident availability remains unestablished. E3/E4 remain partial. |

The focused PostgreSQL run passed the three historical tests, policy
revision/rollback and revocation tests, frozen semantic-score test, phase
recompilation test and advisory-conflict freshness cases. Additional source/test
pointers above were inspected, and their prior full integration result is retained
in the [advisory verification](advisory-conflicts-2026-09-10.md). The audit does not
present those older runs as a new execution of every test.

## Recorded-run cutoff check

The [observed failed OpenCode run](observed-model-failure-2026-09-08.md) retained
an original trial dump and a separate reviewed dump containing the lesson written
after its failure. The check verified the eight source artifact hashes, restored
the reviewed dump into disposable PostgreSQL and migrated that copy through 034.
Clean installed Cairn `4df17af` performed the inspection; no model task ran.

The original scenario file still matches its recorded digest. Its query was used
to recompile operator preflight `46664523-d2f6-42cf-8248-981c31b860d7`. The result
reproduced the seal of actual host receipt
`ad4f4fad-1400-425c-810b-b08d10513b58`, preserving its declared historical revision
`9757fddf68ba3d731a9e3d874e601a0ce2ce17af` and selected note's exact version and
body digest. Read-only inspection found identical frozen candidate sets in the
two receipts. Their single candidate and supporting evidence were retained before
the host compile at 05:52:28 PDT on September 8. The later lesson was captured at
06:12:53 and remained absent from the reconstructed package.

Direct recompilation of the host receipt correctly returned `AUTHORITY_DENIED`
under the CLI's identity. The check neither changed the receipt's owner nor
substituted an impersonated channel. Matching preflight and host inputs are
evidence about those recorded read sets, not a successful authenticated host
recompile. The historical revision object exists locally; no workspace was
recreated or task rerun in this audit.

This is original-time reconstruction relative to the September 8 recurrence
run. Its advice still postdates the older Striatum incident. The original trial's
`post_review_recurrence` classification and rejected outcome remain unchanged.
All eight source hashes matched afterward, and the disposable cluster was removed.

## Consequence for work

Keep the named-receipt interface. The design's `as_of` field identifies a temporal
constraint; it does not make an exported PostgreSQL snapshot durable or justify
inventing omitted ordinary-history events. For an original-incident case, identify
its historical repository revision, actual earlier memory/evidence and recorded
selection inputs. If that material was never retained, record the limitation or
label a later reconstruction as a recurrence. A newly captured preventing lesson
must not be represented as memory available during the original incident.

A universal event log or arbitrary-time query engine is not selected by this
audit. A concrete historical case may reveal a narrower access or reconstruction
need. The missing authenticated historical reader is one such access gap, now
recorded under E3; implementing it would require explicit destination and receipt
ownership checks. It does not justify an operator bypass or a temporal-store
redesign. No new model trial is needed to relabel these recurrence observations.
This preserves the original requirement rather than closing it against a smaller
implementation claim.

The user’s priority and evaluation notes were retrieved through the ordinary
hosted profile and checked against the current roadmap. They reinforced keeping
recovery machinery and additional adapters below work that could improve task
value. The same guidance was present in the conversation and repository, so this
use does not establish an independent memory benefit. No new value case or task
acceptance is claimed.

Private audit inputs and the focused test log are under
`/tmp/cairn-replay-audit-20260910/`; the verification JSON records their hashes and
the doctrine decision boundary. The runtime remains the installed `4df17af` build
on migration 034.


Pincite packet `pkt-7d3944f3e1e70a3d` retains the typed observations and decision
boundary, with a validated receipt and closed citation traces. Its 26 remaining
obligations are classified individually as nonmaterial in the metadata: this
change neither repairs production behavior nor redesigns modules, concurrency
or authority. The audit does not certify every mutation path or close E3.
