# Live cross-machine and provider wake latency

Completed September 27, 2026, between Proximal and Archon. Ten requests woke
an actually idle Codex conversation through Cairn's native queue. All ten
produced the correct selected result, explicit completion and a linked reply,
with one delivery attempt each. All ten native turns finished successfully.

This closes the live-measurement gap left by the
[controlled API-stage benchmark](api-contract-stage.md). It measures the
**deployed `b46a0e1` baseline**, not the newer connection-reuse implementation at
`5595bb2`. Production services and installed binaries were not changed.

## Environment and method

- Proximal: central Cairn API and PostgreSQL; existing hosted profile and agent
  112 published the requests. Archon: enrolled relay over the existing
  tailnet HTTPS route, with the actual API/store on the other machine.
- Both deployed builds reported clean
  `b46a0e1bc7d9b767e32346cc9a2b42c20dc2fcc4`.
- Receiver: Codex `0.157.1`, effective model `gpt-6-astra`, one dedicated
  conversation (`01a0e258-cef9-7433-9b62-6ba42adf8189`, agent 216).
  Its explicit Unix app-server listener supports the same native queue path
  used by the coordinated interactive launcher. No terminal prompt injection
  or synthetic model response was used.
- The trial installed the deployed release's unmodified coordination code in
  a private directory and ran its real `watch` loop, with the default 30-second
  cadence. Its initial READY turn ended before work arrived. Each later turn
  was started by the watcher after observing an idle session and pending work.
  This was one isolated binding, not the production watcher's entire session
  population or a measurement of interactive TUI rendering.
- Ten sequential requests, with inter-sample delays of
  0, 3, 7, 11, 17, 2, 5, 13, 19 and 23 seconds. Each requested one unique result
  beacon, explicit completion and one linked reply. These varied phases are
  not a random arrival distribution. No wake samples were discarded.
- A persistent app-server subscriber recorded selected method, item and turn
  IDs with receiver monotonic receipt times. It retained no provider text.
  A separate observer recorded wake markers and native context metadata with
  a 20 ms polling pause. That pause is not a guaranteed error bound.
- Publisher monotonic time measured publication acknowledgement to observation
  of the matching collected response. This includes a 250 ms polling pause,
  CLI/API calls and small local file-write overhead. Receiver intervals use
  only its own monotonic clock. No interval subtracts clocks from two hosts.

The remote Codex home linked the existing account authentication and used the
previously exercised workspace permission profile with network enabled for
Unix CLI socket calls. Account defaults and production credentials were unchanged.

## Results

Nearest-rank percentiles. With ten wake samples, p95 is also the maximum;
these are observed distributions, not an SLO or a reliable estimate of rare tails.

| Measurement | Samples | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Local Proximal version API call | 30 | 0.456 ms | 1.078 ms | 4.374 ms |
| Archon relay → Proximal version API → Archon | 30 | 14.502 ms | 20.558 ms | 21.481 ms |
| Publisher request start → publication acknowledgement | 10 | 10.312 ms | 22.180 ms | 22.180 ms |
| Archon wake marker observed → native context observed | 10 | 203.202 ms | 243.617 ms | 243.617 ms |
| Archon native context observed → first agent text delta | 10 | 1.319 s | 3.500 s | 3.500 s |
| Archon wake marker observed → first agent text delta | 10 | 1.542 s | 3.704 s | 3.704 s |
| Archon native turn start → first agent text delta | 10 | 1.523 s | 3.713 s | 3.713 s |
| Archon native turn start → native turn completion | 10 | 18.007 s | 26.352 s | 26.352 s |
| Proximal publication acknowledgement → linked response observed | 10 | **35.428 s** | **45.395 s** | **45.395 s** |

The API probes used five warmups, thirty measured calls and a fresh local Unix
connection per call. The remote number includes the deployed relay's upstream
TLS/connection policy, network and API handling; it is not bare network RTT.
The local and remote probe series ran concurrently with the live trial on
shared machines, without load isolation or network shaping.

“First agent text delta” proves actual model-generated output in the matching
turn. Context preparation and queue acceptance alone were not counted as
provider wake. This is externally observed wake-to-output latency, not the
provider's internal dispatch-to-first-token metric.

