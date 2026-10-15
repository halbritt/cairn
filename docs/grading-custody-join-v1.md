# Offline grading custody join

`scripts/trial_grading_custody.py` adds a pure join for the original weekly
evaluation's selected control artifacts. It retains two declared grades and
coordinator dispositions without replacing deterministic outcomes. The separate
legacy `apply_adjudications` and `summarise_agent` functions are unchanged.

This is the first synthetic implementation stage. It checks declared input
consistency. It does not authenticate sessions, prove grader freshness or
blinding, construct packets, resolve disputes, or admit a campaign. Every report
has `custody: "unknown"`, `admission: false`, and
`actual_campaign_success: false`. There is no caller flag, callback, signature,
UUID, or hash that can turn those fields into verified custody in this version.

## Interface and placement

```python
from trial_grading_custody import join_results

report = join_results(bundle, submissions, dispositions)
public_counts = report['aggregate']
```

The three arguments are borrowed JSON values. The function snapshots them and
returns an owned graph; callers must not mutate inputs concurrently during the
snapshot. It does no file, process, network, clock, database, or provider work.
There is no CLI or persistent ledger. The integration owner must retain the
exact inputs, source pin, and serialized report through the existing artifact
custody mechanism. Returned dictionaries are mutable; the join does not provide
durable append-only storage or cross-process concurrency guarantees.

The intended seam is after owner-selected attempt evidence export. Consume the
already prepared request, plan, and schedule identities and retain the report
beside existing results. Do not invoke `cmd_regrade`, load original trial bodies,
or substitute this join for request preparation. No existing call site uses the
new module yet. The imported `canonical_sha`, `need`, `shape`, `Refusal`, and
`original_plan` helpers have effect-free imports at the candidate's base. The
join does not call their file readers or capacity/spend functions.

## Input contract

All join-owned objects have exactly the fields below; additional fields refuse.
Identifiers and verdict labels are ASCII tokens matching
`[A-Za-z0-9][A-Za-z0-9_.-]{0,95}`. Hashes are lowercase SHA-256 hex strings.
Tokens identify declarations and are not authenticated principals.
`canonical_sha` is the repository's sorted-key, compact, UTF-8 JSON hash with
nonfinite numbers forbidden. The argument snapshot is limited to 8 MiB of
encoded JSON. This is an input limit, not hard process-memory containment.
The caller owns decoding, duplicate-JSON-key refusal, and source-file custody.

`bundle` has `schema: "cairn.grading-join-input/1"` and these fields:

| Field | Meaning |
| --- | --- |
| `binding` | `request_id`, `campaign_id`, `occurrence_id`, `request_sha256`, `rubric_sha256`, `changed_pair_definition_sha256`, `blinding_policy_sha256`, plus `plan_sha256`, `schedule_sha256`, `inventory_sha256`, `selection_sha256`, `packets_sha256`, `policy_sha256`. |
| `plan` | Existing original weekly plan, validated by `original_plan`: 21 unique case IDs, three unique integer seeds, CURRENT/CAND, 126 attempts. Workload arithmetic is not repeated. |
| `schedule` | Existing `cairn.weekly-schedule/1` object, including its internal `sha256`, source/execution metadata, task wording, 1900-distractor declaration, and runs. |
| `inventory` | At most 126 selected attempt evidence declarations; missing attempts are omitted from this input and materialized in the report. |
| `selection` | Exactly 63 rows, one per expected case/seed pair, including every unchanged or undetermined pair. |
| `packets` | At most one existing packet declaration per changed pair; missing packets remain visible. No packet bytes are accepted here. |
| `policy` | Explicit comparison vocabulary, expected grader sessions, exclusions, and coordinator declaration. |

The six component hashes in `binding` cover each **whole component**, including
any internal `sha256`. In contrast, the plan and schedule's internal `sha256`
covers that object without its own `sha256` field. These are different digests.
The schedule must bind the plan's internal digest. Its runs must contain every
exact `(case, seed, arm)` once, with positions 0 and 1 once per pair. Counts alone
cannot establish coverage. Source/execution metadata is committed by the whole
schedule hash; this module does not reopen or authenticate it. A consistently
substituted synthetic source remains only a declaration, never proof that the
actual original cases or distractors were used.

An inventory row is `{case, seed, arm, run_id, state, outcome,
evidence_sha256}`. `state` is `completed` or `failed`; evidence hashes are
mandatory for completed attempts and may be null for failures. Run IDs and
attempt keys cannot repeat. The original deterministic `outcome` is an opaque
token retained unchanged. It is never substituted for an absent grade. Missing
and failed attempts cannot contribute to declared agreement.

A selection row is `{case, seed, state}` with `changed`, `unchanged`, or
`undetermined`. Missing, duplicate, or foreign pairs refuse the join. The join
does not compute what changed: the owner supplies a complete selection under
the pinned definition. `undetermined` makes completeness of the changed set
unknown even when all known changed pairs have grades.

A packet declaration is `{case, seed, pair_id, packet_sha256, samples}`.
`pair_id` is unique; `samples` contains two opaque IDs unique across packets.
These IDs and hashes are declarations about an existing packet. Correspondence
between samples, actual evidence, and released bytes remains unverified.

`policy` has exactly:

