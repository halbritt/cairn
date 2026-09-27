# API protocol compatibility

Status: implemented on `agent-204/api-compatibility` (base `0fb09c3`; the
deployed release `b46a0e1` is protocol 1). Code: `localapi/protocol.go`,
`localapi/relay.go`, `localapi/client.go`, and the enrollment/status checks in
`cmd/cairn/machine_enroll.go`. Evidence: `localapi/protocol_test.go` and
`scripts/check_protocol_skew.py`, which runs in `make test-integration`.

## Protocol versus build

A *protocol version* is a positive integer that names the wire contract: the
routes, request and response JSON shapes, error envelope, headers, and the
semantics of each operation. It is separate from the VCS build identity
(`cairn.build/1`: revision, modified flag, Go version). The build identity stays
diagnostic. It can say "same build", "different build" or "unknown build", but
it never decides compatibility.

Each binary declares a supported range `[min, current]` for the roles it plays
(client, relay, server). Two parties are compatible for a request only when the
request's protocol lies in the server's range. A shared "major version" says
nothing about which additions each side knows, so compatibility is decided
per request against an exact integer range, not by a matching major.

| Protocol | Definition |
| --- | --- |
| 1 (legacy) | The deployed `b46a0e1` wire behavior. Requests carry no protocol header. The relay forwards only `Authorization`, `Content-Type`, `Cairn-Agent-ID` and `Cairn-Execution-ID`. Responses carry no protocol field. |
| 2 | Protocol 1 plus protocol declaration: the `Cairn-Protocol` request header, the envelope `protocol` object `{"min":M,"current":N}` on every reply (success and refusal), the same object in the `version` response, and a relay that forwards `Cairn-Protocol` and adds `Cairn-Relay-Protocol`. Operation bodies and semantics are unchanged from protocol 1. |

This release declares server, relay and client range `[1, 2]`.

## Rules for changes

| Change | Protocol effect |
| --- | --- |
| New optional request field, with absence meaning the old behavior | No bump. An older server refuses the unknown field (strict decoding, `INVALID_REQUEST`), so it is never silently ignored. |
| New response field or envelope field | No bump. Clients must ignore unknown response fields and must not strictly decode responses. |
| New operation (route) | No bump. An older server answers `NOT_FOUND` (or `AUTHORITY_DENIED` remotely). Its remote exposure needs an explicit `remoteOperations` decision. |
| New error status | No bump for a new refusal code. Clients treat an unknown status as a refusal with that code, never as success or as a transport failure. |
| New required request field, or removed field or route | Bump `current`. Keep accepting the old form until `min` is raised. Raising `min` is the breaking step, and it is a separate, announced release. |
| Changed meaning of an existing field, route, header, status or retry rule | Not allowed in place. Use a new field or route name, or bump and raise `min`. Retry and idempotency semantics of an existing operation never change within its name. |
| Envelope incompatibility | A new envelope schema string (`cairn.response/2`), which requires a bump. Clients refuse an unknown schema, as they do today. |

Request constraints (limits, deadlines, conditions, idempotency keys) travel
in the JSON body, never only in a header. The relay forwards the body verbatim,
while older relays drop unknown headers. A header may carry only transport,
authentication, session selection, or declaration metadata whose absence is
safe. Absence of `Cairn-Protocol` means protocol 1.

## Enforcement

Negotiation is not a handshake that changes later behavior. Each request is
checked on its own, in both directions:

- the server checks that it supports what the client declares;
- the client, when its own minimum requires it, makes any server that cannot
  meet that minimum refuse before executing.

**Server, per request**, after authentication and before session binding,
decoding or dispatch:

- A duplicate `Cairn-Protocol`, or a value that is not a canonical decimal
  integer (no sign, no leading zero, at most 4 digits): `400 INVALID_REQUEST`.
  The same rule applies to `Cairn-Relay-Protocol`.
- A declared value outside `[min, current]`: `426 PROTOCOL_UNSUPPORTED`. The
  message names the range, and the envelope `protocol` object carries it.
- No header: the request is protocol 1 unless the body guard (below) says
  otherwise. While `min > 1`, a request with neither is refused `426` before
  decoding. On the network listener without `Cairn-Relay-Protocol`, the message
  adds that the host's relay may be a legacy one that drops the header.
- A protocol value never changes role, destination, principal or the
  operation allowlist.

**Body guard (client minimum).** A header cannot protect a client from an
older server that ignores it. That includes a server rolled back behind the
same relay after a successful `version` preflight: a time-of-check gap no
preflight closes. So a client whose minimum `M > 1` adds the reserved
top-level field `"cairn_protocol": M` to every JSON object request. Servers
since protocol 2 verify it before strict decoding and then remove it:

- a canonical positive integer within range is accepted;
- a malformed value, or a header below it, gets `400`;
- a value above the server's range gets `426`.

Every older server refuses the unknown field under its existing strict decoding
(`INVALID_REQUEST`) before executing. The client maps that reply, which carries
no protocol range, to `PROTOCOL_UNSUPPORTED` ("the server predates protocol M").
The body travels through The body travels through every relay unchanged, so the guard also survives
header stripping. The guard is extracted token by token. Every other top-level
member keeps its exact bytes and order, including duplicate or differently
cased keys, so an admitted request decodes, and is digested for idempotency,
exactly as it would without the guard. A duplicate guard, or a case-variant
spelling of it, is refused with `400`. A client never lets a caller supply the
reserved field. This release's client minimum is 1, so it sends no guard and
legacy servers keep accepting its requests.

