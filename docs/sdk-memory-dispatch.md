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