| Request | Acknowledgement → reply observed | Context observed → first text | Native turn duration |
| --- | ---: | ---: | ---: |
| 1 | 30.006 s | 1.319 s | 26.352 s |
| 2 | 44.903 s | 1.160 s | 17.397 s |
| 3 | 21.503 s | 2.310 s | 18.935 s |
| 4 | 45.395 s | 1.270 s | 19.452 s |
| 5 | 38.397 s | 1.904 s | 17.622 s |
| 6 | 25.938 s | 1.509 s | 19.190 s |
| 7 | 20.514 s | 1.289 s | 18.007 s |
| 8 | 43.604 s | 1.215 s | 17.061 s |
| 9 | 38.311 s | 1.664 s | 17.593 s |
| 10 | 35.428 s | 3.500 s | 21.823 s |

The live API path is measured in milliseconds; the complete model-mediated
workflow takes tens of seconds. Its actual path includes the 30-second watcher
cadence and several provider/tool interactions to read, complete and reply.
These results support measuring and improving that path before expecting a
transport migration alone to solve end-to-end responsiveness. They do not
isolate exact one-way polling delay or add component percentiles into a total.

## Completion audit

For every sample, the analyzer independently checked:

1. One expected recipient, one collected response, available result payload,
   and no additional response/delivery pages.
2. The exact event, source version, delivery, native attempt and native turn
   correlations; the intended receiver agent and execution owned the wake.
3. Delivery state `handled`, exactly one delivery attempt, and the same result
   reference in the completion and response group.
4. Retained result body equals the sample's unique beacon, and its observed
   writer is the intended receiver. Requests instructed exact source reads;
   the timestamp-only native trace does not separately certify the read command.
5. Real agent text deltas in the matching thread/turn, followed by
   `turn/completed` with status `completed`. The preceding different native
   turn had completed before the new turn began. No observer error was recorded.

Representative correlation anchors:

| Sample | Request event | Result record, version 1 | Response event |
| --- | --- | --- | --- |
| 1 | `280a158f-a9b9-4788-b3e8-37619787e323` | `ee715358-785b-4ef1-9ff1-255c9b2b5804` | `09333c27-3f98-4b53-ac75-05d5ee50eb73` |
| 10 | `1323add5-d419-4e21-a50e-0d65f11c24cc` | `2e0e2192-769d-4f14-b69a-a5d940aad1a2` | `b0d25c60-c58f-4f22-9c9e-eacbf0034c24` |

The final native turn `01a0e260-9798-7970-9fd5-45994989b917` completed before
cleanup. The recipient was idle with no inbox attempt, intent or retained
journal. Afterwards, the driver, app-server and trial watcher processes were
absent; the temporary auth link was removed and agent 216 was stopped/offline.
Archon's production relay/presence and Proximal's API/presence services remained
active. Selected synthetic records were retained; no production cleanup or
failure injection was performed.

## Reproduction and evidence

[Experimental harness](../../experiments/live-latency/README.md) contains the
receiver, publisher, read-only probe and independent analyzer. The live run used
private scripts which were subsequently parameterized and reviewed for that
directory. The analyzer was run against all ten actual samples; the read-only
probe was run on both actual hosts. The packaged receiver/publisher were not
used to start another paid trial after parameterization. All four packaged
Python scripts passed syntax checks; the packaged read-only probe passed
against the deployed API, a mocked publisher CLI regression completed ten
samples with unique mutation IDs and preserved sender identity, and `make check`
passed. A second agent independently recomputed all seventy monotonic intervals,
percentiles and cleanup checks. No store/runtime code changed,
so the earlier database integration suite was not repeated for this report.

Private selected evidence on Proximal:
`/tmp/cairn-live-latency-20260927/`, including `audit.json`, the ten
`sample-NN.json` receipts, `archon-metadata.jsonl`, `receiver-provenance.json`,
the two read-probe results, `final-receiver-state.json` and `cleanup.json`.
Raw native rollouts and account authentication are not committed.

The remaining boundary is a live comparison after deploying the newer reuse
build, plus broader host/provider/load coverage. This completed baseline does
not establish latency for those unmeasured configurations.
