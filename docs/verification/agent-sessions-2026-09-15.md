# Agent session verification — 2026-09-15

## Session foundation

Implemented contract: [agent sessions](../agent-sessions.md). This is progress
within the [coordination plan](../plans/agent-coordination-v1.md), not acceptance
of the full plan or measured cross-agent usefulness.

Build `df6d1c3a3f9d1bb5c353fa3852a982fb84696cf6` was installed on the owner's host.
The CLI and running API reported the same clean VCS revision. Migration 037
applied after an operator checkpoint and backup, whose dump digest matched its
catalog. All seven worker profiles had no unfinished attempts before shutdown.
The API retained its semantic worker configuration. All seven wake supervisors
and the API were active after restart. No synthetic messages entered production.
The hosted directory read succeeded and returned no sessions; native registration
was not yet connected.

Required checks passed: `make test-integration` (disposable databases, race-enabled
Go tests and real CLI/API/systemd probes), `make check`, `make test-lifecycle`
(real backup/restore including session rows), and 101 Python tests. Coverage
includes separate conversations under one account, resume identity, execution
fencing, context revision conflicts, expiry, destination restrictions, restore
invalidation, base/session inbox separation and API restart.

The wake worker also receives a versioned JSON context with exact completion
arguments. Its process probe checked the file mode, identifiers and agreement
with the prompt. Existing crash/outage/cgroup and explicit-completion checks passed.

Cairn skill guidance was updated in skillpack `621cf8d`, pushed and deployed.
Both files matched across all eight existing installations, including Hermes.
The earlier Hermes CLI/gateway restart remains recorded in the wakeup report;
this foundation changes the shared API/CLI, not the Hermes provider integration.

## Recipient resolution

Current implementation adds exact project aliases, structured unique/ambiguous/
no-match/stale results and transactionally checked publication. The focused real
CLI/API probe verified ambiguous matches, a unique alias match, persisted
resolution, original-recipient retry after resume and rejection of new stale sends.
Store tests additionally check project changes, expiry, restore, visibility and
collection mismatch. `make test-integration`, `make check`, `make test-lifecycle` and 101 Python tests
passed for this follow-up. The fresh lifecycle database caught an initial bug:
resolution validation rejected database generation zero. Validation now accepts
the schema's initial zero, and both the lifecycle and complete integration checks
passed afterward. Deployment is recorded separately from the foundation above.


Build `f8f6dba278242ddaab6057168edb1f2237687451` and migration 038 were then
deployed. All seven profiles again had no active wake attempts before shutdown.
The new pre-upgrade backup matched its catalog digest. API and CLI reported the
same clean revision, and the API plus all seven supervisors were active after
restart. A read-only Codex/Rhumb resolve returned `no-match`, correctly reflecting
that native sessions are not registered yet. Skillpack `95cc5bf` was pushed and
all eight Cairn skill installations matched its source.

## Native presence adapters

The real disposable API probe now covers native process association, concurrent
owner refusal, process death, resume/fork, selected task preservation, model
updates and refusal to revive or stop an obsolete execution. State files exclude
prompt/transcript content. The Python suite passes 106 tests, including account
home filtering and preservation of unrelated hook configuration.

Optional installed-runtime probes passed:

- Codex first-turn hooks register its actual native thread; exact hook trust is
  established through the app-server protocol. Provider points to loopback only.
- Claude hooks register its supplied native conversation UUID with a loopback-only
  provider and no enabled tools/MCP servers.
- OpenCode and Hermes send injected identity to synthetic HTTP providers. Request
  copies preserve retained conversation history. Hermes CLI and gateway lifecycle
  checks distinguish live CLI presence from a completed gateway agent turn.
- Agy PreInvocation/Stop register its actual conversation and model. One explicit
  opt-in turn using the existing account returned the injected Cairn UUID. Agy
  required `--new-project` to bind this test to its temporary workspace. Its
  invocation hooks are flat handler lists, unlike its grouped tool hooks.

All coordination test writes used the disposable API. Agy's provider probe uses
its existing account and creates a native test conversation; other provider
probes use local fixtures. No synthetic coordination messages entered production.
Native presence deployment is recorded below. These tests do not
verify automatic native inbox delivery, pool dispatch or cross-agent task value.

Adapter commit `a2174c2` was pushed and installed for seven account bindings:
both Codex homes, both Claude homes, OpenCode, Agy and Hermes. The shared API/CLI
remains clean build `f8f6dba` with schema 038; this slice changes adapter scripts,
not the store or API binary. `make check` passed. The installed watcher is active
with zero automatic restarts. Codex confirmed all four exact Cairn hook commands
trusted and enabled in each account home. Existing hooks/settings were retained.

Hermes was idle before shutdown. It exited cleanly and restarted in the same
Herdr pane with native session `20260908_022628_ef33f8`; Herdr reported interactive
ready and idle. `hermes-gateway.service` restarted and is active with a new PID
and zero automatic restarts. The directory was still empty at verification:
registration requires a supported native lifecycle event, not process launch
alone. Existing sessions were not fabricated or registered by inspecting history.

Skillpack `97d8a25` was pushed and deployed to all existing harness locations.
Validation and `install.sh --check` passed. Its guidance distinguishes injected
session identity and watcher presence from manual registration and pending
automatic inbox delivery.
