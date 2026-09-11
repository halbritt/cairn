# Use and outcome observations

`cairn use-report [--record UUID] [--limit N] [--offset N] REPO` returns one
row per record/version/receipt exposure. Each
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

`expansion_observed` separately reports whether the service retained an
instrumented expansion event for that exact record/version/receipt. A body or
supporting-evidence expansion qualifies, including a byte excerpt. Later
expansion testimony or a higher-ranked citation does not hide that observation.
The existing `usage`, `usage_witness` and `usage_method` still describe the
selected usage event; citations remain testimony.

For example, `usage: "cited"`, `usage_witness: "testimony"` and
`expansion_observed: true` mean that a citation was reported and the service
observed an expansion. They do not prove successful response delivery,
comprehension or task benefit. False means no such instrumented event is retained;
it does not prove the agent never accessed the content. Coverage remains separate.
An older server omits this field, which must not be interpreted as false. Update
the API for authenticated reports, or the CLI for direct-store reports; no
database migration is needed. [Verification](verification/expansion-observation-2026-09-10.md).

`core.UseReport` accepts `repo`, optional `record_id`, `limit` (1–200) and `offset`.
Optional `task_id` and `run_id` narrow the retained receipt scope.
The report includes pagination metadata. It is an observational join, not a causal
benefit score or a completed recurrence evaluation. Exposures never increase rank.

## Page through use history

The trusted CLI defaults to 100 rows, ordered oldest first. Use `--limit` for
1–200 rows, and pass the returned `next_offset` when `more` is true:

```sh
cairn use-report --limit 100 /path/to/repo
cairn use-report --limit 100 --offset 100 /path/to/repo
cairn use-report --record RECORD_UUID --limit 50 /path/to/repo
```

Replace the example offset with the response's value. Keep the same repository
and record filter across pages. `--record` includes all retained versions of that
record; it does not select only its current version. Flags precede the repository.
Stop when `more` is false. Invalid limits, negative offsets and malformed record
UUIDs return `INVALID_REQUEST`; a valid filter with no exposures returns no rows.

Each page reads current state, not a retained snapshot. Concurrent changes can
shift positions, so a multi-page review is not an atomic historical export.
Paging does not manufacture missing observations or recover pruned data.
Use `assessments` on the appropriate receipt to inspect the reasons and evidence
behind an outcome; aggregate rows do not contain that narrative.

The existing authenticated `agent use-report` accepts JSON containing `repo`,
optional `record_id`, `task_id` and `run_id`, plus `limit` and `offset`. This protected report requires a
provisioned local profile; hosted profiles remain denied. CLI pagination adds
no permission or new exposure of private data. Its [verification](verification/use-history-cli-2026-09-09.md)
uses a disposable history beyond the default page.

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
local-profile-only `run-report` agent endpoint accept `repo`, `limit`, `offset`,
and optional `task_id` and `run_id`.
No raw command, task, query, output or evidence body is added to this report.

## Follow one task across runs

Both reports accept exact task and run filters, applied before pagination:

```sh
cairn run-report --task TASK_ID /path/to/repo
cairn use-report --task TASK_ID /path/to/repo
cairn use-report --task TASK_ID --run RUN_ID --record RECORD_UUID /path/to/repo
```

Replace the placeholders with retained scope labels and a record UUID. Task-only
selection includes matching runs across the repository; adding `--run` narrows
that task. A run filter can also be used alone, across tasks. Neither label is
an independently verified execution identity. Strings match exactly, including
case, spaces and literal `*`; omitted or empty filters leave that dimension
unrestricted. Record and policy filters remain conjunctive with scope filters.

Keep the same filters on subsequent pages; offsets count matching rows. A missing
match returns an empty page. Linked retrievals keep their source receipt and
usage while joining their host outcome; association already requires equal
repository/task/run scope. Run reports retain zero-memory executions and exclude
pure retrievals. Read assessment history for qualitative reasons and corrections;
scope filtering does not infer benefit or compare task quality.

