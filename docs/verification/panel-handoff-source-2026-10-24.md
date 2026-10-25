# Authenticated handoff source setup, 2026-10-24

The `unfinished_handoff` source setup was run through the authenticated panel
controller against four separate databases in one fresh disposable PostgreSQL
cluster. Each run used the current worktree's built Cairn binary, migrated its
own database, and invoked `scripts/check_token_efficiency_panel_controller.py`
for one arm:

| Arm | Result |
| --- | --- |
| `baseline` | passed |
| `envelope` | passed |
| `batch` | passed |
| `combined` | passed |

The controller created the frozen A/self/shareable source through the sender's
authenticated API, then searched, pulled the whole source, and read its exact
history through the registered worker session. The checker verified source
identity, version, body hash, writer, and whole extent. The durable receipt
contains only those identifiers and hashes; it does not contain source body
text.

The receipt keeps `provider_called=false`, `native_turn_join=unimplemented`,
and `task_outcome=unassessed`. Its observed transition list is empty, while
`source_preparation.unrun_transitions` identifies the frozen
`before_resume/pause_after_source_check` boundary as unrun. No actor or provider
was launched. This verifies disposable source preparation and authenticated
recall only; it does not demonstrate a native pause/resume, report completion,
accepted task outcome, or token savings. The CAIRN-227 release decision remains
`defer`.

Validation: `make check` passed; Python syntax compilation passed; `git diff
--check` passed. No Store implementation changed, so the focused disposable
controller cases were run without the broad integration suite.
