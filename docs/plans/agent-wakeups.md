# Agent wakeup implementation plan

1. [x] Update the Cairn skill in skillpack and deploy it before implementation.
   Commit 8d24765, pushed and verified in all seven local skill locations.
2. [x] Record the selected contract in ../agent-wakeups.md.
3. [x] Add transactional wake attempts, request-only claims, retry scheduling,
   receipt links, inspection and a hold respected by ordinary inbox claims.
4. [x] Add the host supervisor and worker, systemd lifecycle reconciliation,
   lease renewal, native completion guidance and bounded launch configuration.
5. [x] Exercise store invariants and process/restart failures on disposable
   PostgreSQL; verify native OpenCode and Hermes adapters with controlled input.
6. [x] Run integration, static and lifecycle checks; review error paths.
7. [x] Back up, migrate and install; enable OpenCode and Hermes user services;
   record observed deployment and limits, then update the skill's wakeup guidance.

Recovery owner: the host supervisor stops and reconciles its recorded unit.
The operator decides whether to publish a new request after uncertain execution.
Existing manual event handling and named profiles remain compatible.

Delivered in `e4fa703`; deployment and test limits are recorded in
[verification](../verification/agent-wakeups-2026-09-15.md). Hermes CLI and gateway
were restarted as requested.
