# Legacy harness output to blind selection

`scripts/trial_legacy_blind_selection.py` converts the legacy harness output
(`cairn.task-eval.agent/1` `agent.json`, its `plan.json` and `runs/<run_id>/`,
written by `trial_task_eval.py agent`) into the `cairn.blind-selection/2`
document that [the blind packet draft](blind-packet-draft-v2.md) reads. The
draft output then feeds the [delivery plan](grader-delivery-plan-v1.md) and
[grader delivery](grader-delivery-v1.md) unchanged. CAIRN-107, assigned by
agent-341.

The tests use synthetic legacy output only. Passing them shows that the
converter matches this contract and composes with the unchanged draft. It is
not evidence that a real campaign was converted, delivered or graded.

## Reconciliation (2026-10-21)

Question: can `cairn.blind-selection/2` and its paired request IDs be bound
from legacy output without weakening a guard?

Verdict: **no, not from legacy output alone. There are two exact
blockers: pair identity and original-attempt content custody.**

**Blocker.** `blind-source/2` keys every sample to `PAIR_KEY`
(`trial_blind_packet_draft.py:14`): `request_id`, `request_sha256`,
`campaign_id`, `occurrence_id`, `schedule_sha256`, plus case, seed and wording,
and to an arm role `CURRENT` or `CAND`. The paired request route
(`trial_paired_request.prepare`) issues those five identifiers, and its launch
still refuses (`trial_paired_request.py:646`, `trial_weekly_request.py:195`).
Legacy `agent.json` has no request identity. Its arms are memory labels such
as `baseline-mem`, which do not say which one is the current build. Deriving
these values from legacy output would make up an identity that no request
issued.

**Resolution.** Pair identity is a binding seam. The caller supplies a
pinned `cairn.legacy-pair-binding/1`:

```text
agent_sha256, plan_sha256      the exact legacy agent.json and plan.json
request_id, request_sha256, campaign_id, occurrence_id, schedule_sha256
arm_roles                      {legacy label: CURRENT | CAND}, both labels
```

With no binding, conversion refuses `legacy_pair_identity_unbound`. A binding
for other output bytes or other arms refuses `legacy_binding_mismatch`. The
receipt records `pair_identity: declared_binding` and keeps
`runtime_admitted`, `release_authority_verified`, `delivery_available` and
`grading_performed` false. The weekly runner (a separate lane) is the
intended issuer of a binding. Until a reviewed issuer exists, the binding is
a declaration only.

**Second blocker: original-attempt content custody.** agent-341's review
showed that length checks do not bind content: a stream answer or task
statement swapped for one of the same length passed them. Legacy output
before this change records no hash of the stream or the prompt. `agent.json`
and `result.json` carry only `answer_chars` and `prompt_bytes`. All existing
legacy runs, including the 2026-09-29 run, therefore refuse
`original_attempt_provenance_unavailable`. Grading release from them has no
custody for the original attempt's content.

**Resolution.** `trial_task_eval.run_agent` now writes the exact stream bytes
and records `stream_sha256` and `prompt_sha256` (the prompt actually sent) in
each legacy attempt record, in both `result.json` and `agent.json`. The
converter requires both hashes on every record. The stream bytes must hash to
`stream_sha256` (`legacy_attempt_content_mismatch`). The supplied task
statement must hash to `prompt_sha256` and match `prompt_bytes`
(`legacy_task_statement_mismatch`). The length check on `answer_chars` is kept
only as a consistency check. These hashes are the harness's own testimony,
written by the same process that wrote the stream. A later substitution is
detected only because the binding pins the `agent.json` hash. They are not
independent custody.

**Declared and observed stay separate.** The receipt has:

- `custody`: `{pair_identity: declared_binding, attempt_content:
  harness_recorded_hash, independent_attempt_custody: false}`
- per attempt, `declared_by_harness` (the recorded stream and prompt hashes)
  and `observed_now` (the hash of the stream bytes this conversion read, and
  the digest of the selected answer)

A hash observed now records only what was selected now. It is not provenance
for the original attempt.

**Indeterminate pairs block completion.** The receipt's
`changed_pair_coverage` is `{state, complete_possible, indeterminate}`. While
any pair is indeterminate, the state is `blocked_indeterminate_pairs` and
`complete_possible` is false. Changed-pair grading of such a run cannot be
reported as complete, because an unclassified pair may hide a change.

Guards, checked against current source:

| Guard | Status |
|---|---|
| Blindness to arm | Kept. Sample IDs are hashes. Comparison samples are sorted by sample ID, not by arm. Run IDs, labels and roles stay in the private selection. The test decodes the draft's public packets and finds no role, label or run ID. Downstream checks (`grader_request_not_blind`) are unchanged. |
| Exact attempt and pair identity | Kept, with a checked map. See below. |
| Changed-pair selection | Implemented as specified: both deterministic outcomes (`correct`, `mistake`, `incomplete`) and unequal. Pairs with `undetermined` or `needs_review` are not selected. The receipt lists them as `indeterminate`, and they block completion (see above). |
| Case and oracle privacy | Kept. From each record the converter reads only identity, outcome, `prompt_bytes`, `answer_chars` and `harness`. It never copies check verdicts, check logs or memory evidence. Task statements, grading instructions and case contracts come from the caller as pins. Only channels the existing contract allows are emitted. |
| Excluded grader identities | Unchanged and enforced downstream. Graders are not selected here. |

### Legacy identity (agent-341's correction)

The compatibility review (`docs/sdk-weekly-compatibility.md`) names two
legacy defects. Their effect on this converter, checked against current
`trial_task_eval.py`:

- **Fixture-ID to record-ID maps.** These maps cover the memory corpus. The
  converter reads no memory record IDs and no delivery evidence, so pair
  identity does not depend on them.
