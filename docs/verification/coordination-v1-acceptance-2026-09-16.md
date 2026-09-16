# Coordination v1 acceptance checkpoint — 2026-09-16

Runtime `a15cbee` with migration 047 is deployed. This checkpoint distinguishes
implemented behavior, observed native use and remaining work against the
[v1 plan](../plans/agent-coordination-v1.md).

| Area | Evidence and remaining work |
| --- | --- |
| Session UUIDs, account namespaces, resume fencing and exact metadata resolution | Implemented and tested through disposable store/API and native lifecycle probes; see [sessions](agent-sessions-2026-09-15.md). |
| Existing-session delivery while busy | Installed Codex/Claude loopback-provider probes and an opted-in Agy real-model probe passed. The live Codex/Rhumb trial remains open. |
| Real metadata-selected review exchange | Parent resolved the offered Agy session by harness/project, sent a real v1 review and read its result. Its explicit reply filled the deployed response group; see [response groups](response-groups-2026-09-16.md). |
| Two Codex and two Claude accounts | Separate bindings, principals and health are implemented. Earlier real account probes include successful completion and a Codex account-limit failure; [the wakeup report](agent-wakeups-2026-09-15.md) records these. They do not establish current capacity or all native failure paths for both homes. |
| Provider failures | Selected Codex, Claude, OpenCode and Hermes observations are implemented and tested. Agy automatic provider-failure observation is missing; [current coverage](../provider-failures.md) remains explicit. |
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

## Next work and conditional features

Investigate a supported Agy provider-error envelope and add automatic health
reporting only from verified native observations. Complete the existing-session
Rhumb trial after that conversation loads the installed hooks. Keep account
launch success, recorded quota failures and task acceptance separate.

Priority aging, new trust-boundary policy, recurrence, wildcard subscriptions,
batching, a broker, cross-host leadership and general workflows retain the plan's
explicit triggers. Their absence is not silently treated as completed work.
Native interruption remains an explicit capability limitation, not a simulated
cancellation by killing a shared gateway. Full v1 acceptance remains open.
