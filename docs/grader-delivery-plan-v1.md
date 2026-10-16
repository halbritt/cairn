# Offline grader delivery plan v1

`scripts/trial_grader_delivery_plan.py` binds a frozen blind draft to the existing
grading join and two declared grader sessions. It creates private preparation
artifacts. It does not release evidence, render a grader request, deliver anything,
create submissions, choose changed pairs, or authenticate custody.

The selected source contract is
`GRADER-DELIVERY-PLAN-SOURCE-CONTRACT-v1.md`, 5610 bytes, SHA256
`9491a4917cd7b51f794372f4d353a889975f60156b87413a28e8008936ffb496`.
Root clarification `e11f660a-54aa-4ac2-84d7-39d1d43dd840@1` fixes the two
sessions for the entire occurrence and distinguishes complete-object join hashes
from weekly receipt inner hashes. Root owns integration and operational selection.

## Entry points and trust boundary

```python
receipt = build_plan(selection_pin, absent_output, now=utc_datetime)
```

Only this source API prepares files. `deliver(*args, **kwargs)` raises
`grader_delivery_unavailable`; `main(argv)` and the module CLI print that fixed
refusal and return 2 without inspecting arguments or reading private inputs.
There is no renderer, transport, adapter factory, credential access, native call,
model call, account admission, or implicit session creation.

Inputs and output parent must be private, trusted, and quiescent throughout the
call. Files are read through the existing no-follow regular-file reader. Paths
are absolute and canonical; symlinks, output overlaps, stale selected pins, and
an existing destination are refused. This is not protection against a hostile
concurrent directory writer, and hashes do not authenticate the producer.

The API borrows the caller's pin without mutation, reads its own JSON snapshots,
and returns a fresh receipt. No accepted helper is changed. It reuses the blind
module's canonical, pin, schema, metadata, bound, and declared-measurement helpers
and the join's public `join_results(bundle, [], [])` validation. Those imports are
effect-free. Changes to their private helper contracts require source review.

## Closed inputs

Every `pin` below is exactly `{path, bytes, sha256}`. Bytes is a nonnegative integer
(never boolean), at most the existing 8 MiB per-file bound. SHA256 is lowercase
64-character hexadecimal. Structured JSON rejects duplicate keys, every float
(including exponent overflow), nonfinite values, unsafe integers and invalid UTF-8.
Opaque policy, role, route, workload, renderer and observation bodies are pinned
bytes, never recursively interpreted as pins or authority.

The selection has exactly these fields:

```text
schema: cairn.grader-delivery-selection/1
crosswalk: pin to cairn.blind-crosswalk/2
draft_receipt: pin to cairn.blind-draft-receipt/2
approval: pin to cairn.blind-declared-approval/2
policy: complete cairn.blind-policy/2 tuple
grading_bundle: pin to cairn.grading-join-input/1
sessions: pin to cairn.grader-session-selection/1
pairs: [{pair_id, comparison: pin to exact C(cairn.blind-comparison-draft/1)}]
```

`policy` is the unchanged rubric, changed-pair-definition, blinding-policy,
release-specification and review-budget tuple, each `{version, pin}`. The selected
tuple must equal the frozen subject and receipt. Public policy tokens must match
both embedded samples and the frozen selection. Approval must name the exact
subject, be declared approved, and contain `observed_at <= now < valid_until`.
Its issuer-role pin is checked; its role is not authenticated.

The explicit read graph is: selection; crosswalk; its frozen selection pin;
receipt; approval and issuer-role; five policy bodies; case contracts; both
review controls; grading bundle; session declarations and their control, route,
workload, optional observation and envelope pins; selected comparison files.
No other pin-shaped object is followed.

Frozen task/evidence input and channel observation pins are historical
declarations. Their exact pin/path shape, conflicts, output overlap and declared
byte bounds are checked, but their bodies are **not reopened**. Frozen transforms
must be `exact_copy` or closed scalar JSON projections with explicit nonoverlapping
key paths. Exact-copy input and output digests must match. Projection results are
checked against retained public bytes, not recomputed from original sources.
Observation pins remain declarations; the planner cannot re-establish their
original source binding without the separately qualified release process.

