# Notification latency fixture, 2026-09-26

This is a reproducible local component benchmark, not an end-to-end agent wake
measurement. It separates committed publication, `event-watch` API and relay
transport, coordination presence polling, native context preparation, and the
configured scheduler periods. The benchmark changes no runtime API or schema.

Run from the repository root:

```sh
bash scripts/bench-notification-latency.sh --samples 30 --warmup 5 \
  --output /tmp/cairn-notification-benchmark.json
```

The runner creates a fresh PostgreSQL cluster under a mode-0700 `/tmp`
directory, with a private Unix socket and no TCP listener. It verifies
`fsync=on` and `synchronous_commit=on`, runs migrations, and removes the
cluster and synthetic credentials on exit. The probe refuses any other database
URL or fixture root. No production profile or database is used. The driver
generates a one-day self-signed localhost certificate, verifies it on the
relay upstream connection, and removes it with the fixture. Samples and warmup
are bounded to 1–200 and 0–50. The JSON output contains every sorted sample
and the parameters; it is not committed.

The measured paths are:

| Series | Timed boundary |
| --- | --- |
| `store_publish_commit` | Go `Store.PublishEvent` call through return after the PostgreSQL transaction commits; the exact instant of commit is inside this interval. |
| `central_unix_publish_ack` | Central API `event-publish` request through its acknowledgement, including client connection and transport. |
| `*_watch_empty` | One new-client `event-watch` request from a caught-up cursor, returning no delivery. |
| `*_watch_fresh` | One new-client `event-watch` request from a cursor immediately after a separate publication acknowledgement, returning exactly the new delivery. |
| `*_ack_to_watch_return` | Wall time from publication acknowledgement to return of that immediate `event-watch` request. This is a client observation bound, not a timestamp of first visibility. |
| `presence_watch_once` | Actual coordination `watch_once` call for one live, busy synthetic session, including relay heartbeat and state write. No inbox claim or model wake occurs in this series. |
| `native_context_boundary` | Actual `inbox_context` call for a published notice through a returned prompt and native context file. It includes claim/renew and CLI/subprocess overhead, but excludes model scheduling, acknowledgement, and response. Each notice is acknowledged and reconciled outside the timed interval. |

The central endpoint is a Unix socket. The relay endpoint is a different local
Unix socket that forwards over verified loopback TLS to the same central
server. A publisher sends each notice through the central API in both endpoint
series; `relay_tls_*` labels describe the **watch** transport only. There is
no remote host or tailnet in this fixture. All calls are sequential, each
notice has one recipient, and no background producer load is introduced.

One run on Linux 6.8 x86_64, PostgreSQL 17.10, and Go 1.25.0, from source
based on `0fb09c3c6698d96ae62921d546e3263000e59732`, used 5 unreported
warmups and 30 measured samples per series. Times are milliseconds;
percentiles use nearest rank on the 30 samples.

| Measured series | Min | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| `store_publish_commit` | 6.903 | 8.016 | 12.004 | 12.169 |
| `central_unix_publish_ack` | 5.752 | 7.153 | 10.119 | 14.535 |
| `central_unix_watch_empty` | 0.298 | 0.366 | 0.533 | 0.598 |
| `central_unix_watch_fresh` | 0.494 | 0.555 | 0.860 | 0.892 |
| `central_unix_ack_to_watch_return` | 0.506 | 0.565 | 0.871 | 0.903 |
| `relay_tls_watch_empty` | 2.165 | 2.399 | 3.001 | 3.012 |
| `relay_tls_watch_fresh` | 2.359 | 2.579 | 3.315 | 3.461 |
| `relay_tls_ack_to_watch_return` | 2.371 | 2.592 | 3.327 | 3.473 |
| `presence_watch_once` | 68.414 | 74.619 | 86.572 | 211.770 |
| `native_context_boundary` | 261.593 | 279.212 | 304.656 | 312.165 |

The scheduling fixtures put 30 arrivals at evenly spaced midpoints in one
configured period. They do not wait, schedule a process, poll the API, or
sample real arrivals. The periods come from the CLI event-watch loop (1 s),
the `wake serve` empty-claim pause (2 s), and the coordination presence watcher
loop (30 s). Their calculated values are:

| Simulated phase only | Period | Min | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `simulated_watch_cli_phase` | 1,000 | 16.667 | 483.333 | 950.000 | 983.333 |
| `simulated_wake_serve_phase` | 2,000 | 33.333 | 966.667 | 1,900.000 | 1,966.667 |
| `simulated_presence_scheduler_phase` | 30,000 | 500.000 | 14,500.000 | 28,500.000 | 29,500.000 |

These components cannot be summed into an observed delivery latency: the
publication and watch series use separate events, and their clocks do not mark
the first possible visibility instant. The presence watcher is not the wake
supervisor. This run does not measure cross-machine network time, concurrent
load, host queueing, worker launch, provider/model wake, or time until the
agent reads a notice. A real end-to-end trial would need correlated timestamps
from publication through host claim and the native model-turn boundary, with
explicit clock handling across hosts and an actual owner-configured session.
