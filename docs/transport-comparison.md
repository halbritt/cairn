# Connection reuse and transport comparison

Implementation decision, 2026-09-26: retain JSON/HTTP and adopt bounded HTTP/1.1
connection reuse in the relay, with explicit prevention of transport replay.
Keep gRPC experimental. This is a code and local measurement result; root owns
integration, release acceptance and deployment.

The baseline is main `0fb09c3` (deployed runtime `b46a0e1`). The relay previously
opened a fresh TLS connection for every call. The candidate changes only the
connection policy and non-replayable body construction: 16 idle connections,
32 maximum connections per origin, 45-second idle expiry. `Close` discards idle
connections. Certificates, bearer forwarding, operation/path checks, body
bounds, timeout, redirect refusal and error envelopes retain their contracts.
The pooled Unix client also clears `GetBody`, fixing its existing mismatch with
its documented no-retry guarantee. No production dependency or database schema
is added. The gRPC dependencies live only in `experiments/transportbench`.

Idle cadence follow-up: the measured candidate used a 30-second idle expiry,
equal to the production watcher's 30-second scan interval. The tight-loop
measurements below establish reuse within a burst, but do not establish reuse
between scans: the original pool could expire by the next scan. The relay now
uses 45 seconds, allowing 15 seconds of cadence slack while remaining below
the central server's 60-second idle timeout. This retains the same connection
count bounds and non-replayable POST construction. The experimental HTTP pool
remains at the measured 30 seconds; the tables have not been remeasured after
this cadence change.

`TestRelayReuseAcrossWatcherCadence` uses real verified loopback TLS and a
60-second server idle timeout. It checks that successful requests separated by
31 seconds use one connection, then loses a reply on that retained connection
and requires `UPSTREAM_UNCERTAIN` with no extra connection or handler call.
This tests the timer boundary directly, not the production watcher itself.
Longer scheduling gaps, shorter intermediary idle limits, and WAN behavior
remain outside this result; discarded idle connections may require fresh TLS.

## Reproduce

Use Go 1.25.0 (the measured toolchain). Run timing without the race detector and
without other builds or benchmark processes when possible. Correctness tests
use `-race` separately. The experiment module has its own pinned dependencies;
initial downloads can require `GOPROXY=https://proxy.golang.org`.

```sh
# Repository root; a real TLS hop through the actual Relay implementation.
go test ./localapi -run '^$' -bench BenchmarkRelayConnectionPolicy \
  -benchtime=500x -count=3 -cpu=4 -benchmem

# Independent typed JSON/Protobuf comparison.
cd experiments/transportbench
go test -race ./...
go vet ./...
go test -run '^$' -bench BenchmarkExchange -benchtime=500x -count=3 -cpu=4 -benchmem
```

Add `-json` to retain machine-readable output outside the repository. Generated
Protobuf bindings and regeneration instructions are in the experiment README.
The main `make check`/`make test-integration` do not traverse the nested module;
run its two checks explicitly.

Environment: Linux 6.8.0-138-generic amd64, Intel Core i5-11400F (12 logical CPUs), Go 1.25.0,
GOMAXPROCS=4, gRPC 1.75.1, Protobuf 1.36.8. Both ends run on this same shared
host over loopback. No WAN/tailnet shaping or production data was used. These
are observed local distributions, not cross-machine estimates. Other agents
were active on the host; CPU scheduling was not isolated. Broad repeat ranges
and the large-payload tail behavior below must remain visible.

Each case has 500 measured calls and three repeats, 1 or 8 fixed workers,
256 B / 16 KiB / 256 KiB identical body text in each direction, verified TLS 1.3,
no compression, no session resumption. Warmup is excluded. Per-call latency
includes encoding/decoding and response validation in the typed comparison;
relay timing includes its actual validation/forwarding/copying but uses opaque
bodies and excludes the Unix client hop. Neither experiment includes database
work, CLI startup, real event semantics or host notification scheduling.