Case contracts preserve required channel order, inventory and representations.
Both declared input totals and retained output totals independently retain the
original task 9500, answer 8192, action trace 32000, artifact/instruction/observation
64 MiB work-product ceilings, 4096 artifact count, 16 MiB patch/index ceiling,
and stricter 8 MiB file bounds. No grader allowance is inferred from actor limits.
Canonical base64 content, decoded length/digest and output declarations must match.

The sessions document is exactly:

```text
schema: cairn.grader-session-selection/1
binding_sha256: canonical_sha(grading_bundle.binding)
observed_at, valid_until: UTC timestamps
exclusions: [{role, agent_id, session_id, execution_id}]
slots: [primary_slot, secondary_slot]
```

Each exclusion role is `actor_engineer`, `reviewer`, `coordinator`, or
`earlier_exposed`. This is the explicit selected exclusion inventory, including
prior-occurrence exposure; an omitted person is not independently discovered.
It must cover the join's excluded agent/session lists and coordinator identity.
Duplicate exclusion rows are refused. Both slots must be distinct by agent,
session and execution and absent from every declared exclusion dimension.
The document must be time-current. All of this is declared correspondence only.

Each slot has exactly:

```text
slot: primary | secondary (in this order)
agent_id, session_id, execution_id: opaque bounded identifiers
freshness: declared_fresh | unknown
observation: opaque pin | null
control, route, workload: opaque pins
envelope: {
  renderer: opaque pin | null,
  unit: bytes | tokens | unknown,
  semantics: full_rendered_request | unknown,
  limit: nonnegative safe integer | null,
  transport, framing, tool_schema: opaque pins | null,
  declared_input_bytes, declared_input_tokens: nonnegative safe integer | null
}
```

There is one primary and one secondary identity for the entire original
request/campaign/occurrence, including all selected pairs. They must match the
unchanged join policy. Per-pair overrides and extra fields are refused. Execution
IDs belong to this private plan; legacy submission rows do not acquire that field.
If operational policy later requires per-pair sessions, admission must stop as
incompatible/unknown pending a separate consumer decision, rather than relabeling.

## Exact identity and hash domains

The supplied grading bundle retains its original 21 cases × 3 seeds × 2 arms,
126 schedule rows, 1900 distractor identity, complete changed/unchanged/undetermined
selection and existing inventory. The planner never manufactures those components.
Every selected draft comparison and join packet row must appear exactly once;
all samples and case contracts must be used. Missing rows, repeated IDs,
conflicting case/seed/run mappings and same-arm pairs are refused. The planner may
prepare only the packets already supplied by the join bundle; it does not prove
that all changed pairs have eligible grades or that the supplied selection is true.

Both sample hashes, ordered sample IDs and complete private
request/request-hash/campaign/occurrence/schedule/case/seed/wording identities are
checked. The source schedule hash must equal the join's complete schedule hash.
Run IDs must match the supplied attempt inventory. A failed attempt may have
retained evidence; this plan does not promote its eligibility.

| Value | Exact domain |
|---|---|
| File pin | SHA256 of the exact file bytes, with explicit byte length |
| Sample hash | SHA256 of blind `C(sample)` |
| `packet_sha256` / PAIR | SHA256 of exact blind `C(comparison)`, including final LF |
| Join `binding_sha256` | Existing `paired.canonical_sha(bundle.binding)`, without LF |
| Join `plan_sha256` / `schedule_sha256` | Existing `canonical_sha` of the **complete** component, including its internal `sha256` field |
| Weekly receipt scalars | Inner `plan['sha256']` / `schedule['sha256']`; not interchangeable with the preceding row |

Blind C is compact sorted UTF-8 JSON, unescaped Unicode, plus LF. Comparisons
must already be exact C bytes. Other structured source files may have different
whitespace; their file pins still bind exact bytes. Inner plan/schedule hashes and
original identities are checked by the unchanged join before whole-object binding.

## Private outputs, budgets and failure

Success creates mode-0700 output directories and exclusive mode-0600 files:

```text
plan.json
receipt.json
controls/primary.bin, controls/secondary.bin
overhead/<slot>-<transport|framing|tool_schema>.bin  (when selected)
comparisons/<pair_id>-primary.json
comparisons/<pair_id>-secondary.json
```

