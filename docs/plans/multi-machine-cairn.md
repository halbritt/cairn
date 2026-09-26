# Multiple machines sharing one Cairn

Status: implementation authorized by the owner after agent-183 review.
The owner requires a functional, robust implementation, cross-model review of
plans and builds, and permits deployment and agent testing on Archon. No
implementation or deployment is claimed yet. The original runtime source review
used `ddb01a6`; implementation starts from `2dbee9a`.

## Intended experience

Agents on different machines share memory, discover one another, and exchange
durable requests and replies through one Cairn server. Each machine runs a local
host service. Only the central server connects to PostgreSQL.

The owner selected this topology and explicitly asked to keep authentication
easy. Joining a machine should require importing one enrollment file containing
the server address and credentials. Setup installs role-specific token files
automatically. Existing agents keep using their local Cairn socket and tools.
There is no per-agent login or client-certificate enrollment.

```text
Machine A: agents / native hooks -> local host service --+
                                                       +-> Cairn API -> PostgreSQL
Machine B: agents / native hooks -> local host service --+
```

## Responsibilities

The central server owns memory, collection access, agent identity, directory
resolution, events, leases, durable attempt holds, and completion records.
Database time remains authoritative for expiry. The central server authenticates
every operation and retains the existing destination and authority checks.

The local service forwards the existing Unix socket API to the central HTTPS
endpoint, preserving Authorization and session headers. It does not parse
operations, select roles, or retry mutations automatically. The existing watcher
and harness adapters continue to own native context files, process checks and
wake delivery. Their sockets and process references stay local to each machine.
Polling or watching is outbound; remote machines need no inbound listener.
Enforce the remote operation allowlist at the central API, per identity.

PostgreSQL is the only operational store. Local files retain the existing host
attempt/retry state needed to reconcile uncertain calls. They are not an offline
memory database. There is no automatic copy of repositories or working files.

## Simple authentication and setup

Reuse the existing bearer-token profiles. Each token currently maps to exactly
one principal, collection, role and destination (`localapi/server.go`). Keep an
ordinary agent profile and, when host observation is needed, a separate observer
profile for each machine. The existing worker supervisor needs both; the first
trial excludes that supervisor and can start with the agent profile alone.
Provision required profiles together in one owner-only enrollment file. The
setup helper imports it, writes the existing token files, installs/starts the
local service and checks connectivity. Users do not manage individual agent
credentials or select roles on requests.

Use an ordinary HTTPS endpoint with normal server verification, preferably over
the owner's existing private network. Agent-183 suggested the existing tailnet
and deployment-managed HTTPS; verify that deployment route before installation.
There is no Cairn certificate authority, client-certificate enrollment or OAuth.
The server retains token digests; never put token values in command arguments.

Every machine needs distinct stable principals, for example `machine:box-b/agent`
and `machine:box-b/observer`. Token replacement keeps those principal names.
Machine display labels can change without changing principals. Revoking a machine
means disabling all of its profiles, not merely one role's token.

The current configuration loads at server startup, permits up to 32 profiles,
and requires unique principals. For the two-machine prototype, central enrollment,
rotation and revocation update configuration and restart the API. Safe hot reload
is an optional later convenience, not a prerequisite for this trial. The helper
must check available profile capacity and report restart failure explicitly.

The forwarder never substitutes an observer token for an ordinary client's token.
The central server retains role, collection and hosted-destination enforcement;
request fields cannot select another principal. Operator endpoints remain absent
from the agent API. The cooperative same-owner-UID trust model remains explicit;
this does not isolate hostile local processes under that same account.

The first slice uses hosted-eligible memory only. Being another machine owned by
the same person does not automatically permit delivery of local-only records.

## Identity and collection scope

