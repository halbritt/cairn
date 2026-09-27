# API contract stage: integrated acceptance

Accepted 2026-09-26. Runtime and contract source tested at
`16a255f845278d3d481b5e9eb5405aa7faad22b0`; subsequent `bd4679f` corrects test
fixtures for the raised-minimum build, and `b90a3d1` documents Unicode decoding.
Benchmarks below ran from the latter clean checkout. These later commits change
no production runtime. This record accepts the implemented stage, not a
production rollout, WAN latency target, or the full Cairn design.

## Requirements and evidence

| Requirement | Implemented result | Evidence and limits |
| --- | --- | --- |
| Formal contract | Generated OpenAPI 3.1 / JSON Schema 2020-12 covers 77 operations, including 39 allowed remotely; request/success/error shapes, authentication, roles, destination/session constraints, limits and retry classes | [Contract](../api-contract.md), [specification](../api/openapi.json); drift tests and independent validator, real-handler conformance with disposable PostgreSQL. Unicode, numeric lexical/precision, key casing and domain-validation limits are explicit. |
| Safe compatibility | Protocol 1/2 support is separate from build identity; per-request declarations and body guard; strict refusal before incompatible operations; no implicit negotiation | [Policy](../api-compatibility.md); eight historical/current CLI/relay/server combinations preserve exact retry behavior; 26 additional cases cover legacy, raised minimum, malformed metadata and server rollback after preflight. |
| Connection cost | Bounded HTTP/1.1 TLS reuse, 16 idle / 32 maximum connections, 45-second idle expiry | Repeated production-relay policy benchmark below; real 31-second idle-gap test retains one connection. Large serial-payload tails regressed in this repeat, so there is no universal latency or throughput claim. |
| Retry/authentication/durability | Non-replayable POST bodies, per-request authentication, surfaced uncertain outcomes and explicit same-ID retries | Race tests cover stale reused connections, zero/partial writes, empty bodies, pool waits, truncated/lost replies and a lost response after a store commit; integration covers two relays, token rotation/revocation and restore. |
| Notification stages | Publication return, observation transport, presence service time and native-context preparation; correlated real-timer trials separate polling delay and timer overshoot | [Method](notification-latency-2026-09-26.md), integrated results below. Controlled 120 ms fixture, not the production 30-second watcher or provider/model wake. |
| Transport choice | Retain HTTP with reuse; keep native and ServeHTTP Protobuf/gRPC experimental | Repeated matched typed workloads below and [comparison](../transport-comparison.md). Native gRPC improves large-payload cost; full operation, authorization, retry and rollout parity is still absent. |

Agent 200 implemented the contract and reviewed root's envelope/decoder fixes;
204 implemented compatibility and addressed protocol-guard review findings;
201 implemented connection reuse, fault tests and the transport comparison;
203 implemented notification measurement and correlated timer trials. Agent 112
integrated, reviewed and ran the acceptance checks.

## Validation

All listed checks passed locally. Database checks used runner-owned disposable
PostgreSQL clusters and synthetic credentials.

- `make check`: vet, deterministic contract drift, formatting and pinned design sources.
- `make test-integration`: every Go package with `-race`, real-handler contract
  conformance, 26 protocol-skew scenarios against independently built `b46a0e1`,
  two-relay and lifecycle/process checks. The core and API suites ran with their
  own databases. This demonstrates transaction/retry behavior, not power-loss testing.
- `bash scripts/test-api-version-skew.sh --output /tmp/cairn-api-stage-final-skew.json`:
  all eight combinations passed. Clean independently stamped peers were
  `0fb09c3c6698d96ae62921d546e3263000e59732` and
  `16a255f845278d3d481b5e9eb5405aa7faad22b0`. Each case verified creation,
  identical retry, changed-intent rejection, append retry, current read and
  durable request-row count.
- `python3 -B -m unittest discover -s scripts -p 'test_*.py'`: 418 tests,
  15 skips. Native/provider-specific omissions stay outside the claim; the
  integration worker probe used its generic fixture when no OpenCode binary
  was supplied.
