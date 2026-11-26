# Use Cairn in Claude Code

Connect Claude Code to Cairn to search prior project decisions, inspect their
sources, and save selected findings. Start with a working
[Cairn API and agent profile](../quickstart.md).

## Generate a configuration for a task

Set the connection variables from the quickstart, then generate an MCP file:

```sh
cairn claude-config \
  --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" \
  --repo "$CAIRN_COLLECTION" --task investigate-storage --run attempt-1 \
  --client-harness claude --tool-profile compact/2 > cairn-claude.json
claude --mcp-config cairn-claude.json
```

Use labels for the actual work. The output contains `mcpServers.cairn` with a
stdio command and absolute paths. Example generated configuration shape:

```json
{
  "mcpServers": {
    "cairn": {
      "type": "stdio",
      "command": "/absolute/path/to/cairn",
      "args": [
        "mcp",
        "--socket", "/absolute/path/to/api.sock",
        "--token-file", "/absolute/path/to/agent.token",
        "--repo", "REPOSITORY",
        "--task", "TASK",
        "--run", "RUN",
        "--tokens", "16000",
        "--search-view", "compact/1",
        "--tool-profile", "compact/2",
        "--client-harness", "claude"
      ]
    }
  }
}
```

The generator resolves executable, socket, and token paths to absolute paths
without reading credentials or connecting to Cairn. It forwards `--tokens`
(default `16000`) and the existing MCP flags (`--revision`, `--workspace-sha256`,
`--task-class`, `--task-phase`, `--binding`, `--capability`, `--search-view`,
`--tool-profile`, `--messaging`). It forwards `--client-harness LABEL` (`claude`
in the command above) only when you pass it; without it,
[observed clients](../client-observations.md) lists this connection with harness
`unknown`. Paths, quotes, and whitespace are JSON
encoded. Arguments must be valid UTF-8 without NUL bytes and cannot contain
`${`, because Claude interprets that syntax as environment-variable expansion
and could alter a path or declared scope; resolve such values before generation.
Missing scope, invalid flags, and unsupported scope modes fail nonzero on
stderr with no configuration on stdout.

The generator does not install a server, modify Claude's permissions, or change
other registrations. Keep the generated file local to your configuration
workflow; it contains deployment paths and retrieval labels.

Claude configuration requires explicit task/run labels. `--codex-thread` is
rejected. Reusing fixed labels combines retrieval observations from those
sessions; it does not identify individual native turns.

## Verify and use the tools

In a fresh session, call `cairn_client_info`, search for a known shareable note,
and pull it with the returned arguments. Follow
[searching memory](../guides/memory-search.md) before using saved guidance and
[saving notes](../guides/memory-capture.md) when retaining a useful finding.
The model's tool use remains subject to Claude's normal permissions.

For persistent registration, use your installed Claude client's MCP configuration
commands. Claude's `add-json` command takes the inner `mcpServers.cairn` object;
`claude --mcp-config` takes the whole generated document. Regenerate the entry
if you change its explicit scope or installed Cairn path.

Optional [Claude lifecycle hooks](../claude-lifecycle.md) add automatic recall
and selected capture. They are separate from MCP installation. See the
[MCP overview](overview.md) for connection failures and context budgets.
