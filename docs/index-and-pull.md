# Index, body and evidence expansion

`cairn index` accepts the same JSON scope, query, context pins and budget as
`compile`. The authenticated agent API exposes `index`, `expand` and `expand-evidence` with its
configured destination. This is an explicit tool route; the H0 process wrapper
continues to compile full context before launch and refuses index mode.

An index contains mandatory instructions in full, plus optional pointers with
record/version, class, kind, a summary of at most 160 UTF-8 bytes and a body digest.
It uses the same currentness, authority, conflict, evidence and destination gates
as ordinary compilation. It fits at most 100 pointers into the existing optional
budget and reserves room for the handle envelope. Semantic format v4 seals the
index and bootstrap; opaque delivery handles stay outside that seal. Historical
recompilation reproduces the index without issuing new handles.

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
