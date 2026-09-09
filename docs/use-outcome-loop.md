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

## Retrieval during an observed run

An agent can fetch memory under its own identity while a separate host observes
the execution. The host explicitly associates each retrieval with its run:

```sh
cairn agent --token-file OBSERVER_TOKEN link-run-retrieval < association.json
```

The JSON request contains `request_id`, `run_receipt_id`,
`retrieval_receipt_id`, `expected_reader` (the authenticated agent principal),
and `method` (a bounded description/version of the host's observation method).
The library entry point is `Store.LinkRunRetrieval`; the Unix API exposes
`POST /v1/link-run-retrieval` and `Client.LinkRunRetrieval`.

The host must own the run receipt and its bound, independently observed attempt.
The run needs a launch claim or process outcome. The retrieval must belong to
the expected reader, match the exact repository/task/run and destination, and
have been created after the host receipt and attempt spawn and before any
recorded terminal or process outcome. These checks constrain a host observation;
matching scope and timestamps alone never create an association. The host must
obtain the exact receipt from its observed tool route. An ordinary agent cannot
assert this observation or acquire access to the host receipt through it.

A host can record the association after the process finishes if the retrieval
was created during that interval. Retry the exact request after an ambiguous
response. One source retrieval can belong to one host run; a new request cannot
move it. Association corrections and unlinking are not implemented. Receipts
that have an execution binding, claim or outcome cannot be linked as retrievals;
a linked retrieval cannot later become another execution.

`use-report` retains the source `receipt_id`, exposure, delivery and usage. Its
optional `run_receipt_id`, `run_link_observer` and `run_link_method` identify the
explicit association. Process outcome, binding and latest assessment come from
that host run. An agent's assessment of its own retrieval does not replace the
host assessment. `run-report` still counts one execution: `linked_retrievals`
counts associated receipts, including empty retrievals, and `exposure_rows`
includes their record/version exposures. Repeated citations do not multiply
exposures. There is no inferred causal benefit or automatic lesson promotion.

The [verification record](verification/run-retrieval-2026-09-08.md) covers the
store boundaries and an observed child making authenticated index/pull calls.

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

## Qualitative and cumulative review

Task acceptance and memory benefit answer different questions. An accepted task
can receive no discernible help from memory; a rejected or unfinished task can
still contain a useful insight prompted by it. The outcome labels above must not
be treated as a complete vocabulary for value.

For an assessment tied to a receipt, `reason` can describe the observed work,
the proposed memory contribution, contrary evidence and costs. `method` identifies
how that judgment was made, and `evidence_ids` references explicitly selected
support. Use `task_outcome: "unknown"` when task acceptance is unknown, including
when there is a plausible qualitative benefit. Unknown acceptance does not mean
zero benefit. The existing failure-domain consistency and receipt-access checks
still apply; a narrative does not grant authority or turn an agent's judgment
into human acceptance.

Inspect `cairn assessments RECEIPT_UUID` for the retained reasons, evidence IDs,
observer and version history. The aggregate `run-report` and `use-report` rows
omit the narrative and evidence IDs. For a linked retrieval, inspect the
assessment on its `run_receipt_id`; that is the assessment those rows join.
Do not infer the full judgment from an aggregate outcome label alone.

For benefit that develops across turns, sessions or tasks, retain a source-linked
case describing the sequence and relevant versions, receipts or artifacts.
Ordinary project documentation can carry such a review without inventing a
single run for the whole sequence or changing its individual task assessments.
Preserve the reviewer's identity, evidence limits, alternatives and negative
observations. The [adapter-repair case](verification/value-case-validation-2026-09-09.md)
demonstrates this format with existing evidence. These reviews do not automatically
produce demand proposals, rank changes or a numeric memory-value score.

## Completed tasks with open delegates

An authenticated observer calls `core.ObserveTask` or `/v1/task-state` with an exact
scope, expected version, state (`completed`, `cancelled`, `reopened`) and method.
The docket shows `OPEN_DELEGATE_AFTER_TASK` for that observer's still-open attempts
in the task. A late terminal observation clears the finding; reopening the task
also removes it from this closed-task query. `UNFINISHED_RUN` remains a separate
hint about wrapper outcome recovery. The wrapper never infers whole-task closure
from process exit.
