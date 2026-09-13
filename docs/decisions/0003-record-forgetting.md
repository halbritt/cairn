# Record forgetting and retained copies

Decision: implement record-body forgetting as a Class D transition with immediate
logical exclusion and durable database purge effects. This is a partial delivery
of design §10 and roadmap L5. The user delegated implementation decisions; no
operational forgetting request was made or executed during development.

The current store retains three distinct body copies: record versions, canonical
retrieval packages (including index summaries), and mutation response JSON.
Changing only the current record would still disclose old content through replay
and retries. Before physical SQL purge, each copy receives a deletion reference;
read paths refuse unavailable payloads while keeping identity and use history.
The new record version is a tombstone. Cited A uses the same audited D path.

The core owns the transaction that validates the live `redact` grant, expected
version, caller-owned impact/copy preview and conflict guard, and commits the
matching event, tombstone, exclusion flags and effect inventory. Each database
purge effect and its completion share a separate transaction. A crash rolls back
that effect. SQL failure observations are separately retained after rollback;
if that fails too, the operation reports the missing observation explicitly.
This extends the existing PostgreSQL owner rather than introducing a broker.

Copies outside these database fields remain visible as residuals. A record-level
request cannot infer permission to delete shared evidence or determine which
parts of dependent records repeat its content. Known dependent versions are
flagged and excluded from compilation until review/revision; mandatory affected
instructions refuse the package. Open-conflict forgetting remains refused.
Metadata/evidence redaction and managed-file effects need their own contracts.

Alternatives considered: leaving old responses readable fails the accepted
forgetting requirement; nulling payloads without explicit state would convert a
known deletion into a decoding/integrity error or false retry success; deleting
whole related records would erase material outside the selected subject. Global
physical erasure cannot be established by SQL updates or an unsigned checkpoint.

The implementation preserves previous semantic seals and history until an
explicit D request excludes their material. Historical inspection reports the
loss; it never reconstructs bytes from a digest. The upgrade preserves existing
records. Tests use disposable databases, including a real killed CLI worker and
restored pending effects. Installation does not automatically purge anything.

Reopen the design when adding managed copies, metadata/evidence redaction,
retention, external failure recovery, or post-backup security reconciliation.
Those remain required work, not acceptance inferred from current green checks.

Pincite's validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`
(corpus `corpus-2026-07-12-a11702cc9217`, doctrine
`doctrine-f6bbb5196a3f8bf9`) supplied the bounded atomicity, temporal-completion,
explicit-failure and authority review. Repository contracts governed the choice.
The typed decision receipt is retained locally at
`/tmp/cairn-deletion-decision-receipt.json`; its unresolved generic obligations
are classified there. No cost, performance, physical-erasure or usefulness claim
is made from these implementation tests.
