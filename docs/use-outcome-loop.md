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

## Wrapper metadata

`cairn run` records a SHA-256 digest of its command argv before launch. Raw argv is
not retained. `--task-class`, `--binding`, `--capability`, `--revision` and
`--workspace-sha256` supply declared comparison metadata. Binding and capability
are distinct; unknown capability stays `unknown`. Revision/workspace labels in
this table describe the run and do not yet establish retrieval eligibility.
Run metadata is bound before launch and cannot be replaced under a new request.

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

## Completed tasks with open delegates

An authenticated observer calls `core.ObserveTask` or `/v1/task-state` with an exact
scope, expected version, state (`completed`, `cancelled`, `reopened`) and method.
The docket shows `OPEN_DELEGATE_AFTER_TASK` for that observer's still-open attempts
in the task. A late terminal observation clears the finding; reopening the task
also removes it from this closed-task query. `UNFINISHED_RUN` remains a separate
hint about wrapper outcome recovery. The wrapper never infers whole-task closure
from process exit.
