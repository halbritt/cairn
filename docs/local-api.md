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
socket. Each omitted flag uses its default under `CAIRN_HOME`, or under
`$HOME/.local/share/cairn` when `CAIRN_HOME` is unset. Supplying both paths
avoids home-directory lookup, so an agent with neither environment variable can
still connect. An explicitly empty path returns `INVALID_REQUEST`; an invalid
explicit token path is not replaced with the default token. The agent client
never opens the database. Use the same request UUID for a transport retry. The
server has bounded request bodies and deadlines; an HTTP write failure does not roll back a committed mutation.

Operations: `create`, `edit`, `delete`, ordinary `supersede`, `compile`, `index`, `expand`, `expand-evidence`, `get`, `evidence`, `usage`, `use-report`, `run-report`, `run-status`,
local-profile-only `conflicts`, `conflict`, `preview-retract` and `supersession`,
`assess-run`, and observer-only `spawn`, `terminal`, `task-state`, `bind-run`,
`claim-run`, `link-run-retrieval`, `register-context`, `delivery`, `outcome`, `usage-coverage`. All use `POST /v1/OPERATION` with JSON
matching the corresponding core request. `get` takes `record_id`. The [expansion contract](index-and-pull.md) binds
body/evidence pulls to an indexed version and one shared session budget. `compile` takes
no destination field; the configured profile owns that decision. A hosted profile
cannot use the protected `use-report` or `run-report` endpoints. The server checks repository scope
at the store boundary as well as endpoint authorization. SIGTERM shuts down
requests and removes the owned socket; a second listener cannot replace it.

[Supersession](supersession.md) on the agent endpoint requires an A source and
omits `grant_id`. B supersession uses the operator CLI. The preview and
supersession inspection endpoints take `record_id`; a preview belongs to the
authenticated caller that obtained it.

An observer can use [the authenticated process runner](authenticated-runner.md)
with `cairn agent --token-file OBSERVER_TOKEN run ...`, without opening the database.
The server records observations; commands execute on the client host.

An observer may reserve one launch with `claim-run` and a `receipt_id`. The
receipt must belong to that observer. Repeated claims do not authorize another
execution; ambiguous responses require [owner-only run status](run-status.md)
using the same profile and receipt ID. The read grants no launch permission. [Restore fences](restore-fencing.md)
make old receipts unusable for new claims without changing historical outcomes.

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

The inspected Striatum commit `a6b1ae71d95cdf99200c10d8c6ce855d9da70a69`
declares only work graphs and optional diagnostic review ledgers as inputs to
build contract v3 (`catalog/passes/build.yaml`). Its driver sources ordinary
dispatch inputs from accepted artifact heads, with admitted evidence handled
separately (`internal/driver/session_dispatch.go`). An arbitrary Cairn export
file therefore cannot enter a real build as an extra input under that contract.
Native integration must establish the permitted input and provenance path in
the owning compiler contract before enabling delivery; an adapter probe cannot
establish that contract change.

The same inspected tree has an accepted
`catalog/target-states/recall-compiles-to-knowledge.yaml` target. It names hippo
recall entering knowledge promotion through existing foreign-head attestations
and exogenous-change records. That is a relevant existing admission path to
investigate before adding Cairn-specific artifact machinery. It does not declare
Cairn a producer, make raw recall authoritative, or authorize ambient build inputs.
The decision index still lists RFC 0015 as in flight, and the RFC index records
its ladder as parked. Current driver dispatch also seals empty prompt assets.
The owning RFC/decision and admission code therefore still need reconciliation;
that target entry does not establish an available native Cairn route. The subsequent
[native admission assessment](verification/native-admission-assessment-2026-09-08.md)
found the accepted promotion contract and implementation, traced the historical
Verified request to a fixture-labelled audit, and reproduced limitations in the
current knowledge checker. It preserves the distinction between recorded
artifact acceptance, current source, live knowledge, and native Cairn delivery.

## User services

The units under `deploy/` manage this repository's dedicated PostgreSQL cluster
and Unix API. They assume the checkout is `~/git/cairn`, the installed binary is
`~/.local/bin/cairn`, and the store is `~/.local/share/cairn`. Adjust those paths
before installation if your layout differs. The API requires the store service;
neither unit uses the host PostgreSQL cluster or opens a TCP listener.

After building/installing the binary and creating the owner-only identity/token
files described above, install both units into `~/.config/systemd/user/` and run
`systemctl --user daemon-reload`. If the dedicated cluster was started manually,
back it up, then stop it with `bash scripts/local-store.sh stop` before starting
it under systemd. Enable/start `cairn-api.service`; it brings up `cairn-store.service`.
The store service uses `CAIRN_BINARY` to run migrations with the installed binary.

```sh
systemctl --user enable --now cairn-api.service
systemctl --user status cairn-api.service cairn-store.service
journalctl --user -u cairn-api.service -n 30
```

The API runs for the user-manager lifetime. Whether that includes time before
login or after logout depends on the machine's existing user lingering policy.
Stopping the API leaves the dedicated store running. Stop the store service to
shut down both. Unit files, source code and generated credentials have separate
lifecycles; never commit the token/config files.