In the typed comparison, both servers authenticate the same synthetic bearer
value on every call, validate request ID and collection, hash the same body,
and return request ID, body and SHA-256. gRPC uses a generated unary service, not a byte-echo custom
codec. `grpc-proto` uses `grpc.Server.ServeHTTP` over Go's HTTP/2 server;
`grpc-native` uses the native `grpc.Server.Serve` TLS transport. Both multiplex
one connection; HTTP/1 uses the pool under the same worker count. The native
server enforces 32 maximum streams. ServeHTTP uses Go HTTP/2 defaults; gRPC's
MaxConcurrentStreams option does not configure it. Concurrency eight stays
below either transport's normal limit. Every reply is checked and the
handler call count must match; all measured calls succeeded.

`ns/op` is throughput inverse, not concurrent request latency. The tables use
per-call percentiles. Warmup does not guarantee all eight pool lanes are open;
extra measured dials are included. Benchmark logging suppresses TLS server diagnostics from cancelled speculative
dials at teardown so they cannot split metric rows. Returned call failures
still fail the benchmark. These final tables come from matched four-variant
and relay runs after adding the native gRPC comparison, not the earlier
exploratory runs.

## Relay connection policy

All latency figures are microseconds. Each cell is the median of three per-run metrics; the throughput range shows the minimum and maximum run, not a confidence interval.

| Payload | Workers | Variant | p50 | p95 | p99 | Calls/s (range) | New conns/call |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 256 B | 1 | fresh HTTP | 1893 | 4220 | 5309 | 435 (353–506) | 1.000 |
| 256 B | 1 | reused HTTP | 54 | 77 | 93 | 17390 (16344–17447) | 0.000 |
| 256 B | 8 | fresh HTTP | 3493 | 6772 | 9114 | 2094 (2077–2123) | 1.000 |
| 256 B | 8 | reused HTTP | 118 | 795 | 4173 | 26970 (15066–35439) | 0.030 |
| 16 KiB | 1 | fresh HTTP | 2024 | 3319 | 3947 | 454 (366–492) | 1.000 |
| 16 KiB | 1 | reused HTTP | 110 | 289 | 352 | 7397 (7304–7573) | 0.000 |
| 16 KiB | 8 | fresh HTTP | 4743 | 7890 | 9219 | 1616 (1584–1683) | 1.000 |
| 16 KiB | 8 | reused HTTP | 691 | 1546 | 1999 | 10177 (9622–10708) | 0.000 |
| 256 KiB | 1 | fresh HTTP | 3165 | 3898 | 4307 | 308 (298–318) | 1.000 |
| 256 KiB | 1 | reused HTTP | 1081 | 1375 | 1509 | 916 (641–917) | 0.000 |
| 256 KiB | 8 | fresh HTTP | 8616 | 12708 | 14997 | 897 (732–935) | 1.000 |
| 256 KiB | 8 | reused HTTP | 4539 | 8694 | 15138 | 1586 (1501–1683) | 0.000 |

## Typed payload comparison

All latency figures are microseconds. Each cell is the median of three per-run metrics; the throughput range shows the minimum and maximum run, not a confidence interval.

