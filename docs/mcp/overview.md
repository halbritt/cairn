# Connect an agent through MCP

Cairn's Model Context Protocol (MCP) server lets an agent search shared memory,
read selected notes, and save or correct useful findings. It runs as a local
stdio process and calls the authenticated Cairn API. The agent does not need
PostgreSQL credentials.

```text
Agent host → cairn mcp (stdio) → authenticated API → PostgreSQL
```

## Configure the connection

First complete the [quickstart](../quickstart.md) or obtain a provisioned socket,
token-file path, and collection name. Configure your MCP host to launch this
command with actual task and run labels:

```sh
cairn mcp \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION" --task investigate-storage --run attempt-1
```

These shell variables must be set before running the example. In a JSON or TOML
client configuration, use expanded absolute paths; shell syntax is not generally
expanded in argument arrays. Keep token bytes in the token file.

The profile determines the authenticated principal, allowed collection, and
local or hosted destination. The facade's task/run labels group retrievals; they
do not authenticate an execution. Ordinary `cairn_remember` calls default to
repository scope, so selected guidance remains available in later sessions.

See the client guides for [Codex](codex.md), [Claude Code](claude-code.md),
[OpenCode](opencode.md), and [Agy or Hermes](agy-hermes.md). Start a fresh client
session after changing its configuration. Installing MCP tools does not enable
automatic recall or capture; those use separate lifecycle adapters.

## Verify the tools

Call `cairn_client_info` to inspect the connected facade and API builds and
capabilities. Then search for a known shareable note and pull it using the
returned arguments. A configuration entry alone does not establish a working
connection.

The server exposes these tools:

| Task | Tools |
| --- | --- |
| Find and inspect memory | `cairn_search`, `cairn_pull`, `cairn_pull_evidence` |
| Save and revise notes | `cairn_remember`, `cairn_prepare_note`, `cairn_edit` |
| Inspect retained versions | `cairn_history` |
| Record or inspect assessments | `cairn_assess`, `cairn_assessments` |
| Diagnose the connection | `cairn_client_info` |

Use the [tool reference](../reference/mcp-tools.md) for arguments and the
[MCP contract](../mcp.md) for detailed behavior. Coordination commands are a
separate CLI/API interface.

## Control context use

Search returns required context and bounded candidate previews. Pull relevant
candidates deliberately. Set `available_tokens` to actual available room and
`memory_budget_bytes` to the allowance for memory. Cairn uses conservative
UTF-8 byte accounting. An inspection policy reserves room for a complete source;
it does not automatically read that source. See
[searching memory](../guides/memory-search.md).

Tool profiles and search presentation are independent settings:

- `--tool-profile compact/1` shortens search and pull tool descriptions.
- `--tool-profile compact/2` shortens descriptions across all ten tools.
- `--search-view compact/1` changes the search result presentation.

Tool profiles preserve tool names, schemas, and handlers. No fixed percentage
saving applies to every host or tokenizer. `--tokens` supplies default result
room; `--max-tokens` caps explicit larger requests. Budget the combined context
from searches and pulls, not just each call in isolation.

## Troubleshoot connection failures

`CLIENT_SETUP_FAILED` indicates that the token file could not be read;
`API_CONNECTION_FAILED` indicates that the API socket could not be reached.
Check the configured paths, file permissions, and service. A hosted profile will
not return local-only note bodies. After a binary or configuration change,
restart the facade before using its reported build to assess the change.
See [build identity](../build-identity.md) and [client observations](../client-observations.md).
