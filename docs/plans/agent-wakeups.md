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

## Additional harness bindings — 2026-09-15

The owner asked about Codex, Claude and Agy after the first two bindings shipped.
Use the existing supervisor for all three fresh noninteractive CLI paths.

- [x] Inspect installed interfaces and provisioned profiles.
- [x] Verify native Codex completion against a disposable database and install its service.
- [x] Verify Claude and Agy native completion and install their services.
- [x] Update shared skill guidance and record the tested deployment limits.

The configured Claude Fable model reported exhausted usage credits in its first
live probe; test the existing account's Sonnet route before selecting the binding.
No new credential provisioning or credit purchase is part of this change.

All five bindings are installed. Claude uses `claude-sonnet-5`, the model resolved
by the successful Sonnet probe; the original Fable credit-limit failure remains
in the verification record. No runtime or schema change was needed.


## Account and identity correction — 2026-09-15

The owner corrected the missing second Codex and Claude accounts, then required
fungible agent names with harness/model metadata and routing by current work.
The initial harness-named deployments above are historical snapshots.

- [x] Verify that both Codex and both Claude account homes are authenticated and distinct.
- [x] Install seven ordinal worker-slot profiles and services with explicit profile context.
- [x] Retire old wake services after checking active attempts and pending requests; preserve inbox histories.
- [x] Probe the additional accounts: Claude completed; Codex returned a provider usage limit.
- [x] Record the [coordination v1 plan](agent-coordination-v1.md), including every deferred feature.

Current worker slots provide account coverage. They do not provide live session
identity or route messages to an existing interactive conversation. Those are
separate milestones in the coordination plan.
