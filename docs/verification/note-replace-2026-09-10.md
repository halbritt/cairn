# Exact passage correction — 2026-09-10

Agents can correct one passage in an ordinary note without reconstructing its
other instructions. The existing `cairn_edit` tool accepts
`replace: {old_text, new_text}` through MCP and native OpenCode. The CLI/API
operation is `replace`. It returns the new record ID and version without echoing
the body. See the [request contract](../local-api.md#replace-one-exact-passage).

## Why this change

The recorded [procedure maintenance](cumulative-maintenance-value-2026-09-09.md)
lost unrelated instructions during full-body consolidation. Retained history
helped recover them. Full-body editing remains necessary for consolidation, and
append now handles additions, but neither expresses one targeted correction.
Exact replacement avoids resending and reconstructing text outside that passage.
It does not prevent an incorrect selected correction or resolve contradictions.

The initial API regression returned `NOT_FOUND: unknown endpoint`. The new
operation performs one literal match inside the existing serializable ordinary
edit transaction. Missing, repeated and overlapping matches refuse. No fuzzy
matching, automatic merge, new harness, database migration or background
maintenance is introduced.

## Preserved behavior

The old passage must match exactly once in the expected active A version.
Whitespace, case and Unicode normalization are not changed. The caller supplies
`new_text`; an explicit empty string can remove a passage if the note remains
nonblank. Each text field and the resulting body retain a 65,536-byte limit;
the two-field JSON envelope allows 1 MiB for escaping.

The remaining body bytes, scope, applicability, entities and citations survive.
Previous versions remain inspectable. Existing authority and repository checks
apply. A stale expected version refuses before matching, and the ordinary edit
locks and rechecks that version before committing. An identical request returns
its original result after later edits; changed intent conflicts. Earlier mutation
operations retain their own canonical request identities.

## Verification and limits

The disposable PostgreSQL race/API/MCP integration suite, static checks and 40
Python checks pass. Tests cover exact text and metadata preservation, retained
history and citations, unique and overlapping matches, explicit passage removal,
byte limits, missing replacement text, retries after later edits, changed intent,
concurrent full edits and authority/repository refusals. Public agent and operator
CLI checks send both maximally escaped text fields. Additional MCP checks cover
ambiguous edit modes and malformed replacement arguments.

An initial core test used nonexistent evidence/history field names and failed to
compile. It was corrected against the actual source types; the failed log remains
retained. This was a test-authoring error, separate from the absent API operation.

The owner-priority and evaluation notes were retrieved and checked against the
current roadmap during work selection. This feature provides a correction
capability motivated by recorded maintenance trouble; no independent model-task
benefit, omission reduction or net savings has been measured. The earlier
maintenance failures and broader task-value requirements remain open.

[Verification metadata](note-replace-2026-09-10.json) records source and check
hashes, doctrine provenance and remaining obligations. Local working evidence is
retained under `/tmp/cairn-note-replace-20260910`.


Native OpenCode verification also passes using the actual 1.18.21 harness and
scripted loopback completions, with no model inference. It covers exact passage
replacement and identical retry, malformed replacement arguments, surrounding
text preservation and the existing ordinary-tool workflows. Previous installed
CLI mutation responses still retry through the new binary. The checks use a
disposable store and isolated harness configuration.

Pincite packet `pkt-973a8ef54d989d3b` has a schema-validated decision receipt
and three closed citation loops. Its 21 remaining domain-model and Go-interface
obligations are nonmaterial to this change; the metadata retains each reason.
No entity/lifecycle semantics or interface hierarchy is redesigned.


## Installation and CI


[CI 34531223764](https://github.com/halbritt/cairn/actions/runs/34531223764)
passed both jobs and every recorded step on clean `96e8eae875a85650b2547f680455c0ae24a93233`.
Installed that source in the CLI and API after a store backup, together with the
matching native OpenCode adapter. The API restarted at PID 3716773; PostgreSQL
remained at PID 163669 and migration 034. All 78 retained record versions and
their combined digest are unchanged. Credentials, host configuration, recent-file
plugin and semantic-worker settings retain their prior hashes.

A fresh installed MCP initialization advertises `replace` on the existing edit
tool, with six ordinary tools still available. The facade, CLI and API report the
installed source. No operational note was changed for verification. This records
installation and the tested correction capability; downstream task benefit and
net savings remain unmeasured.
