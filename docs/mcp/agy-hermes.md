# Use Cairn in Agy and Hermes

Agy and Hermes can share Cairn notes with other agent hosts through the same
provisioned collection. Use a hosted destination profile when retrieved content
will reach a remote model. MCP memory tools and native session coordination are
separate integrations.

## Agy

Configure Agy's stdio MCP connection to launch Cairn with explicit connection
paths and retrieval labels:

```sh
cairn mcp \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION" --task investigate-storage --run attempt-1 \
  --client-harness agy --tool-profile compact/2
```

Set the variables as in the [quickstart](../quickstart.md). For an argument-array
configuration, substitute expanded absolute paths. Use your installed Agy
client's MCP registration interface; see [Agy setup](../agy.md) for the supported
registration recipe. Fixed task/run labels remain fixed across sessions until
you update them or use a launcher that supplies new labels.

Start a fresh session, call `cairn_client_info`, and search and pull a known
shareable note. Registration alone does not verify native tool execution.

## Hermes

Hermes can use the same stdio MCP server for explicit memory tools. Cairn also
ships a Hermes provider that adds task/resume retrieval and selected capture
through the shared lifecycle engine. The provider uses Hermes's external
memory-provider slot; built-in `MEMORY.md` and `USER.md` remain independent.

Follow [Hermes installation](../hermes-lifecycle.md#installation) for its Python
environment, existing connection configuration, provider settings, and restart
steps. The installer has deployment-specific defaults: inspect its `--help` and
set paths for your installation. Installing the provider can require replacing
a conflicting external provider, so review the existing configuration first.

Explicit MCP tools and lifecycle requests use different retrieval labels:
profile-level labels for shared MCP connections, actual session labels for the
lifecycle adapter. Neither label grants authority to act.

## Coordinate work separately

Native coordination hooks manage session registration, presence, and supported
inbox delivery. Use their supplied agent and execution UUIDs. Do not substitute
a display name such as `agent-12` for a UUID or register a second session to
handle an existing native delivery. See
[session coordination](../guides/session-coordination.md) and
[native inbox integration](../native-inbox.md).
