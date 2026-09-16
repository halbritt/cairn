# Agent sessions

Status: session registry, native presence and turn-boundary inbox adapters, 2026-09-15. Pool dispatch and remaining operations stay
in the [coordination plan](plans/agent-coordination-v1.md).

## Identity on the trusted host

A continuing conversation has a store-assigned UUID, an `agent-N` display name
and an inbox `agent/UUID`. Harness, model, project, workspace and task are reported
context. They do not define identity. Two conversations under one account have
different inboxes. Two account homes use different launcher bindings, so identical
native session IDs do not merge them.

Registration uses an existing Cairn profile token. No session credentials or
provider logins are created. Requests select their session with its agent UUID
and current execution UUID. Cairn checks the registered profile association on
every transaction, including retries. This prevents accidental routing through
another profile or an obsolete execution; it is not isolation between hostile
local agents.

Registering the same binding and native session ID with a new request UUID resumes
its existing agent UUID with a new execution UUID. A fork needs a different native
session ID. Retrying identical registration JSON with the original request UUID
returns the original response. A resume fences earlier executions, including their
completion attempts. A held wake attempt must first be stopped and reconciled.

Ordinals are display aids and are not reused within retained database history.
Restoring an older backup can discard later ordinals. Store canonical UUIDs in
references. Binding/account configuration is not included in directory responses.
Native session references and selected task descriptions are directory metadata;
do not put credentials or transcripts in them.

## CLI

```sh
cairn agents register --profile PROFILE --request-id NEW_UUID \
  --binding codex-default --native-session NATIVE_THREAD \
  --harness codex --model MODEL --project rhumb --workspace /work/rhumb \
  --task 'Implement the chart editor' --state busy

cairn agents list --profile PROFILE --harness codex --project rhumb

cairn agents heartbeat --profile PROFILE --agent-id AGENT_UUID \
  --execution-id EXECUTION_UUID

cairn inbox next --profile PROFILE --agent-id AGENT_UUID \
  --execution-id EXECUTION_UUID

cairn agents leave --profile PROFILE --agent-id AGENT_UUID \
  --execution-id EXECUTION_UUID
```

`--profile` selects an existing event profile. `--token-file` selects another
existing profile. Connection flags and options precede positional values.
Ordinary event commands accept both session-selection flags. Without them, the
profile's original inbox and producer identity remain in use. Ordinary memory
clients continue using their existing profile by default.

`agents context` replaces metadata with `--expected-revision` and a request UUID.
A stale revision fails with `VERSION_CONFLICT`; refresh context before retrying.
Heartbeats update only presence, never context. Use a heartbeat every 30 seconds;
expiry is 90 seconds by database time. Leaving marks the execution stopped and
requires registration to resume. These operations do not themselves install a
heartbeat process or reach a native conversation.

Directory reads default to live sessions. `--include-offline` includes stopped,
expired and pre-restore records. Harness/project match case-insensitively and
exactly; model matches observed model when supplied, otherwise configured model.
Explicit `--project-alias NAME,...` values also match the project selector
case-insensitively. Up to eight distinct aliases are allowed. Other selectors
match exactly. Paging uses `--after ORDINAL` and `--limit N`.
Entries label context `metadata_source: reported`. A directory result is a
snapshot. Use a resolution for publication that requires current context.

## Resolve and publish

```sh
cairn agents resolve --profile PROFILE --harness codex --project rhumb > match.json
jq -e '.data.resolution // error("recipient is not unique and live")' match.json > selected.json
cairn publish --profile PROFILE --request-id NEW_UUID --resolution selected.json \
  --kind request --version SOURCE_VERSION SOURCE_UUID
```

Resolution returns `unique`, `ambiguous`, `no-match` or `stale`. Only `unique`
includes a resolution object. Ambiguous/offline results provide up to 100 candidate
entries, including display names, workspace and task; `more` signals truncation.
Use paged directory reads to inspect further candidates. Offline candidates are
diagnostic: if registration changes during the two reads, resolve again.
No outcome launches a worker or sends a message by itself.

The resolution pins a concrete UUID, execution, database generation and context
revision. Publication locks the selected session, checks those fields and current
presence, and creates the event/delivery in one transaction. A changed context,
replaced execution, expiry, restore or inaccessible recipient returns
`STALE_RESOLUTION` with no event. Resolve again for a new intended publication.
A shareable event cannot retain a local-only session resolution.

Retain the original resolution and publication request UUID when the response is
uncertain. An exact retry returns the committed event and original recipient,
even if that session has since changed. Never re-resolve a retry to another agent.
`--to agent/UUID` deliberately bypasses the presence/context check for offline
mail. Historical events retain the accepted resolution; it proves only the
routing check, not delivery to a native conversation or successful handling.