| Payload | Workers | Variant | p50 | p95 | p99 | Calls/s (range) | New conns/call |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 256 B | 1 | http-fresh | 1882 | 3435 | 4408 | 475 (431–515) | 1.000 |
| 256 B | 1 | http-reuse | 68 | 90 | 116 | 14190 (12074–14577) | 0.000 |
| 256 B | 1 | grpc-proto | 140 | 186 | 261 | 6821 (6513–7185) | 0.000 |
| 256 B | 1 | grpc-native | 92 | 128 | 172 | 10641 (10628–10885) | 0.000 |
| 256 B | 8 | http-fresh | 3700 | 7179 | 9580 | 2008 (1786–2040) | 1.000 |
| 256 B | 8 | http-reuse | 131 | 638 | 4897 | 27354 (24122–38504) | 0.030 |
| 256 B | 8 | grpc-proto | 335 | 693 | 838 | 22126 (21873–22165) | 0.000 |
| 256 B | 8 | grpc-native | 163 | 370 | 526 | 43454 (43444–44351) | 0.000 |
| 16 KiB | 1 | http-fresh | 2127 | 3088 | 4327 | 442 (434–452) | 1.000 |
| 16 KiB | 1 | http-reuse | 292 | 540 | 695 | 2904 (2420–3089) | 0.000 |
| 16 KiB | 1 | grpc-proto | 222 | 318 | 521 | 4289 (4232–4311) | 0.000 |
| 16 KiB | 1 | grpc-native | 145 | 239 | 335 | 6458 (6053–6587) | 0.000 |
| 16 KiB | 8 | http-fresh | 5271 | 8372 | 9745 | 1456 (1449–1506) | 1.000 |
| 16 KiB | 8 | http-reuse | 1076 | 2107 | 3009 | 6831 (5949–7065) | 0.006 |
| 16 KiB | 8 | grpc-proto | 682 | 1088 | 1233 | 11410 (10860–11865) | 0.000 |
| 16 KiB | 8 | grpc-native | 368 | 680 | 789 | 19906 (19519–20359) | 0.000 |
| 256 KiB | 1 | http-fresh | 6204 | 8364 | 11016 | 155 (154–156) | 1.000 |
| 256 KiB | 1 | http-reuse | 4052 | 6442 | 9288 | 224 (211–246) | 0.000 |
| 256 KiB | 1 | grpc-proto | 1118 | 1515 | 1650 | 864 (846–866) | 0.000 |
| 256 KiB | 1 | grpc-native | 1062 | 1395 | 1619 | 924 (836–927) | 0.000 |
| 256 KiB | 8 | http-fresh | 16176 | 24163 | 28200 | 481 (458–500) | 1.000 |
| 256 KiB | 8 | http-reuse | 9293 | 16014 | 20650 | 803 (644–829) | 0.000 |
| 256 KiB | 8 | grpc-proto | 5157 | 8173 | 9607 | 1485 (1454–1613) | 0.000 |
| 256 KiB | 8 | grpc-native | 3906 | 5966 | 6820 | 1979 (1569–2070) | 0.000 |

For serial 256 B requests in the typed experiment, median TLS bytes per call
were 5,125 fresh HTTP, 1,076 reused HTTP, 924 ServeHTTP gRPC and 916 native
gRPC. At 256 KiB they were 529,937, 525,602, 526,945 and 526,079: Protobuf does not imply less total wire traffic
for every payload. Counters include both directions at the TCP/TLS boundary
and measured handshakes, excluding IP/TCP framing. HTTP/2 control frames can
arrive asynchronously. `first-us` in raw benchmark output separately includes
the first call's connection/channel establishment; it is not included in the
steady distributions. These first-call samples are too few for a cold-tail
claim.

## Interpretation and safety evidence

The relay comparison isolates reuse from encoding. For 256 B serial calls it
reduced median p50 from 1,893 to 54 microseconds, with no new steady connections.
This supports removing per-call handshakes. It does **not** establish a universal
latency improvement: the 256 KiB/eight-worker relay p99 was 15,138
microseconds with reuse versus 14,997 fresh, despite lower p50/p95. Earlier
exploratory runs under higher shared-host load showed much larger tail
regressions; the final repeat ranges still expose variability. Reproduce under the intended workload before setting an
SLO or choosing pool capacity; no capacity tuning is inferred here.

The matched typed experiment shows why native gRPC remains worth studying:
for serial 256 KiB calls its median p50 was 1,062 microseconds versus 4,052
for reused HTTP, and at eight workers it delivered 1,979 versus 803 calls/s.
For serial 256 B calls, reused HTTP was faster (68 versus 92 microseconds
p50); native gRPC had greater throughput at eight workers (43,454 versus
27,354 calls/s). ServeHTTP gRPC was slower than native on most cases. That comparison changes
encoding, multiplexing and server integration
together; the speed difference is not attributed solely to Protobuf. Reuse is
separately demonstrated by identical HTTP paths. A migration would additionally
require typed operation coverage, store/role/session authorization, protocol
compatibility, durable idempotency, event completion and cancellation contracts,
legacy rollout, and fault qualification of HTTP/2/gRPC retry behavior. None of
those are supplied by a synthetic service. No gRPC endpoint enters Cairn.

