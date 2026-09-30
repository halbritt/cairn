# Recall on an admitted inbox request

This opt-in adapter joins the existing coordination hook and memory hook. It
returns an admitted request's exact historical source and checked memory in the
same `UserPromptSubmit` response. It does not create an agent, submit a task,
claim work independently, or complete the request. Without an explicit binding,
existing behavior is unchanged.

## Binding and activation

Both configurations must name the same absolute `inbox_recall_binding` manifest.
The manifest is owner-only. Each referenced file is owned by that user, is not
writable by another user, and is pinned by SHA-256:

```json
{
  "schema": "cairn.inbox-recall-binding/1",
  "enabled": false,
  "bridge": {"path": "/absolute/inbox_recall.py", "sha256": "SHA256"},
  "engine": {"path": "/absolute/memory.py", "sha256": "SHA256"},
  "memory_config": {"path": "/absolute/memory.json", "sha256": "SHA256"},
  "coordination_configs": [
    {"path": "/absolute/coordination.json", "sha256": "SHA256"}
  ]
}
```

The memory engine must implement inbox recall version 1. Configurations must
agree on explicit `harness: "codex"`, `cairn`, `socket`, `token_file`,
and repository. Memory uses `recall_mode: "agent_tools"` and a byte allowance
between 1000 and 9500. Coordination uses `native_delivery: true`. State directories
are absolute and the coordination and memory directories differ. Credentials,
models, and API scope are not selected or created by this adapter.

A shared memory configuration can reference up to eight coordination
configurations. Multiple entries require distinct absolute `config_home` values.
The memory hook selects the actual `CODEX_HOME`, with the same native default
home used by coordination. It never chooses the first entry
on ambiguity. An unmatched home retains ordinary required-only wake recall.

Stage immutable files and configurations pointing to an owner-only manifest with
`enabled: false`. Disabled callbacks preserve the original source instructions,
required-memory hook, and accounting path without checking partly staged pins.
Run the pure `inbox_recall.validate_binding(config, require_enabled=False)` check
against the completed configuration set; it validates all pins without reading
memory or invoking a provider. One atomic manifest replacement with `enabled: true`
then activates the adapter. Global opt-outs, lifecycle children, privacy exclusions,
and capture callbacks do not acquire activation metadata or depend on this manifest.

Two callbacks can straddle that replacement. The first complete reserved-wake
callback records an enabled/disabled decision for the actual session and native
turn in the existing memory-budget ledger, under its session lock. The other
callback keeps that decision in either order. This writes mode metadata only;
it does not create a turn grant, change the active turn, read a source, or confer
authority. Locating the ledger during disabled staging reads only the owner-controlled
memory configuration's state directory; it does not require partly staged hashes.
All pins must still validate before enabled suppression or actual source/recall.

The initial introduction still requires already-started hook pairs to settle:
a callback that ran before its configuration referenced the manifest could not
have recorded a decision. The operator must verify that boundary before the first
flip. No broad pause of providers is implemented. A disabled flip is not an
instruction to restore files under a still-running enabled turn; wait for affected
callbacks/attempts to settle, then restore the coherent pinned set. New turns can
choose the disabled path. This slice adds no installer or service changes.

## Admission, source, and failure handling

The wake envelope is only a trigger. Coordination's authenticated admission and
live lease bind the request to the current agent, execution, delivery and attempt.
The actual Codex native turn UUID must equal the attempt's owner. No native turn
identity is synthesized from an inbox ID, session ID or wake text.

The bridge first searches required context and verifies the actual hosted
destination. It then reads the exact event source through authenticated History,
checking record ID, version, whole-body extent, byte length and digest. Historical
wording is task data from the sender, not current owner authority. It is supplied
only to retrieval and delivery; the original prompt and capture input are not
rewritten. Current access and privacy checks still belong to the API.

The task-specific search reuses the normal query builder and eager checked-body
reader. There is no hosted selector call. Whole required selections and competing
positions remain intact. A task-specific policy or authority refusal blocks the
wake, even when an earlier required-only search succeeded. Only fixed transport
unavailability categories permit fallback to that checked required context.

