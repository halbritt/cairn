# Use and outcome observations

`cairn use-report REPO` returns one row per record/version/receipt exposure. Each
observation stream is reduced before joining, so repeated deliveries or citations
do not multiply task outcomes. Rows retain repository/task/run scope, purpose,
task class, binding and capability identity, process duration/exit, current record
version/lifecycle, usage qualification and the latest task assessment.

Unknown observations remain explicit. Best observed delivery means contact was
observed at least once; it does not assert compliance. Usage follows `cited` >
`expanded` > `behaviorally_implicated` > `delivered_only`. Inferred use requires a
service observer and a method/version label, and is reported as `inferred`.
A delivered record with no usage observation is `delivered_only` only when a
service has explicitly recorded complete usage coverage. Generic H0 wrappers
leave coverage unknown. There is no automatic behavior-inference engine.

`core.UseReport` accepts `repo`, optional `record_id`, `limit` (1–200) and `offset`.
The report includes pagination metadata. It is an observational join, not a causal
benefit score or a completed recurrence evaluation. Exposures never increase rank.

## Inspect runs, including no-memory baselines

`cairn run-report [--limit N] [--offset N] REPO` returns one row per receipt with
a launch claim or an observed process outcome. A run needs no memory exposure to
appear. Pure retrievals and binding/assessment-only receipts are outside this
population. This differs from `report`'s process-outcome denominator and
`use-report`'s record-exposure rows.

Rows include scope, compilation time, binding/capability/currentness pins,
command digest, process state/duration/exit and the latest task assessment's
version, witness, method and failure labels. `launch_claimed` reserves execution;
it does not prove a process started. `outcome_observed` distinguishes a missing
outcome from an explicitly observed unknown outcome. Missing duration and exit
are null, not zero. `exposure_rows` counts retained record/version exposures,
including index pointers; it does not establish delivery, use or benefit.

The default page holds 100 rows; limits are 1–200. Follow `more` and
`next_offset` for additional pages. Each page has its own database snapshot;
concurrent new receipts can change later offset pages. `core.RunReport` and the
local-profile-only `run-report` agent endpoint accept `repo`, `limit`, `offset`.
No raw command, task, query, output or evidence body is added to this report.

The [reviewed recurrence report check](verification/run-report-2026-09-08.json)
reads the original M/N/O trial stores without changing their reports or
assessments. It returns exactly one row for each run: zero exposures and rejected
task outcome for M, zero exposures and unknown task outcome for N, and one
exposure and unknown task outcome for O. All retain assessment version 2.

Clean commit `9c65d324c105d4bb4a0c4b4db53b6881300f1057` is installed locally after
a backup and [passing CI](https://github.com/halbritt/cairn/actions/runs/34189273876).
The installed CLI and authenticated API return identical reports. Operational
record versions retain their previous digest; no migration beyond 020 is needed.

## Wrapper metadata

`cairn run` records a SHA-256 digest of its command argv before launch. Raw argv is
not retained. `--task-class`, `--binding`, `--capability`, `--revision` and
`--workspace-sha256` supply declared comparison metadata. Binding and capability
are distinct; unknown capability stays `unknown`. The same declared tuple is supplied to the compiler for
[currentness matching](currentness-and-replay.md).
Run metadata is bound before launch and cannot be replaced under a new request.
Optional [host attempt linkage](host-attempt-link.md) validates the exact observed
attempt and carries its ID into run reports. It does not infer host completion
from a process result.

## Task assessments and corrections

`cairn assess-run` accepts JSON with `request_id`, `receipt_id`,
`expected_version` (0 initially), `task_outcome`, `failure_domain`, `failure_kind`,
`method`, `evidence_ids` and `reason`. An optional `error_signature_sha256` is a
digest of an explicitly permitted error signature, never an automatic raw log.

Task outcomes: `accepted`, `rejected`, `not_attempted`, `unknown`. Failure domains:
`none`, `binding`, `capability`, `task`, `unknown`. Binding failure kinds: `quota`,
`credentials`, `transport`, `adapter`, `unknown`. Binding failure permits only
`not_attempted` or `unknown`; it cannot establish capability failure. Acceptance
requires failure domain `none`. A task/capability rejection is an explicit
assessment, never derived automatically from process exit.

Every non-unknown assessment requires deliberately captured, resolvable evidence
in the same repository. Assessments retain their observer, witness and method.
They append versions under compare-and-swap and transport idempotency; original
process outcomes and earlier assessments survive corrections. `cairn assessments
RECEIPT_UUID` inspects that history. An assessment does not promote memory or mint
a capability qualification. Current receipt-owner access applies.

`cairn report REPO` counts unknown task outcomes among retained process outcomes.
It uses the latest task assessment when one exists, then falls back to the
process-derived task outcome. A correction back to unknown restores that count.
Compile-only receipts are outside this denominator, and a run needs no memory
exposure to be counted. Other summary counters remain operational observations.

## Completed tasks with open delegates

An authenticated observer calls `core.ObserveTask` or `/v1/task-state` with an exact
scope, expected version, state (`completed`, `cancelled`, `reopened`) and method.
The docket shows `OPEN_DELEGATE_AFTER_TASK` for that observer's still-open attempts
in the task. A late terminal observation clears the finding; reopening the task
also removes it from this closed-task query. `UNFINISHED_RUN` remains a separate
hint about wrapper outcome recovery. The wrapper never infers whole-task closure
from process exit.