Go 1.25's `persistConn.shouldRetryRequest` permits a reused-connection zero-write
retry when the body is empty or rewindable. Merely clearing `GetBody` is not
enough for a raw empty relay request. Relay POST bodies now remain non-nil and
non-rewindable, including empty input (unknown length/chunked); protocol headers
exclude idempotency keys, and HTTP/2 stays disabled. `Client.Call` always has a
nonempty JSON body and clears `GetBody`. A failed connection handed to the
transport stays `UPSTREAM_UNCERTAIN`, even if a test knows zero bytes were
written. Failure before assignment, including expiry waiting for a pool slot,
is `UPSTREAM_UNAVAILABLE`. Detecting and discarding a dead idle connection
before assignment can establish a fresh connection; it does not replay a call.
The Unix client's existing raw transport-error behavior is unchanged; this
change does not invent an upstream certainty classification for that hop.

Tests in `localapi/relay_reuse_test.go` inject deterministic zero-byte and
partial writes on an already-used connection, for empty and nonempty relay
bodies and for the Unix client. They assert no second dial, no second handler
invocation and a surfaced error. Real TLS tests also cover reply loss,
truncation, timeout, pool-wait cancellation, changing authorization on the same
connection, and idle-pool closure. Existing tests cover untrusted certificates,
redirects, duplicate/untrusted headers and exact/oversized request/response
bounds. `TestRelayReusedConnectionLostCommittedResponse` runs the real remote
handler against disposable PostgreSQL: a record commits, the reply is lost,
no automatic replay occurs, and the caller's explicit identical retry returns
the same record ID/version. Store and journal retry semantics are unchanged.

The experiment uses gRPC retry disabling, zero retry buffering and response
headers before synthetic application work. Its tests demonstrate one handler
invocation for an admitted `Unavailable` error, equivalent Unicode fields and
per-call auth. They do not qualify every gRPC transparent retry or failure path
for real Cairn writes. General gRPC transparent retries differ from the relay's
contract; they must be analyzed before any migration.

References: [Go transport retry implementation, pinned Go 1.25.0](https://github.com/golang/go/blob/go1.25.0/src/net/http/transport.go),
[Go request replayability and outgoing length](https://github.com/golang/go/blob/go1.25.0/src/net/http/request.go),
and [gRPC retry semantics](https://grpc.io/docs/guides/retry/).

Revisit gRPC if production profiling finds encoding or concurrent transport is
a material bottleneck after reuse, then repeat on representative hosts with
real route semantics and the full durability/failure matrix. Notification
polling and host scheduling are measured separately by agent-203.

## Validation

- Idle cadence follow-up: `make check` and
  `go test ./localapi -run 'TestRelay|TestClientReused' -race -count=1 -timeout=90s`
  passed after the 45-second change, including the real 31-second idle-gap
  test. The full PostgreSQL suite and timing matrix below were run on the
  preceding 30-second candidate, not repeated for this timeout-only follow-up.
- `make check`: passed on the completed runtime changes and benchmark source.
- `make test-integration`: passed, including the new real-store response-loss
  test, existing two-relay durability/rotation/revocation/restore scenarios,
  and local authentication checks. Only disposable PostgreSQL was used.
- `go test ./localapi -run 'TestRelay|TestClientReused' -race -count=1 -timeout=60s`:
  passed, including the final pool-wait test added while the full suite ran.
- Nested experiment `go test -race ./...` and `go vet ./...`: passed, including
  both gRPC servers, authentication, equivalent Unicode payloads, invalid
  application inputs, wire bounds and admitted-error no-replay checks.
- Final timing commands above: passed, 18,000 measured relay calls and 36,000
  measured typed-transport calls, zero failed measured calls. Fixture warmup
  and Go benchmark calibration calls are additional and excluded from those
  sample totals. No race-detector timings are used in the tables.

The production module graph is unchanged. The native protocol and compatibility
work from other agents is outside this branch; root must run integrated gates
and repeat measurements after merging those changes. Historical measurements
here are not a claim about a deployed upgraded service.