- Additional protocol tests in the `cairn_protocol_min2` build passed, including
  raised-client legacy refusal, guard edge cases and CLI enrollment/status.
  The initial broader CLI run exposed legacy-only fixture expectations;
  `bd4679f` corrected them and both default and raised-minimum CLI runs passed.
- Python `jsonschema.Draft202012Validator.check_schema` independently accepted
  all 248 component schemas and all operation request/success schemas.
- Nested `experiments/transportbench`: root `go test -race ./...` and
  `go vet ./...` passed. Its source is unchanged since that check; it is excluded
  from the root module's normal recursive checks.
- Benchmark commands below passed: 18,000 measured relay calls and 36,000
  measured typed calls, zero measured failures, plus 30 samples per notification
  series and 30 correlated timer trials. Warmup/calibration is additional.

No GitHub CI, production test database, operational memory capture or
production credential changes were used.

## Integrated timing repeat

Linux amd64, Intel Core i5-11400F, Go 1.25.0; transport benchmarks use
GOMAXPROCS=4, verified loopback TLS, 500 measured calls per case, three repeats,
256 B / 16 KiB / 256 KiB payloads and 1 / 8 workers. Root's integration builds
finished before timing, and root ran these benchmarks sequentially. The host
was shared with other agents and was not CPU-isolated. No WAN shaping or
cross-machine observation is implied. The full method, first-call, allocation,
TLS-byte and connection-counter definitions are in the companion reports.

```sh
go test ./localapi -run '^$' -bench BenchmarkRelayConnectionPolicy \
  -benchtime=500x -count=3 -cpu=4 -benchmem
(cd experiments/transportbench && go test -run '^$' -bench BenchmarkExchange \
  -benchtime=500x -count=3 -cpu=4 -benchmem)
bash scripts/bench-notification-latency.sh --samples 30 --warmup 5 \
  --scheduler-interval-ms 120 --output /tmp/cairn-api-stage-notification-final.json
```

All transport latency columns are microseconds. Cells show the median of three
per-run metrics; the p99 range is the smallest/largest per-run p99, not a
confidence interval. Calls/s is throughput, not reciprocal concurrent latency.

### Actual relay: connection policy

| Payload | Workers | Variant | p50 | p95 | p99 (range) | Calls/s | New connections/call |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 256 B | 1 | fresh HTTP | 1,701 | 2,361 | 2,531 (2,387–6,676) | 549.2 | 1 |
| 256 B | 1 | reused HTTP | 56.71 | 78.28 | 108 (100.7–112.2) | 16,467 | 0 |
| 256 B | 8 | fresh HTTP | 3,509 | 7,044 | 8,439 (8,038–9,438) | 2,061 | 1 |
| 256 B | 8 | reused HTTP | 104.3 | 457.3 | 2,134 (656.2–2,253) | 43,321 | 0.01 |
| 16 KiB | 1 | fresh HTTP | 1,940 | 2,562 | 2,812 (2,778–3,252) | 498.2 | 1 |
| 16 KiB | 1 | reused HTTP | 112 | 302.9 | 364.9 (352.7–386.9) | 7,153 | 0 |
| 16 KiB | 8 | fresh HTTP | 4,903 | 8,273 | 9,780 (9,246–10,402) | 1,548 | 1 |
| 16 KiB | 8 | reused HTTP | 700.1 | 1,577 | 3,230 (1,896–3,397) | 9,810 | 0.014 |
| 256 KiB | 1 | fresh HTTP | 3,477 | 5,437 | 7,621 (5,405–17,043) | 268.5 | 1 |
| 256 KiB | 1 | reused HTTP | 1,642 | 14,930 | 28,990 (6,823–37,163) | 258.7 | 0 |
| 256 KiB | 8 | fresh HTTP | 10,873 | 17,784 | 28,325 (23,780–33,582) | 697.2 | 1 |
| 256 KiB | 8 | reused HTTP | 5,506 | 10,769 | 13,446 (12,436–21,256) | 1,363 | 0 |

