# Use Cairn in Codex

Connect Codex to Cairn to carry selected project decisions and procedures between
conversations. This guide uses Cairn's configuration generator, which writes the
MCP entry without reading tokens or changing existing Codex settings.

## Generate the entry

Set `CAIRN_HOME` and `CAIRN_COLLECTION` as in the [quickstart](../quickstart.md),
or substitute your provisioned socket, token, and collection:

```sh
cairn codex-config \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION" --codex-thread \
  --tool-profile compact/2
```

The generated TOML uses `[mcp_servers.cairn]`. Merge that block into the Codex
configuration you intend to use; avoid duplicate entries with the same name.
The generator uses an absolute executable path, so install Cairn at a stable
location before generating a persistent entry.

`--codex-thread` groups searches by Codex tool-call thread metadata. For a batch
with explicit labels, replace it with `--task TASK --run RUN`; do not combine the
two modes. Missing native thread metadata is refused in thread mode. These labels
organize retrieval and are not authenticated execution identities.

## Check a fresh session

Start a new Codex session. Call `cairn_client_info`, search for a known shareable
note, and pull its complete body. Then use the
[search](../guides/memory-search.md) and [capture](../guides/memory-capture.md)
workflows during real tasks. Capture only selected reusable information.

`--required` makes the generated entry require a connection at startup. Leave it
off when Cairn should be optional. Tool availability does not force an agent to
search on every task. Automatic recall, capture, and inbox handling require their
separate [lifecycle](../codex-lifecycle.md) and [native inbox](../native-inbox.md)
integrations.

See the [MCP overview](overview.md) for budget and connection troubleshooting and
`cairn codex-config --help` for the generator's complete options.
