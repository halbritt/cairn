# API protocol compatibility

Status: proposed policy (agent-204) for the API contract stage
(`docs/plans/api-contract-stage.md`). The base is `0fb09c3`; `b46a0e1` is
deployed. Enforcement is not yet implemented.

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

**Server, per request**, after authentication and before session binding,
decoding or dispatch:

- Several `Cairn-Protocol` values, or a value that is not a canonical decimal
  integer (no sign, no leading zero, at most 4 digits): `400 INVALID_REQUEST`.
- A value outside `[min, current]`, or an absent header while `min > 1`:
  `426 PROTOCOL_UNSUPPORTED`. The message names the supported range, and the envelope `protocol` object carries it in structured form. When
  `Cairn-Relay-Protocol` is absent on the network listener, the message says
  the relay may be the legacy one that drops the header.
- A protocol value never changes role, destination, principal, or which
  operations are allowed; authentication and `remoteOperations` still decide
  those. The check runs for `version` too, but it cannot lock a client out of
  learning the range, because refusals carry the range.

**Relay**: forwards exactly one `Cairn-Protocol` value (duplicates are
refused, as for the other protocol headers) and sets `Cairn-Relay-Protocol:
<relay current>`, overwriting any client value. It still forwards only the
response `Content-Type`; protocol information in responses therefore lives in
the envelope body.

**Client**: sends `Cairn-Protocol: <client current>` on every call. It ignores
unknown response and envelope fields, and refuses an unknown envelope schema.
When the envelope has no `protocol` field, the server is legacy (protocol 1).
The Go client records this for diagnostics; it does not refuse, since protocol 2
has no body changes.

**Enrollment and `machine status`**: compatibility becomes a protocol result.

- `compatible`: the ranges overlap and the checked path, this host's relay plus
  the central server, speaks the chosen protocol.
- `incompatible`: the ranges are disjoint.
- `unknown`: the reply is malformed.

A server with no `protocol` object is legacy `[1,1]`. Build identity is
reported separately as `same`, `different` or `unknown`. It is no longer a gate:
the gate was the stand-in for the missing protocol check. Enrollment refuses
`incompatible` and `unknown`. Status exits nonzero on them, but not on a build
difference.

**After enrollment**: every normal request is checked by the server. If the
server is upgraded so that its `min` exceeds a host's protocol, that host's next
call is refused before execution with `PROTOCOL_UNSUPPORTED`; nothing depends on
running `machine status`. A newer client against an older server stays safe
through strict request decoding and the no-changed-semantics rule. The older
server executes only requests whose bodies it fully understands.

## Transition from the deployed release

- Legacy clients and relays (`b46a0e1`) send no header. They are accepted as
  protocol 1 while `min` is 1, which it is in this release.
- A protocol 2 client behind a legacy relay loses its header and is treated as
  protocol 1. That is correct because protocol 2 bodies equal protocol 1 bodies.
- A protocol 2 relay or client against a legacy server: the server ignores the
  header and the response has no `protocol` field. It keeps working, and
  diagnostics report a legacy server.
- `min` rises to 2 only in a later release, after `machine status` on every
  enrolled host reports protocol 2 relays and clients. The server can observe
  this from the headers.

## Test plan

Independent old-version fixtures: the deployed `b46a0e1` binary, built from a
clean clone of that revision. Each combination is a separate case:

- a new client through a new relay to a new server;
- a legacy client and legacy relay to a new server;
- a new client through a legacy relay to a new server (the header is stripped,
  so protocol 1 is accepted);
- a new client and new relay to a legacy server;
- a legacy client to a legacy server as a baseline;
- malformed, duplicate and out-of-range headers;
- a new server built for tests with `min=2` (a test-only build tag, not a
  runtime flag), refusing legacy and header-stripped requests before execution;
- the enrollment and status results for each;
- no effect is committed on any refusal.

Unit tests cover header parsing, range checks, the envelope field, client
tolerance of unknown response fields, and relay header forwarding.
