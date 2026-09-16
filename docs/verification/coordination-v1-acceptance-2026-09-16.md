# Coordination v1 acceptance checkpoint — 2026-09-16

Runtime `6fdffe0` with migration 048 is deployed; see the
[native turn binding verification](native-turn-binding-2026-09-16.md).
This checkpoint distinguishes
implemented behavior, observed native use and explicit limitations against the
[v1 plan](../plans/agent-coordination-v1.md).

| Area | Evidence and remaining work |
| --- | --- |
| Session UUIDs, account namespaces, resume fencing and exact metadata resolution | Implemented and tested through disposable store/API and native lifecycle probes; see [sessions](agent-sessions-2026-09-15.md). |
| Existing-session delivery while busy | Installed Codex/Claude loopback-provider probes and an opted-in Agy real-model probe passed. The real Codex/Rhumb request queued while busy and completed at an owner-prompt boundary; see [the live trial](rhumb-live-routing-2026-09-16.md). |
| Automatic idle-session wakeup | Deployed across seven bindings; the real ai-newsroom conversation woke through the presence service, completed once and replied, then returned idle with its hold released. See [the automatic trial and host limitations](idle-session-wakeups-2026-09-16.md). |
| Real metadata-selected review exchange | Parent resolved the offered Agy session by harness/project, sent a real v1 review and read its result. Its explicit reply filled the deployed response group; see [response groups](response-groups-2026-09-16.md). |
| Two Codex and two Claude accounts | Separate bindings, principals and health are implemented. The later [automatic account trials](native-wakeup-accounts-2026-09-16.md) passed across all seven bindings, including both homes for each harness. These selected exchanges do not establish future capacity or all failure paths. |
| Provider failures | Selected Codex, Claude, OpenCode, Hermes and Agy observations are implemented and tested; [current coverage](../provider-failures.md) remains explicit. Agy's native recovery case requires step freshness because its final result retains an old 429. Claude's [native probe](claude-provider-failures-2026-09-16.md) found and verified a fix for stale 429 retention after terminal HTTP 400. |
| Fresh pools, watch and recovery | Deployed with bounded queues, retained process holds and independent task-assessment semantics. See [pools](worker-pools-2026-09-15.md), [watch](../inbox-watch.md) and [recovery](../event-recovery.md). |
| Scheduling, admission expiry, task deadlines and cancellation | Deployed and tested on disposable databases and real cgroups. Active native/manual interruption returns `UNSUPPORTED_CONTROL` until the host has a per-request stop contract; see [controls](request-controls-2026-09-16.md). |
| Response groups | Fixed recipients, explicit replies, deadline policy, duplicate/late handling, payload availability and restore fencing deployed and verified. |

## Live Rhumb registration gap

At 09:47 UTC Herdr showed a working Codex process in `/home/halbritt/git/rhumb`,
while Cairn's `harness=codex, project=rhumb` directory query returned no match.
Read-only process metadata showed that process started on September 14 using
the second Codex home; its hook configuration was installed on September 16.
The installer requires a fresh native process to load these hooks. This supports
an installation-timing explanation; it is not evidence that metadata routing
failed for a registered session. The active conversation was not restarted,
fabricated, renamed or replaced to manufacture a passing result.

On September 16 the owner directed us to wait until this Rhumb session is idle
before restarting and resuming the same conversation to load the hooks. Preserve
that boundary; an observer timeout does not establish that its work has ended.

## Live results and conditional features

Keep account launch success, recorded quota failures and task acceptance separate.

Update at 14:15 UTC: a different, genuine Rhumb conversation is now running with
the installed hooks. The process change happened outside this rollout; no restart
was performed by this agent. Exact metadata resolution succeeded and a real
review request was queued while that session was busy. It completed and
explicitly replied by 14:26:46 UTC after the owner’s inbox prompt created a
supported boundary. The parent read the result and acknowledged
the reply; the native hold finished. See [the live trial](rhumb-live-routing-2026-09-16.md).
This does not claim the old conversation resumed or that idle terminals wake
automatically. The owner selected ai-newsroom for a separate
[idle host-wakeup trial](ai-newsroom-idle-wakeup-2026-09-16.md).

A September 16 loopback-provider probe of Agy captured an ordinary HTTP 400
failure with exit code 1 and a terminal JSON object using `event: "result"`
and `result.status: "ERROR"`. This contradicts the reviewer's inferred
`type`/`data` envelope. The HTTP 429 probe made ten local requests and timed out
after 260 seconds without a terminal result; its repeated `error_message` step
events contain no provider classification. This does not verify automatic quota
reporting. Selected local evidence is in `/tmp/agy_nonquota_result.json` and
`/tmp/agy_quota_result.json`; the probe used a private settings bind mount and a
loopback endpoint. No Agy classifier was added from those incomplete observations.
A subsequent raw capture of print-timeout and recovery behavior supplied the
missing evidence. The [Agy report](agy-provider-failures-2026-09-16.md) records the
implemented classifier and successful native API/systemd checks.

