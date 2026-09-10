# Selected additive note maintenance — 2026-09-10

Cairn can append selected guidance to an active ordinary note without asking the
caller to reproduce its existing body. The core operation, authenticated API,
CLI, MCP and native OpenCode share the existing revision transaction. Native
clients select `append` on `cairn_edit`; the tool count remains six. No migration
or new authority path is introduced.

## Why this change

The [maintenance review](procedure-maintenance-2026-09-09.md) retained concrete
examples of instructions lost while rewriting procedures. Append does not solve
consolidation, but an additive update need not carry that same reproduction risk.
The caller supplies just the suffix, including whitespace. The combined body
must fit 65,536 bytes. Stored text, draft metadata and source citations remain;
a new version records the authenticated writer. Responses contain identifiers,
without echoing the body. See the [interface contract](../local-api.md#append-selected-guidance).

The alternatives remain useful: replace text when correcting or consolidating,
and create a separate note for an independent topic. Automatic merging would
introduce a semantic policy without evidence that it is needed. Append can still
add contradictions, redundant wording or obsolete guidance; read the current note
and verify the proposed addition against its sources.

## Verification

The first authenticated API test failed because the endpoint was absent, then
passed with exact old-plus-suffix bytes and a stable retry. Store tests cover
preserved metadata and earlier bodies, retry after a later replacement, changed
intent, stale versions, concurrent append/full edits and competing appenders,
combined UTF-8 byte limits, failure rollback, degraded citations and B/C refusal.
A metadata test includes pins, entities, relations and attribution.

The disposable PostgreSQL integration suite and static checks pass. The 40 Python
tests pass. CLI transport checks include a maximally escaped suffix at the body
limit and unchanged retries for mutations created with the previously installed
binary. MCP tests exercise the authenticated tool, compact response and exclusive
edit modes. The real OpenCode 1.18.21 client also passed both direct tool checks and a
normal session with scripted loopback responses, including append/retry, compact
output, ambiguous-mode refusal and invalid suffix types. No model inference was
used. Installation is recorded below when complete.

Early tests had invalid fixtures: a shareable note referenced a local source,
qualifying evidence was local, and an instruction omitted its required policy
key. Existing validation correctly refused them; the fixtures were corrected.
The first native run also loaded an older test helper before its expected-error
assertion was updated. It returned the intended ambiguous-mode error, but the
loaded assertion expected a generic type-validation error. The failed logs are
retained under `/tmp/cairn-note-append-20260910`; they are not passing evidence.

## Limits

This adds an exact operation for additive maintenance. It does not establish
faster completed tasks, lower total context use, independent cross-harness
benefit or positive net value. Earlier full-body edits already returned compact
identifiers. The improvement is avoiding reproduction of unchanged input text;
reading, selecting and verifying guidance still has a cost. General task-value
requirements stay open, including qualitative and cumulative evidence.

## Decision provenance

[Verification metadata](note-append-2026-09-10.json) retains source and log hashes,
including failed checks. Pincite packet `pkt-60a6733bfe653db3` used corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, and release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. The validated receipt records
alternatives, delegated authority and reopening conditions. All three retrieval
citation loops are closed. Four Go-interface obligations remain nonmaterial:
this change adds no interface or substitution boundary. The original packet and
typed observations remain private under `/tmp/cairn-note-append-20260910`.
