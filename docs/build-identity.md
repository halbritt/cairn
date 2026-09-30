# Identify the installed binaries

The [changelog](../CHANGELOG.md) summarizes development changes and upgrade
requirements. Cairn currently has no numbered release tags; the build revision
identifies the source used for an installation.

When saved guidance names an option that the current installation does not
recognize, inspect the executable versions before changing the guidance:

```sh
cairn version
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" version
```

The first command reports the local CLI and needs no database or configuration.
The second uses the normal authenticated Unix connection and returns separate
`client` and `server` objects. It takes no stdin request and does not connect the
client to PostgreSQL. An older API without this endpoint returns an error; the
CLI does not substitute its own identity for an unavailable server.

Each build object contains `schema: "cairn.build/1"`, the Go version, optional
module version and available VCS kind, revision and commit time. `vcs_modified`
is `true` or `false` when stamped and `null` when unknown. A source archive,
unstamped build or `go build -buildvcs=false` may lack a revision; absence does
not mean a clean checkout. These values describe the executable's build, not
the current working tree or installed Python worker.

The API exposes the same server object at authenticated `POST /v1/version` with
`{}`. It uses the ordinary response envelope and reads no repository data.
Caller-supplied version fields are rejected. Build flags, module paths,
dependency inventories, environment variables and credentials are excluded.

MCP initialization reports the facade executable in `serverInfo.version`: its
VCS revision, with `-modified` or `-unknown` when appropriate; otherwise its
module version or `unknown`. This replaces the previous constant `1`. It is
not the MCP protocol version or the remote API identity. Existing MCP processes
keep their original executable until restarted.

A matching source revision does not prove identical binaries, supported
capabilities, source freshness or correctness. Different flags and toolchains
can produce different executables. Use these identities to locate the relevant
source and verification records; retain an executable hash when exact bytes
matter. Do not invalidate a memory merely because its source revision differs.

## Inspect the connected MCP facade

Call native `cairn_client_info` with `{}` through the conversation's existing
Cairn connection. Its `cairn.client-info/1` result separates:

- `facade.build`: the executing facade's build, captured at server construction.
  `facade.search_memory_budget_bytes: "supported"` is a local declaration with
  `support_basis: "registered_search_contract"`. Tests cover the registered
  search argument, validation and forwarding to the authenticated API.
- `api.build`: the build returned by one authenticated `/v1/version` request,
  when available. `api.search_memory_budget_bytes` remains `"unknown"`, with
  `support_basis: "no_api_capability_contract"`: the current version endpoint
  declares builds and wire protocol, not feature support. This applies to older
  responses too; equal revisions or unknown extra response fields do not change it.

The probe has a two-second timeout and never retries. An unavailable API leaves
facade information intact with `api.state: "unavailable"` and a fixed diagnostic
such as `API_CONNECTION_FAILED`, `API_TIMEOUT`, `API_ACCESS_DENIED`,
`VERSION_UNAVAILABLE`, or `API_PROBE_FAILED`. Malformed build metadata produces
`invalid_response` / `INVALID_BUILD_IDENTITY`; arbitrary API error text and
unknown fields are not forwarded. Build strings must fit the diagnostic's
bounded recognized formats; unrecognized identities are not guessed at.

The complete MCP result must fit both 4,096 UTF-8 bytes and the configured tool
output room. Too little room refuses the result with `BUDGET_REFUSED`, without
truncation. No socket, credential, repository or session paths are included.
The tool does not enumerate clients, identify Python code, compare release order,
restart anything or establish that an API operation is authorized or available.
It performs no database writes. A newly launched facade diagnoses only itself;
it cannot establish which implementation another conversation still uses.

## Upgrade a running MCP facade

Replacing the installed Cairn file does not change a process that already loaded
the previous executable. Inspect the actual conversation's tool declarations and,
when the host exposes it, the identity of its connected facade. A separate MCP
discovery process can report the new build while the conversation still uses an
older one.

For Codex, consult the running host's protocol and the
[App Server documentation](https://learn.chatgpt.com/docs/app-server) before using
`config/mcpServer/reload`. An accepted reload is not proof that the facade was
replaced: an unchanged server configuration can reuse an existing connection.
Inspect the effective configuration layers first. A project `.codex/config.toml`
can override the Cairn command in the user's configuration.

When a host reuses connections by command, a verified executable at a new,
content-addressed path lets the configured command identify the intended bytes.
Change only `mcp_servers.cairn.command` in the effective layer; preserve the server
name, arguments, profile, collection and tool restrictions. Check which loaded
conversations a reload affects and use an idle boundary. Keep the previous
configuration and compare it before applying or reverting an edit, so concurrent
changes are not overwritten. This is an operator-controlled deployment procedure,
not an automatic session restart.

Verify the resulting process identity and behavior through that conversation's
connection. A rejected unknown argument and a recognized argument with an invalid
value are different observations. A bounded invalid-input check can establish
validation behavior without reading memory; it does not show that an agent used
the feature correctly. Confirm the agent-visible tool declarations on its next
ordinary task, and report that separately from runtime replacement and task
usefulness.
