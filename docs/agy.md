# Agy MCP setup

Agy 1.2.0 has native stdio MCP registration. It can store a command for Cairn's
existing MCP server, so no additional Cairn adapter is needed for this setup
step. Registration, argument preservation and configuration updates are verified
in an isolated home. Native tool discovery/execution and an Agy memory task remain
unverified. [Evidence and limits](verification/agy-configuration-2026-09-09.md).

The observed configuration is **user-wide** at
`~/.gemini/config/mcp_config.json`; the registration command exposes no project
scope flag. The following example has fixed Cairn repository/task/run scope.
Choose actual task/run identifiers before using it, and keep the registration
enabled only for that work. Agy's project or conversation selection does not
automatically update these arguments. Concurrent unrelated sessions should not
share one fixed task/run registration.

```sh
cairn_bin="$(command -v cairn)"
agy mcp add cairn-cairn -- "$cairn_bin" mcp \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" --task investigate-storage --run agy-1 \
  --tokens 32000
agy mcp list
```

Use an existing ordinary profile for the intended destination. The example names
a token file, not token bytes, and supplies no database credentials. Agy's `--`
separator before the command preserves Cairn's flags as server arguments.
Absolute executable, socket and token paths avoid dependence on the harness's
working directory. Existing `--revision`, `--workspace-sha256`, `--task-class`,
`--task-phase`, `--binding` and `--capability` arguments can follow `--tokens`.

`agy mcp add` updates an existing registration with the same name. Inspect that
name before replacing it. To change task/run scope, repeat registration with the
new arguments. To stop exposing the configured server to other work:

```sh
agy mcp disable cairn-cairn
```

`agy mcp enable cairn-cairn` enables it again; `agy mcp remove cairn-cairn` removes
the registration. Registration and enablement do not establish that an already
running session reloaded its tools. Session reload and permission behavior still
need native verification. No owner registration was made during this work.

The expected tools are Cairn's existing `cairn_search`, `cairn_pull`,
`cairn_pull_evidence`, `cairn_remember` and `cairn_edit`. Their
[MCP contract](mcp.md#tools) retains ordinary testimony, explicit sharing,
compare-and-swap editing, currentness checks and bounded pulls. The Agy runtime
must still be shown to discover and execute these tools with its own permissions;
a stored command or generic MCP check does not establish that result.