### Typed workload: HTTP and Protobuf/gRPC

| Payload | Workers | Variant | p50 | p95 | p99 (range) | Calls/s | New connections/call |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 256 B | 1 | http-fresh | 2,194 | 2,827 | 3,738 (3,431–8,224) | 450.3 | 1 |
| 256 B | 1 | http-reuse | 72.97 | 94.32 | 116.5 (114.3–133.6) | 13,445 | 0 |
| 256 B | 1 | grpc-proto | 169.5 | 213.5 | 335.1 (282.9–336) | 5,745 | 0 |
| 256 B | 1 | grpc-native | 94.21 | 126 | 177.3 (167.6–182.4) | 10,444 | 0 |
| 256 B | 8 | http-fresh | 4,111 | 7,695 | 10,367 (8,450–11,339) | 1,803 | 1 |
| 256 B | 8 | http-reuse | 152.9 | 716.1 | 3,226 (2,688–3,735) | 27,570 | 0.018 |
| 256 B | 8 | grpc-proto | 345.8 | 642.6 | 829.1 (766.4–829.8) | 21,047 | 0 |
| 256 B | 8 | grpc-native | 166.7 | 393.6 | 501.5 (485.8–538.2) | 42,738 | 0 |
| 16 KiB | 1 | http-fresh | 2,206 | 2,845 | 3,211 (3,079–3,221) | 439.3 | 1 |
| 16 KiB | 1 | http-reuse | 291.5 | 509.9 | 583.3 (542.8–616.8) | 3,072 | 0 |
| 16 KiB | 1 | grpc-proto | 225.4 | 337.7 | 471 (452.4–547.8) | 4,189 | 0 |
| 16 KiB | 1 | grpc-native | 136.3 | 203.6 | 313.5 (294.6–339.1) | 6,890 | 0 |
| 16 KiB | 8 | http-fresh | 5,399 | 8,544 | 10,439 (10,372–10,555) | 1,415 | 1 |
| 16 KiB | 8 | http-reuse | 1,237 | 2,356 | 3,250 (3,248–6,699) | 5,983 | 0.006 |
| 16 KiB | 8 | grpc-proto | 652.1 | 1,030 | 1,141 (1,137–1,163) | 11,514 | 0 |
| 16 KiB | 8 | grpc-native | 356.6 | 672.3 | 769.8 (744.9–812.8) | 20,205 | 0 |
| 256 KiB | 1 | http-fresh | 5,810 | 7,160 | 7,822 (7,574–25,440) | 167.1 | 1 |
| 256 KiB | 1 | http-reuse | 3,693 | 5,075 | 6,086 (5,615–8,044) | 259.5 | 0 |
| 256 KiB | 1 | grpc-proto | 1,092 | 1,439 | 1,579 (1,579–1,793) | 883.4 | 0 |
| 256 KiB | 1 | grpc-native | 1,042 | 1,399 | 1,575 (1,541–1,645) | 930 | 0 |
| 256 KiB | 8 | http-fresh | 14,061 | 20,164 | 22,766 (22,632–22,798) | 556.8 | 1 |
| 256 KiB | 8 | http-reuse | 9,291 | 15,123 | 17,729 (17,469–18,845) | 825.9 | 0 |
| 256 KiB | 8 | grpc-proto | 4,254 | 5,937 | 7,036 (6,229–7,174) | 1,854 | 0 |
| 256 KiB | 8 | grpc-native | 3,557 | 5,543 | 5,914 (5,817–6,312) | 2,234 | 0 |

The large serial relay case deserves explicit attention: reused p95/p99 were
14.930/28.990 ms versus fresh 5.437/7.621 ms, and median throughput was slightly
lower (258.7 versus 268.5 calls/s), despite a lower reused p50. This run cannot
isolate a transport cause from shared-host scheduling/GC effects. Retaining
that result prevents the small-call improvement from becoming a blanket claim.
The separate typed large-payload workload did not show the same regression.
No pool-capacity tuning or production SLO is inferred from these measurements.

