# Find a reviewed lesson by failure signature

Supply `error_signature_sha256` when a known failure signature should help find
a previously reviewed lesson. The digest must be 64 hexadecimal characters;
Cairn normalizes it to lowercase. It is supplied retrieval intent, not an
observation that the current task failed.

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task TASK_ID --run RUN_ID \
  --error-signature-sha256 SIGNATURE_SHA256 'connection refused'
```

Replace the scope and signature placeholders with the actual task and the digest
used by its failure assessment. Use the same signature method across tasks;
Cairn does not parse or normalize error messages automatically. The ordinary
MCP and OpenCode `cairn_search` tools accept the same `error_signature_sha256`
field. Local `search`, `agent start`, and fresh or retained `run` commands accept
`--error-signature-sha256` too.

The query is optional when a signature is supplied. With no query, only matching
optional lessons and required instructions are returned. With query text,
ordinary lexical matches remain available; `--semantic` can add semantic discovery
and still requires a nonempty query. Browsing cannot be combined with a signature.
`--offset 0` starts ranked pages; keep the signature, query, kinds and context on
later pages. Each page has its own budget and reads current state.

## Make a reviewed association available

First inspect an evidence-attached [proposal](demand-review.md) and the lesson
version. Convert the proposal using the existing operator review command:

```json
{
  "request_id": "REVIEW_UUID",
  "proposal_id": "PROPOSAL_UUID",
  "expected_version": 1,
  "disposition": "converted",
  "result_record": "LESSON_UUID",
  "result_version": 3,
  "signature_shareable": true,
  "reason": "Link the inspected lesson and share this signature association"
}
```

Replace the UUIDs and versions with the inspected values and pass the object to
`cairn review-proposal` on stdin. `VERSION_CONFLICT` means the proposal or lesson
changed: inspect the current version before retrying. Ordinary agent tools cannot
perform this review or promote the lesson.

The association is local unless that exact review sets `signature_shareable` to
true. Sharing the lesson alone does not publish its association with a private
failure. Hosted retrieval needs both the explicit review choice and a shareable,
eligible lesson. Sharing the association does not deliver proposal reasons,
assessment bodies, raw logs or source evidence. `proposal` and `proposal-history`
show the review's sharing choice; false is omitted from their JSON responses.
The flag is accepted only for conversion. Reopening or replacing a review does
not inherit an earlier sharing choice.

## Matching and history

A match requires the same repository and digest, a currently converted review,
unchanged source assessment versions, and the exact current lesson version pinned
by that review. Legacy reviews with unknown result versions cannot match. Editing
the lesson, reopening its proposal or correcting a source assessment removes the
association from fresh search until a current conversion is reviewed.

Lesson scope, applicability, destination, authority, conflict and budget checks
still apply. Old task-class, binding and capability labels describe the source
execution; they do not automatically restrict reuse on another task or harness.
Use lesson applicability pins when those restrictions are necessary. A matching
signature never promotes ordinary testimony or establishes a successful repair.
Relevant ordinary testimony refused for consequential use still creates protected
blocked demand, including when the text query shares no words with the lesson.

An exact quoted-text match or a signature match gives an optional candidate one
preference ahead of ordinary lexical/semantic ordering. Matching both, repeated
proposals and repeated exposures do not add more weight. Mandatory context
precedes optional notes. The protected explanation retains one deterministic
proposal/review reference for a matched candidate. Body selection reasons label
the signature match; compact indexes retain their usual source previews.

Historical recompilation uses the retained review reference and ranking facts.
It can reproduce a previous selection after a review is reopened or a source
assessment is corrected. It does not reinterpret that history as current advice.
Body availability and pull eligibility still use their existing checks. Existing
handles do not become new searches when a review changes; search again for current
ranking. Retained execution requires the same supplied signature as its package.

Migration 033 adds the review sharing flag with a false default, including writes
from older binaries. Signature searches seal `cairn.semantic/12` with
`lexical-scope-recency/6` or ready `semantic-scope-recency/3`. Searches without a
signature keep their existing schema and ranking profiles. Upgrade the API,
ordinary facades and direct readers before using the new field; old clients can
discard fields they do not understand. General metadata redaction and retention
remain separate roadmap work.

The feature provides a reuse path for reviewed failures. Its tests establish
retrieval behavior, not reduced recurrence or durable task benefit.
A [retained real-failure check](verification/real-signature-retrieval-2026-09-10.md)
also retrieves the original local lesson after a new exact-version review, with
its historical revision supplied. Ordinary text search already ranks that lesson
first for the three diagnostic queries; the check adds no task-benefit claim.