## API and recovery

The existing Unix API exposes `agent-register`, `agent-context`,
`agent-heartbeat`, `agent-leave`, `agent-directory` and `agent-resolve`. Each has JSON help under
`cairn agent OPERATION --help`. Session-selected event requests use headers
`Cairn-Agent-ID` and `Cairn-Execution-ID`; both must be nonempty canonical UUIDs.
Session lifecycle operations use the base profile and explicit references in the
request. Existing collection and local/hosted restrictions apply to metadata.

Migration 037 adds the registry; 038 retains accepted event resolutions. Backups retain session identity and metadata.
The existing restore fence changes the database generation: restored presence
becomes ineligible for live lookup, and old executions cannot heartbeat or act.
Register with a new request UUID after restore. API restart alone preserves
identity, presence expiry and inbox history.

## Native presence adapters

`scripts/install-agent-coordination.py` installs separate coordination hooks and
one `cairn-presence.service` watcher. Supply an existing token file and a distinct
`--binding` for each account home. Codex and Claude hooks also check their active
configuration home, so merged hook layers cannot register a conversation under
another account. Codex hook trust is recorded through its installed app-server
API for the four exact Cairn commands. Other hooks and settings are preserved.

```sh
python3 scripts/install-agent-coordination.py \
  --harness codex --binding codex-two --settings "$HOME/.codex-harm/hooks.json"
```

For Claude, `--settings` names its `settings.json`; for Agy, its `hooks.json`.
For OpenCode and Hermes it names the configuration directory. Start a fresh
native process to load installed hooks. Hermes CLI/gateway require restart.

With `--idle-wakeup`, installation also covers the selected account home with
Herdr's own native integration: it checks `herdr integration status` and runs
the installed `herdr integration install <target>` when that integration is
missing or outdated, never copying Herdr's private assets. Claude selects the
home through `CLAUDE_CONFIG_DIR`, Agy through `ANTIGRAVITY_CLI_CONFIG_DIR` and
Hermes through `HERMES_HOME`; OpenCode has one integration home at
`~/.config/opencode`, and an OpenCode `--settings` directory elsewhere is
refused because Herdr cannot observe it. Codex needs no Herdr hooks: idle
matching identifies it by its unique open rollout file. Both installers
preserve unrelated hooks and settings.

Hook installation follows `CLAUDE_CONFIG_DIR`. The installed Claude 2.1.273
still lists memory from the default `~/.claude/CLAUDE.md` in the second
account, so a configured directory is not proof that second-home memory loads.
Omit the variable when launching the default account: an explicit `~/.claude`
there redirects onboarding JSON and falsely triggers login. Native inbox
handling remains an owner-authorized read/completion/reply capability; the
owner-installed trusted-host coordination guidance in each account's
`CLAUDE.md` describes that workflow. This installer never modifies memory files.

Hooks associate the real native conversation ID with a process PID, start time
and host boot ID. The watcher heartbeats every 30 seconds while that process
lives. A dead process is stopped; unavailable API calls allow the existing
90-second presence to expire. Neither watcher retries nor late leave hooks can
replace or stop a newer execution. A real hook can resume the unchanged native
session after a database restore; the watcher cannot do that by itself.

Codex/Claude use SessionStart, UserPromptSubmit, Stop and SessionEnd. Agy uses
flat PreInvocation/Stop handlers and its `fullyIdle` observation. OpenCode uses
request transformation and idle/delete/dispose events. Hermes uses pre/post-turn
hooks and request middleware. Hermes gateway presence ends with each agent turn:
the gateway PID alone does not establish an idle conversation's reachability.

Context injection identifies the agent and execution using the existing profile.
Model/workspace/state observations preserve the selected task summary and project
aliases, except that changing workspace resets the inferred project and aliases.
No prompt or transcript is retained by coordination. Hermes/OpenCode inject only
into request copies. `.cairn-no-coordination`, `.cairn-no-memory` and the existing
lifecycle disable/child controls suppress hook registration. Fresh wake workers
with `CAIRN_WAKE_CONTEXT` retain their separate supervisor lifecycle.

Presence does not imply immediate idle-process wakeup. Opt-in
[native inbox delivery](native-inbox.md) consumes queued events at supported
turn boundaries with a durable ownership hold and explicit completion.
Known metadata is reported context, not proof that an agent is working correctly.

Tests use disposable PostgreSQL clusters and the real Unix API/CLI. They cover
resume, same-account separation, context revision checks, visibility, execution
fencing, API restart and backup/restore. Native probes exercise installed Codex,
Claude, OpenCode and Hermes with fixture-only providers, plus an explicitly opted-in
Agy model turn. See the [verification report](verification/agent-sessions-2026-09-15.md).
These checks do not establish useful cross-agent task completion.
