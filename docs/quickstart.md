# Quickstart

Cairn gives agents memory they can use in later sessions: project decisions,
preferences, procedures, and lessons from completed work. This guide builds Cairn,
starts a private local store, and saves and retrieves a note. You can then connect
an agent through MCP.

## Prerequisites

Use Linux with Go 1.25 or newer, PostgreSQL 17 server and client tools, Python 3,
Bash, Make, and `flock`. PostgreSQL 17 is the version used by the local lifecycle
tests. Run the local store as your ordinary Unix user, not root. `pg_config`
must identify the installation containing `initdb`, `pg_ctl`, and `psql`.

For an existing installation, obtain its socket path, token-file path, and
collection name from its administrator, then skip to **Save and find a note**.
The collection name is the `repo` value provisioned for your profile; it need
not equal your current working directory.

## Build and start a local store

```sh
git clone https://github.com/halbritt/cairn.git
cd cairn
make build
export PATH="$PWD/bin:$PATH"
export CAIRN_HOME="$HOME/.local/share/cairn-quickstart"
export CAIRN_COLLECTION="$PWD"
bash scripts/local-store.sh start
cairn version
```

This example uses a separate `cairn-quickstart` state directory. Choose another
unused directory if that path already holds a store you need to preserve:
`start` also migrates an existing store at the selected path.

The store script creates a dedicated PostgreSQL cluster under `CAIRN_HOME`,
listening on a private Unix socket, and applies Cairn's migrations. It does not
use the system PostgreSQL service. Keep `CAIRN_DATABASE_URL` unset for this
setup; that variable overrides the default connection. For another PostgreSQL
deployment, see [deployment](operator/deployment.md).

## Create an agent profile

The API authenticates agents with token files. For this new installation, create
one profile whose notes may be read by hosted models:

```sh
python3 - <<'PYPROFILE'
import hashlib, json, os, pathlib, secrets
root = pathlib.Path(os.environ['CAIRN_HOME'])
root.mkdir(mode=0o700, parents=True, exist_ok=True)
paths = [root / 'agent.token', root / 'identities.json']
if any(path.exists() for path in paths):
    raise SystemExit('Profile already exists; use it or configure a separate profile.')
token = secrets.token_urlsafe(32)
identity = dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                principal='agent:example', repo=os.environ['CAIRN_COLLECTION'],
                role='agent', destination='hosted')
for path, text in zip(paths, [token + '\n', json.dumps([identity]) + '\n']):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)
PYPROFILE
cairn serve
```

Leave `cairn serve` running. It reads the identity file and serves the authenticated
API at `$CAIRN_HOME/api.sock`. Token bytes stay in the token file. See
[configuration](operator/configuration.md) to add profiles or choose a local
destination.

## Save and find a note

Open a second terminal in the same checkout. Set `CAIRN_HOME`,
`CAIRN_COLLECTION`, and `PATH` as above. For an existing installation, substitute
your provisioned paths and collection.

```sh
cairn agent --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" remember \
  --repo "$CAIRN_COLLECTION" --kind procedure --shareable \
  'Example project: run database tests against disposable clusters.'

cairn agent --socket "$CAIRN_HOME/api.sock" \
  --token-file "$CAIRN_HOME/agent.token" search \
  --repo "$CAIRN_COLLECTION" --task quickstart --run first-try \
  'disposable clusters'
```

A successful capture returns a record ID and version. Search returns required
context and candidate previews; run the returned `pull_command` for a relevant
candidate to read its body. Check the note against current project facts before
using it. Search labels organize retrieval; saving this note uses repository
scope so it is eligible in later tasks.

`--shareable` allows hosted delivery. Without it, a note is local-only and will
not appear through this hosted profile. `CLIENT_SETUP_FAILED` indicates a token
file problem; `API_CONNECTION_FAILED` indicates a socket or service problem.
Check the configured paths and running service before retrying. For a capture
whose response was lost, reuse a request UUID supplied with `--request-id` and
identical arguments; a new invocation without it may create another note.

## Connect an agent

Follow the [MCP overview](mcp/overview.md), then the guide for
[Codex](mcp/codex.md), [Claude Code](mcp/claude-code.md),
[OpenCode](mcp/opencode.md), or [Agy and Hermes](mcp/agy-hermes.md).
MCP provides explicit memory tools. Automatic recall and capture require the
separate lifecycle integration for your agent host.

Continue with [searching memory](guides/memory-search.md),
[saving and correcting notes](guides/memory-capture.md), or
[coordinating sessions](guides/session-coordination.md). For a persistent service,
use the [deployment guide](operator/deployment.md). To stop this foreground
example, press Ctrl-C in the server terminal, then run
`bash scripts/local-store.sh stop` from the checkout.