Priority aging, new trust-boundary policy, recurrence, wildcard subscriptions,
batching, a broker, cross-host leadership and general workflows retain the plan's
explicit triggers. Their absence is not silently treated as completed work.
Native interruption remains an explicit capability limitation, not a simulated
cancellation by killing a shared gateway.

## Acceptance disposition

The seven milestones are implemented and deployed within their stated contracts.
The remaining live Rhumb exchange has completed. The additional owner-selected
ai-newsroom idle trial also completed: an explicit host prompt woke the existing
conversation, its native hook handled the queued request once, and its reply
collected before the deadline. The parent read and checked both results, then
acknowledged their replies; neither delivery retains a process hold.

The subsequent automatic idle-session extension is also implemented, tested and
deployed. At 15:15:31 UTC a new request was queued for the idle ai-newsroom
conversation. The presence service woke it without a parent terminal prompt;
native handling completed once, its explicit reply collected at 15:16:48 UTC,
and its hold finished at 15:16:53 UTC. The parent read and verified the result,
acknowledged the reply and confirmed the same conversation/process returned idle.

Coordination v1 remains incomplete. The owner rejected the earlier completion
claim on September 16 because required follow-ups remained. Their current state:

- Live automatic-wakeup verification across both Codex and both Claude accounts,
  Agy, OpenCode and Hermes. The later [account trials](native-wakeup-accounts-2026-09-16.md)
  now cover all seven accounts: Codex-one used its Reserve fallback and Claude-one
  resumed after its limit reset. This basic coverage is complete; resumed
  Claude-two refusals remain recorded failures.
- Herdr's final check-to-submission race and preservation of existing composer
  drafts. The explicit Codex Unix-listener route now uses a native queue;
  embedded Codex and other harnesses retain the unresolved terminal limitation.
- Per-request cancellation of active interactive work, which currently returns
  `UNSUPPORTED_CONTROL`.

The implementation, deployment and observed exchanges above remain valid
checkpoints. They do not close the overall goal. These gaps must be resolved or
explicitly deferred by the owner before completion; calling them follow-ups is
not a scope decision. The separately conditional features retain their recorded
triggers. Measured productivity remains a separate evaluation question.

### Native transport work in progress

An owned Claude Code 2.1.273 conversation received a local MCP channel notification
without terminal input and responded while its unsubmitted composer draft stayed
unchanged. An observer recorded the same native `prompt_id` on that channel's
`UserPromptSubmit` and `Stop` hooks; `MessageDisplay` supplied a separate `turn_id`.
This demonstrates a candidate submission route and request-ownership key. It is
not yet a deployed Cairn inbox exchange, a busy-queue test, or cancellation proof.
Selected local evidence is under `/tmp/cairn-claude-channel-probe`.

The two owner-offered agents are implementing the Claude/Codex and Hermes/OpenCode
adapters and their tests. Their work is pending review and deployment. Final
acceptance still requires the following evidence:

| Requirement | Evidence needed to close it |
| --- | --- |
| Preserve drafts and busy work on each installed harness/account | Real native submission with a pre-existing unsubmitted draft; queued work follows the current turn; the same conversation handles the intended delivery once. |
| Bind a queued wake to its intended conversation and delivery | Stale or changed-session input cannot claim another delivery; retries cannot duplicate an uncertain native submission. |
| Cancel one active interactive request | Completion is fenced; only the owning native request is stopped; its tools are confirmed stopped before its hold is released. A later owner turn remains unaffected. |
| Recover after host or adapter failure | Retained ownership reconciles uncertain submission and tool cleanup without replay or releasing an unconfirmed hold. |
| Deploy the verified adapters | Installed versions and required native startup configuration match the reviewed code; busy Rhumb remains uninterrupted. |

## Acceptance audit follow-up

The September 16 audit compared the original v0 spec, accepted v1 contract and
milestone tests/reports. PostgreSQL polling is an explicit accepted replacement
for the original broker proposal. No additional mandatory gap was identified
within that review beyond the live Rhumb trial. The offered agent independently
reviewed the same scope; selected result
`658ef19f-f6c1-4968-96a5-d166fc322e82/1` was read and acknowledged.
Its identified Claude native-verification gap led to the reproduced and repaired
terminal-status defect. This review does not convert passing tests into full
design acceptance. At 11:04 UTC Rhumb still reported working and remained untouched.
