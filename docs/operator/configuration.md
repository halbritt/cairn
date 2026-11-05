# Configure access and storage

Cairn separates database administration from agent access. The API service opens
PostgreSQL; agents connect through an authenticated socket with a provisioned
profile. A profile binds the caller's identity, collection, and destination.

## Paths and database connection

| Setting | Behavior |
| --- | --- |
| `CAIRN_HOME` | State and default connection directory; defaults to `~/.local/share/cairn`. |
| `CAIRN_DATABASE_URL` | Overrides the database connection for commands that open the store. Without it, Cairn uses the private socket under `CAIRN_HOME/socket`, database `cairn`, and the current Unix username. |
| `CAIRN_PG_BIN` | PostgreSQL binary directory for `scripts/local-store.sh`; defaults to `pg_config --bindir`. |
| `CAIRN_BINARY` | Cairn executable for the local-store script; defaults to the checkout's `bin/cairn`. |

`cairn serve` defaults to `CAIRN_HOME/identities.json` and
`CAIRN_HOME/api.sock`. Override them with `--identities` and `--socket`.
Ordinary agent connections use `--socket` and `--token-file`; provide both to
avoid dependence on home-directory defaults. MCP requires explicit paths.

The `repo` value is a collection identity, not a directory that the API scans.
Agent requests must use their profile's provisioned value. A collection may
contain several projects; keep project context in each note.

## Provision identities

The [quickstart](../quickstart.md#create-an-agent-profile) creates one token and
identity file without putting token bytes into shell history. The identity file
is a JSON array; each entry contains:

| Field | Meaning |
| --- | --- |
| `token_sha256` | SHA-256 digest of the bearer token. Store the plaintext token only in its private token file. |
| `principal` | Authenticated writer and default inbox identity. |
| `repo` | Authorized collection identity. |
| `role` | `agent` for ordinary operations, or `observer` for host-observed execution facts. |
| `destination` | `hosted` or `local`. Hosted access excludes local-only bodies. |
| `remote`, `machine_id` | Remote-profile fields managed by machine provisioning. |

The configuration and tokens must be regular files owned by the service user
with owner-only permissions. The server accepts up to 32 profiles and reads them
at startup. Restart it after identity changes. Each configured profile requires
a distinct token digest and principal.
Sessions sharing a profile use its default inbox unless they select their own
registered session UUIDs.

There is no `operator` API role. Administrative commands use the trusted local
store channel. An `observer` token does not grant authority to issue instructions
or promote claims. Neither a body field nor a caller-selected label authenticates
an observation.

A local destination can read eligible local content as well as shareable content.
It still has collection and operation restrictions. Use a hosted profile for
hosted models; do not substitute local access to bypass a withheld result.
These controls assume a trusted Unix account, not hostile processes sharing that
account.

## Remote access

For multiple machines, use [machine enrollment](../multi-machine.md). The
central API can serve TLS directly with `--listen`, `--tls-cert`, and `--tls-key`,
or use a loopback listener with `--tls-terminated-proxy` behind a managed HTTPS
proxy. Transport authentication uses bearer tokens; these flags do not configure
mutual TLS. Remote profiles have a narrower operation allowlist.

See [deployment](deployment.md) for service setup and
[authenticated access](../local-api.md) for limits and failure responses.
