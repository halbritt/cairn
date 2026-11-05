# Claude Code memory access

Connect Claude Code to Cairn to search saved knowledge, inspect sources, and save
selected lessons from a task. Start with the [Claude setup guide](mcp/claude-code.md)
for prerequisites. Optional [lifecycle hooks](claude-lifecycle.md) add automatic
retrieval and selected checkpoints before compaction or exit.

## Explicit configuration generator

`cairn claude-config` generates an MCP server configuration for one collection,
task, and run. Use this when you want to control retrieval scope for a particular
invocation instead of relying on an installed session-aware launcher.

Generate a configuration for the intended task and execution:

```sh
cairn claude-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo /path/to/collection --task investigate-storage --run attempt-1 \
  > ./cairn-claude.json
claude --mcp-config ./cairn-claude.json
```

Use the repository identity and ordinary hosted profile provisioned for the
project. Replace task/run names for the actual work; reusing them combines
retrieval observations. Native Claude session metadata is not assumed:
`--codex-thread` is refused. Capture defaults to repository scope; individual saves can choose task or run
scope. These labels describe applicability, not an authenticated execution attempt.

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
revision/workspace/task-class/task-phase/binding/capability flags are forwarded. Paths,
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
that Claude retrieved or applied a note. [Native verification](verification/claude-native-tools-2026-09-09.md) checks actual
Claude capture, correction, search, body/evidence pulls, retries, stale refusals,
hosted filtering and a denied edit across two isolated native sessions. Scripted
local responses drive the calls; no inference, owner configuration changes or
operational test records are involved. Direct MCP rejects non-boolean sharing
flags. Claude's native path accepted textual `"false"` as a local note in this
version, while refusing unrecognized boolean text. Supply actual booleans and
do not rely on client coercion. Run it with:

```sh
CAIRN_CLAUDE_BINARY="$(command -v claude)" make test-integration
```

The required CLI version and remaining U8 work are recorded in the
[setup report](verification/claude-config-2026-09-09.md) and
[native tool report](verification/claude-native-tools-2026-09-09.md).
