# CAIRN-220 multi-arrival watcher pilot

The opt-in `scripts/run_notice_watcher_probe.sh --multi-arrival-pilot` runs
against a fresh disposable PostgreSQL cluster and local Cairn API. It uses the
real `coordination.py watch --once` process and real clocks. Its synthetic live
Codex child has no native queue endpoint, and the probe makes no provider call.

The experiment publishes three routine notices about five seconds apart in
each arm. For the immediate baseline, each event gets one watcher eligibility
decision. The harness then explicitly fails and reconciles that synthetic
delivery before observing the next ready head. Treatment events each retain
their own event ID, delivery ID and 60-second deadline. The authenticated
disposable publisher flushes all three at a shared release point 50 seconds
after the first arrival, before every event's own deadline. After release, the
harness observes each ready head with the watcher, explicitly fails/reconciles
that synthetic delivery, then observes the next one. This queue advancement is
fixture cleanup, not actor work.

The final run recorded three immediate baseline decisions and three treatment
decisions: **zero difference in sampled ready-head decision count**. Baseline
arrival-to-observed-eligibility latencies were 146.069, 150.974 and 177.611 ms.
Treatment arrivals were 20:45:47.336151, 20:45:52.351415 and 20:45:57.366428
PDT. Their publisher flushes set `available_at` to 20:46:37.342059,
20:46:37.356991 and 20:46:37.370219 PDT, all before the respective
20:46:47.336151, 20:46:52.351415 and 20:46:57.366428 deadlines. The watcher
decisions were made at 20:46:37.507166, 20:46:37.673894 and 20:46:37.840494
PDT. Treatment arrival-to-observed-eligibility latencies were 50.171, 45.322
and 40.474 seconds. There were eight pre-release watcher cycles and no current
treatment eligibility decision during them.

The pilot reports real per-event watcher eligibility decisions and latency for
this synthetic workflow. Its equal 3/3 sample counts provide no eligibility
decision reduction. The per-event synthetic failures are required to expose
later ready heads; they do not model a host consuming a batch in one turn. The
experiment does **not** establish native wake savings, fewer model turns,
provider usage reduction, or task actionability. Accepted native wakes and
provider calls were both zero. Every treatment event's terminal disposition
comes from explicit synthetic failed cleanup, not implicit acknowledgment.

The pre-release pass exposed a retained `delivery_observation` with an older
delivery ID and condition `none`. Source inspection confirmed that this is a
diagnostic tombstone written while clearing an old exact-delivery diagnosis;
`prepare_idle_wake` does not use it. The experiment counts only a fresh
`native_transport_unavailable` observation paired with the current ready
delivery ID.

The opt-in runner extends the synthetic child lifetime to 240 seconds so it
outlives the roughly two-minute single-notice plus multi-arrival experiment.
The default integration path does not enable this real-clock wait.

## Remaining acceptance gap

CAIRN-220 still needs native-queue accepted wake evidence and paired
provider-accepted/model-turn outcomes to establish real wake reduction and
quality. It also needs actionability and latency grading, focused/busy-host
coverage, sustained-arrival starvation/overflow evidence, and a one-account
canary with exercised rollback. This pilot establishes none of those outcomes.
