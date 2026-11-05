# Use Cairn in OpenCode

OpenCode can access Cairn through its native tools adapter or through MCP. Both
routes use the authenticated API and a provisioned agent token. Choose one route
for a project to avoid duplicate memory tools.

## Install the native adapter

With the connection variables from the [quickstart](../quickstart.md), run this
from the project where you want the tools:

```sh
cairn opencode-install --project "$PWD" \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION"
```

The installer writes `.opencode/tools/cairn.ts` and `.opencode/cairn.json`.
The project directory must already exist. Identical reruns are safe; differing
existing files require explicit `--replace` after you inspect them. The adapter
still needs the Cairn API service.

Native sessions supply retrieval labels by default. `--task TASK` selects a
shared task; optional `--run RUN` requires that task. The collection identity
stays fixed even when the working project changes. `--recent-files` also installs
a plugin for session-local file hints; these are optional retrieval hints, not
proof of a file's contents.

## Use MCP instead

Generate an OpenCode configuration entry with explicit retrieval labels:

```sh
cairn opencode-config \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION" --task investigate-storage --run attempt-1
```

Merge the generated `mcp.cairn` entry into your OpenCode configuration. The
optional `--memory-only` output denies all tools except Cairn search and body
pull. It is a restricted inspection configuration, not an option that merely
hides coordination tools; do not use it for an ordinary coding session.

## Verify the integration

Start a fresh OpenCode session and check that the Cairn tools are available.
Search for a known shareable note and pull it, then inspect the connected build
with the client-info tool. Configuration changes do not establish that an
already running session reloaded.

See the [native adapter contract](../opencode-tools.md) for names and behavior,
the [MCP overview](overview.md) for the alternate route, and
[OpenCode lifecycle integration](../opencode-lifecycle.md) for optional automatic
recall and capture.
