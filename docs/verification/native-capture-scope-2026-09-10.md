# Native capture scope — 2026-09-10

Native MCP and OpenCode callers can save selected guidance for their current
task or run. Before this change, native `cairn_remember` always created a
repository-wide note, while ordinary CLI/API capture already supported narrower
scope. This closes that interface gap. It does not establish that the additional
choice improves model task outcomes.

## Behavior

`scope` accepts `repository`, `task` or `run`; omission preserves repository-wide
capture. Task capture uses the host search task and wildcard run. Run capture uses
both host search labels. Fixed MCP configuration, Codex thread metadata or native
OpenCode settings/session defaults determine those labels. There is no second
caller-controlled set of task/run IDs. Repository capture keeps its independence
from native metadata. The [MCP guide](../mcp.md#choose-capture-scope) and
[OpenCode guide](../opencode-tools.md) explain configuration and retries.

Existing A attribution, local sensitivity default, explicit pins/associations,
API authentication and ordinary edit restrictions remain. Search context is not
inherited. Effective stored scope participates in the existing create request
identity: retries preserve it, and changed drafts refuse. A task capture remains
the same draft when only the host's run changes.

## Checks

Tests use disposable PostgreSQL databases and synthetic notes. MCP checks use the
real SDK transport and authenticated Unix-socket API. Native OpenCode 1.18.21
checks execute actual custom tools with model inference disabled.

- Fixed and Codex-thread MCP capture retain exact selected scope, A testimony
  and authenticated writer. Repository defaults need no thread metadata.
- Fresh hosts retrieve and pull repository/task/run notes according to scope;
  hosted readers omit local notes. Invalid choices, arbitrary task overrides and
  invalid/missing required thread metadata refuse.
- Actual OpenCode capture uses configured task/run or its native session labels.
  Task capture can be retried after a run change and retrieved in a fresh session.
  Another task omits it; another run omits a run-specific note. Changed effective
  scope under the same UUID refuses.
- Default and explicit repository capture remain broadly applicable. Context pins
  are not inherited, including the native fixture's configured task phase.

The first MCP and native tests rejected the new scope argument before production
changes. Expanded fixtures then exposed three test mistakes: adding broad notes
before an existing browse assertion changed its inventory; a shared lexical
marker selected unrelated fixtures and exhausted pull credits; identical task/run
bodies triggered ordinary redundancy suppression. Tests now run after the older
inventory assertions and use unique queries and distinct note bodies. Production
matching, deduplication and budgets were unchanged to accommodate these tests.

The [evidence manifest](native-capture-scope-2026-09-10.json) retains completed
check results and their hashes, including failed fixture runs. Raw scratch data
is under `/tmp/cairn-capture-scope-20260910`; it is not a durable archive.

## Decision and limits

The owner delegated routine implementation and integration through the active
Cairn goal. The change extends the existing adapters and create contract. Keeping
CLI-only narrow capture leaves native callers unable to express it. Automatic
scope inheritance would change existing repository capture; arbitrary labels
would duplicate the host's configuration. The explicit choice preserves defaults
while making the existing applicability available.

Pincite informed interface placement and preservation of existing behavior.
The manifest identifies the validated release, packet, decision receipt and
nonmaterial obligations about new architecture boundaries, Go interfaces and
representation/optimization choices. None of those changes is proposed here.

These checks establish capture and retrieval behavior, with no answering-model
trial or incremental memory-benefit claim. Host labels are declared groupings;
independent execution, automatic task propagation and sustained task-value
assessment remain separate requirements. Upgrade the MCP binary and restart
its host process, or update the bundled native OpenCode adapter. Existing API
and database contracts suffice; ordinary edits still cannot change note scope.
