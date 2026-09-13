# Hermes shared memory

The Cairn provider connects interactive Hermes CLI and messaging gateway turns
to the shared lifecycle engine. Explicit search, pull, remember and edit use
Hermes's ordinary MCP tools; selected capture uses the installed Claude selector
without changing Hermes's task model. The adapter occupies Hermes's one external
memory-provider slot. Built-in `MEMORY.md` and `USER.md` remain independent.
Native memory writes are not mirrored or bulk imported.

## Installation

Use the Python environment belonging to Hermes (it supplies PyYAML):

```sh
~/.hermes/hermes-agent/venv/bin/python scripts/install-hermes-integration.py
```

Defaults use `~/.hermes`, the existing hosted connection in
`~/.config/opencode/cairn.json`, the installed Claude binary/model, and the shared
Cairn skill in `~/.codex-harm/skills/cairn/SKILL.md`. Override `--hermes-home` for
each separate profile, `--native-config` for another existing hosted profile, or
`--skill`, `--claude` and `--model` for their installed locations. The collection's
`repo` remains independent of the Hermes working directory.

The installer copies the adapter and engine into `plugins/cairn`, writes
`cairn-lifecycle.json` and `cairn/engine.json`, and installs `skills/cairn/SKILL.md`.
It also installs and enables the `cairn-controls` commands plugin.
It adds `mcp_servers.cairn` and `memory.provider: cairn`, retaining other settings.
It refuses to replace another external provider. The first original configuration
is backed up as `config.yaml.before-cairn`; subsequent installation is idempotent.
Configuration is written atomically as JSON, which Hermes's YAML reader accepts.
Comments and original YAML formatting remain in the backup.

Start fresh CLI processes and restart the affected gateway service to load it.
`cairn/installed.json` records the source revision, whether the checkout was
modified, and hashes of the installed adapter, engine and skill.

For uninstall, remove `mcp_servers.cairn` and `memory.provider` (when it is `cairn`)
from the current configuration, remove `cairn-controls` from `plugins.enabled`,
then remove `plugins/cairn`, `plugins/cairn-controls`, `skills/cairn`,
`cairn-lifecycle.json` and the profile's `cairn` directory. Restart the gateway and
start a fresh CLI. Restore the backup wholesale only if no later settings need
preserving. Uninstall does not delete shared notes or native Hermes memory.

## Conversation controls

Use `/cairn` in the CLI or `!cairn` in Slack. Commands work before the first
agent turn. `status` shows the chosen context, last recalled IDs/versions, last
capture outcome and duration, saved IDs and whether capture is pending. It makes
no model calls. Routine successful turns remain quiet.

`context <existing-project-directory> [workstream]` binds this conversation
independently of its terminal directory. Quote paths containing spaces.
`context` shows the choice; `context clear` restores automatic project selection.
Slack choices persist across reset/restart and are isolated by Hermes's native
conversation key. CLI choices belong to that process. These labels do not grant
permissions or change the shared collection identity.

`retry` retries pending capture using the live provider, or after restart reads
at most 64 active messages from Hermes's existing history. The normalized snapshot
must exactly match the saved digest. Compacted, changed or missing history is
refused; use explicit memory tools if the original snapshot is unavailable.
`discard` explicitly abandons that pending retry so context can be changed.
It does not delete shared notes. Local control files contain context, identifiers,
outcomes and digests, never another copy of conversation text.

## Event and identity contract

The provider registers pre-turn, completed-turn and tool-result hooks, plus LLM
request middleware. Registrations belong to each provider instance and are
released on shutdown; gateway agent recreation does not accumulate callbacks.
Callbacks check the actual session ID and context-local `HERMES_HOME`, keeping
conversations and profiles separate. The actual turn event binds dialogue to its
session label; queued `/new` callbacks cannot overwrite a subsequent `/resume`.
Each provider retains at most 32 recognized labels. Cairn engine state filenames use SHA-256
keys; the original Hermes session label is retained in task/run scope.