- `comparison: "exact-sample-verdicts/1"` and `verdict_labels`: 1–32 unique
  opaque tokens from the pinned rubric. The comparison means equality of the
  two sample-to-verdict maps. The rubric owner must select this representation;
  it is not an inferred definition of changed pairs or grading correctness.
- `graders: {primary: {agent_id, session_id}, secondary: {agent_id, session_id}}`.
  This stage expects one declared session per grader slot for the occurrence.
  Both agents and sessions must differ. Models need not differ.
- `excluded_agents` and `excluded_sessions`: unique lists of at most 1024
  tokens each. The caller must supply engineer/author and other required
  exclusions. The module cannot establish their completeness.
- `coordinator: {agent_id, session_id, delegation_sha256}`. Graders must differ
  from the coordinator too. Delegation may be null when no disposition is
  supplied; a disposition requires a matching nonnull digest. Matching does
  not authenticate delegation or prove its scope.

## Grades and coordinator dispositions

Each of at most 1024 submissions has exactly `submission_id`, `binding_sha256`,
`pair_id`, `packet_sha256`, `rubric_sha256`, `slot`, `agent_id`, `session_id`,
`state`, `verdicts`, and `reason_sha256`. The binding hash covers the whole
`binding` object. Identity, packet, rubric, and grader-slot references must
match. `state` is `determinate` or `undetermined`. Determinate `verdicts` maps
exactly the packet's two sample IDs to permitted labels; undetermined verdicts
are null. Reasons are referenced by hash, not included as text.

The report retains submissions by their computed whole-submission digest.
Identical replay is idempotent. Different submissions in the same slot remain
an explicit conflict, including different bodies using one submission ID.
Reusing a submission ID across pairs or slots refuses. Input order never
chooses a winner; corrections/supersession are not implemented.

Each of at most 256 coordinator dispositions has exactly `disposition_id`,
`binding_sha256`, `pair_id`, `grade_sha256`, `agent_id`, `session_id`,
`delegation_sha256`, `state`, `verdicts`, and `reason_sha256`. `grade_sha256`
is a `{primary, secondary}` map binding the exact two retained submissions.
Only a pair with a determinate declared disagreement can receive a disposition.
The same verdict shape applies as for submissions. Foreign authority/grade
references refuse; different dispositions remain conflicts; identical replays
deduplicate; a disposition ID cannot cross pairs.

The report keeps the original grades, deterministic outcomes, raw disagreement,
and disposition separately. `disposition_state` is `missing`, `declared`, or
`conflict`; `resolution` stays `unresolved` for disagreements. The coordinator
has delegated dispute-decision authority, but this join has no concrete trust
adapter for authenticating its exercise. A script never resolves the dispute.

## Reading the report

`pairs` retains all 63 expected pairs and both attempt slots. Grade states are
`missing`, `conflict`, `undetermined`, or `determinate`. Pair issues preserve
multiple simultaneous problems, including missing attempts and missing grades.
The report also retains the binding, bundle digest, and deduplicated submission
and disposition lists. Treat this detailed report as private control metadata.
Only `aggregate` is designed as a body-free public counts projection; it contains
no caller-supplied identifiers, verdict labels, reasons, case bodies, or outputs.

`declared_raw_agreement` reports numerator, denominator, and rate:

- Denominator: changed pairs with both completed evidence declarations, a
  packet, and exactly one determinate submission per declared grader slot.
- Numerator: those pairs whose two sample-to-verdict maps are equal.
- Zero denominator: `rate: null`, including zero changed pairs or two matching
  undetermined grades. Dispositions never alter this statistic.

`changed_pair_coverage.denominator` is the number of explicitly changed pairs.
It also reports packet coverage, each grader's presence, unconflicted dual
submissions, and determinate dual eligibility. Unconflicted dual counts may
include undetermined grades or missing attempts; determinate dual cannot.
`changed_pair_issues` counts overlap and must not be summed as disjoint buckets.
Selection counts always sum to 63; attempt counts always expose the expected
126. `selection_completeness` is `undetermined` if any pair's selection is
undetermined, otherwise `declared`.

`verified_raw_agreement` remains 0/0 with a null rate. No verified-custody
denominator exists in this stage. Declared 100% agreement, complete counts, or
a coordinator declaration cannot establish actual weekly campaign success.

## Validation and remaining work

Run only the focused synthetic checks for this addition:

```sh
python3 -B -m unittest discover -s scripts -p test_trial_grading_custody.py
git diff --check
```

The tests cover duplicate-plus-missing schedule substitution, complete selection,
failed/missing evidence, conflicting/replayed submissions, binding mismatches,
declared exclusion failures, undefined agreement, coordinator binding/conflicts,
input ownership, public-count privacy, and denied file/process/network effects.
They use generated data, not original case bodies, grading outputs, or oracles.

Remaining full-goal work includes reviewed packet construction and sample mapping;
fresh authenticated grader sessions excluding engineers; actual payload, context,
tool and mount blinding evidence; trusted coordinator-authority verification;
and root-owned report/runtime integration. The original requirement remains
21 × 3 × 2 = 126 attempts, 63 pairs, and 1900 distractors **per campaign**, with
two actual consecutive weekly campaigns and dated reports. No runtime, provider,
budget arithmetic, provisioning, or campaign work is implemented by this slice.
