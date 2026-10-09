# Internal owned worker boundary

This source slice prepares the controller/transport boundary for a separately
reviewed fixed coding worker. It contains no native request, public execution
route, arbitrary argv factory, provider admission, cell validity or permission
decision. Trusted runner code must pin and mount its fixed workers/dependencies
before allowing READY. The root-owned runtime adapter and actual coding/profile,
input, permission, terminal/export composition remain separate work.

`trial_owned_workers` extracts the existing default fixture's `WaitFact`,
`OwnedCgroup`, `Child`, `Unavailable`, resource controls and `end_containment`.
Their selected AST is preserved; `trial_external_supervision` retains import
names and includes this dependency in `source_manifest`. The fixed default API,
selected_native pins/profile=None, four-second wall policy and original 21
external fixtures remain unchanged.

The new internal `OwnedWorker` adds a pidfd for the actual direct unreaped
launcher and an `OwnedWaitFact` with controller-observed boot identity. Legacy
wait output stays unchanged. Its separate acquisition constructor stores every pipe before Popen and retains
the actual child on its held leaf immediately after Popen, before further setup.
A post-Popen setup fault refuses binding/gating with fixed
owned_worker_setup_unknown; actual wait/release and any safe descriptor cleanup
remain observable through that partial owned child. An indeterminate status-writer
close is checked against the still-held read pipe before cleanup, so an unrelated
reused FD is never closed. Legacy Child setup remains unchanged.
Missing pidfd refuses construction and saves the
partial owned child on its held leaf for cleanup; there is no PID fallback and
no actor gate. Missing boot/start identity remains null with fixed unknown.
These handles confer cleanup authority for owned processes, not native admission
or proof that actor declarations authenticate execution.

`AttemptDomain` owns an empty aggregate cgroup and fresh capture, composition,
diagnostic and export leaves. Its parent enforces the same combined 512MiB,
zero-swap, 32-PID, 100000/100000 CPU ceiling. Each leaf has those ceilings too;
the parent prevents allowance multiplication. The outer disposable service keeps
its existing 1GiB/64-task ceiling. Parent/leaf identity and control files are held
by FDs. `stop_role(role, deadline)` stops the owned launcher through its pidfd
and repeatedly kills the held leaf until its actual wait and kernel empty
observation or the supplied absolute deadline. Streams remain open for drain.
`close_role` then closes every owned handle and removes the leaf, preserving
wait/release facts and fixed concurrent cleanup errors. `close(deadline)` closes
all remaining roles before the held aggregate parent. It returns facts and role
errors; a true parent release does not erase a role's uncertainty. An exhausted
cleanup deadline or failed removal stays unknown. Root must enforce the frozen
wall budget and outer service closure; outer observer/host-loss recovery remains
unsupported/unknown, with no replay.

`WorkerTransport(domain, run_id, source_manifest_sha256, intent_sha256)` accepts
only domain-bound workers constructed by trusted internal fixed builders. Its
nonblocking `step` is bounded to 50ms per poll; queue/capture metadata 128KiB,
other control channels 8KiB, packets 64KiB, frozen actor input 8500 bytes (excluding
the one-byte worker gate), default D stream 4MiB. The internal maximum stream
ceiling remains 64MiB; it is not a native caller allowance. `gate_capture(bytes)`
requires exact READY and independently observed live composition/capture
PID/mount/network/user namespaces and held-cgroup membership before sending G
plus input. `gate_auxiliary('diagnostic'|'export', bytes <= 65536)` independently
verifies that owned worker before its first input. Auxiliary and sink closure
requires EOF, actual controller wait 0, kernel release and absence of cleanup
errors; any returned manifest is still only a worker declaration. Root must
validate the fixed diagnostic/export receipt and selected file persistence.

The fixed worker contract is:

- READY stdout line: `{kind: "READY", run_id, intent_sha256}`. The sink has already
  exclusively created/fsynced INTENT. Existing intent is consumed and refuses;
  the boundary never retries/reuses it.