Current registration is scoped by collection, authenticated profile owner,
launcher binding, and native session ID (`core/agent_sessions.go`). Distinct
stable principals per machine are required: sharing a principal can merge equal
native session IDs and fence the other machine. Reuse that existing namespace.
The worker supervisor also reconciles attempts owned by its principal at startup;
sharing it across machines would confuse local cleanup with remote execution.

The directory should expose a server-derived machine label/ID so operators can
select an agent on a particular box. A copied native session ID on another box
must create a separate agent. Moving a continuing conversation between machines
is outside the first slice.

All machines use the same configured collection identity. Today that identity
is a canonical repository-path string, independent of working directory; keep
that existing identity during the trial. A local checkout path remains reported
workspace metadata. Different local paths must not create different memory
collections, and equal paths on different machines must not imply shared files.

## Disconnect and retry behavior

| Situation | Required behavior |
| --- | --- |
| Machine is disconnected before admission | Presence expires; it is unavailable for live resolution. Explicit offline inbox mail remains queued centrally. |
| API reply is lost after a mutation commits | Retry the same operation with the same request ID and arguments. Never change recipient or manufacture a new request ID. |
| Connection fails after native delivery | Retain the durable attempt hold. A lease or presence expiry does not authorize a second delivery. |
| Work continues while disconnected | The local process may continue, but shared reads/writes fail explicitly. No offline success or fabricated completion. |
| Completion reply is lost | Retry the exact completion under its original execution before re-registration. An already-committed response can replay despite lease expiry; a replaced execution is refused before cache lookup. Inspect event status when direct replay is no longer possible. |
| Lease expires before completion is accepted | Current behavior refuses with `STALE_LEASE`. The durable hold prevents duplicate work, but there is no late-result acceptance path. Reconcile as unresolved/failed on the central host; do not silently reacquire or mark success. |
| Local service restarts while an agent survives | Recover local attempt state and verify PID, process start, boot ID and native session before renewing or reconciling. Do not register a replacement execution around a hold. |
| Central API restarts | Retained DB state continues to govern identity and attempts. Reconnect with bounded backoff. |
| Database is restored | Existing generation fences apply. Stale executions cannot renew or complete; reconciliation precedes fresh registration. |
| Machine token is revoked | Further API operations are refused. Already-running local work may continue; revocation does not prove process termination. |

Reconnect automatically at the transport layer. This is not automatic replay of
work. Server or machine unavailability can leave work unresolved until the host
returns or an operator reviews it. The proposal does not promise exactly-once
external effects or automatic recovery of every partition.

Cancellation stays limited to existing proven native capabilities. No remote
success acknowledgment is inferred from a sent command or a missing heartbeat.
Remote cancellation remains disabled in the trial. Migration 049 currently
uniquely binds exclusive turns by collection and native turn ID, without owner
scope; qualify that identity before proposing remote cancellation.

### Recovery work exposed by review

Native leases last 90 seconds. A partition spanning completion can therefore
leave a real result uncommitted. A longer lease only changes the window; it does
not solve arbitrary partitions. The prototype initially preserves today's
conservative refusal and demonstrates the unresolved hold. Implementation must
supply a usable recovery path before acceptance: explicit operator recovery or
a separately tested late-completion contract tied to the same unreleased hold,
execution and database generation.
Do not relax the lease check as a transport implementation detail.

Before re-registering after restart, recover pending operations with their
original request IDs and execution. A committed completion's retry can replay
from the cache, but `core/store.go` checks session validity first. If that
execution has already been replaced, use status inspection and central operator
reconciliation rather than rewriting its identity.

Native claims retain idempotent poll IDs; ordinary `event-next` does not have a
request ID. Keep remote consumers on the native claim path in this trial.
`release_inbox` persists its reconciliation ID but makes a new one when the
reason changes: finish or inspect the pending intent before replacing it.
The deferred worker supervisor's `wake-change` also generates a fresh request ID
per call and needs persisted retry intents before remote worker support.

Operator recovery commands use direct database access on the central host.
A remote service cannot perform that recovery by forwarding an operator command.