### Notification and native delivery components

Thirty measured samples after five warmups. Times are milliseconds; p99 equals
the maximum at this sample count. PostgreSQL used `fsync=on` and
`synchronous_commit=on`. The integrated relay reuses TLS upstream connections;
API clients still open a new local Unix connection per call.

| Component | p50 | p95 | Max |
| --- | ---: | ---: | ---: |
| `store_publish_commit` | 8.521 | 15.273 | 17.697 |
| `central_unix_watch_empty` | 0.488 | 1.116 | 1.29 |
| `central_unix_publish_ack` | 6.567 | 12.083 | 15.301 |
| `central_unix_watch_fresh` | 0.66 | 1.351 | 1.408 |
| `central_unix_ack_to_watch_return` | 0.673 | 1.378 | 1.424 |
| `relay_tls_watch_empty` | 0.532 | 0.772 | 3.109 |
| `relay_tls_watch_fresh` | 0.813 | 1.439 | 1.988 |
| `relay_tls_ack_to_watch_return` | 0.827 | 1.455 | 2.003 |
| `presence_watch_once` | 68.542 | 94.183 | 153.563 |
| `native_context_boundary` | 302.058 | 395.441 | 401.047 |

The following rows correlate the same event in the controlled 120 ms timer
fixture. Publication phase is imposed at 15–55% of the interval. All timestamps
use one monotonic clock. The total is measured directly; adding component
percentiles does not reconstruct it.

| Correlated segment | p50 | p95 | Max |
| --- | ---: | ---: | ---: |
| `publish_timer_overshoot` | 0.062 | 0.09 | 0.093 |
| `poll_timer_overshoot` | 0.063 | 0.092 | 0.158 |
| `publish_ack_to_poll_start` | 69.251 | 92.094 | 96.693 |
| `poll_start_to_ready_return` | 2.808 | 12.586 | 14.991 |
| `ready_return_to_context_return` | 291.843 | 428.571 | 445.913 |
| `poll_start_to_context_return` | 295.191 | 441.157 | 447.981 |
| `publish_ack_to_context_return` | 370.381 | 498.361 | 528.159 |

The independent publication/store/transport series cannot be subtracted to
isolate database or network cost. Native-context timing includes real CLI,
claim and file preparation work, but excludes the provider/model turn. The
production 30-second watcher cadence, multiple live sessions, host launch
queues and cross-host clocks remain unmeasured. The arithmetic 1/2/30-second
phase examples remain simulations and are not counted as observed latency.

## Decision and next measurement boundary

Retain the existing JSON/HTTP API and bounded connection reuse. In this repeat,
serial 256 B typed HTTP reuse had 72.970 µs p50 versus native gRPC 94.210 µs;
serial 256 KiB native gRPC had 1.042 ms versus HTTP reuse 3.693 ms. Those are
workload-specific advantages. The gRPC experiment has a generated Protobuf
service and per-call synthetic authentication, but it does not implement Cairn's
77 operations, scoped store/session authority, durable idempotency, cancellation,
legacy rollout or qualified transparent-retry behavior. Its benefit does not yet
justify migrating the production API.

Revisit after profiling representative enrolled hosts shows a material encoding
or concurrent-transport bottleneck after reuse. Any migration must preserve the
contract and retry/fault matrix and demonstrate an operational improvement on
that workload. Likewise, measure actual watcher/provider wake paths before
setting an end-to-end notification target. These are future measurement
boundaries, not unfinished requirements of the accepted controlled stage.

Selected raw artifacts are local and uncommitted:
`/tmp/cairn-api-stage-final-integration.log`,
`/tmp/cairn-api-stage-final-skew.json`,
`/tmp/cairn-api-stage-relay-bench.txt`,
`/tmp/cairn-api-stage-transports-bench.txt`, and
`/tmp/cairn-api-stage-notification-final.json`.
The checked-in commands, fixtures, summaries and limits remain reproducible
without relying on those temporary files.
