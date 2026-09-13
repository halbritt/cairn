# Claude Code memory access

Claude Code is now registered at user scope on the owner's host for
[shared memory across projects](shared-memory.md). Start a fresh session in any
project and use the `cairn` MCP tools. The installed launcher supplies connection
settings and session labels automatically. A normal Claude session in Pincite
read a setup note contributed by Codex and OpenCode, appended its own setup
details, and read the saved revision on 2026-09-11.

For automatic task/resume retrieval and selected checkpoints before compaction
or exit, install the optional [lifecycle hooks](claude-lifecycle.md).

## Explicit configuration generator

`cairn claude-config` generates the Claude Code MCP configuration for Cairn's
ordinary tools: search, body/evidence pull, capture, edit and retained history.
Claude Code 2.1.265 has accepted the generated entry and executed search,
body/evidence pull, capture and edit in scripted native sessions. The newer
`cairn_history` operation has MCP/OpenCode coverage; native Claude execution of
that operation remains unverified. The later shared-memory installation verifies
model-selected search, pull, and edit; broader task benefit remains open.

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
