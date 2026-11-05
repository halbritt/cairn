# Agy MCP configuration

Agy can use Cairn's ordinary memory tools through a stdio MCP registration.
Start with the [Agy and Hermes setup guide](mcp/agy-hermes.md) for prerequisites.
Use [shared memory](shared-memory.md) when one collection serves several projects.

## Explicit task and run scope

A fixed MCP registration keeps the same collection, task, and run arguments until
you change it. Agy's project or conversation selection does not automatically
update those arguments. Use distinct labels for unrelated work, or configure a
session-aware launcher as described in the setup guide.

This example uses the registration interface checked with Agy 1.2.0. Confirm the
installed Agy command's help if its interface differs:

```sh
cairn_bin="$(command -v cairn)"
agy mcp add cairn -- "$cairn_bin" mcp \
  --socket /absolute/path/to/api.sock \
  --token-file /absolute/path/to/agent.token \
  --repo /path/to/collection --task investigate-storage --run agy-1 \
  --tokens 32000
agy mcp list
```

Supply your provisioned collection and ordinary agent profile. The token-file
argument names a file, not token bytes. The `--` separator preserves Cairn's
flags as server arguments. Absolute executable, socket, and token paths avoid
relying on Agy's working directory.

Registration is user-wide in the checked configuration. Inspect an existing
registration before replacing it. Start a fresh conversation after changing the
configuration, then confirm that the [MCP tools](reference/mcp-tools.md) appear.
Registration alone does not demonstrate a successful memory request.

## Disable or remove a registration

```sh
agy mcp disable cairn
agy mcp enable cairn
agy mcp remove cairn
```

Use disable/enable to retain configuration while controlling availability;
remove deletes the registration. The [historical configuration check](verification/agy-configuration-2026-09-09.md)
records the tested Agy version and its limitations.
