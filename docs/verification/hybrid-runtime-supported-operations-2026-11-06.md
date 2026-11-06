# Hybrid runtime supported operations — 2026-11-06

This characterizes the existing lifecycle source for CAIRN-303 / R-01.
Runtime and focused-fixture bytes were measured at
`d3c255a4bb75438e93527eaa1d83da780fd11eea` and rechecked unchanged at
`db45c4474fa7911b5569f2f55ebf2bd19c9a81bb`. The latter adds campaign
documentation. This page records source behavior and bounded fixture results;
it does not establish installed-client acceptance or close R-01 or X-01.

The current [bundle inventory](../../integrations/lifecycle/lifecycle_bundle.py#L16)
has 17 members. Its source-derived descriptor, after the normal installed-loader
marker is applied, is SHA-256
`53649855290f423dbb82992873a7c2f0bc7be6f91d78a807f9036009421398f1`.
The memory engine's own source SHA-256 is
`6c9cafcde3600671ed11e4f49f220c086e33ebf998051969931a9e232059f597`.
Bundle identity and memory caller-origin identity remain separate; see
[lifecycle bundles](../lifecycle-bundles.md).

## Ordinary recall and selected capture

Ordinary memory hooks operate independently of native inbox admission. Recall
must preserve whole required context; optional recall can defer or degrade under
the documented conditions. Capture saves selected knowledge from bounded
original dialogue. The [shared admission module](../../integrations/lifecycle/hook_admission.py#L317)
applies account, environment, identity and privacy checks.

| Harness | Ordinary recall / capture boundary | Limits and source |
| --- | --- | --- |
| Claude | `SessionStart` and `UserPromptSubmit` recall; `PostToolUse` / `PostToolUseFailure` retain hints; `PreCompact` and `SessionEnd` capture. | Ordinary `Stop` does not capture. The [installer](../../scripts/install-claude-hooks.py#L91) installs the six memory hooks; coordination has its own Stop hook. [Guide](../claude-lifecycle.md). |
| Codex | `SessionStart` / `UserPromptSubmit` recall; `PreCompact` and asynchronous `Stop` capture. | Stop normally requires six new top-level messages; a pending capture retry has its documented exception. A `stop_hook_active` continuation is not a new capture boundary. No ordinary `SessionEnd` capture or tool-hint hook is installed. [Event table](../../scripts/install-codex-hooks.py#L24), [admission](../../integrations/lifecycle/hook_admission.py#L344), [guide](../codex-lifecycle.md). |
| OpenCode | The TS plugin recalls for a new top-level user message through the task-request transformation, collects tool hints, and captures before compaction and on idle/disposal. | It changes the request copy, excludes child sessions, and bounds retained context. Idle/disposal capture can fail or be lost on abrupt termination. The required TS host plugin remains part of the path. [Plugin](../../integrations/opencode/lifecycle.ts#L33), [guide](../opencode-lifecycle.md). |
| Agy | No ordinary lifecycle memory installer or host adapter exists in this installer set. Explicit MCP memory uses declared scope. | The ordinary installed fixture enumerates Claude, Codex, OpenCode and Hermes. Agy's coordination hooks do not imply ordinary automatic recall/capture. [Fixture](../../scripts/test_installed_lifecycle_bundle.py#L72), [scope guide](../agy.md). |
| Hermes | The Python memory provider recalls before an owner turn, adds context through request middleware, and captures completed turns and before compaction. | Host session/profile checks, registration disposal and bounded original dialogue remain required. A failed operation can retain pending capture. Required Hermes Python is part of the integration. [Provider](../../integrations/hermes/__init__.py#L192), [guide](../hermes-lifecycle.md). |

Codex exit does not guarantee a checkpoint: unfinished async Stop work can be
cancelled and dialogue after the last completed capture can remain unsaved.
The [capture-marker assertions](../../scripts/test_codex_lifecycle.py#L398)
distinguish changed/truncated transcripts, failed capture and Stop continuations.

## Bound required inbox recall

The explicit `cairn.inbox-recall-binding/2` bridge supports **Codex and Claude**,
with `agent_tools` memory and native coordination delivery. It checks complete
code/configuration pins, matching profiles, distinct state paths and actual
native turn or channel-prompt ownership before source reads. Wake text alone
does not authenticate an assignment. OpenCode, Agy and Hermes do not gain this
bridge from their ordinary memory or coordination adapters.

The [binding validator](../../integrations/lifecycle/inbox_binding.py#L75) and
[bound-recall guide](../inbox-recall.md) describe these conditions. Pin drift,
wrong ownership, unavailable required context or lost lease can refuse the
native prompt. Repeating a bound delivery cannot renew optional credits.
Claude `SessionStart` failure remains nonblocking; known required-context
obligations are retained for a later supported prompt refusal, rather than
guaranteeing that every intervening dispatch is blocked. Source assertions:
[Claude required-context handling](../../scripts/test_claude_inbox_recall.py#L145)
and [bound repeat / lease failures](../../scripts/test_inbox_recall_bridge.py#L595).
Those suites were inspected, not executed in this discovery.

## Native delivery, idle wake and cancellation

Native delivery is opt-in and admits work through the existing authenticated
session/attempt. Default delivery admits one item per native turn or supported
Stop continuation. The optional [compact notice batches](../compact-inbox.md#bounded-notice-batches)
have separate bounded advance rules; batch delivery was not exercised here.
Idle wake is an additional capability; a missing native endpoint
does not authorize a terminal-input fallback. See
[native inbox boundaries](../native-inbox.md#boundaries-and-ownership) and the
[transport implementations](../../integrations/lifecycle/wake_transport.py).

| Harness | Delivery / idle wake | Cancellation boundary |
| --- | --- | --- |
| Claude | Start/prompt hooks and one Stop continuation; idle wake uses a configured native channel. A daemon-managed background `SessionStart` does not claim work, and a missing channel waits for a prompt. | Public running native request cancellation is unsupported. A joined owner/channel prompt does not prove exclusive request ownership. |
| Codex | Start/prompt hooks and one Stop continuation; idle wake uses the verified native Unix queue and its exact delivery/turn binding. `Interrupt` updates presence only; it does not prove tool cleanup or release work. | Public running native request cancellation is unsupported. The installer deadline fixture's synthetic turn interruption is a separate process-cleanup test. |
| OpenCode | The next main request transformation admits context. Idle/post-turn callbacks reconcile existing handling; a peer-verified idle bridge can create the next boundary. | Only the qualified opt-in trial can attest cancellable ownership. It requires `opencode_cancel_enabled`, both peer capabilities, exact request/turn identity and captured tools. No general cancellation guarantee follows from ordinary installation. |
| Agy | `PreInvocation` injects context; a fully idle Stop can continue once. Background work keeps Stop busy. Only top-level idle conversations have the Herdr wake route; a subagent inside its parent's process waits for a parent-initiated turn. | No supported public native request cancellation route. A parent's pane identity cannot be substituted for a subagent's conversation. |
| Hermes | Context arrives before a turn through host hooks/middleware. Idle wake uses the exact-session native queue; post-turn reconciliation does not itself create a turn. | The host-local `session/abort` handler has exact queue/admission/turn checks. It does not attest Store-owned exclusive request cancellation; public native cancellation remains unsupported. |

The [coordination hook](../../integrations/lifecycle/coordination.py#L2689)
owns admission. Its [Interrupt boundary](../../integrations/lifecycle/coordination.py#L4011)
and [Agy output](../../integrations/lifecycle/coordination.py#L4079) are distinct
host contracts. [Request controls](../request-controls.md#native-interactive-cancellation-opencode-trial-gate)
qualifies OpenCode's trial; the
[OpenCode wake transport](../../integrations/lifecycle/wake_transport.py#L195)
checks capabilities, and
[tool capture](../../integrations/lifecycle/coordination.py#L1672) rejects
unsupported tools. Hermes's [local abort handler](../../integrations/hermes/coordination.py#L292)
does not broaden that public contract.

## Recovery and installation limits

An unchanged explicit inbox command can retry through the authenticated API,
including a locally settled slot. Ordinary automatic journal replay sends
pending entries; refused outcomes are final for that recovery path. Preserve
the existing `finish_reply` exception: if completion committed without its exact
result reference and no response exists, recovery retries that same completion
to obtain the reference before creating its reply. This does not replay arbitrary
settled commands. See [the journal owner](../../integrations/lifecycle/coordination.py#L2353)
and [explicit retry / refusal assertions](../../scripts/test_inbox_recovery.py#L810).

Supported explicit recovery remains evidence-bound. Codex refusal/ended-turn
recovery and Claude channel refusal recovery do not authorize another session's
claim, fabricated turn identity or automatic resubmission of uncertain work.
The [recovery implementations](../../integrations/lifecycle/coordination.py#L3295)
and [native recovery guide](../native-inbox.md) define those paths.

A current complete bundle is required by the bound bridge. An enabled binding
blocks runtime replacement, including another enabled account sharing the
coordination runtime. Reinstall preserves configured state and retained ledger
bytes. Disabled staging and later activation have their own drain/pin procedure;
they do not establish mixed-version fencing or full live-client refresh.
[Bundle upgrade guide](../lifecycle-bundles.md#upgrade-and-rollback),
[installed preservation/refusal tests](../../scripts/test_installed_lifecycle_bundle.py#L120),
[validated-byte isolation tests](../../integrations/lifecycle/test_lifecycle_bundle.py#L70).
Historical binding/trial selections were not rerun here.

## Fixture coverage observed in discovery

The following existing groups ran on 2026-11-06 against the pinned bytes above:
35 distinct tests, no failures or skips. The installed-bundle group ran three
ordinary samples; each other group ran once, giving 49 test executions. A
separate timing-only wrapper reran the same seven installed-bundle assertions.
It changed no assertion or expected outcome. No Go/Rust build was needed.

| Executed group | Distinct tests | What the result covers |
| --- | --- | --- |
| [Installed lifecycle bundle](../../scripts/test_installed_lifecycle_bundle.py) | 7 | Four ordinary-memory and five coordination installations; help and PostToolUse entry points without a source checkout; synthetic profiles, network isolation, preservation, enabled/shared-binding refusal and helper drift. Coordination hooks use the fixture's privacy exclusion. Help/hint/excluded calls are closure smoke, not effectful recall/capture or native-delivery acceptance. |
| [Lifecycle bundle](../../integrations/lifecycle/test_lifecycle_bundle.py) | 6 | Complete inventory; changed/missing/nonregular/alias refusals; bundle-local validated bytes; [shared staged hook admission](../../integrations/lifecycle/test_lifecycle_bundle.py#L102); queue-adapter loading; checked bundle copy. These do not establish host callback reload. |
| [Coordination installer](../../scripts/test_install_agent_coordination.py) | 20 | Scratch installation limits, unrelated-profile preservation, shared-watcher refusal, compact configuration checks and Herdr installation in an isolated account home. No provider task or production watcher was started. |
| [Setup deadline](../../scripts/test_install_agent_coordination_timeout.py) | 1 | Termination of a synthetic app-server stalled during setup RPC. |
| [Active-turn deadline](../../scripts/test_install_agent_coordination_active_turn.py) | 1 | Exact synthetic turn interruption, sandbox descendant cleanup and absence of false settlement. This uses a Node stand-in, not a real model. |

Run these entry points from the repository root with PyYAML available to the
Python interpreter; the [Hermes installer imports it](../../scripts/install-hermes-integration.py#L12).
Installed closure needs a working Linux `bwrap` namespace sandbox. The
[native Herdr preservation case](../../scripts/test_install_agent_coordination.py#L194)
needs `herdr`, and the [active-turn deadline case](../../scripts/test_install_agent_coordination_active_turn.py#L35)
also needs `/usr/bin/node`. Those fixture decorators skip cases when their
tools are absent. The reported zero-skip discovery run had these prerequisites;
a skipped case leaves its coverage unverified.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_installed_lifecycle_bundle.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -B integrations/lifecycle/test_lifecycle_bundle.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_agent_coordination.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_agent_coordination_timeout.py -v
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/test_install_agent_coordination_active_turn.py -v
```

The initial module-style discovery runner omitted the scripts import path and
produced loader errors; the script entry points above passed without source
changes. Smoke subprocess timings include interpreter/bundle/bwrap startup and
some excluded or hint-only hooks. They establish no recall, capture, model or
task latency, performance improvement or measured usefulness.

| Source-inspected assertions, not run in discovery | Remaining claim boundary |
| --- | --- |
| [Bound source/required-context/lease/pin failures](../../scripts/test_inbox_recall_bridge.py#L595) and [Claude prompt ownership](../../scripts/test_claude_inbox_recall.py#L125) | No effectful installed bound-recall acceptance from the closure smoke above. |
| [Both output pipes and input/output pressure](../../scripts/test_claude_lifecycle.py#L525), [capture locking](../../scripts/test_claude_lifecycle.py#L248), [Hermes timeout/shutdown](../../scripts/test_hermes_lifecycle.py#L140) | These assertions do not establish a real multi-process writer-crash/restart campaign here. |
| [Completion/reply persistence and lost responses](../../scripts/test_inbox_recovery.py#L493), [peer identity and frame bounds](../../integrations/lifecycle/test_unix_peer_transport.py#L54) | Simulated loss/restart and same-process sockets do not replace abrupt process death or independent-peer acceptance. |

Effectful installed search/capture, representative native delivery/idle-wake and
recovery cycles, actual multi-process crash/restart/rollback, model execution,
database tests and live refresh remain unverified by this page. Configuration
pins and process images alone cannot establish which connection or cached hook
implementation a live session uses. Operational observations remain outside
this source/fixture record. The [testing guide](../developer/testing.md) and
[deployment guide](../operator/deployment.md) describe their separate checks.
