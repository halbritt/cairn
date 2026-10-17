# SDK engine memory dispatch

`trial_sdk_memory_gate.py` implements a serialized, attempt-owned debit and
forward boundary. `trial_sdk_engine_adapter.py` connects it to the authored
Cairn lifecycle engine's command path. These are source prerequisites for the
producer integration. There is no process broker or production coordinator in
this change, and independently constructing a gate in each producer would fail
the shared-budget contract.

One `MemoryDispatchGate` must live for the entire attempt. Its two fixed forwards
are supplied by the trusted builder, not by a request. `native()` accepts only
`mcp__cairn__cairn_search` and `mcp__cairn__cairn_pull`, matching
`trial_coding_launch.py`. Native searches debit the original two-search budget;
hook searches are counted separately. Native pulls and hook `pull`,
`pull-evidence`, and `history` calls share the original four-expansion budget.
Every hook history request charges, regardless of its version. The legacy
observer's native-history classifier does not authorize that tool in this profile.

The debit precedes the fixed forward. A returned service error still spends it.
A raised or interrupted forward leaves its outcome unknown, retains the debit,
and prevents further dispatch. Budget and validation refusals are sticky;
catching the exception cannot restore the attempt. A fifth expansion cannot
reach the forward even after another receipt or hook channel is created.
`requests` counts entries into the gate's dispatch method, including refusals;
frontend channel binding/timeout refusals increment `refusals` without entering
that method. Neither counter replaces the real engine's attempted-call meter.
The snapshot retains fixed fields with counts and the last operation/outcome, not query,
arguments, source text, or response bodies.

The builder supplies the original capture-origin-plus-30-second native deadline.
Each hook channel receives its original first-read-plus-five-second deadline.
A shorter requested RPC timeout can tighten that deadline; a new channel cannot
restore expansion credit. The gate checks time before and after forwarding.
The downstream owner must honor the passed absolute deadline. This synchronous
component cannot preempt a blocking forward. An arbitrary supplied timestamp is
not evidence of the capture origin or final native result delivery; callback
reply writing still belongs inside the original five-second interval.

## Explicit pending dispatch

The existing native()/hook.call() paths retain their synchronous behavior.
The parent service broker can instead use native_start()/hook.start() with the
same operation classification, debit and deadlines. This reserves one identity
slot before calling the SAME fixed forward and returns an opaque local handle.
No process is started by this gate. While that slot is unresolved, neither a
synchronous nor another pending dispatch can spend another debit or reach a
forward. An overlapping request is a sticky attempt failure, not a queue. The
coordinator must park legitimate concurrent requests outside this serialized
profile, within their original clocks and reservations; workload fit remains
unproved.

The gate retains only the once-tightened absolute deadline, scalar lifecycle
metadata and the fixed forward's returned value. The trusted broker should use a
bounded prepared operation as that value and must retain the actual service/job
INDEPENDENTLY before dispatch. Never return or retain the request through that
value. All retained references and pending metadata belong to ORIGINAL attempt
reservations; this API allocates no separate pool. The identity handle is neither
remote authentication nor current receipt/config/source/read authority.

complete_pending(handle) consumes that EXACT held handle and returns that exact
forward value once, after checks against the original deadline before and after
local retirement. This is local lifecycle closure, not API/CLI success or actual
service wait. A known nonzero outcome or refusal envelope can be returned without
certifying it. Actual response validation/delivery and callback cleanup still
must finish within the original clock. No caller-supplied result or success flag
can replace the held forward value.

fail_pending(handle) consumes its eligibility/value and returns None, records
completion uncertainty, poisons the same gate and preserves its FIRST failure.
It is valid after expiry or an earlier caught failure, so known local reference
cleanup needs no fresh time grant. It performs no job cancellation, actual wait,
remote free or budget refund. The independent job owner must still clean up.
Closing a hook channel only prevents its next call, preserving its existing
contract. The broker must explicitly fail the pending handle when its originating
callback/peer is lost; channel.close() alone is not cancellation or permission
to deliver a result to a closed callback.
Fake, foreign and stale handles refuse WITHOUT clearing the currently held slot;
its exact owner may then fail it. Retired/failed handles cannot settle again.

The new pending/last_pending diagnostics contain copied scalar sequence, deadline
and phase only, never the identity token, returned value or request. Phases are
forwarding, pending, retired and unknown. dispatches/returned/last.outcome keep
historical FORWARD meanings even when a later lifecycle fails. completion_unknown
is separate from dispatch_unknown and accounting_unknown. A late result or clock
failure cannot be delivered by complete_pending. Its failed retirement consumes
delivery eligibility; it does not restore a handle for replay. Critical guards
include final cleanup and return. Controlled interruption tests cover that exact
boundary; this is serialized parent source accounting, not universal recovery
from arbitrary asynchronous faults or hostile shared-memory access.

No authenticated service protocol, coordinator, actual peer/credential boundary
or real pending service job is composed by these tests. The source feature is a
required lifecycle prerequisite; it does not enable native execution/admission.

`EngineMemoryAdapter` accepts the selected Claude `agent_tools` config with
`recall_observations` explicitly false and no `inbox_recall_binding`. It wraps
`run_json` and replaces `bounded_command`, retaining the actual engine's decoder
and error mapping. Its channel returns `CommandOutcome` with actual waited
return code and bounded raw stdout/stderr. No fallback launches the original
command. The exact memory command prefix and read-operation set are checked on
both entry paths; arbitrary selectors, capture writes and telemetry commands
refuse. The original engine's `report_recall` catches errors, so a sticky adapter
failure is checked again when the adapter scope closes. Caught decoder failures
and unconfirmed envelopes likewise prevent a qualified callback result.

The adapter restores the original module functions on normal exit. Interrupted
restoration consumes its attempts, retains unknown states, and cannot replace a
body exception or permit qualification. An uncertain restore is never retried.
A raised clock read likewise poisons the attempt, retaining a known return when
it was already observed. Interrupted accounting is explicitly unknown.
It does not load or
pin the engine/config, close the channel, authenticate a producer, or contain
hostile code. Those belong to the fixed builder. In a separate producer, its
channel must connect to one parent-owned broker with actual owned-peer/lifetime
binding. The broker must hold the sole downstream credentials/socket; a config
shim alone does not prevent bypass. The current `NativeProcessOwner` has only
three stdio channels and no extra inherited RPC descriptor. Canonical finite
callback stdin and one JSON stdout result cannot silently become a duplex broker.
Broker queues and descendants must consume the original resource domain.

`engine_context()` accepts the pinned engine's empty object or a recall result
with exactly `hookSpecificOutput`, the matching recall event, and string
`additionalContext`. Post-tool events currently return an empty object. `None`,
other fields, wrong events and malformed context refuse. Empty output proves no
retrieval or delivery. The SDK controller still applies actual reply framing and
selected-input allowances; this extractor grants no independent byte allowance.

Tests exercise the actual public authored `integrations/lifecycle/memory.py`
module with inert downstream outcomes, including its `Memory.call` and original
JSON/error decoder. They do not run `handle()`/the engine CLI, read installed SDK
code, launch producer/CLI/model processes, or call the memory service. Actual
broker scheduling, kernel peer binding, exclusive authority, real engine state
and outputs, native compatibility and full CAIRN-107 evaluation remain unproved.
The earlier 7/16/8 shorthand has no verified memory-call interpretation; these
modules do not implement it or remove any separate original tool-loop bound.
