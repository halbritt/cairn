# Multiple machines

One central Cairn API owns the PostgreSQL store. Each joining machine runs a
small relay service that exposes the usual `~/.local/share/cairn/api.sock` and
forwards calls to the central API over HTTPS. Agents, hooks and the CLI on the
joining machine keep using the socket and token files they already use; they
never hold database credentials. The design and its accepted limits are in
[the proposal](plans/multi-machine-cairn.md).

Status: trial implementation, not yet deployed or accepted. The enrollment
commands, the network listener (`cairn serve --listen`) and the relay exist. An
isolated end-to-end test covers provisioning, the TLS listener, the relay,
enrollment, shared notes, directory attribution, remote limits and revocation.
Real harness delivery between two hosts is still unproven. No remote worker
pools, conversation migration, offline memory, repository sync or automatic
placement exist.

## What joining installs

A single enrollment file carries the central address, the canonical collection
and one token per role. `cairn machine enroll` installs:

| Path on the joining machine | Contents |
| --- | --- |
| `~/.local/share/cairn/hosted-agent.token` | ordinary agent profile `machine:ID/agent` |
| `~/.local/share/cairn/hosted-observer.token` | host observer profile, only when provisioned with `--observer`; it has no remote authority beyond `version` in this slice |
| `~/.local/share/cairn/machine.json` | machine ID, upstream, collection and principals; no secrets |
| `~/.config/systemd/user/cairn-relay.service` | `cairn relay --socket ~/.local/share/cairn/api.sock --upstream URL` |

The file names match the central host's, so existing skills, `AGENTS.md`
instructions and coordination bindings work unchanged. On an enrolled machine,
`cairn agent ...` and `cairn watch` default to `hosted-agent.token`. Agent routes
default `--repo` to the enrolled collection instead of the working directory, so
commands run from any checkout address the shared collection. The collection is
an identity string, not a local path: equal paths on two machines do not mean
shared files. Operator commands that open a local database are unaffected.

## Prerequisites

- The same Cairn build on every host, from one clean commit. Enrollment compares
  VCS revisions through the relay and refuses unstamped, modified or different
  builds. Build with `go build -o ~/.local/bin/cairn ./cmd/cairn` from a clean
  checkout; `cairn version` shows the stamp.
- An HTTPS route from each joining machine to the central API, verified with the
  system's ordinary certificate roots. On a tailnet, keep the API listener on
  loopback and let Tailscale terminate TLS:

  ```sh
  # central host: add these flags to cairn-api.service's ExecStart
  cairn serve --listen 127.0.0.1:8787 --tls-terminated-proxy
  tailscale serve --bg --https=8443 http://127.0.0.1:8787
  tailscale serve status
  ```

  Without a proxy, `--listen ADDR --tls-cert FILE --tls-key FILE` serves TLS
  directly. Plain HTTP is accepted only on a literal loopback address with
  `--tls-terminated-proxy`. `--machine-id` (default: the short hostname) names
  the central host's own sessions in the directory. The upstream is then
  `https://HOST.TAILNET.ts.net:8443`. Any local account on
  the central host can reach the loopback port, but every call still needs a
  bearer token, and the network listener admits only remote profiles.
- systemd user services on the joining machine. Enable lingering
  (`loginctl enable-linger`) so the relay keeps running after logout; enrollment
  reports the current setting.

## Join a machine

On the central host, provision the machine. `--collection` must match an
existing identity's repository unless `--allow-new-collection` is given, which
catches typos that would silently create a separate memory collection.

```sh
cairn machine provision --machine archon \
  --upstream https://proximal.TAILNET.ts.net:8443 \
  --collection "$HOME/git/cairn" --out ~/archon.enroll --restart
```

This adds `machine:archon/agent` (and `machine:archon/observer` with
`--observer`) to `identities.json` under the same lock
`scripts/provision-event-profiles.py` uses. Existing entries are preserved
exactly, and a timestamped owner-only backup is written first. The enrollment
file is created owner-only and exclusively before the configuration changes.
The API loads identities only at startup. `--restart` restarts
`cairn-api.service`, waits for it, and proves each new token over the local
socket. Without `--restart`, the result says a restart is required. Restarting
interrupts in-flight calls from every machine. Their relays report them as
uncertain (see below).

Copy the file privately and enroll:

