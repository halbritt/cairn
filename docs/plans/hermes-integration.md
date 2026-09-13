# Hermes integration plan

Drafted 2026-09-13 against Cairn `d9c0884` and installed Hermes v0.20.5.
This records the original plan. Implementation and deployment evidence is tracked
in the [verification report](../verification/hermes-integration-2026-09-13.md).
Owner-confirmed initial scope: interactive CLI sessions **and messaging gateways**.
Both surfaces must pass their lifecycle checks before the first release is complete.
This supersedes the initial CLI-first assumption.

## Intended result

Hermes can explicitly search, pull, save and correct the same Cairn collection
used by the other agents. Relevant notes appear during work; useful decisions,
corrections and unfinished work survive session changes and compaction. Existing
Hermes memory stays available, with deliberate imports into Cairn.

Prefer a native Cairn memory-provider plugin that also registers a `pre_llm_call`
hook for recall. Keep recall in one place: the provider's ordinary `prefetch`
path must not perform a second search. Use provider callbacks for capture and
session transitions. Reuse Cairn's shared lifecycle engine and existing hosted
profile. Explicit tools use the normal Cairn MCP interface and concise skill.

Installed source supports that proposed route: the memory-provider loader forwards
`register_hook` to a real plugin context, built-in memory can coexist with one
external provider, and providers receive `on_pre_compress`. It still needs a native
probe before we treat the route as working. Activating Cairn occupies Hermes's
single external-provider slot.

## Ordered work

### 1. Prove the Hermes event contract

Build a small isolated fixture against the installed binary, using synthetic
dialogue and a scripted model endpoint. Confirm provider/plugin registration,
pre-turn recall, successful and interrupted turns, `/compact`, `/new`, `/resume`,
normal exit, and session-ID changes. Exercise both CLI and gateway dispatch paths
using synthetic dialogue; capture gateway responses in the fixture transport.
Inventory the configured gateway platforms and profiles, then test their actual
routing into the shared agent lifecycle. Inspect only fixture transcripts.

Check the actual tool and transcript formats, current working-directory source,
profile selection through `HERMES_HOME`, and how MCP receives scope labels.
Retain original Hermes session labels in scope; use a safe separate key for local
state if the IDs cannot serve as filenames. Do not describe profile-scoped MCP
labels as per-session identity unless the host actually supplies that scope.
For gateways, verify conversation, sender, channel/thread and profile mapping,
including concurrent conversations, reset, idle expiry, reconnect and service
restart. Use Hermes routing metadata to keep live conversation state separate;
repository-scoped Cairn notes remain intentionally shared across agents.

Two source-level complications must be exercised:

- Provider prefetch skips prompts including `continue`; the plugin pre-turn hook
  should still recover the current workstream.
- Injected context is retained in an `api_content` sidecar. Prove how request
  copies can bound accumulated Cairn context, refresh changed records and avoid
  feeding recalled text back into extraction. Use a supported request transform
  if needed; do not silently rewrite the user's original conversation.

Exit condition: a recorded event/identity map, a verified injection route and a
verified pre-compaction callback for both CLI and gateway paths. If a proposed
hook does not fire, revise this
adapter choice before building on it. No Hermes core fork is planned.

### 2. Install explicit Cairn access

Add an idempotent installer for the CLI and gateway profiles in use. Configure the existing
hosted Cairn MCP profile and install the shared skill. Preserve other MCP servers,
plugins, native memory files and task-model settings. Document uninstall and
scope behavior.

Exit condition: a real Hermes task discovers the tools, searches and pulls a
fixture note, creates selected shareable guidance, and revises that same record.
Use a disposable Cairn store for this test. Repeat installation without changing
unrelated settings. Verify tool availability from both a CLI task and a gateway
conversation; do not infer gateway readiness from CLI success.

### 3. Add ambient recall through the shared engine

Translate Hermes events into the existing lifecycle input. Carry project and
workstream context, file/error hints and the host's actual session identity.
Retain relevance filtering, required context, version suppression and a combined
12,000-byte Cairn context ceiling. Respect existing opt-outs. Skip automatic work
for child, auxiliary and cron runs. Interactive gateway conversations are included
in this first release.

Exit condition: a fresh Hermes session resumes a named Cairn handoff, including
when asked only to continue; unrelated prompts add no weak optional notes;
unchanged records do not accumulate copies; changed records can be recalled;
compaction makes evicted context eligible again. Measure retrieval latency and
context bytes during these checks.

### 4. Add selective storage and compaction checkpoints

Feed bounded original user/assistant content into the shared selector. Exclude
tool output, reasoning, compaction summaries and injected `api_content`. Keep
reusable notes separate from workstream checkpoints; read existing notes before
revision. Leave native-memory write mirroring disabled so there is one intentional
Cairn capture path.

