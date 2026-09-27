# Notification latency fixture, 2026-09-26

This is a reproducible local component benchmark, not an end-to-end agent wake
measurement. It separates committed publication, `event-watch` API and relay
transport, coordination presence polling, native context preparation, and the
configured scheduler periods. The benchmark changes no runtime API or schema.

Run from the repository root:

```sh
bash scripts/bench-notification-latency.sh --samples 30 --warmup 5 \
  --scheduler-interval-ms 120 \
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

The controlled real-timer series schedules a publication at a known offset
inside each **120 ms fixture interval**, then sleeps until the next poll target.
The poll calls the real relay `session-inbox-ready` route; after it reports the
delivery, the real `inbox_context` path claims it and writes the native context.
A separate publisher thread avoids delaying the poll timer while the write is
in flight. Publication is targeted at 15–55% of the interval, evenly spaced
across measured samples, with a 20 ms fixture startup guard. These delays are
imposed harness inputs, not measured Cairn scheduling policy. The JSON report
keeps correlated per-trial target, start, acknowledgement, readiness and context
return points from one `time.monotonic_ns` clock, plus the segment durations.
Timer overshoot is measured separately from acknowledgement-to-poll delay.
The harness controls this poll loop; it does not launch the production
coordination watcher, wake supervisor, or a model turn.

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
| `store_publish_commit` | 4.971 | 8.125 | 10.571 | 19.804 |
| `central_unix_publish_ack` | 5.811 | 7.116 | 10.909 | 11.259 |
| `central_unix_watch_empty` | 0.334 | 0.439 | 0.588 | 0.611 |
| `central_unix_watch_fresh` | 0.499 | 0.640 | 0.812 | 0.877 |
| `central_unix_ack_to_watch_return` | 0.509 | 0.653 | 0.828 | 0.889 |
| `relay_tls_watch_empty` | 2.099 | 2.651 | 3.947 | 5.109 |
| `relay_tls_watch_fresh` | 2.287 | 2.893 | 3.899 | 4.691 |
| `relay_tls_ack_to_watch_return` | 2.297 | 2.906 | 3.915 | 4.710 |
| `presence_watch_once` | 64.553 | 76.836 | 96.303 | 136.066 |
| `native_context_boundary` | 254.407 | 281.068 | 317.986 | 320.577 |

The same run's controlled real-timer trial produced these observed segments.
`publish_ack_to_context_return` is measured directly per trial, so its
percentiles must not be reconstructed by adding other percentile columns.

| Controlled real-timer series | Min | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: |
| Publication timer overshoot | 0.024 | 0.063 | 0.081 | 2.222 |
| Poll timer overshoot | 0.021 | 0.062 | 0.069 | 2.309 |
| Publication ack → poll start | 46.632 | 71.363 | 90.016 | 94.886 |
| Poll start → readiness return | 3.871 | 5.150 | 14.690 | 29.967 |
| Readiness return → native context return | 249.552 | 289.103 | 380.051 | 406.460 |
| Poll start → native context return | 253.827 | 293.347 | 388.107 | 412.780 |
| Publication ack → native context return | 319.060 | 371.099 | 439.659 | 465.345 |

The scheduling fixtures put 30 arrivals at evenly spaced midpoints in one
configured period. They do not wait, schedule a process, poll the API, or
sample real arrivals. The periods come from the CLI event-watch loop (1 s),
the `wake serve` empty-claim pause (2 s), and the coordination presence watcher
loop (30 s). The coordination watcher scans every configured session before
waiting `max(1, 30 - scan_elapsed)` seconds; session count, scan time and lock
contention can change the real cadence. The one-session `presence_watch_once`
measurement is service time for that call, not an observed inter-poll interval.
The fixture adds no sleeps inside any timed measured boundary. Its calculated
phase values are:

| Simulated phase only | Period | Min | p50 | p95 | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `simulated_watch_cli_phase` | 1,000 | 16.667 | 483.333 | 950.000 | 983.333 |
| `simulated_wake_serve_phase` | 2,000 | 33.333 | 966.667 | 1,900.000 | 1,966.667 |
| `simulated_presence_scheduler_phase` | 30,000 | 500.000 | 14,500.000 | 28,500.000 | 29,500.000 |

The independent component series cannot be summed into an observed delivery
latency: they use separate events and do not mark the first possible visibility
instant. The controlled real-timer trial does correlate one event through
publication acknowledgement, one scheduled readiness poll, native claim and
context preparation; it measures neither the production watcher cadence nor
the provider/model turn. The presence watcher is not the wake supervisor. This
run does not measure cross-machine network time, concurrent load, real host
queueing, worker launch, provider/model wake, or time until an agent reads a
notice. A production end-to-end trial would need correlated timestamps through
the actual owner-configured host and model turn, with explicit clock handling
across hosts.
