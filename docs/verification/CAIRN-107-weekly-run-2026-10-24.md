# CAIRN-107 first weekly run, 2026-10-24

This is the dated report for the first Codex Luna weekly occurrence. The actor
run finished, but independent grading and CAIRN-107 acceptance did not.

## Retained evidence

The owner-only weekly-run receipt for campaign
`4b51af63-b708-4e7b-9b3f-3289f2f9b1d7`, request
`6c5262e3-3d75-560e-ac9d-a1cf5740d9e4` has SHA-256
`2b4a9a2d3f694d1d67e08661b3aaae763d000b8bb332cc296284495069fb43dd`.
Its pinned `agent/agent.json` has SHA-256
`901d0903b8c37addbffa33649b7ffd41dbc05e4173c861acafc69df18b4254a6`.
Both remain in the private weekly-run directory under
`~/.local/share/cairn/weekly-runs/cairn107/`; this report retains no task
transcripts or private grading crosswalk.

The receipt reports `status: complete`, 126 planned, admitted and recorded
attempts, zero not started, and `accepted: false`. The 21 cases, three seeds and
two arms produced 63 pairs. Each of three distinct Codex subscription accounts
carried 42 attempts. All 126 processes exited zero; the agent summary records
zero harness and provider errors. Its limit assessment marks all 126 attempts
within the selected original limits. The selected model was `gpt-6-luna` at
medium effort.

The existing primary labels report CURRENT 18 correct, 3 mistake and 42
incomplete, versus CAND 17 correct, 3 mistake and 43 incomplete. The declared
selection has one changed and 62 unchanged pairs. These labels are provisional
for task-quality conclusions because the independent grading gate did not pass.

## Capacity and grading

The three account preflights observed subscription utilization percentages and
ordinary usage eligibility, but no absolute remaining allowance. The owner
authorized this one 126-attempt run despite that missing denominator. The
receipt therefore records `capacity_unverified`, `runtime_admission: false` and
`accepted: false`. Zero observed provider-limit failures in this run does not
establish capacity for a later run.

On 2026-10-25 the owner selected Cairn principal `local-uid:1000` for grader
disagreements. The one changed pair was sent blind to two separate Luna session
inboxes under the refreshed delivery plan. Both deliveries were handled once,
but the collector retained both responses as invalid: neither supplied the
required `verdicts` object keyed by sample ID. Coverage is two dispatched, zero
valid grades, zero pending; agreement has denominator zero. There was no
determinate disagreement to route. The one-shot journal preserves both results
without replay or reassignment. The future-run request format was clarified in
`99a0ecd`; that change does not repair this occurrence.

CAIRN-107 remains open. Its remaining acceptance evidence includes valid blind
second-grader coverage and agreement for changed pairs, another consecutive
complete weekly run without provider-limit losses, a sound whole-run capacity
precheck, and a branch-request path that needs no manual setup. See
[grader delivery](../grader-delivery-v1.md) for the one-shot response contract
and [the measured plan](../plans/token-efficiency-2026-10-23.md) for the broader
evaluation gate.