Authenticated JSON requests use `task_id` and `run_id` on the existing protected
report endpoints. Update the API before sending these fields to an older server;
no database migration is required. Hosted profiles remain denied. The local
operator CLI requires the updated executable.

The [reviewed recurrence report check](verification/run-report-2026-09-08.json)
reads the original M/N/O trial stores without changing their reports or
assessments. It returns exactly one row for each run: zero exposures and rejected
task outcome for M, zero exposures and unknown task outcome for N, and one
exposure and unknown task outcome for O. All retain assessment version 2.

The initial run-report implementation at clean commit
`9c65d324c105d4bb4a0c4b4db53b6881300f1057` was installed locally after
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

## Command and delivery digests

`run-report` already includes `command_sha256` from the service-observed run
binding. The generic runner computes SHA-256 over Go's JSON encoding of the
supplied command argument vector, before attaching memory/task input. It is not
a hash of a shell-joined command string. Argument boundaries matter: one argument
`"two words"` differs from two arguments `"two", "words"`. Reproducing the digest
requires the same JSON encoding, including its escaping behavior.
Go's JSON encoder replaces invalid UTF-8 bytes with the Unicode replacement rune,
so this is not a byte-exact fingerprint of arbitrary Unix argument bytes.

The related observations identify different parts of a run:

| Observation | Meaning |
| --- | --- |
| `command_sha256` in the binding/report | JSON representation of the supplied argument vector, before carrier input is attached. |
| Delivery `carrier` and `rendered_sha256` | How the combined rendered memory and transient task text were supplied, and that combined input's byte digest. With `argv`, the runner appends this input as the last argument after computing the command digest. |
| Outcome `stdout_sha256` / `stderr_sha256` | Digests of the observed output streams; their raw bytes are not automatically stored in the database. |
| Assessment `error_signature_sha256`, method, reason and evidence IDs | Explicitly supplied assessment evidence, separate from process observations and retained across corrections. |

Equal command digests can accompany different prompts, memory or carriers. They
do not establish an identical working directory, environment, executable contents
or model behavior. A binding is written before the launch claim, so its presence
alone does not prove that a process started. Use the process/delivery observations
and the task context needed for the comparison; do not treat a digest or exit zero
as task acceptance. A qualitative value judgment need not reduce to these fields.

A [bounded check](verification/run-evidence-audit-2026-09-09.json) ran four
`/bin/true` processes in a disposable store. Changing the prompt or carrier left
the command digest unchanged; splitting an argument changed it. Delivery digests
matched the actual combined input, and all four task outcomes remained unknown.
Argument/prompt canaries were absent from the database dump and saved context
files. This verifies those inputs and paths, not every capture route or physical
erasure. Existing [historical run rows](verification/run-report-2026-09-08.json)
already included command digests before this audit.

The generic runner now supports [explicit file selections](run-artifact-evidence.md)
with `--artifact LABEL=PATH`. It captures an observer-generated manifest of file
sizes and digests after the process outcome, using existing evidence storage.
Default workspace capture remains off. A selected saved diff can be fingerprinted
like any file; Cairn does not generate a diff or infer which files the task produced.
Assessments can reference the evidence ID without treating the fingerprint as
correctness or memory-benefit proof. The earlier command-digest audit itself
required no new runtime fields or storage.

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

Authenticated `agent assess-run` also checks the receipt's repository and original
destination against the current profile before a new write or an exact retry.
If that principal's repository or destination has changed, the old receipt
returns `AUTHORITY_DENIED`; retrying cannot recover its cached assessment through
the new binding. Earlier assessments remain retained. The direct-store
`AssessRun` contract is unchanged; the API uses `AssessRunForDestination`.
See the [binding repair](verification/assessment-binding-2026-09-10.md).

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
observer and version history through the direct store interface. With an existing
authenticated profile, the same history is available without database access:

```sh
cairn agent --socket /path/to/api.sock --token-file /path/to/profile.token \
  assessments < assessment-history-request.json
```

