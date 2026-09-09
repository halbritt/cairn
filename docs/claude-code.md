# Claude Code memory access

`cairn claude-config` generates the Claude Code MCP configuration for Cairn's
existing five ordinary tools: search, body/evidence pull, capture and edit.
Claude Code 2.1.265 has accepted the generated entry and connected to its stdio
server. Native model-selected tool use and task benefit remain unverified.

Generate a configuration for the intended task and execution:

```sh
cairn claude-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" --task investigate-storage --run attempt-1 \
  > /tmp/cairn-claude.json
claude --mcp-config /tmp/cairn-claude.json
```

Use the repository identity and ordinary hosted profile provisioned for the
project. Replace task/run names for the actual work; reusing them combines
retrieval observations. Native Claude session metadata is not assumed:
`--codex-thread` is refused. Capture remains repository-wide, and task/run
scope describes retrieval rather than an authenticated execution attempt.

The output uses [Claude's documented MCP format](https://code.claude.com/docs/en/mcp):

```json
{
  "mcpServers": {
    "cairn": {
      "type": "stdio",
      "command": "/absolute/path/to/cairn",
      "args": ["mcp", "--socket", "/absolute/path/to/api.sock",
               "--token-file", "/absolute/path/to/ordinary-agent.token",
               "--repo", "REPOSITORY", "--task", "TASK", "--run", "RUN",
               "--tokens", "32000"]
    }
  }
}
```

The generator resolves executable/socket/token paths to absolute paths without
reading credentials or connecting to Cairn. `--tokens` and the existing MCP
revision/workspace/task-class/binding/capability flags are forwarded. Paths,
quotes and whitespace are JSON encoded. Arguments must be UTF-8 and cannot
contain `${`, because Claude would interpret that syntax as environment-variable
expansion and potentially change a path or declared scope. Resolve such values
before generation. Missing scope, invalid flags and unsupported scope modes
return nonzero with stderr and no configuration on stdout. If writing output
fails, discard the output file.

The generator emits server configuration only. It does not change Claude trust,
permissions, project files or existing servers. Use Claude's normal tool approval
policy. For persistent registration, Claude's `mcp add-json` takes the inner
`mcpServers.cairn` object, not the whole generated document; `mcp get cairn`
checks its connection. Persisting fixed task/run names also persists that grouping,
so per-invocation configuration is appropriate when execution scopes change.

[Ordinary tool behavior](mcp.md#tools) retains the authenticated API's repository,
destination, version and stale-handle checks. A connection does not establish
that Claude retrieved or applied a note. The current verification checks actual
Claude configuration registration and connection in an isolated home, plus
search and exact revised-note retrieval through the generated launch command
with an independent MCP client. No inference, owner configuration changes or
operational test records are involved. Run it with:

```sh
CAIRN_CLAUDE_BINARY="$(command -v claude)" make test-integration
```

The required CLI version and remaining U8 work are recorded in the
[verification report](verification/claude-config-2026-09-09.md).