- **Join between setup, commit and snapshot.** Legacy `prepare_workspace`
  does not verify the workspace base. For this reason the converter emits no
  `artifact_bundle`, `action_trace` or `evaluator_observation`. A contract
  that requires any of them refuses `legacy_channel_unbound`. The
  deterministic outcome that selects a pair rests on that same unverified
  setup. Both arms use the same setup, so it is applied equally to each, but
  it is not verified.
- **Run ID map.** `run_id` is `case.arm.sSEED` (`trial_task_eval.py:1048`).
  This map is not injective when a case or arm contains a dot, so dotted
  names refuse `legacy_attempt_identity_ambiguous`. So do a `run_id` that
  differs from the formula and a duplicate `run_id`. Every record must match
  its retained `runs/<run_id>/result.json` exactly
  (`legacy_attempt_mismatch`). The plan rows and the records must be the same
  set, and each case and seed must have both arms (`legacy_attempt_missing`).
  Admission must be `complete` with nothing unstarted
  (`legacy_run_incomplete`). Every outcome must be a grade, not
  `harness_error`, `provider_error` or `not_regradable`
  (`legacy_attempt_not_graded`).
- **Answer and task binding.** The final answer comes from `stream.jsonl`,
  parsed by the harness's own `parse_stream`, after the stream bytes match
  the recorded `stream_sha256`. The task statement must match the recorded
  `prompt_sha256`. See the second blocker above. Wording must be `task`
  (`legacy_wording_unsupported`).

## Runs with no changed pair

An empty plan or an empty record set refuses `legacy_run_empty`, because
every coverage check would otherwise pass vacuously. The converter keeps
exact coverage of whatever plan it is given. Requiring the full weekly shape
(126 attempts, 63 pairs) is the runner's job.

After every coverage, identity, provenance and binding check has passed, a run
can have zero changed pairs. In that case the converter writes only
`receipt.json`. It produces no blind selection, answers or packets, so the
nonempty packet contract is unchanged. The receipt's `selection` is `null`
and `comparisons` is empty. `changed_pair_grading` then says what grading
still requires:

| Pairs | `state` | `complete` | `deliveries_required` |
|---|---|---|---|
| all determinate and unchanged | `no_grading_needed` | true | 0 |
| all indeterminate | `incomplete_all_pairs_indeterminate` | false | null |
| unchanged and indeterminate, none changed | `blocked_indeterminate_pairs` | false | null |
| changed and indeterminate | `blocked_indeterminate_pairs` | false | 2 per changed pair |
| changed, none indeterminate | `awaiting_grading` | false | 2 per changed pair |

Only the first row says grading is complete, and only because there is
nothing to grade. For a full 126-attempt run with 63 determinate unchanged
pairs, `pairs` is `{total: 63, changed: 0, unchanged: 63, indeterminate: 0}`.

### Downstream join input

The grading join (`trial_grading_custody.join_results`) and
`trial_run_report` should consume these receipt fields:

- `pair_classification`: one `{case, seed, state}` row per pair, with
  `state` already in the join's vocabulary (`changed`, `unchanged`,
  `undetermined`). These are the grading bundle's `selection` rows. In the
  zero-changed case every row is `unchanged`, so the join marks every pair
  `not_selected`. Its `changed_pair_coverage.denominator` is 0 and
  `selection_completeness` is `declared`. The report then has no
  `changed_pairs_not_dual_graded` reason and reports agreement as `null`
  over a 0 denominator.
- `changed_pair_grading`: `state`, `complete` and `deliveries_required`.
  A coordinator plans zero deliveries only when `state` is
  `no_grading_needed`. Any indeterminate pair appears as `undetermined` in
  `pair_classification`, which the join and report already treat as
  `selection_undetermined`, so the run stays incomplete.
- `pairs`, `changed_pair_coverage`, `sources` (the pinned `agent.json`,
  `plan.json` and binding) and the custody flags, unchanged. `agent.json`
  remains the report's attempt input.

This classification is the converter's own. It is not admission, release
authority or a grade, and the receipt's flags stay false.

## Interface

```python
receipt = convert(request_pin, output_dir)   # request_pin = {path, bytes, sha256}
```

```sh
python3 scripts/trial_legacy_blind_selection.py --request REQ.json --request-sha256 HEX --output DIR
```

The request `cairn.legacy-blind-conversion/1` is closed. Its fields are
`agent` and `plan` (pins), `binding` (pin or null), `namespace`, the
`policy`, `policy_ref` and `review_measurement` for the draft, and `cases`,
a list of `{case, contract, task_statement, grading_instructions}` pins.
The output directory holds `selection.json`, `answers/<sample_id>.txt` and a
private `receipt.json`. The receipt's `selection` pin is the draft's
`selection_pin`.

The converter checks each written selection with the unchanged
`trial_blind_packet_draft.approval_subject`. If that check refuses, the
output directory is removed. The output is deterministic for a given
request and output path. A rerun with byte-identical existing output returns
the same receipt. Any other existing content refuses
`legacy_output_conflict` and is left untouched.

## Validation and remaining work

```sh
python3 -B -m unittest test_trial_legacy_blind_selection   # from scripts/
```

Remaining work:

- legacy runs made before `stream_sha256` and `prompt_sha256` were recorded
  cannot be released for grading; new runs are needed
- a reviewed issuer for the pair binding (the weekly runner lane), which
  ties request identity and arm roles to the commits that were actually run
- the owner-held release authority and grader choice for delivery
- artifact, trace and evaluator-observation channels, which need an owned
  workspace setup and an observation contract
- conversion of a real run, then delivery, grading and the agreement report
