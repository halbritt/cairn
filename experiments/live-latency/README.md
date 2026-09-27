# Live cross-machine latency trial

These opt-in scripts run ten small requests through a real remote relay, the
native 30-second presence watcher, an idle Codex conversation, and its actual
model provider. They measure a deployed baseline; they do not install a release.
Run only with owner authorization for the named hosts and provider account.

Unlike the disposable integration tests, this trial intentionally creates
selected synthetic source notes, requests, completion notes and linked replies
in the configured collection. It does not inject failures, delete records,
change credentials, or modify production services. The receiver runs its own
watcher against one new trial binding; never point it at another session's state.

Requirements: Python 3, `websocket-client` on the receiver, a working enrolled
relay, installed Cairn source matching its deployed release, and Codex supporting
the Unix app-server, trusted hooks and native queue APIs. The September 27 run
used Cairn `b46a0e1` and Codex `0.157.1`. The receiver inherits the provider's
default model; inspect `started.json` and `agent.json` to record its effective
model. Do not describe a different model or release as the same measurement.

## Run

On the receiving machine, use **new**, absolute private paths. The selected
auth file is linked into an isolated Codex home. Only this trial home enables
network access inside the workspace permission profile, as required for CLI
Unix-socket calls; account defaults are unchanged.

```sh
python3 receiver.py \
  --root "$trial_receiver" --source "$deployed_source" \
  --codex "$native_codex" --cairn "$cairn_binary" \
  --socket "$cairn_socket" --token-file "$hosted_token" \
  --auth-file "$existing_codex_auth" --collection "$collection"
```

Keep the process running. Its initial turn replies READY and ends. The native
hooks register the new conversation; read `agent.json` for its real agent ID
and idle state. Do not register or claim its inbox yourself. The watcher queues
every subsequent wake through the actual native queue.

On the publishing machine, use the **existing supplied** agent/execution
identity and hosted profile, and the recipient UUID from `agent.json`:

```sh
python3 publisher.py \
  --root "$trial_publisher" --token-file "$hosted_token" \
  --agent-id "$sender_agent" --execution-id "$sender_execution" \
  --recipient "$trial_recipient" --collection "$collection"
```

The publisher saves a stable mutation UUID and exact arguments before each
publication. Any uncertain outcome stops the run; do not rerun into a new
directory as a retry. Inspect the existing event and native process instead.
It waits for idle metadata and applies fixed inter-sample delays of
0, 3, 7, 11, 17, 2, 5, 13, 19 and 23 seconds. These are varied phases, not a
random arrival distribution. Every request has a ten-minute response deadline.

Run `python3 probe.py` separately on each host to measure the read-only version
route, with five warmups and thirty samples. This opens a fresh local Unix
connection per call; upstream connection policy is whatever the deployed relay
implements. The remote result includes relay, TLS, network and API cost.

## Audit and cleanup

`controller-finished.json` establishes response collection, not finished native
work. Wait for the final matching `turn/completed` with status `completed`, an
idle recipient and no unfinished `inbox_attempt` or `inbox_intent` in its state.
Copy the selected `metadata.jsonl` to the publisher and run:

```sh
python3 analyze.py --root "$trial_publisher" \
  --metadata "$receiver_metadata_copy" --token-file "$hosted_token" \
  > "$trial_publisher/audit.json"
```

The audit checks all ten exact result beacons by reading their retained source
versions, one delivery/attempt/response each, source/delivery/attempt/native-turn
correlation, the preceding native turn's completion, actual agent text deltas,
and successful turn completion. Merely receiving `turn/started` or a context
file cannot pass. A text-first response is required by this analyzer; a provider
that emits only tools needs an explicitly qualified tool-output metric.

Write the receiver's `finish` file only after auditing the final turn. The
receiver waits for native idle and released attempt state, stops only its own
watcher/app-server, reconciles its session and removes its auth link. Verify
those processes exited, the link is absent, the trial session is offline and
the production services remain active. SIGKILL cannot run cleanup: inspect the
recorded PIDs and session before acting; never launch a replacement for an
uncertain request. Keep private evidence outside Git; do not commit rollouts,
tokens, raw provider output or operational state.

## Timing interpretation

- Publisher monotonic acknowledgement → response observation includes remote
  polling, native preparation, provider inference, source read, completion,
  response publication and the controller's 250 ms polling pause plus CLI/API
  and file-write overhead. It is an end-to-end observation, not one-way latency.
- Receiver monotonic wake-marker/context/turn → first agent text delta is
  provider-output observation latency. It is not the provider's internal
  request-to-first-token measurement. Only matching native turn IDs count.
- File observation uses a 20 ms polling pause; scheduling and observer work can
  add delay. WebSocket timestamps are receipt times. No hard 20 ms accuracy
  guarantee is claimed.
- Central database event creation → completion uses timestamps from that
  database. Event insertion precedes commit, so this is a lifecycle interval.
- No durations subtract clocks from different hosts. No percentile components
  are added to reconstruct an end-to-end percentile. Report sample count and
  nearest-rank percentiles; with ten wake samples, p95 is the maximum.

The measurement scripts were parameterized and reviewed after the initial live
run; the verification report records that distinction. They are experimental
tools, outside normal integration tests, and never run automatically.
