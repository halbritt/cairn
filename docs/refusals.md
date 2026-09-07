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
