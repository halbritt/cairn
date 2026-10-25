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
