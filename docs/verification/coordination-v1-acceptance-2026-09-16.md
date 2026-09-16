# Coordination v1 acceptance checkpoint — 2026-09-16

Runtime `1846f95` with migration 047 is deployed. This checkpoint distinguishes
implemented behavior, observed native use and remaining work against the
[v1 plan](../plans/agent-coordination-v1.md).

| Area | Evidence and remaining work |
| --- | --- |
| Session UUIDs, account namespaces, resume fencing and exact metadata resolution | Implemented and tested through disposable store/API and native lifecycle probes; see [sessions](agent-sessions-2026-09-15.md). |
| Existing-session delivery while busy | Installed Codex/Claude loopback-provider probes and an opted-in Agy real-model probe passed. The live Codex/Rhumb trial remains open. |
| Real metadata-selected review exchange | Parent resolved the offered Agy session by harness/project, sent a real v1 review and read its result. Its explicit reply filled the deployed response group; see [response groups](response-groups-2026-09-16.md). |
| Two Codex and two Claude accounts | Separate bindings, principals and health are implemented. Earlier real account probes include successful completion and a Codex account-limit failure; [the wakeup report](agent-wakeups-2026-09-15.md) records these. They do not establish current capacity or all native failure paths for both homes. |
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

## Next work and conditional features

Complete the existing-session Rhumb trial after that conversation loads the
installed hooks. Keep account
launch success, recorded quota failures and task acceptance separate.

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
cancellation by killing a shared gateway. Full v1 acceptance remains open.

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