Hermes shares MCP connections within a profile. Explicit tools therefore carry
profile labels, not attested per-turn or per-session identity. Lifecycle requests
use the actual Hermes session label. Ordinary repository-scoped notes are
intentionally shared between profiles, surfaces and other agents.

Working-directory hints prefer Hermes's per-task/per-session terminal state and
per-task override, then the configured `TERMINAL_CWD` and process directory.
Prompt paths/quoted errors and observed file/error identifiers feed the shared
retrieval policy. Slack's workspace, conversation, sender and thread mapping
remains Hermes's responsibility; the resulting session ID owns local state.

## Recall and selected capture

Recall runs before an owner turn, including a bare `continue` or `resume`.
Request middleware appends at most 12,000 UTF-8 bytes of Cairn context to the API
copy. It does not use Hermes's persisted `api_content` sidecar. Tool-loop requests
reuse one bundle; the next owner turn searches again because no Cairn bundle
remains in stored history. Changed notes and context evicted by compaction are
eligible again. Unrelated prompts receive no optional boilerplate.

Completed-turn capture runs synchronously before Hermes returns the finished
turn. The pre-compaction callback checkpoints original dialogue before it is
removed. Gateway manual `/compress` uses a helper with memory disabled, so the
live provider observes `pre_command` to checkpoint its retained original turn
before that helper runs. A bounded in-memory pending turn permits retries after
failed capture, including interruption and compression. Unchanged selected dialogue uses the shared digest to skip another
selector call at compaction or exit. Successful compaction does not re-extract
its summary during shutdown. Failed operations remain retryable.

Capture reads at most 64 recent messages and the shared engine bounds selected
original user/assistant text to 24,000 UTF-8 bytes. It excludes tool results,
assistant tool-call narration, reasoning, compaction summaries and `api_content`.
Reusable decisions/corrections and unfinished-work checkpoints are saved
separately; existing candidate notes are read before revision. When an optional
candidate expansion exhausts its receipt budget, selection uses the candidates
already read. Other failures still propagate. A matching topic outside that
set requires explicit reconciliation before any revision. There is no
adapter transcript spool, background capture worker or new daemon.

Each provider serializes its own operations. The engine also uses a per-session
file lock. The selector has a 35-second timeout, individual Cairn calls five
seconds, and the complete adapter operation a 150-second ceiling. Synchronous
capture adds turn-completion latency, but does not depend on Hermes's five-second
background drain. Failures are labelled in Hermes logs and CLI warnings; tool
output and dialogue are excluded from those diagnostics. Abrupt process death
can still lose work, and selection remains fallible.

`CAIRN_LIFECYCLE_DISABLED=1`, `.cairn-no-memory` in the working directory or Git
root, and `CAIRN_LIFECYCLE_CHILD=1` disable automatic memory. Child, cron, flush
and auxiliary runs are excluded. An observed opt-out also discards pending
dialogue so re-enabling does not capture it later. Explicit tools remain available.

The initial native target is the installed Hermes chat-completions request path
used by its CLI and Slack gateway. Other transports must pass their native
request-format checks before recall is claimed for them.

## Verification

Run focused boundary tests without Hermes:

```sh
python3 -B -m unittest discover -s scripts -p 'test_*lifecycle.py'
```

The native fixture uses Hermes and Claude against scripted model endpoints, a
fresh PostgreSQL cluster, synthetic Slack events and a captured outbound
transport. It never sends a real Slack message or uses the operational database:

```sh
~/.hermes/hermes-agent/venv/bin/python scripts/check_hermes_lifecycle.py \
  --hermes-root ~/.hermes/hermes-agent --binary ~/.local/bin/cairn \
  --claude ~/.local/bin/claude --output /tmp/cairn-hermes-native-check
```

The output directory must not exist. Synthetic transcripts and diagnostics stay
outside the repository. Passing these mechanics does not measure long-term
selection quality or task benefit. See the [verification report](verification/hermes-integration-2026-09-13.md)
for tested revisions, remaining limits and deployment evidence.