- Capture stderr: one strict JSON line `{kind: "ACTOR_WAIT", run_id, actor_wait,
  input_bytes, input_bytes_written, observations}`. Capture owns the actor wait;
  controller capture wait and namespace observations come from separate handles.
  Missing/malformed declaration becomes explicit null facts and fixed observations.
- Sink stdin: D or E byte, unsigned network-order uint32 payload length, payload.
  D is transient raw stream; E is strict selected JSON described below.
- CLOSED stdout line: `{kind: "CLOSED", run_id, intent_sha256, outcome:
  "success_retention"|"failure_evidence", manifest_sha256, selected_bytes}`.
  Exactly READY then CLOSED are accepted. CLOSED does not replace actual wait,
  containment release or persistence inspection. A nonzero sink return refuses
  closure even when it emitted CLOSED. `closed_sink()` returns the declaration
  and independently owned wait/release/namespace facts, not validity.

The pure `trial_worker_protocol.validate_end(value, *, run_id,
source_manifest_sha256)` and `decode_end(payload, ...)` share the closed E
projection between emitter and fixed sink. They use strict selected_native JSON,
reject unknown fields/codes, duplicate keys, nonfinite/deep JSON and inconsistent
raw terminal status/returncode. This is shape/binding validation, not runtime
authentication. E schema `cairn.worker-end-observation/1` has exactly:

- schema, run_id, source_manifest_sha256;
- stream_sha256/stream_bytes: precisely the D payload sequence enqueued for sink;
- observed_stream_sha256/observed_stream_bytes: all raw bytes read by controller;
- actor_wait/capture_wait: null or `{pid, start_ticks, boot_id, raw_status,
  returncode}`, each field nullable; non-null raw status is canonical POSIX
  terminal status and consistent with known returncode; known boot IDs must agree;
- capture_released: true or null; input_bytes: frozen controller input size or
  null, input_bytes_written: capture-owned actor delivery count or null;
- observations and capture_cleanup_observations: sorted unique closed fixed codes.

Emitter binds known input_bytes to the gated input, retains full frozen input
count even if capture metadata is absent, and automatically marks every unknown
wait/release/input-delivery condition. Known nonzero or signal actor return is an
observation, not uncertainty. Nulls stay unknown. No observer code invents a
successful actor status or invokes reader finish/Retention.prepare.

On stream budget crossing, only the admitted prefix becomes D; its digest never
includes dropped bytes. Controller retains a separate observed digest/count and
fixed stream_budget_exhausted, and requires capture stop before further drain.
The post-stop observed drain is capped at stream ceiling+128KiB. A failure sink
can close selected observations with matching D digest; missing actor status
still remains unknown. Normal queue saturation pauses capture reads and applies pipe backpressure
without dropping D bytes. Actual queue/I/O transport loss instead marks
stream_transport_unknown and cannot be relabelled as budget termination. If the
sink hangs, failure persistence is unknown, even when owned kill/wait/release
succeed. D packets currently carry bytes only. Capture-relative timing and hook telemetry
transport are unimplemented; sink arrival time cannot substitute for actual
capture observations. The fixed coding-worker follow-on must add and pin that
bounded timing transport separately. Root owns B3/D1 interpretation and the real coding worker's successful
Retention.prepare-before-observations ordering. Default success retention is not
changed by this boundary.

`forget_transient()` is the runner's finally hook after selecting receipts. It
clears input, queued raw D and all worker channel buffers; receipts/owned wait,
namespace, release facts survive separately. Disposable workers retain only
selected metadata, never raw D or private input. Their fixed failure fixture
writer proves transport/closure with real processes; it does not attest the
coding profile, native actor, provider accounts, task correctness or success.

Run `sh scripts/test-owned-worker-boundary.sh`,
`sh scripts/test-external-supervision.sh` and `make check` locally. Both scripts
create exclusively owned delegated transient services, not existing services.
