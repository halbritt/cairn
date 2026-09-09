# Index, body and evidence expansion

`cairn index` accepts the same JSON scope, query, context pins and budget as
`compile`. The authenticated agent API exposes `index`, `expand` and `expand-evidence` with its
configured destination. This is an explicit tool route; the H0 process wrapper
continues to compile full context before launch and refuses index mode.

## Agent commands without request JSON

With a host-provisioned token and Unix socket, an agent can search directly:

```sh
cairn agent --socket /path/to/api.sock --token-file /path/to/agent.token \
  search --repo /path/to/repo --task TASK_ID --run RUN_ID 'relevant query'
```

Use the same host task/run identities across queries. They are required; the
repository defaults to the current directory. The token determines caller,
repository boundary and destination. Search does not accept a destination or
observer override and does not use database credentials. Optional flags are
`--tokens` (default 32000), `--request-id`, `--revision`, `--workspace-sha256`,
`--task-class`, `--binding` and `--capability`.

The `cairn.agent-search/1` view retains the index order and metadata, full
mandatory `selected` entries, scope/currentness/policy fields, omission counts
and session limits. Each index entry includes a complete `pull_command` with
the correct receipt and version-bound handle. Run that command to inspect the
body; it carries the executable/socket/token-file paths used by search. Paths
are shell-quoted. The token contents never appear in the command. The included
pull request UUID lets the exact displayed command be retried without spending
another credit, subject to the usual live checks. A repeated search can generate
new pull request UUIDs; retain the original command when retrying a pull.
Entries also include `pull_arguments` containing the same `request_id`,
`receipt_id` and `handle`. Native adapters can pass that object to `agent expand`
without parsing shell text; the command and structured form share one retry key.

This is a presentation view, not a new sealed semantic package. `source_schema`
and `source_seal` identify the underlying package; `receipt_id` identifies the
original retrieval for host observation. The entire encoded view is checked
against the declared input room. If command paths make it too large, it refuses
without truncating entries or mandatory context. The original index may already
be recorded; this refusal does not erase exposure history or prove delivery.

Body and evidence commands are also available directly:

```sh
cairn agent --socket /path/to/api.sock --token-file /path/to/agent.token \
  pull --request-id NEW_UUID RECEIPT_UUID HANDLE_UUID
cairn agent --socket /path/to/api.sock --token-file /path/to/agent.token \
  pull-evidence --request-id NEW_UUID RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256
```

The evidence ID and expected SHA-256 come from the pulled selection's attached
evidence metadata. These commands return the existing expansion response. A
missing request ID generates a new one; preserve an explicit ID for retries.
The raw JSON `agent index`, `expand` and `expand-evidence` operations remain
available. A host may [link the retrieval to its run](use-outcome-loop.md#retrieval-during-an-observed-run);
search itself does not assert that association or task acceptance.

## Index and expansion contract

An index contains mandatory instructions in full, plus optional pointers with
record/version, class, kind, a summary of at most 160 UTF-8 bytes and a body digest.
It uses the same currentness, authority, conflict, evidence and destination gates
as ordinary compilation. It fits at most 100 pointers into the existing optional
budget and reserves room for the handle envelope. Semantic format v5 seals the
index and bootstrap; opaque delivery handles stay outside that seal. Historical
recompilation reproduces the index without issuing new handles, including the
prefix previews in older v4 packages.

Search previews show a matching passage when it contains more distinct query
terms than the note's opening 160 bytes. They use the ranker's existing word and
identifier matching, preserve exact source text, and mark omitted context with
`...`. Empty queries, unmatched queries and ties keep the opening preview.
The 160-byte limit includes omission markers. A preview can cut an explanation
short: pull the body before applying its advice. The
[verification report](verification/index-previews-2026-09-08.md) records the
observed problem and compatibility checks.

The result supplies `package`, `handles`, `expires_at`, `credits_remaining` and
`bytes_remaining`. Each handle is bound to one authenticated caller, receipt,
record version and destination. A session lasts 15 minutes, permits four body/evidence
pulls in total, and reserves at most 24,000 additional bytes within its declared context
room. Limits use the same conservative UTF-8-byte upper bound as compilation.
These are per-retrieval limits; a real host must account for its overall task
context and control when it starts another retrieval.

Submit a pull as JSON:

```json
{"request_id":"NEW_UUID","receipt_id":"INDEX_RECEIPT_UUID","handle":"OPAQUE_HANDLE_UUID"}
```

`cairn expand` is local operator inspection. `cairn agent expand` uses the
configured agent identity and destination. The response includes the selection,
its evidence/authority metadata, and remaining session credits and bytes.
To inspect one attached evidence object, use `cairn agent expand-evidence` with
its `evidence_id` and `sha256` from the selection's evidence metadata:

```json
{"request_id":"NEW_UUID","receipt_id":"INDEX_RECEIPT_UUID","handle":"OPAQUE_HANDLE_UUID","evidence_id":"ATTACHED_EVIDENCE_UUID","expected_sha256":"CAPTURED_EVIDENCE_SHA256"}
```

The response identifies the record/version and returns `evidence` with its exact
captured body, expected/actual digests, witness and check state. Non-UTF-8 bytes
use `body_base64`. Evidence must belong to that exact current record version and
repository, match the expected digest, remain resolvable, and permit the configured
destination. A shareable record does not override local evidence sensitivity.
Unavailable/divergent objects refuse; no external file or locator is opened.
The expected digest also binds retries if an object's body and stored digest
change together.

Evidence pulls share the same credits and bytes as body pulls, including encoded
metadata and a response-envelope allowance. Oversized objects refuse intact and
do not spend a credit. Identical transport retries return the prior response
without another spend while repeating live authorization and availability checks.
`cairn expand-evidence` is the local operator form. The existing `cairn evidence ID`
remains separate local inspection and does not consume this retrieval budget.

New body-pull responses describe their reason as
`indexed record; current eligibility revalidated`. The recheck does not have the
original query, so it does not report a new lexical match count. Original ranking
features remain in the protected retrieval explanation. Previously committed
pull responses retain their original text when returned for the same request UUID.

Every pull rechecks current version, body digest, eligibility, scope pins and
mandatory bootstrap before returning bytes. New mandatory instructions, revoked
authority, expired sessions and changed records require a fresh index. Transport
retries do not spend another credit, but still pass those live checks before
returning the earlier committed response. A receipt retry is not a permanent
right to disclose old content.

The use report retains `exposure_kind: index` for the original pointer. A later
pull appends an instrumented `expanded` observation with method
`authorized-body-pull/1` or `authorized-evidence-pull/1`; it does not rewrite the earlier exposure as though a
full body had been present originally. Pull observations invalidate earlier
retraction previews. They establish that a body or supporting object was requested and made available,
not that a model read it or that the task improved. HTTP response loss can still
leave an observed committed pull; a same-request retry recovers that result.

Forgetting the exposing record excludes and purges its cached evidence-pull
responses alongside other retained copies. The independently captured evidence
object remains a separately retained deletion residual. Recovery inspection
checks the response exclusion too.

Before admitting traffic to a restored database, follow the
[restore-session procedure](restore-admission.md). `begin-restore` pauses ordinary
work and fences previous delivery; explicit verified resume requires fresh
compilation. Standalone `invalidate-handles` expires only index sessions and
cannot establish restore admission.

The existing local `get` command/API remains advisory inspection, and full
`compile` remains available. The new credits govern index expansion, not every
possible source of context. No adapter can yet attest to H1 model tool routing,
mid-run refresh, or a global task budget.