Use completed turns for early selected capture and the provider's pre-compression
callback for the last checkpoint. Serialize work per session and coalesce repeated
requests for unchanged content. Check interrupted turns and session switches too.
For gateways, cover agent recreation between messages, duplicate inbound delivery,
concurrent conversations, idle expiry and graceful restart. A repeated gateway
event must not duplicate a save or attach another conversation's checkpoint.
Hermes's memory-manager shutdown drain is five seconds, while Cairn selection can
take longer: exit must not be the only point that starts extraction. The native
probe must establish completion/flush behavior and bounded shutdown before
claiming final-turn capture is reliable. Do not add a new daemon or raw transcript
spool as an assumed solution.

Initially reuse the installed Claude selector, as OpenCode already does. Hermes
keeps its configured task model. Record selection call count and latency; consider
Hermes's auxiliary-model facility later if it provides a concrete benefit.

Exit condition: durable guidance and a checkpoint are saved separately, an owner
correction updates its existing note, unchanged compaction/exit makes no extra
selection call, and a failed save remains retryable. Service/selector failures
produce a visible diagnostic while the main task can continue.

### 5. Verify continuity, then deploy

Run focused adapter tests plus a native Hermes fixture against disposable
PostgreSQL. Verify Hermes can continue a handoff from another agent and that the
other agent can retrieve Hermes's selected correction. This is new adapter
regression coverage; the owner does not need to repeat the earlier manual demo.

Run the same recall/save/correction/compaction scenarios through CLI and gateway
entry points. Include CLI-to-gateway and gateway-to-CLI handoff continuation.
Cover compaction, session switches, duplicate events, two concurrent gateway
conversations, profile routing, reconnect, idle expiry, graceful service restart,
service failure, capture timeout and opt-out. Run the existing lifecycle suite and
required repository checks to protect Claude and OpenCode. Report native behavior
separately from selection quality and usefulness.

Install into the real CLI and gateway profiles after both fixture paths pass.
Load the updated plugin in fresh CLI sessions and restarted gateway services.
Verify tool availability, ordinary selected retrieval and the installed revision
for each surface. Observe normal use for relevance, repeated context, missed
checkpoints and capture cost. Completion requires both CLI and gateway coverage;
a passing CLI adapter alone is an intermediate milestone.

## Deliverables and limits

Expected source areas: `integrations/hermes/`,
`scripts/install-hermes-integration.py`, focused adapter/native fixture tests,
small Hermes-specific changes in `integrations/lifecycle/memory.py`, and
`docs/hermes-lifecycle.md`. The installer also deploys the existing Cairn skill.
No Cairn database migration, new retrieval algorithm or bulk Hermes-memory import
is expected.

A plain MCP-and-skill setup is the fallback useful first milestone if native
lifecycle probes fail. It does not meet the ambient recall/checkpoint objective.
A generic hooks-only plugin remains an alternative if it can prove all required
lifecycle behavior without occupying the external-provider slot. The preferred
provider-plus-hook route is provisional until milestone 1 passes.

Sources: installed `plugins/memory/__init__.py` (`_ProviderCollector`),
`agent/agent_init.py`, `agent/turn_context.py`, `agent/memory_manager.py`,
`agent/conversation_compression.py`; Cairn `integrations/lifecycle/memory.py` and
[OpenCode lifecycle contract](../opencode-lifecycle.md). Official
[Hermes hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)
and [plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
provide the public extension contract. Source inspection is not native acceptance.

The [planning receipt](hermes-integration-decision.json) records the bounded
recommendation, evidence and implementation gates. The selected doctrine is to
verify native capability before depending on it, reuse the existing boundary,
and give background capture explicit ownership and completion behavior.

Pincite retrieval used validated release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, doctrine-f6bbb5196a3f8bf9 and
`retriever-ec995ecdd083b2c8`. Packet `pkt-065672483169d69f` has SHA-256
`065672483169d69f932497874478786b640c00519b323ee7123be117d907e34d`.
The receipt explicitly retains 13 unmet implementation-verification obligations;
they do not block choosing the first probe, and they do block claiming the
integration already works.

## Implemented route

Native probes confirmed the provider route, with request middleware for recall
and synchronous completed-turn capture. Gateway manual compression requires a
`pre_command` observer because its helper disables memory initialization. Turn
events bind dialogue to session labels, avoiding stale queued `/new` callbacks
after `/resume`. These adaptations preserve the shared selection policy and need
no Hermes core patch. The planning receipt above remains a historical proposal;
see the implementation receipt linked from the verification report for observed
coverage and limits.
