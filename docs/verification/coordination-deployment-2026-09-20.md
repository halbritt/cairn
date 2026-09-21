# Coordination deployment — 2026-09-20

## Completion standard and current result

The owner rejected the source-only completion claim. The delivery goal requires
committed and pushed work, deployment, and verification of the running system.
This report records the actual rollout; it does not accept coordination v1 or
claim that existing native processes have reloaded their plugins.

At 21:15 PDT, the coordinator upgraded the CLI/API to clean
`b5aab96b7dca46fa73db52e400548c058a5d226c` and migrated the operational store
from 048 to 049. Main was pushed to `origin/main`. All seven bindings were
reinstalled, and the presence watcher was restarted at 21:17 PDT. Hermes was
fast-forwarded from the clean patch base `13f4cfeb` to the reviewed
`131e95b312d6aa82511b7db922ff497a6eed19c1`.

The release binary SHA-256 is
`cd53a17499e95f9167f05a2787c1226e90975dc22e8915d1e335cc4059952880`.
Deployment evidence and private recovery artifacts are retained under
`~/.local/share/cairn/releases/20260921T040708Z/`; they are not committed.
Later documentation commits do not change the binary's recorded source identity.

## Safeguards and live checks

A production backup completed successfully before migration; its checksum and
`pg_restore --list` both passed. A recovery export, old binary, bindings and
selected adapter/configuration backups were retained. No production database
was used for tests or cleanup.

The coordinator stopped scheduler/wake supervisors, observed zero active managed
wake attempts, briefly stopped API/presence, ran the candidate migration, atomically
replaced the CLI, and restarted the services. The immediate first version probe
raced socket startup and returned `API_CONNECTION_FAILED`; a subsequent probe
succeeded. The watcher likewise recorded startup connection failures, then resumed.
This was not a zero-error or continuously available rollout.

An independent reviewer verified eight core checks with no failures:

- CLI and API report clean `b5aab96`; installed, candidate and running-process
  executable hashes match.
- Schema 049's stored checksum matches the committed migration.
- API, presence, scheduler, seven wake services and store are running.
- All three original attempt/delivery identities survived. Two later completed
  normally; the remaining attempt retained its lease and renewed.
- No managed wake attempt was active and no original attempt acquired exclusive
  cancellation state.

The coordinator also exercised an ordinary hosted search and pulled the current
owner-correction note through the deployed API. A separate post-deployment
read-only verification request to existing agent24 is queued as event
`1d862786-dcbe-4910-b345-444064e4272e`; publication alone is not successful delivery.

The separate post-deployment Agy adapter audit completed in the existing agent2
conversation at 21:23 PDT: event `41689dca-7bce-4c59-b1e9-80723550f06c`, delivery
`139e7fd8-a208-4ced-9e05-afccb6a40eb2`, exactly one claim, explicit completion,
and native attempt `644ba8d9-7759-40ea-ac72-22319f2b41a6` finished. The selected
result `fca41321-e94a-452f-9306-4c5ac3b59479/1` confirms the supported Agy hooks,
installed helper hashes and absence of the unsupported Agy queue prototype.
The coordinator compared those hashes against its independent installed manifest.
This establishes live existing-session handling after rollout on Agy, not
OpenCode queue activation or native exclusive cancellation.


Migration 049 is additive, but constraints/index creation can scan rows. Do not
call it necessarily instant or catalog-only. Reinstalling the old binary alone
is not a supported rollback after migration commits: its migration check rejects
a newer schema and its code lacks new cancellation fences. Prefer forward repair;
a database restore requires coordinated admission and recovery reconciliation.

## Adapter deployment and verification

The coordinator corrected the prepared installer manifest before execution:
Codex settings are `hooks.json`, not `config.toml`; Agy's existing hooks are at
`~/.gemini/config/hooks.json`. The actual installer completed for codex-one,
codex-two, claude-one, claude-two, opencode-one, hermes-one and agy-one.
Codex's launcher was installed and both accounts' five hook definitions passed
the installer's native trust check. Existing unrelated settings were preserved.

