# Deploy the local API

A Cairn installation needs a PostgreSQL store, an API process, and profiles for
its agent clients. Start with the [quickstart](../quickstart.md) to verify that
capture, search, and pull work before adding persistent services or remote
machines.

The quickstart selects `~/.local/share/cairn-quickstart`; the defaults in this
guide use `~/.local/share/cairn`. Choose the intended `CAIRN_HOME` before starting
the store, and use that same directory for profiles, clients and services. A
systemd service needs its own configuration for a nondefault directory; it does
not inherit the exports from your terminal.

## Choose a PostgreSQL setup

The repository's `scripts/local-store.sh` manages a dedicated cluster under
`CAIRN_HOME`. It runs as an ordinary user, uses a private Unix socket, and never
redirects to the system PostgreSQL service. It requires Linux, Bash, `flock`,
Python 3, and PostgreSQL server/client tools. PostgreSQL 17 is used by lifecycle
validation; building Cairn requires Go 1.25 or newer.
The script's backup command requires Python 3.11 or newer for
`hashlib.file_digest`; see [backup maintenance](maintenance.md#back-up-a-managed-local-store).

From your Cairn checkout:

```sh
make build
bash scripts/local-store.sh start
bash scripts/local-store.sh status
```

Alternatively, provision a dedicated database and role through your PostgreSQL
administration process, set `CAIRN_DATABASE_URL` to that connection, and run
`bin/cairn migrate`. The role must be able to apply the schema. Keep credentials
out of agent configuration and committed files. See
[configuration](configuration.md) for connection defaults.

## Install and start the API

Install the binary at a stable path so generated client configurations do not
point into a temporary build directory:

```sh
install -d "$HOME/.local/bin"
install -m 0755 bin/cairn "$HOME/.local/bin/cairn"
```

Create the private identity and token files using the
[profile recipe](../quickstart.md#create-an-agent-profile). Then run:

```sh
"$HOME/.local/bin/cairn" serve
```

The default Unix socket is `~/.local/share/cairn/api.sock`, mode 0600, in a
private directory. The service still requires bearer authentication. Test an
agent request before installing it as a background service.

## Run under a systemd user service

For the default paths above, create
`~/.config/systemd/user/cairn-api.service`:

```ini
[Unit]
Description=Cairn authenticated API

[Service]
Type=simple
ExecStart=%h/.local/bin/cairn serve
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=default.target
```

This unit assumes the database is already running. Configure its startup
separately using your chosen PostgreSQL setup. The checked-in
[systemd integrations](../../integrations/systemd/) provide additional service
examples; inspect and adapt their checkout, state, and profile paths before use.
For a nondefault state directory or database, supply the same configuration to
the service as to migration commands. A protected environment file can hold the
database connection; do not embed a password in a public unit example.

Enable the unit and inspect failures:

```sh
systemctl --user daemon-reload
systemctl --user enable --now cairn-api.service
systemctl --user status cairn-api.service
journalctl --user -u cairn-api.service -n 50
```

User-service startup depends on the system's user-manager configuration. Arrange
boot persistence according to your host's service policy if the API must run
without an interactive login.

## Verify client access

Use the profile's socket and token paths to query the running API build:

```sh
"$HOME/.local/bin/cairn" agent \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/agent.token" version
```

`cairn version` reports the executable; `cairn agent ... version` reports the
server. Search and pull a known shareable note through a hosted profile to check
the intended delivery path. See [build identity](../build-identity.md).

## Add optional services

Semantic workers, scheduling, and agent wake supervisors are separate choices;
they are not required for ordinary lexical memory access. Follow
[background services](daemon-deployment.md) for their contracts. To serve another
machine, use [multi-machine deployment](../multi-machine.md), which configures
HTTPS, remote profiles, and a local relay on the joining host.

Before upgrades or restore drills, follow [maintenance](maintenance.md). Test
with disposable clusters, never the operational database.