The request file contains `{"receipt_id":"RECEIPT_UUID"}`, replacing the placeholder
with an actual owned receipt. `POST /v1/assessments` calls
`Store.AssessmentHistory` with the authenticated destination. Both agent and
observer profiles can read their own history; role and witness are preserved.
Another caller, repository or receipt destination returns `AUTHORITY_DENIED`.
An invalid UUID returns `INVALID_REQUEST`; an absent receipt returns `NOT_FOUND`.
An owned receipt without assessments returns `[]`. Reads do not append a version
or consume expansion credits, and they return evidence IDs without evidence bodies.
The existing 1,000-version history bound and client response-size limit apply;
there is no history pagination. The direct `Store.Assessments` contract is unchanged.

The aggregate `run-report` and `use-report` rows
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

The [cumulative maintenance case](verification/cumulative-maintenance-value-2026-09-09.md)
follows successive related changes and includes a correction caused by earlier
maintenance. It also distinguishes supplied retrieval scopes from independent
model sessions and counts maintenance/review work among the costs. Related reads
and notes should not be presented as independent replications of value.

### Record a qualitative review

Use this when you have an existing agent profile and a retrieval receipt owned by
that profile, in its repository and destination. A retrieval linked to a host run
does not grant access to the host's assessment. This example records the agent's
judgment as testimony; it does not establish human acceptance or measured benefit.
It requires the Cairn CLI, Python 3 and a POSIX shell.

Set these three variables to your existing socket, token file and owned receipt.
Keep the shell open for the following steps. The private directory holds the
selected review and saved request, not a transcript.

```sh
set -eu
: "${CAIRN_REVIEW_SOCKET:?Set the existing API socket path}"
: "${CAIRN_REVIEW_TOKEN:?Set the existing agent token file path}"
: "${CAIRN_REVIEW_RECEIPT:?Set an owned retrieval receipt UUID}"
umask 077
review_dir=$(mktemp -d)
cairn agent --socket "$CAIRN_REVIEW_SOCKET" --token-file "$CAIRN_REVIEW_TOKEN" \
  assessments > "$review_dir/history.json" <<EOF
{"receipt_id":"$CAIRN_REVIEW_RECEIPT"}
EOF
python3 -m json.tool "$review_dir/history.json"
```

Read the complete returned history before continuing. Create
`$review_dir/reason.txt` with your selected account: what you observed, how memory
may have helped, alternative explanations, costs and uncertainty. Use actual
observations, and include source pointers where useful; do not copy an example
judgment as your own. The trimmed reason must contain 8–4,000 characters.

For example, a review might distinguish recalling a previously rejected approach
from proving that the recall saved work. Name the earlier decision, describe the
current choice, and say whether the prompt or current source already supplied
the same guidance. A multi-turn contribution can remain plausible while task
acceptance and net benefit are unknown.

Prepare one request from the history you reviewed. The example leaves acceptance
and failure classification unknown, uses a named self-review method and attaches
no evidence objects. Source pointers in the reason are narrative, not captured
`evidence_ids`. Use the existing selected-evidence workflow when you have evidence
to attach; non-unknown task outcomes require it.

```sh
python3 - "$CAIRN_REVIEW_RECEIPT" "$review_dir" <<'PY'
import json
from pathlib import Path
import sys
import uuid

receipt = str(uuid.UUID(sys.argv[1]))
directory = Path(sys.argv[2])
history = json.loads((directory / "history.json").read_text(encoding="utf-8"))
if not history["ok"]:
    raise SystemExit("Read assessment history successfully before writing")
reason = (directory / "reason.txt").read_text(encoding="utf-8").strip()
if not 8 <= len(reason) <= 4000:
    raise SystemExit("Choose a reason containing 8-4000 characters")
request = dict(
    request_id=str(uuid.uuid4()), receipt_id=receipt,
    expected_version=history["data"][-1]["version"] if history["data"] else 0,
    task_outcome="unknown", failure_domain="unknown", failure_kind="",
    method="qualitative-self-review/1", evidence_ids=[], reason=reason,
)
with (directory / "assessment-request.json").open("x", encoding="utf-8") as output:
    json.dump(request, output, ensure_ascii=False)
    output.write("\n")
PY
cairn agent --socket "$CAIRN_REVIEW_SOCKET" --token-file "$CAIRN_REVIEW_TOKEN" \
  assess-run < "$review_dir/assessment-request.json"
cairn agent --socket "$CAIRN_REVIEW_SOCKET" --token-file "$CAIRN_REVIEW_TOKEN" \
  assessments <<EOF
{"receipt_id":"$CAIRN_REVIEW_RECEIPT"}
EOF
```