A source that is temporarily unavailable or too large for whole inline delivery
keeps the existing exact-source read workflow, with a visible reason and no new
optional lookup allowance. It is never truncated or treated as an empty task.
Source identity/access failures, a lost lease, unavailable required context,
changed bindings, and duplicate grants use the native prompt refusal path.

Notice/response and unadmitted wakes retain required-only memory. Coordination
opt-outs leave the independent memory hook active; a memory privacy opt-out
suppresses memory in both paths. The bridge also checks the explicit project root
and recorded workspace. A reserved wake alone does not authorize a read.

## Accounting and continuation

The initial combined allowance covers the complete emitted UTF-8 hook JSON, its
newline, source, control instructions, required context, candidate bodies and
framing. The renderer computes remaining native allowance using that same
serialization. Coordination control bytes are also reported separately and are
included in the total. Native tool-result envelopes, errors and retries must fit
the remaining optional allowance. Agent compliance is required for native calls;
this bridge does not intercept every tool result or provide a global API budget.

After compaction, current required-only context is restored whole under a separate
per-emission serialized limit. It does not consume or renew an already reserved
optional grant. Every such output is accumulated in `required_refresh_bytes`;
initial `emitted_hook_bytes` and reserved optional bytes remain unchanged.
**This does not satisfy an evaluation requiring all cumulative task memory bytes
to stay below 9500 when initial delivery plus refreshes exceeds that limit.**
Retain that original evaluation failure instead of silently changing its denominator.
The runtime distinguishes a bounded current-context delivery from optional-read
permission; neither measures the model's actual free context.

Recall I/O shares a five-second deadline; each source/pull operation is bounded by
two seconds. Coordination admission and final lease renewal use the enclosing
hook deadline. This is not a measured five-second end-to-end latency guarantee.
The normal coordination hook must have enough timeout for admission and cleanup.

Under the existing session locks, a separate durable marker and grant ledger are
written and fsynced before output. They contain identities, counters, hashes and
fixed statuses, not source bodies or queries. A crash after persistence can lose
an output but cannot renew its allowance. Missing/corrupt initialized state or a
repeat of the same delivery refuses; clearing an ordinary capture state does not
clear these grants.

The existing Codex native-turn ledger also records the grant. A matching
`serialized_hook_bytes` counter stamp identifies already encoded bytes. Missing or
stale stamps trigger conservative conversion of legacy/mixed raw counters; a stale
stamp after an old engine resets startup bytes does not invalidate or refund the
ledger. Compaction cannot mint another optional allowance. A new actual native
turn receives its own grant; a retired turn cannot renew one.

The ledger retains only a count, selection digest and pending status for known
required context, never its bodies. A fresh authenticated successful selection
updates that fact; a successful empty check can clear it. A service outage cannot
clear a known or pending requirement. An old package is not replayed as current
authority. A required first-save failure remains a typed refusal even when its
identity could not reach disk.

Only a proven current/known-required failure or explicit applicable policy refusal
uses the supported native stop. An unavailable profile, optional outage, generic
scan budget refusal, configuration drift or busy lock without known required
context keeps the ordinary labeled nonblocking behavior. `BUDGET_REFUSED` alone
is not evidence of a mandatory instruction: it also labels bounded scan refusal.
For bound Codex SessionStart, a typed required failure returns `continue: false`
and a fixed `stopReason` with exit zero. The
[Codex native contract](https://learn.chatgpt.com/docs/hooks#sessionstart) specifies
that this stops automatic compact continuation before another model request.
UserPromptSubmit uses exit 2. Nonzero SessionStart exits alone are not treated as
blocking.

**Claude activation is refused in this slice.** An isolated installed Claude
2.1.285 resume probe with a scripted localhost response observed another model
dispatch after both exit 2 and exit-zero `continue: false`. Its normal unbound
required-memory and coordination paths are preserved. Supporting that host needs
a proven continuation boundary; claiming parity from the common JSON field would
be incorrect. Other harnesses are also unsupported by this binding. This is a
staged implementation, not a reduction of the cross-agent retrieval goal.

Focused tests exercise
public hook JSON/exit behavior and disposable transport fixtures; they do not
establish task benefit, live lease races, model compliance, or host adoption.