```sh
scp -p ~/archon.enroll archon:archon.enroll && rm ~/archon.enroll
ssh archon 'chmod 600 archon.enroll && cairn machine enroll --file archon.enroll'
```

Enrollment checks every target before its first write. It refuses:

- a file that is not a regular 0600 file owned by you, has unknown fields, or
  names principals other than `machine:ID/ROLE`;
- a central host (one with `identities.json`);
- a live local API on the socket;
- a token file that holds a different token when this host is not already
  enrolled as that machine;
- symlinked or non-regular targets;
- an existing relay unit on a host that is not enrolled.

It then writes tokens, `machine.json` and the unit, enables and restarts the
relay, and calls `version` through the socket with each token. A failed
connectivity or build check reports `INSTALL_FAILED` with the step reached and
keeps the enrollment file. Fix the cause and rerun the same command; files that
already match are left unchanged. On success the enrollment file is deleted
unless `--keep-file` is given. Nothing is registered: agent sessions register
themselves as they do on the central host.

`cairn machine status` shows the enrollment, relay state, lingering and a
per-role connectivity check. It never prints tokens.

## Connect agents on the joining machine

Configure harnesses as on the central host, passing the canonical collection:

```sh
cairn claude-config --socket ~/.local/share/cairn/api.sock \
  --token-file ~/.local/share/cairn/hosted-agent.token \
  --repo /home/OWNER/git/cairn --task TASK --run RUN
```

Remote profiles use the hosted destination: local-only notes are not returned
to them. The central API also refuses operations outside the remote allowlist:
operator commands, worker and wake operations, managed-context registration,
remote process wrapping, cancellation capture, exclusive turns, and use/outcome
recording (`delivery`, `outcome`, `assess-run`, reports). Lifecycle hooks that
record uses and outcomes therefore get `AUTHORITY_DENIED` on a joining machine.
Memory search, notes, the directory, native inbox delivery, completion and
replies work. Directory output attributes each session to its server-configured
machine. Use `cairn agents list --machine-id ID` or `agents resolve --machine-id
ID` to select agents on one host.

## Rotate, revoke, leave

| Task | Command |
| --- | --- |
| Replace a machine's tokens | central: `cairn machine rotate --machine ID --upstream URL --out FILE --restart`; machine: `cairn machine enroll --file FILE` |
| Remove a machine's access | central: `cairn machine revoke --machine ID --restart` |
| Remove a machine's local setup | machine: `cairn machine unenroll` |
| Inspect configured machines | central: `cairn machine list` |

Rotation keeps the machine's principals, so its registered sessions,
inbox address and history are unchanged. Old tokens stop working at the
restart. The machine then enrolls the new file, which replaces its own tokens
without extra flags. A rotated file that changes the machine ID or its roles is
refused. To change those, run `unenroll`, then enroll the new file.

Revocation removes every profile with that machine ID. It does not stop
processes that are already running there. Their next API call is refused, and
their session rows remain in the directory until presence expires. `unenroll`
removes local files only. Revoke centrally as well.

The API accepts at most 32 identities. Each machine uses one or two, and
provisioning refuses before writing when the limit would be exceeded.

## Failed restart and rollback

When `--restart` cannot bring the API back, or a new token is not accepted, the
error names the backup and the exact rollback:

```sh
install -m 600 ~/.local/share/cairn/identities.json.before-provision-archon-TIMESTAMP \
  ~/.local/share/cairn/identities.json
systemctl --user restart cairn-api.service
```

Rolling back after provisioning leaves an unused enrollment file. Delete it.

## Errors through the relay

| Status | Meaning | Action |
| --- | --- | --- |
| `UPSTREAM_UNAVAILABLE` | The relay could not connect or complete TLS; nothing was sent | Retry when the central API is reachable |
| `UPSTREAM_UNCERTAIN` | The call may have been sent; its outcome is unknown | Retry only with the same request ID and arguments, or inspect state (`event-status`, `history`) |
| `AUTHORITY_DENIED` | Token revoked, rotated away, or operation outside the remote allowlist | Check `cairn machine status`; enroll a rotated file |
| `STALE_SESSION`, `STALE_LEASE` | Same meanings as on the central host | See [native inbox](native-inbox.md) and [agent sessions](agent-sessions.md) |

Transport reconnection never replays work. A disconnected machine's presence
expires, and explicit offline mail waits centrally. A lost completion is
reconciled through the recovery contract, never by registering a replacement
execution.
