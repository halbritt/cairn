# Ordinary note source citations — 2026-09-09

## Result and scope

Ordinary A notes can now retain exact captured-source citations and expose them
through the existing body/evidence pull workflow. `agent cite`, operator `cite`
and the native edit tools' `evidence_citations` input create a new retained version
with unchanged body and draft metadata. This makes source inspection available
without a qualification grant. The note remains fallible A testimony.

Baseline: `1d022c0`. The earlier create/edit interfaces did not attach evidence
to ordinary notes, and compiler selection populated supporting references only
for B. Source labels in prose could not supply an authorized evidence pull.
This is an additional retrieval/storage capability under L4, not evidence that
memory improved a model task or a claim that the full lifecycle is complete.

## Contract

The explicit array replaces current citations; `[]` clears them, while omission
or null refuses. Existing full-source digest, byte-span, repository and sensitivity
checks apply. The maximum remains 32 sources, each with up to 32 passages.
Successful retries preserve their original result; changed intent conflicts.
CAS, record/attempt locks, source checks, version creation and request settlement
share the existing serializable transaction.

Ordinary body/full-draft revisions copy existing references, including degraded
ones, without asserting a new check. Earlier versions retain their references.
Advisory pulls show degradation; unavailable source bytes refuse expansion.
A records still cannot enter consequential planning. Qualified correction and
promotion retain their independent authority requirements. No schema, dependency,
semantic payload shape, native tool name or source-capture mechanism was added.
The new citation operation returns identifiers, not stored note/source content.

The new sources are writer-asserted support, not source truth or relevance.
Capture and citation remain separate transactions. Current source pulls require a
fresh eligible index handle; historical receipts retain frozen evidence facts.
Ordinary `history` still exposes note metadata/body, not citation objects.

## Verification and corrections

The initial tracer test failed to compile because the public citation operation
was absent. Initial executable fixtures had two errors: a passage length was one
byte short, and a fixture creator tried to promote its own record. Corrected the
length and used an independent producer, preserving the self-promotion refusal.

The full disposable PostgreSQL/race package suite passes, including:

- Citation → text revision → exact source passage pull, with the note still A.
- Missing/null, duplicate, wrong-digest, out-of-range, local-to-shareable and
  foreign-repository refusals without advancing the note.
- Exact retry, changed-intent conflict, stale-version refusal and citation/edit
  races with exactly one successful update.
- Degraded-reference preservation through full edits, explicit clearing and
  unchanged frozen historical receipts; qualified records refuse ordinary cite.

The initial CLI integration extension reused a source from an existing impact
fixture, violating that fixture's one-record assumption. Its source is now
separate; the existing impact assertions remain intact. CLI/stdio source-update
checks passed afterward. The first native OpenCode invocation timed out during
startup. An isolated process/network trace observed npm registry contact before
tool execution. Disabling default plugins in the diagnostic reached Cairn argument
validation; the test harness now disables them too. Owner configuration and native
permission checks remain unchanged. The diagnostic that outlived TERM was stopped
by its exact verified PID; no other OpenCode processes were stopped.

The final isolated CLI/API run passes, including stdio MCP and actual OpenCode
1.18.21 custom-tool calls. Both native edit surfaces verify citation replacement,
body-edit preservation, exact passage pulls, empty-list clearing, retries,
changed-intent and stale-handle refusals. Existing permission, hosted filtering,
maximum-size note and scripted native-session checks also pass. This exercises
native tools without model inference. Installation is recorded separately.

Raw test logs and diagnosis files are retained under
`/tmp/cairn-ordinary-citations-*`. They contain only disposable fixture state,
not operational memory.

## Compatibility and limits

A disposable old/new executable check verified that existing `create` and `revise`
requests retain their retry results. It also reproduced the limitation: installed
baseline `463ccef` ignores A citations during compilation and drops current A
references when it edits a cited note. Upgrade all store writers before using this
feature; an old binary is not a safe rollback after cited notes are introduced.
There is no automatic version fence or migration in this slice.

The older API and native adapter are not claimed to support citation updates.
No answering model was invoked. The tests demonstrate transport, state and
retrieval behavior; correctness of a cited claim, upstream freshness, net task
benefit and independent cross-harness task advantage remain unassessed.

## Review method

The owner's delegated implementation authority, accepted evidence lifecycle and
current source govern. Pincite's validated release `d3e0c0d` supplied discovery
packet `pkt-5fd700d2453859cc` and evidence-attached packet
`pkt-9dbe4ec856198351`. Seven remaining Go-interface obligations are nonmaterial:
no new Go interface or interface-valued absence contract was introduced. Existing
transaction, evidence and edit owners contain the change. A decision receipt and
consumption observations are retained with the verification metadata.