**Relay**: forwards exactly one `Cairn-Protocol` value (duplicates are refused,
as for the other protocol headers). It sets `Cairn-Relay-Protocol: <relay
current>`, overwriting any client value. It forwards the body verbatim. It
still returns only the response `Content-Type`, so protocol information in
replies lives in the envelope body. The `protocol` object is always the
replying party's range: the server's for forwarded replies, and the relay's
for the relay's own `UPSTREAM_*` refusals.

**Client**: sends `Cairn-Protocol: <client current>` on every call, and the
body guard when its minimum exceeds 1. It ignores unknown response and envelope
fields, and refuses an unknown envelope schema. A reply without a `protocol`
object comes from a legacy server. That is treated as protocol 1 only because
the retained `b46a0e1` fixture establishes protocol 1 semantics; a malformed
range counts as `unknown` and fails closed.

**Enrollment and `machine status`**: compatibility becomes a protocol result.

- `compatible`: the ranges overlap.
- `incompatible`: the ranges are disjoint, or the server refused the declared
  protocol.
- `unknown`: no answer, or a malformed range.

A server with no `protocol` object is legacy `[1,1]`. Enrollment refuses
`incompatible` and `unknown`, and status exits nonzero on them. Build identity
is reported separately and never gates.

"Compatible" is conditional, and it is a statement about protocol-2 bodies.
Using an optional request field that a given server predates is still refused
by that server's strict decoder (`INVALID_REQUEST`) at the time of use. That
is safe, but a status of `compatible` does not promise every newer optional
field. It also does not re-check a server that changes afterwards. The per-request checks
cover that: the server's header check, and the body guard for clients that
require a minimum.

**After enrollment**: if the central server is upgraded so that its minimum
exceeds a host's protocol, that host's next request is refused before
execution, whether or not status has run. If the server is rolled back below
a client's minimum, the body guard refuses. A client with minimum 1 needs no
guard, because protocol 1 and 2 bodies and semantics are identical.

## Transition from the deployed release

- Legacy clients and relays (`b46a0e1`) send no header. They are accepted as
  protocol 1 while `min` is 1, which it is in this release.
- A protocol 2 client behind a legacy relay loses its header and is treated as
  protocol 1. That is correct because protocol 2 bodies equal protocol 1 bodies.
- A protocol 2 relay or client against a legacy server: the server ignores the
  header and the response has no `protocol` field. It keeps working, and
  diagnostics report a legacy server.
- `min` rises to 2 only in a later release, after every enrolled host runs
  protocol 2 clients and relays. The server can observe this from the headers.
  Once raised, a legacy relay that strips the header is refused unless the
  client sends the body guard.
- Future semantics must never rely on a stripped header. A change whose absence
  would be unsafe travels in the body (a new field, or the body guard), so any
  server that cannot honor it refuses the request.

## Evidence and limits

`scripts/check_protocol_skew.py` runs real `serve`, `relay` and CLI binaries
over HTTPS against a disposable database. It uses three binaries:

- **legacy**: `b46a0e1`, built from a clean `git clone --no-local` at that
  revision, or the independently retained binary named by
  `CAIRN_LEGACY_BINARY`. The suite verifies its clean stamp.
- **current**: the build under test.
- **raised**: the build under test with the test-only `cairn_protocol_min2`
  tag, standing in for a future server that has raised its minimum.

It covers:

- legacy-only as a baseline;
- both skew directions, and a current client behind a legacy relay that strips
  the header;
- a raised server refusing a legacy client, a current client behind a legacy
  relay, and a fully legacy path, each with `PROTOCOL_UNSUPPORTED` and no store
  change;
- the other incompatible direction: a raised client refused by a legacy server
  (directly, and behind a legacy relay), with no store change. The raised
  client's guard survives a legacy relay to current and raised servers. After a
  successful `version` preflight against a current server, the server is rolled
  back to legacy behind the same relay; the next mutation is refused and nothing
  is written;
- a current client through a current relay to the raised server, which
  succeeds;
- malformed and out-of-range declarations and body guards sent straight to the
  TLS listener, including a header contradicting the guard;
- `machine status` against current, legacy and raised servers.

Unit tests cover:

- header parsing, including duplicate, signed, zero-padded, non-ASCII and
  oversized values;
- range overlap;
- admission before dispatch (a store route with no store attached);
- the envelope and `version` fields;
- relay forwarding, including overwriting a client-supplied
  `Cairn-Relay-Protocol`;
- client tolerance of unknown response fields;
- the enrollment and status decisions.

Limits:

- The deployed CLI replaces messages of statuses it does not know with a
  generic text. A legacy client refused by a raised server sees the
  `PROTOCOL_UNSUPPORTED` status and exit code but not the explanation. Current
  clients show the full message.
- A newer client with minimum 1 talking to an older server is protected by
  strict request decoding and the no-changed-semantics rule, not by a server
  check. A client with a higher minimum is protected by the body guard, which
  depends on strict decoding. A hypothetical older server that ignored unknown
  fields would not be protected; no such Cairn release exists.
- The full client, relay and server matrix with retry deduplication belongs to
  root's independent harness (`scripts/check_api_version_skew.py`). This suite
  targets refusals, raised minimums and status.
- The protocol claim is declaration metadata. A client that lies about its
  protocol gains nothing: authorization is unchanged, and a request body the
  server does not understand is refused.
- Python lifecycle adapters call the CLI, so they declare the CLI's protocol.
  They do not parse the envelope's `protocol` object.
