# CAIRN-227 one-workflow dry run, 2026-10-24

`scripts/token_efficiency_single_workflow.py` provides a provider-free path for
the `baseline.single_notice` fixture only. It verifies the existing 22-case
fixture and private controller export, then pins the selected `~/.codex` home,
GPT-6 Luna at low effort, the current repository revision, Codex client, and
installed Cairn CLI/API and effective hook/inbox files. Repository HEAD and the
installed API revision are recorded independently. The selected account's
identity is not exported.

The `freeze` command reads the Codex app-server `account/rateLimits/read`
status twice. It records one owner-only caller status document for initial
admission and a separate document bound to the workflow and recipient stage.
Both are percentage utilization snapshots, not absolute remaining capacity or
provider attestation. API-key environment variables are removed from the Codex
child process, and custom provider configuration refuses the check. This command
does not send a task prompt, start inference, change hooks, or activate a
candidate. Its report keeps `provider_task_started=false`,
`live_admission_authorized=false`, `independent_task_acceptance=unassessed`,
`full_panel_status=incomplete`, and `release_decision=defer`.

The `join` command checks a later observation against one exact event/workload,
recipient attempt, native turn, model/effort, four deployment pins, response
references, committed settlement, and a fresh stage-specific capacity sample.
It also fails closed unless native start/end timestamps and complete usage
record counts are present, and the observed native-turn elapsed time, reported
input total, and reported output total stay within the frozen manifest ceilings.
The input total already includes cached input, and the output total already
includes its reasoning-token subset, so neither breakdown is added again. This
post-turn check validates a reported join; it does not launch, interrupt, or
provider-side cap a workflow, and it does not grade the task. The frozen
acceptance-check hash is a rubric reference, not an independent grade.

The tool rejects all non-baseline arms and all cases except `single_notice`,
including `concurrent_receivers` and `unchanged_capture`. It does not bypass or
change `scripts/token_efficiency_gate.py`; CAIRN-227 still requires the full
22-case/four-arm ledger, exact native joins, independent task grades, remaining
failure and latency evidence, effective candidate deployment pins, and an
exercised rollback before release review. No provider model task was run for
this implementation.

## Provider-free native fixture bridge, 2026-10-25

`check_agent_coordination.py` now sends the full-inbox fixture's returned report
through `provider_free_fixture_refusal` in
`token_efficiency_single_workflow.py`. The bridge binds the report to the
`baseline.single_notice.recipient` identity from a fixture/controller manifest.
It accepts only the local Responses stub's completed or stalled fixture shapes.
For completion, it requires the report's event, delivery, attempt, native-turn,
and source references plus the one-event/one-delivery/one-attempt assertion.
For a stall, it requires the retained lease, one attempt, and no settlement or
retry after cleanup.
Its receipt marks fixture transport facts separately from actor decision and
provider usage, records `freeze_status=not_frozen` and
`capacity_status=not_sampled`, and sets `join_eligible=false`. The existing
native join still requires `state=observed`, so this refusal cannot become a
provider observation or task grade.

The manifest used in this disposable fixture path is an identity manifest, not
the owner-only live freeze receipt. This path creates no account-capacity sample
and authorizes no live task. The env-gated integration call is wired to the
disposable API and PostgreSQL runner; it was not exercised here because
`CAIRN_CODEX_BINARY`, `CAIRN_CODEX_ENTRY`, and `CAIRN_CODEX_ROOT` were unset.
The focused Python tests cover the identity binding, completion and stall
refusals, rejection of provider activity and the live join's refusal of a
refusal-shaped observation. `make check` passed.

No reusable live native-actor launcher is established by this slice. The
existing Codex weekly task evaluator runs `codex exec` under its external bwrap
launcher and captures JSONL usage, but its run path does not create a Cairn
single-notice delivery or join that delivery's attempt and native turn to the
same response usage. The full-inbox fixture does create and settle a disposable
notice, but uses scripted Responses. A prior direct app-server experiment with
the external bwrap launcher did not dispatch model-facing `functions.exec`.
Codex 0.164 also exposes an app-server `--code-mode-host` endpoint and packages
`codex-code-mode-host`; this separate-host boundary is a candidate, not yet
verified as an actor tool route or as part of the one-workflow delivery/attempt/
turn/usage join. It needs a provider-free dispatch and isolation proof, then an
integration proof with the disposable notice and owned-process timeout before
live admission can be considered. A root-reviewed one-run admission packet,
exact selected account and fresh capacity at freeze and launch are also absent.
No freeze, capacity status read, selected auth mount, provider task, production
database or installed setting was used for this change.