Check the returned version, reason, method, evidence IDs, observer and
`witness: "testimony"`. If a connection fails after submission, preserve and
resubmit the exact saved request with `assess-run`; do not generate a new request
ID merely because its response was lost. An exact successful retry returns the
same assessment version. `VERSION_CONFLICT` means another assessment changed the
history: read it again and decide whether a further assessment is warranted.
Prepare a new request only after that review, preserving the earlier request;
do not blindly replace `expected_version` or overwrite the retained account.

## Completed tasks with open delegates

An authenticated observer calls `core.ObserveTask` or `/v1/task-state` with an exact
scope, expected version, state (`completed`, `cancelled`, `reopened`) and method.
The docket shows `OPEN_DELEGATE_AFTER_TASK` for that observer's still-open attempts
in the task. A late terminal observation clears the finding; reopening the task
also removes it from this closed-task query. `UNFINISHED_RUN` remains a separate
hint about wrapper outcome recovery. The wrapper never infers whole-task closure
from process exit.

## Native review tools

MCP and the native OpenCode adapter expose `cairn_assessments` and `cairn_assess`.
Use `cairn_assessments` with a known `receipt_id` to read its retained reviews in
ascending version order. Empty history returns `[]`. Review every returned
reason before selecting the latest version as `expected_version`; use 0 for an
empty history. A retrieval receipt belongs to its agent profile. Linking it to
a host run does not give that agent access to the host's assessment.

`cairn_assess` accepts the same fields as the authenticated `assess-run` request
in the [worked example](#record-a-qualitative-review): choose a UUID before
writing, supply the receipt and reviewed version, and name the review method.
For uncertain task acceptance, `task_outcome: "unknown"`,
`failure_domain: "unknown"`, `failure_kind: ""` and `evidence_ids: []` permit a
qualitative account. Describe actual observations, competing explanations, costs
and uncertainty in `reason` (8–4000 trimmed characters). Unknown acceptance does
not rule out real memory value. Other outcome labels require selected evidence.
The API validates outcome/domain combinations and binds both reads and writes
to the authenticated profile's owner, repository and receipt destination.

The write response contains `receipt_id`, `version`, `request_id`, `witness` and
`observer`, without echoing the reason. Read history afterward to verify the
retained narrative and evidence IDs. Agent reviews remain testimony, including
when they cite evidence; these tools do not confer host or human acceptance.
They do not capture evidence or change retrieval ranking.

Retry an uncertain write using the exact saved request. Even after a later
revision, that retry returns its original result. Changed intent with the same
UUID gives `IDEMPOTENCY_CONFLICT`; a new UUID with a stale version gives
`VERSION_CONFLICT`. Read and reconcile history before another revision. A
connection or response-budget failure can occur after commitment.

History returns evidence IDs without evidence bodies, refuses more than 1000
versions, and has no pagination. Each tool's configured output budget applies
without silent truncation; large histories can require more room. OpenCode's
existing CLI response limit also applies. These are per-call limits, not
whole-task accounting.

Upgrade the MCP executable or reinstall the matching OpenCode adapter, then
start a fresh harness session. The API must include the
[assessment binding repair](verification/assessment-binding-2026-09-10.md);
no database migration is needed. Explicit OpenCode allowlists need
`cairn_assessments` and `cairn_assess` permissions. With OpenCode's MCP connection,
the names carry its usual `cairn_cairn_` prefix. Installation does not change
permissions, and memory-only startup allowlists remain search/pull only.

[Interface checks](verification/native-assessments-2026-09-10.md) cover the
workflow and refusals. They establish interface behavior; downstream review
quality and task benefit remain to be observed in actual work.
