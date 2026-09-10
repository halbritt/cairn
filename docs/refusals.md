# Durable policy refusals

Compiler budget/conflict/unenforceable-policy failures and blocked B demotions or
retractions now return a `refusal_id` after retaining an observation. The refused
operation commits no content or authority change. Its denial is recorded in a
separate transaction before the error returns. A database or process failure can
still prevent that observation from committing; Cairn never claims a durable ID
in that case, and a failed observation write returns `REFUSAL_UNRECORDED`.

A refusal retains caller, request identity/digest, operation, scope, destination,
status, bounded considered record/version IDs, query digest when applicable, and
a source snapshot label for compiler refusals. It does not retain raw queries or
record bodies. The trace is explicitly partial: refusal can stop selection early,
and at most 1,000 considered references are retained with the observed count.
This is not a complete historical candidate explanation or a replayable decision.

New compiler refusals retain `explanation_version: 2` and up to 1,000
`candidates`, ordered by record ID/version to match the considered references.
Each entry contains the reason and ranking/allocation features computed before
the refusal: lexical matches, optional semantic score, scope specificity,
timestamp, mandatory flag, rank and cost when available. Evidence/grant snapshots
and bodies are excluded. `EVALUATION_INCOMPLETE` means processing stopped before
a candidate reason was assigned; zero rank/cost can mean allocation was not
reached. These are diagnostic observations from an aborted compilation. A
`SELECTED` reason does not mean a package was committed or delivered.

Version 2 preserves known mandatory flags and gate results before an instruction
aborts collection. When missing context is the stopping cause, its visible instruction is labelled
`CONTEXT_MISSING`; an unsupported mandatory runtime requirement or unavailable
required support retains `POLICY_UNENFORCEABLE`; a disputed instruction retains
`OPEN_CONFLICT`. Its mandatory flag describes the stored instruction, not a
successful admission. A mandatory category-limit failure is labelled
`CATEGORY_BUDGET`, rather than `SELECTED` or `EVALUATION_INCOMPLETE`.

These labels describe only candidates actually inspected and retained within the
existing cap. Collection still stops at the first binding failure; later records
are not retrospectively evaluated. Private instructions still have no candidate
ID or diagnostic entry in a hosted-destination trace. Version 1 observations may
lack these known fields. Exact retries keep the original observation and do not
upgrade or rewrite its historical explanation.
The refusal also records `available_tokens`, plus `optional_limit` and `ranking`
when compilation reached their initialization. These use the compiler's recorded
conservative byte-based token bound, not an independently measured model tokenizer.

The observed `considered_count` can exceed the retained 1,000-entry prefix.
`trace_complete` remains false even when all observed candidates fit, because
selection or packing may have stopped early. Older and non-compiler refusals
report `explanation_version: 0` with no reconstructed candidate details.
Refusal observations have no automatic expiry or purge command; the per-observation
cap does not bound total database growth. These diagnostic fields follow that
existing retention lifetime.

Identical caller/operation/request/intent/status refusals reuse the original
observation. Changed state can allow a later retry to succeed; the older refusal
remains historical. Start new intent with a new request UUID. This grouping does
not treat repeated denials as new evidence or confer authority.

`cairn refusal REFUSAL_UUID` inspects a refusal owned by the local CLI caller.
An authenticated local agent uses `cairn agent refusal` with JSON `refusal_id`.
Only the original caller can read the protected detail. Hosted API profiles get
the opaque error identifier but cannot inspect protected refusal traces.

The feature covers these implemented policy boundaries. Invalid payloads,
authentication failures, arbitrary storage errors, and every possible adapter or
expansion failure are not automatically retained. General refusal analytics,
review dispositions and the remaining class D/governed-policy paths remain open.
