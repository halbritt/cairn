# Relay transport and connection reuse

Cairn's relay forwards JSON/HTTP requests from a local Unix socket to one HTTPS
origin. It reuses HTTP/1.1 connections to avoid establishing TLS for every call.
For enrollment and deployment, see [multiple machines](multi-machine.md).

## Connection policy

The [relay implementation](../localapi/relay.go) allows up to 16 idle connections
and 32 total connections per origin. Idle connections expire after 45 seconds;
closing the relay closes its idle connections. TLS certificate validation,
authentication, operation checks, and request/response limits apply on reused
connections too. Redirects are refused.

Connection reuse does not permit automatic replay. The relay constructs
non-rewindable POST bodies, including empty requests. If it cannot establish a
connection, it returns `UPSTREAM_UNAVAILABLE`. Once a connection has been assigned,
a failed exchange returns `UPSTREAM_UNCERTAIN`, because the upstream may have
received the request. Reconcile the result or retry with the exact same request
ID and arguments. Do not create a new mutation request to recover a lost reply.

The local Unix client also disables body replay. These transport rules preserve
Cairn's application-level retry contract; connection pooling does not establish
that a request completed.

## Evaluate transport changes

The [September 2026 comparison](history/transport-comparison-2026-09.md) records
local relay benchmarks and a separate JSON/Protobuf experiment, including
reproduction commands, hardware, repeat ranges, and limits. The production path
uses JSON/HTTP; the gRPC experiment is a separate module under
[`experiments/transportbench`](../experiments/transportbench/README.md).

The measurements compare encoding and transport costs over loopback. They do
not measure WAN behavior, database work, native notification latency, or task
outcomes. Run the experiment's own checks when changing it: the repository's
main test targets do not traverse that nested Go module.