Both comparison files are byte-identical to the selected frozen comparison.
They are offline copies, not a proposed grader transport or base64 carrier.
Controls and selected overhead are copied once per slot and referenced by exact
output pins. The same source bytes may legitimately be selected by multiple
slots/parts; each physical copy is charged. No deduplication credit is applied.

The closed `cairn.grader-delivery-plan/1` document contains `selection_pin`, the
complete `selection`, `binding`, `binding_sha256`, complete `sessions`, `pairs`,
the unchanged draft `declared_grader_fit`, `wire_fit`, `budget_state`,
`qualification`, and the three strong flags below. Each pair contains `pair_id`,
`comparison_id`, `pair_key`, ordered `samples`, ordered private `sources`, its
source `comparison` pin, and two `deliveries`. A delivery contains slot/agent/
session/execution IDs, copied comparison/control/overhead pins, PAIR hash,
`budget`, and `wire_fit`.

Budget fields are `selected_component_bytes`, `overhead_inventory`, `unit`,
`limit`, `declared_input_measurement`, `state`. The component byte sum is the whole
comparison + control + each explicitly selected transport/framing/tool-schema
body, per pair and slot. Null overhead is unknown, not zero proof. All three
selected parts yield only `declared_complete`. A full-envelope byte declaration
smaller than that sum is refused. A selected byte lower bound or explicit full
measurement above its same-unit limit yields `over_budget`; otherwise state is
`unknown`. Supplied renderer pins and token counts remain unsupported declarations.
Wire fit is always `unknown`, even if a declared budget fits. Existing draft
comparison-plus-control fit is retained separately. No byte-to-token conversion
or provider budget arithmetic is introduced.

The receipt schema `cairn.grader-delivery-plan-receipt/1` contains `selection_pin`,
`files` (exact absolute output pins excluding the receipt itself),
`qualification`, `retention_fit`, `wire_fit`, and the strong flags. The stored
receipt is exact C of the returned receipt. `plan.json` is pinned by the receipt;
the output root is therefore bound in both documents without a recursive hash.

Preflight counts every emitted byte: both comparison copies, all control and
overhead copies, private plan, stored receipt and returned receipt. Their sum must
fit the unchanged aggregate 8 MiB retention bound. Existing input files are not
charged again unless copied/embedded. Selected-source per-file bounds remain
independent. This is not a bounded-heap claim. No truncation, cleanup or hidden
retry occurs. Files are only created after complete preflight. Any I/O failure
after destination creation reports `delivery_io_consumed`, preserves partial
files and prevents reuse of that destination. A refusal contains a fixed code,
not private paths or exception text.

`qualification` is always `unqualified`. `release_authority_verified`,
`runtime_admitted`, and `delivery_available` are always false.

## Operational work still required

Before any private operational read, root must select a reviewed release adapter
through trusted out-of-payload host context and establish its real authority.
That boundary must qualify frozen release policy/evidence, independently resolve
and hold fresh occurrence sessions and exclusion history, render and measure all
actual request/control/transport/tool-schema bytes, and obtain account, capacity,
reservation, pacing and debit-before-forward proof. Session registration,
acknowledgement, caller claims and `host_reported` observations do not prove this.

Transport must bind the observed input to the held session, PAIR and controls.
Returned-grade custody must bind original delivery identity, input hash, PAIR,
slot, agent/session/execution and policy/binding, while retaining wait/failure/
usage and private reason/grade pins. Model-echoed IDs and exit zero are insufficient.
Failure, timeout or ambiguous origin remains unknown/consumed; no blind retry or
session replacement. Only a separately reviewed trusted adapter may project real
submissions into the unchanged join. Coordinator dispositions remain separately
bound; the script never resolves a dispute.

Synthetic tests cover draft → plan → separate join inputs, the full 126/63
mapping, hash-domain and session-scope negatives, policy/control changes, malformed
JSON/base64, retained-input bounds without source reopening, envelope uncertainty,
physical copy inflation, exclusive outputs and real file-boundary failures. These
are source consistency results. Actual policy pins remain unselected; full
126-attempt/1900-distractor/two-consecutive-week evaluation, no-provider-loss,
dedicated accounts, reports and self-service remain unproved. Engineer grading
exclusions and capture HOLD remain in force.
