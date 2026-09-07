# Index and body expansion

`cairn index` accepts the same JSON scope, query, context pins and budget as
`compile`. The authenticated agent API exposes `index` and `expand` with its
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
record version and destination. A session lasts 15 minutes, permits four body
pulls, and reserves at most 24,000 additional bytes within its declared context
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
Evidence bytes remain separately selected local inspection; this endpoint does
not expand arbitrary files or locators. An oversized body is refused intact.

Every pull rechecks current version, body digest, eligibility, scope pins and
mandatory bootstrap before returning bytes. New mandatory instructions, revoked
authority, expired sessions and changed records require a fresh index. Transport
retries do not spend another credit, but still pass those live checks before
returning the earlier committed response. A receipt retry is not a permanent
right to disclose old content.

The use report retains `exposure_kind: index` for the original pointer. A later
pull appends an instrumented `expanded` observation with method
`authorized-body-pull/1`; it does not rewrite the earlier exposure as though a
full body had been present originally. Pull observations invalidate earlier
retraction previews. They establish that a body was requested and made available,
not that a model read it or that the task improved. HTTP response loss can still
leave an observed committed pull; a same-request retry recovers that result.

Before admitting traffic to a restored database, run `cairn invalidate-handles`
with JSON `request_id` and `reason`. This expires all restored sessions; retrieve
fresh context afterward. It requires an unscoped operator identity and is absent
from the agent API. It does not reconcile newer revoked grants or deletion effects.

The existing local `get` command/API remains advisory inspection, and full
`compile` remains available. The new credits govern index expansion, not every
possible source of context. No adapter can yet attest to H1 model tool routing,
mid-run refresh, or a global task budget.