## First implementation slice

1. Reuse the existing HTTP API handler behind an authenticated network listener;
   retain the local socket. Define an explicit remote operation allowlist.
2. Add a small Unix-socket-to-HTTPS forwarder with bounded timeouts and no
   application retry policy. Ordinary CLI/MCP calls still target its Unix socket.
   Central profiles enforce scope, role, destination and the remote allowlist.
3. Connect the current watcher and native delivery adapter through the relay.
   Add machine attribution to directory output without changing conversation UUIDs.
4. Demonstrate two agents on two machines sharing one selected note and exchanging
   one request, completion and explicit reply.
5. Interrupt the connection at admission, delivery and completion boundaries;
   demonstrate either safe reconciliation or an explicit retained hold, with no
   automatic duplicate execution.

Remote worker pools, placement, conversation migration, offline memory caching,
and distributed PostgreSQL are deferred. The first trial supports existing agent
sessions with the same tested protocol version on both hosts.

The API does not directly execute remote processes or purge remote files. The
hazard is accepting local coordinates as central-host facts.
`core/managed_context.go` records directory/device/inode coordinates without a
machine namespace; the operator purge can later act on the wrong filesystem.
Before exposing the remote listener, mark remote identities in server
configuration and refuse `register-context` for them. Remote process wrapping
that requires context registration is therefore excluded. Selected evidence
bytes may use an explicitly supported byte-upload contract; a remote path must
never be opened on the server as if it were local. The forwarder can stay a
pass-through transport because these restrictions are enforced centrally.

## Acceptance checks for the trial

- A fresh second machine joins by importing one enrollment file; starting another
  agent requires no additional enrollment or manual role configuration.
- A shareable note saved on A is retrieved on B under the same collection.
  Local-only content remains excluded from hosted retrieval.
- Equal binding/native-session IDs on A and B do not merge agent identities.
  A's credential cannot act as B's machine/profile or complete B's attempt.
- An ordinary local client cannot invoke host-observer or operator operations.
- Both agents complete a selected request/reply exchange. Completion and task
  acceptance remain separate claims.
- Dropped replies and repeated requests preserve event/attempt identity. A
  disconnected active attempt cannot be claimed by a second consumer after expiry.
- API restart, local-service restart, token replacement/revocation, and disposable
  database restore produce the explicit behaviors above.
- The local socket workflow remains usable with remote mode disabled.

Use a disposable PostgreSQL cluster and isolated host fixtures for automated
failure tests, then two actual machines for the bounded trial. Store changes
require `make test-integration` and `make check`. Passing simulated network tests
does not establish real harness delivery; retain that distinction in the result.

## Review questions and decision status

Agent-183 reviewed the draft and source. Incorporated corrections: reuse the
existing token-per-role model inside a single enrollment file; require distinct
machine principals; keep the forwarder free of application logic; enforce remote
restrictions centrally; and distinguish a lost completion response from a result
that never committed before lease expiry. The original claim that one token
could cover both agent and observer roles is withdrawn. Agent-only sessions may
use one token; enabling observer operations requires their existing separate role.

The topology is the owner's selected direction. The review is not implementation
acceptance. Material open questions are the precise operation allowlist, recovery
of pending intents on restart, and whether routine use needs late completion.
The trial must answer those before rollout. Hot reload, performance, broad host
support and automatic failover remain outside the trial's acceptance claims.

Alternatives considered: continuing local-only operation does not meet the owner
request; separate Cairn servers with direct shared-DB access were declined in
favor of the central API; a transparent socket tunnel is a useful transport
fixture but does not by itself resolve machine attribution or file ownership.

Relevant existing contracts: [local API](../local-api.md),
[agent sessions](../agent-sessions.md), [native inbox](../native-inbox.md),
[request controls](../request-controls.md), and
[restore fencing](../restore-fencing.md).