Eight installed source artifacts match the committed bytes: coordinator,
Codex/Claude/OpenCode/Hermes helpers, Codex launcher, and both native plugins.
All seven installed bindings load successfully. Hermes's checkout is clean at
the reviewed companion revision; the complete patch is tracked in Cairn.

Fresh-process verification against the installed Hermes source passed the real
socket enqueue/admission path, request/delivery/draft preservation, native
wrapper/hook/tool turn identity, foreign/missing hooks, concurrent abort, and
ordinary owner input without Cairn identity contamination. All 21 standing
Hermes fixture tests also passed against installed native code. The tests used
temporary homes and substituted provider/Relay/hook boundaries; they made no
production registrations, claims or model calls. Evidence is under
`/tmp/cairn-agent88-installed-hermes-review/`.

Existing Hermes and OpenCode processes predate deployment and had no bridge
socket at inspection. They require a normal safe restart to load these routes.
The coordinator did not signal or replace active conversations. Gateway reload
can eventually interrupt work after its bounded drain and does not confer the
interactive CLI's queue capability. Thus installed artifacts and fresh-process
checks do not establish activation in every existing native conversation.

## Account activation follow-up

The post-rollout account audit found correct installed hooks in both Codex and
both Claude homes, but configuration alone did not establish automatic delivery:

| Account | Verified state | Remaining activation |
| --- | --- | --- |
| Codex one | New launcher and trusted hooks installed; inspected existing sessions had no native queue endpoint. | Same-session normal restart through the installed launcher. |
| Codex two | An existing native queue endpoint was discoverable. | Its process predates the native binary update; current-version acceptance remains unverified. |
| Claude one and two | Hooks installed; channel registration and activation configuration staged as described below. | Explicit channel-enabled native launch and live account-specific acceptance. |
| OpenCode | Plugin installed; agents24/67 retain old loaded modules. | Fresh processes resuming the same conversation IDs. |
| Hermes | Companion and plugin installed; fresh-process checks pass. | Older production processes still need normal restart. |
| Agy | Existing native audit request completed once after rollout. | Client-only queue prototype remains unsupported. |

The coordinator registered `cairn-events` with the deployed binary in each
selected Claude account home's `.claude.json` and prepared matching channel-ready
bindings in private release artifacts. The directories are account-specific under
`~/.local/share/cairn/coordination/channels/`, mode 0700. Registration contents
match the intended command. The coordinator removed the newly added
`claude_channel_dir` from live bindings after verifying that enabling it before
a native channel exists would suppress ordinary turn-boundary delivery. Apply
the staged field only with native activation, then verify the channel.

Activation requires the explicit selected `CLAUDE_CONFIG_DIR` and
`--dangerously-load-development-channels server:cairn-events`. This matters
especially for account one: its current production processes have no explicit
config directory and use `~/.claude.json`; staged registration is under the
selected `~/.claude` home. Account two already selects `~/.claude-harm`.
No existing process was restarted and organization policy is not bypassed.
Older isolated Claude probe channels are not production deployment evidence.

Read-only OpenCode source review found that SIGUSR2 reload and instance disposal
lack a busy/admission guard; plain module imports can also reuse cached plugin
code. Those operations are not a proven safe activation path. The coordinator
requested approval specifically to close and resume agents24/67 in their existing
conversations because unsent drafts or newly started work could be lost. No such
restart has been performed. The exact same-session resume plan is retained with
private release artifacts; it does not create replacement conversations.

## Exclusions and remaining acceptance

The residual Agy idle-queue code is a client-only prototype without a native
socket server. Its three files are committed at
`f5ceea6bc2e8500c52391875f3bf50abbe6c638b` and pushed on
`agent2/agy-residual`; its 13 isolated tests pass. Unsupported integration hunks
and superseded scratch patches were removed from main after preservation in
private recovery artifacts. Agy's supported lifecycle hooks remain installed.
No working Agy idle-queue capability is claimed.

Native exclusive admission/revocation, real-model cancellation with observed
cleanup, and broader live draft/submission-race acceptance remain open. Dormant
core controls and source fixture success do not enable these capabilities.
The delivery goal remains active while native activation and the queued live
verification are unresolved; the rollout is not a new full-v1 completion claim.
