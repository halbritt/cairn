# Native Codex compact with a pending notice (2026-10-24)

The bounded fixture in [`check_codex_recovery_fixture_disposable.py`](../../scripts/check_codex_recovery_fixture_disposable.py)
uses Codex 0.164.0, a private `CODEX_HOME`, disposable PostgreSQL, and a
loopback Responses stub. Both staged coordination and memory hook command
families are trusted and wrapped. The lifecycle selector is a fixture-only
local executable. The run contacted no external provider and observed no
provider usage.

The sequence registered a native session and waited for its matching
`turn/completed`. It then published one notice, confirmed that the delivery
was pending with zero attempts, and called `thread/compact/start`. The method
returned an empty acceptance object. The fixture drained app-server
notifications until the compact turn reached its matching `turn/completed`.
The compact `PreCompact` hook exited 0. The same notice remained pending with
the same delivery ID and zero attempts through that terminal boundary.

On the continuation, `SessionStart(source=compact)` and
`UserPromptSubmit` both exited 0. The compact SessionStart has no turn ID. The
coordination path therefore admitted the notice at session scope, with no
`native_turn_id`; the Store claim contract requires the request ID and session,
while a native turn binding is optional. The fixture preserves that null value
and does not rewrite the attempt as turn-bound. The continuation then read the
exact whole source and settled the same delivery as handled with one attempt.
Five scripted Responses requests reached the local stub.

The compact notifier initially appeared absent because the fixture's observer
moved notifications into a side queue only while an RPC call was waiting. The
reader now routes notifications continuously. This was a fixture observer bug,
not a Codex or Cairn source defect.

This result establishes native compact sequencing, lifecycle callbacks, and
pending notice identity through compact completion in this fixture. It does
not establish accepted model work, provider use or cost, broader memory value,
or a canary and rollback. CAIRN-224 remains open for those acceptance claims.
