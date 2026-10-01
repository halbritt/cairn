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

The memory engine must implement inbox recall version 1. The bridge executes the
engine's pinned bytes and hands those same bytes to it, so a current engine's
reported [caller-origin](client-observations.md#python-lifecycle-memory-origin)
`implementation_id` equals the manifest's `engine.sha256`; an older engine reports
none. Configurations must
agree on explicit `harness: "codex"` or `harness: "claude"`, `cairn`, `socket`, `token_file`,
and repository. Memory uses `recall_mode: "agent_tools"` and a byte allowance
between 1000 and 9500. Coordination uses `native_delivery: true`. State directories
are absolute and the coordination and memory directories differ. Credentials,
models, and API scope are not selected or created by this adapter.

A shared memory configuration can reference up to eight coordination
configurations. Multiple entries require distinct absolute `config_home` values.
The memory hook selects the actual `CODEX_HOME` or `CLAUDE_CONFIG_DIR`, with the same native default
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
turn in the existing memory-budget ledger, or Claude prompt owner in the inbox
grant ledger, under its session lock. The other
callback keeps that decision in either order. Activation waits at most 250 ms for
a busy session lock, then refuses if it remains unavailable. This wait precedes
the handler deadline and can add up to 250 ms to callback latency; it never
bypasses the ledger or allocates a grant. This writes mode metadata only;
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
The actual Codex native turn UUID must equal the attempt's owner. For Claude, the
complete channel envelope must name this native session, and the admitted owner
must equal `claude-channel:<prompt_id>` from the host event. Missing or mismatched
ownership refuses the wake before source reads. No native turn identity is
synthesized from an inbox ID, session ID or wake text. A Claude prompt owner does
not prove permanent exclusivity: the existing coordinator still handles joined
owner/channel input.

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
initial Codex `emitted_hook_bytes` (Claude `output_bytes`) and reserved optional
bytes remain unchanged.
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

### Claude entry support and continuation limit

Claude uses the existing admitted `UserPromptSubmit` channel boundary. Its grant
is scoped to the authenticated delivery and native prompt owner, not a fabricated
Codex turn. Before the first grant, serialized startup required context is carried
into the initial allowance once. A new ordinary host prompt can receive its own
grant; repeated or joined input with the same prompt ID cannot renew optional
credits. Missing ordinary prompt identity permits only required context. Original
owner input and capture callbacks remain unchanged, including PreCompact.

Bound Claude SessionStart always refreshes required context only. Successful
refresh records every emitted byte separately; it never repeats source History,
optional pulls or the initial optional allowance. A failure to restore known
required context stays pending. A successful authenticated current check may clear
it. A subsequent UserPromptSubmit refuses a still-proven required failure with
exit 2, including when a new prompt arrives before restoration. If a failed status write
could not record `pending`, the known active selection still requires a fresh
authenticated check across that prompt boundary; it is not silently cleared. Configuration or
optional-service failures with no known requirement remain nonblocking.

**Claude SessionStart failure does not stop the next model dispatch.** An isolated
installed Claude 2.1.285 resume probe with a scripted localhost response observed
another request after both exit 2 and exit-zero `continue: false`. This adapter
therefore emits a labeled error and retains pending status at that boundary; it
does not return a misleading stop decision. No PreCompact refusal or PreToolUse
guard is added here. A model request or text-only answer can occur before a later
supported prompt boundary. This is entry retrieval plus required refresh, not
complete mandatory-context enforcement through every Claude continuation.

Other harnesses are unsupported by this binding. Default unbound Claude memory
and coordination behavior is preserved. This extension supplies no installer,
channel activation, native-session restart, or measured task-benefit claim.

Focused tests exercise
public hook JSON/exit behavior and disposable transport fixtures; they do not
establish task benefit, live lease races, model compliance, or host adoption.

### Optional local route diagnostics

The pinned memory config may set `inbox_recall_trace: true` to observe the next
ordinary inbox invocations. It is off by default and requires a bridge and engine
with the route-trace contract. An older engine remains unobserved. Enabling it is
an explicit config/binding update; task text cannot enable diagnostics.

The bridge writes one private JSON file below the memory state directory's
`inbox-recall-traces/` after both locks release, within the existing reporting
deadline. It makes no additional Cairn or model calls. The trace includes host
attempt identifiers, checked source IDs/versions/hashes/spans, monotonic phase
and RPC durations, expansion/refusal branches, packing bounds and the final
bridge-returned source extents. `checked_source_returned` can precede a packing
refusal and is not delivery. `bridge_returned` does not acknowledge native
consumption or attention, and a later coordinator/host failure is outside this
bridge observation. A bridge persistence failure records error and zero returned
bytes, without a final emission list.

No task/query/note bodies, reasoning, handles, commands, exception messages,
credentials or token fields are retained. The sidecar is separate from the model
context and durable grant. At most eight RPCs, eight phase segments, 32 route
events and 16 source entries per collection are recorded, with a 32KiB file cap.
`complete: false` marks metadata collection errors or truncation; missing files
mean unknown observation. Invalid/unvalidated hosted destinations produce no
trace. Files and their directory must be owned and private; unsafe paths, write
failures or an elapsed deadline omit the trace without changing hook behavior.
The local write has no retries/fsync, but filesystem stalls cannot be assigned a
hard real-time bound. File count is per invocation: turn this diagnostic off after
the bounded observation and manage retained files explicitly.

The trace does not contain lexical/dense ranks or establish applicability,
currentness of claims within a note, useful action or comparative benefit.
