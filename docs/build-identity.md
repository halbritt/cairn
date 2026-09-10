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
