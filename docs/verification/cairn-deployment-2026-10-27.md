# Cairn release — 2026-10-27

The primary CLI and API were deployed on 2026-10-27 at 14:35 PDT from clean
source revision
`b0ae063d364ebd5377ba157f5aa205acef14e107`, built with Go 1.25.0. Binary SHA-256:
`984c40170dc7fc77d76caecd537583d3b64a58ab8393926bfdd77c23e067a86d`.
The previous installed CLI/API reported clean revision `0df2bf5`; its retained
binary SHA-256 is `234831d9953aa713e1052ca870aff1416203f1260de1f273b4ded02e244bb4b3`.

## Recovery point and validation

A pgBackRest differential completed successfully as
`20261025-010451F_20261027-140310D`, based on full backup
`20261025-010451F`. It ran from 14:03:10 to 14:10:38 PDT and covered the full
configured PostgreSQL cluster. The reported differential size was 145.7 GB.
The archived WAL range was `00000001000001AB00000071` through
`00000001000001AB00000072`, with stop LSN `1AB/72726E38`. The backup chain
reported `ok`, and PostgreSQL reported zero archive failures.

The initial full disposable integration run passed. The refusal change's full
`make test-integration` also passed before landing, including race-enabled Go
packages and Python API, coordination and MCP fixtures. Rebased, combined source
passed `make check`. Lifecycle tests passed 315 cases and a nine-case old/new
context/state/refusal comparison. Weekly tests ran 119 cases successfully with
one existing test skipped for absent explicit disposable-store inputs.
Production was not used for tests or test cleanup.

## Deployed binary and schema

The live store advanced from migration 072 to 073. Migration 073's stored
SHA-256 matches source:
`7778cd230f9e424c8a1c950fab80e0c20192bc0b5af43b347aa9460e031b694a`.
It extends the recovery-action ledger's allowed actions/outcomes and permits a
null request ID for polling-path sweeps. It does not remove production rows.
Migration lock acquisition was bounded to five seconds.

The deployment waited for zero unfinished native and wake attempts, zero
unexpired delivery leases, and settled callbacks/journals. It stopped the
scheduler, seven wake supervisors, presence and the primary API, migrated,
replaced the binary atomically, and started those services again. The initial
worker starts raced API socket readiness: each supervisor retried once after
an `API_CONNECTION_FAILED` exit. All ten services then reported active; the
API, presence and scheduler had zero automatic restarts, and each supervisor
had one. No provider-failure recovery or task reissue was performed.

Authenticated client/server version checks both report the exact clean release
revision. A hosted search followed by an exact pull returned the same record
version and matching SHA-256. The operator recovery review read also succeeded.
Final checks found 73 migrations, zero unfinished attempts or unexpired leases,
and zero PostgreSQL archive failures. The three existing inbox bindings still
validate under their installed loader. All archived lifecycle/configuration
preimages match their live files; only the binary changed.

Migrations are forward-only. Retain the previous binary for a reviewed binary
rollback; it is not a schema downgrade. Database restoration still requires the
restore-admission and reconciliation procedure.

## Lifecycle bundle activated — 2026-10-28

The activated complete bundle has digest
`6d79a86d0d061c5c6a0fff959b655baebd6d5f9ad95299f3ee391d8952bde683`.
Pure validation passed against private configuration copies for three inbox
bindings and five hook accounts: Codex one/two/three and Claude one/two. Staged
entrypoint checks also passed. Existing account, credential-path, state-directory,
recall mode and privacy-marker choices are preserved by the plan. No live
hooks, binding pins or token-efficiency opt-ins were changed during staging.

The [lifecycle upgrade procedure](../lifecycle-bundles.md) requires draining
callbacks and restarting long-lived hosts. Native probes established why this
is necessary for the installed Codex version:

| Native probe | Observed command on the second turn after a settings edit |
| --- | --- |
| Codex 0.164.0, same process and thread | Old UserPromptSubmit and Stop commands |
| Codex 0.164.0, explicit thread/resume before the second turn | Old commands |
| Claude 2.1.311, same process and session | New commands |

Each probe used two successful scripted loopback turns in an isolated network
namespace with disposable homes. Codex's new definitions were trusted by a
fresh reviewer app-server before the original thread ran again. No real account
credentials or external model providers were used. These observations apply to
the tested versions and modes, not every host or event.

An existing Codex thread can therefore invoke its retained old memory engine
after settings change. Its 15-member bundle validator rejects the candidate's
17-member descriptor. The release kept the old bindings coherent until the
owner authorized a coordinated checkpoint/restart of the affected hosts. Fresh
app-server trust checks alone cannot establish adoption by existing threads.

Activation switched stable entrypoints to one immutable complete snapshot
instead of replacing live helper files individually. It preserved preimages,
checked exact live hashes, validated disabled bindings, and recorded each
mutation phase. The lifecycle-only operation did not repeat the binary install
or schema migration. The API stayed running while the scheduler, seven wake
supervisors and presence watcher stopped for the switch. Old entrypoint
directories are now compatibility paths; their old manifests are superseded.

The restart procedure preserved each conversation's native ID, pane, account
profile and working directory. A pilot and an isolated installed-version probe
verified bare exact-ID resume with no model request. Remote resume rejects
explicit permission overrides, including `--yolo`; the saved permissions were
restored by the bare resume.

The first automatic verifier encountered transient process records without
`argv` and startup deadlines before several conversations were loaded. It
recorded seven verified resumes and seven verification errors, so it did not
send the planned continuation. This was not a successful unattended completion.
After the owner resumed this conversation, fresh checks verified all 14 original
conversations in their intended panes. Each retained its model, OpenAI provider,
account profile, working directory, high reasoning effort, `never` approval
policy and full-access sandbox. Each fresh host reported all four expected
memory hook definitions as trusted. Metadata-only resume verification used
`excludeTurns` for the two large conversations after full-history responses
exceeded the bounded queue reader's limit.

All three enabled inbox bindings validate under the new immutable loader, and
all five hook account files match the intended definitions. This resumed user
turn exercised the new native hooks. The other idle conversations were checked
without starting model turns; their later task execution is not claimed here.
Final checks found all ten services active with zero automatic restarts since
their latest starts, matching clean CLI/API versions, unchanged migration 073
and its digest, and zero unfinished native/wake attempts or unexpired leases.

## Follow-ups and limits

The [follow-up assessment](architecture-followups-2026-10-27.md) records the
implemented changes and the disposition of every named exploratory candidate.
The weekly runner now supports the pinned execution-profile interface. There is
no installed weekly schedule; the latest historical selection expired. A new
real-account trial still needs a fresh comparison/occurrence, current pins,
capacity evidence or a new authorized override, and its writer cgroup.
Historical profiles and overrides remain frozen.
The owner explicitly chose on 2026-10-28 to keep that trial unlaunched; no new
provider trial or weekly schedule was started.

The private release plan, preimages, logs and native probe evidence are under
`~/.local/share/cairn-reviews/deploy-followups-agent683/`. The prior binary is
`release/before/8`, with its original mode recorded by `release/plan.json`.
Source integration, service deployment, native host adoption, and measured
usefulness remain separate claims. No provider-quality, token-saving, recovery-
effectiveness or full-design acceptance claim follows from this release.
