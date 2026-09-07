# Currentness and historical recompilation

An ordinary record may include `draft.pins`: `revision` (immutable Git object ID),
`workspace_sha256`, `task_class`, `binding_id`, `capability_id`, `valid_from` and
`valid_until`. Unspecified constraints are unpinned. Ordinary edits and B
corrections cannot remove or change these constraints; scope broadening remains
an unimplemented authority transition. A validity interval is half-open.

Compile requests supply matching `context` labels. Missing context, mismatch and
outside-validity omissions have fixed census buckets. A mandatory applicable C
instruction cannot be skipped by omitting required context. These are declared
constraints; matching them does not certify physical workspace state. `search`
and `run` accept revision/workspace/task-class/binding/capability flags. The wrapper
uses one consistent tuple for compilation and run metadata.

Semantic schema `cairn.semantic/3` seals these context pins. Optional JSON/CBOR
fields preserve old v1/v2 decoding and seals. Legacy receipts remain historical;
a current request after compiler-version changes needs a new request identity.

## Recompile a retained read set

`cairn recompile` accepts:

```json
{"receipt_id":"RECEIPT_UUID","query":"the original query"}
```

The query is transient and must reproduce the retained intent digest. The command
loads the original considered versions and frozen eligibility facts, reads their
retained bytes, recomputes lexical ranking and budget packing, and checks the
result against the original seal. It creates no new exposure and cannot authorize
fresh delivery. Its cutoff is the named retrieval's actual read set, not an
arbitrary timestamp or a reusable PostgreSQL transaction handle.

Explanation version 2 retains body digests and the evidence/authority/attribution
facts needed for eligible candidates. This avoids duplicating their bodies while
preserving earlier gate decisions when evidence or grants change later. Body
corruption or a changed result fails integrity verification. Missing retained
versions or older explanation formats return `REPLAY_INCOMPLETE`; `replay` can
still inspect saved bytes where they remain intact. Neither command can recreate
purged material from a digest.

This implements historical recompilation for read sets captured by this compiler.
It does not invent the missing past observation history of older receipts or
establish measured usefulness on real incidents. Real-history evaluation must
separately distinguish original-time availability from later recurrence scenarios.

Lexical ranker v2 removes a fixed set of English function words. Domain tokens
and negation are preserved. A nonempty query containing only those function words
selects no optional advice and generates no blocked promotion demand. An explicitly
empty query remains an unfiltered scoped browse. Ranker v1 is retained for history.
