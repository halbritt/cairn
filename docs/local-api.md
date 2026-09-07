# Authenticated local access

`cairn serve` exposes a Unix socket, mode 0600, in an owner-only directory.
Identity comes from a bearer token whose SHA-256 digest is in an owner-only
configuration file. Requests cannot choose principal, observer role or destination.
The `agent` role records testimony; `observer` also records host-observed spawn,
terminal, delivery, outcome, binding, coverage and task-state facts. Neither role
has operator authority endpoints or access to database credentials.

Generate a local agent profile, keeping the plaintext token out of shell history:

```sh
python3 - <<'PY'
import hashlib, json, os, pathlib, secrets
root = pathlib.Path.home() / '.local/share/cairn'
root.mkdir(mode=0o700, parents=True, exist_ok=True)
token = secrets.token_urlsafe(32)
for name, value in {
    'agent.token': token + '\n',
    'identities.json': json.dumps([dict(
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        principal='agent:cairn', repo=os.getcwd(), role='agent', destination='local'
    )]) + '\n',
}.items():
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
PY
cairn serve
```

The example refuses to overwrite existing credentials. For additional profiles,
add a distinct principal and token digest to the configuration; up to 32 profiles
are supported. Restart the server after changes. Use `destination: hosted` for a
profile whose retrieved context goes to a remote model. Hosted profiles cannot
read local record bodies or protected usage reports. As with the operator CLI,
this is a cooperative local trust boundary; it does not isolate hostile processes
sharing the owner's Unix account.

In another terminal, submit one JSON request to an agent operation:

```sh
cairn agent create < note-request.json
cairn agent compile < compile-request.json
```

`--token-file FILE` and `--socket PATH` select another provisioned profile or
socket. The agent client never opens the database. Use the same request UUID for
a transport retry. The server has bounded request bodies and deadlines; an HTTP
write failure does not roll back a committed mutation.

Operations: `create`, `edit`, `compile`, `get`, `evidence`, `usage`, `use-report`,
`assess-run`, and observer-only `spawn`, `terminal`, `task-state`, `bind-run`,
`delivery`, `outcome`, `usage-coverage`. All use `POST /v1/OPERATION` with JSON
matching the corresponding core request. `get` takes `record_id`. `compile` takes
no destination field; the configured profile owns that decision. A hosted profile
cannot use the protected `use-report` endpoint. The server checks repository scope
at the store boundary as well as endpoint authorization. SIGTERM shuts down
requests and removes the owned socket; a second listener cannot replace it.

## Striatum boundary

Striatum-next's accepted model requires context to enter lanes as sealed, declared
inputs. Its current supervisor renders pinned dispatch inputs before starting the
backend. Ambient memory injection into that prompt would violate those semantics.
This API supplies authenticated host ingestion and agent access, but it does not
change Striatum's pass contracts or declare Cairn integrated into real builds.

The next host integration must compile Cairn context before dispatch sealing,
include it as a permitted dispatch input, retain the Cairn receipt ID beside the
host attempt identity, and forward host terminal/task-state observations. Task
acceptance must come from the host's actual gate evidence; a backend process exit
cannot set it. No live Striatum lane was altered by this implementation.
