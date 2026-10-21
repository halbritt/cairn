# Two-week campaign completion (CAIRN-107)

`scripts/trial_campaign_completion.py` checks two pinned weekly occurrences
against the CAIRN-107 Done-when criteria and returns a typed verdict. It reads
only outputs that already exist:

- the runner profile;
- the runner receipt;
- the occurrence's persisted state snapshot;
- an optional bridge receipt.

It never grades or regrades, never computes agreement, never resolves a
dispute, never promotes an incomplete report, and never claims actual campaign
success. This is source code with synthetic tests. No live campaign, grade or
run exists. With the current repository state the verdict is always
`not_complete`. CAIRN-107 lane D was assigned by agent-341.

```python
verdict = evaluate(input_pin)   # input_pin = {path, bytes, sha256}
```

```sh
python3 -B scripts/trial_campaign_completion.py --input CAMPAIGN.json --input-sha256 HEX
```

The CLI prints the verdict. It exits 0 only for `complete`, 3 for
`not_complete` and 2 for a refusal.

## Input

The input is the closed `cairn.campaign-completion-input/1`:

```json
{"schema": "cairn.campaign-completion-input/1", "campaign_id": "<uuid>",
 "weeks": [{"runner_profile": PIN, "receipt": PIN, "occurrence_snapshot": PIN|null, "bridge_receipt": PIN|null},
           {"...": "the following week"}]}
```

Each `PIN` is `{path, bytes, sha256}`. Exactly two weeks are given, in
order:

- `runner_profile`: the runner's `cairn.weekly-run-profile/1`. Its
  `campaign_id` and `schedule` are the actual ones, not declared copies.
- `receipt`: the slot's `cairn.weekly-run-receipt/1`, with exactly the
  runner's receipt fields.
- `occurrence_snapshot`: `cairn.legacy-occurrence-state-snapshot/1`, written
  by `trial_legacy_occurrence.run_occurrence_snapshot`. See
  [legacy occurrence](legacy-occurrence.md#persisted-state-snapshots).
- `bridge_receipt`: `cairn.candidate-run-bridge-receipt/1`, if the run came
  through the bridge.

## Refusals: changed or inconsistent input

Every pin is checked again. This covers the input, profile, receipt and
snapshot, the receipt's `agent` and `pair_binding`, and the snapshot's
`report`, `join`, `collection` and `routing`. It also covers the bridge
receipt and its selection pin. Any change refuses `campaign_pin_mismatch`.

These also refuse:

| Check | Refusal |
| --- | --- |
| Both profiles and receipts name the input's `campaign_id`. | `campaign_id_mismatch` |
| Both profiles have the same schedule. | `campaign_schedule_changed` |
| `scheduled_at` falls on the schedule's weekday and time, and `expires_at` is `scheduled_at` plus the window. | `campaign_slot_off_schedule` |
| `slot_id` equals the runner's `uuid5(NAMESPACE, campaign_id + "\|" + scheduled_at)`. | `campaign_slot_identity` |
| The second slot starts exactly 7 days after the first. A gap or reversed order fails. | `campaign_weeks_not_consecutive` |
| The two slots are different. | `campaign_weeks_duplicate` |
| The receipt's `attempts` equal the counts that `trial_run_report._agent` reads from the pinned agent document. | `campaign_receipt_agent_mismatch` |
| The pair binding names this slot, campaign, `uuid5(NAMESPACE, "occurrence\|" + slot_id)` and the agent hash. | `campaign_binding_mismatch` |
| An occurrence snapshot exists only for a receipt with a pair binding. | `campaign_occurrence_without_binding` |
| Report and receipt join, as listed below. | `campaign_identity_mismatch` |
| The state's routing disputes equal the pinned routing document's. | `campaign_routing_mismatch` |
| The bridge receipt names exactly this runner receipt, its content, status and slot. | `campaign_bridge_mismatch` |

The report and receipt join checks:

- `report.campaign.request_id == slot_id`;
- `occurrence_id` and `campaign_id` match the binding;
- `report.inputs.agent_sha256` is the canonical hash of the receipt's agent
  document;
- `report.inputs.join_sha256` is the join's hash, and `state.snapshot` is its
  prefix;
- `report.report_sha256` is the report digest;
- `state.report_status == report.status`;
- the state's grading figures equal the report's.

## Verdict

The verdict is `cairn.campaign-completion-verdict/1`:

```text
{schema, campaign_id, input_sha256, status: complete|not_complete,
 unmet: ["<criterion>:<week>:<reason>", ...],
 criteria: {consecutive_slots, complete_attempts, second_grader_coverage,
            disputes_routed, branch_request_without_manual_setup},
 week_reports: [{slot_id, report_status, incomplete_reasons}],
 remaining_live_evidence: [...],
 custody: "unknown", actual_campaign_success: false, runtime_admission: false}
```

Each criterion has a `state` (`met`, `unmet` or `unproved`), its `unmet`
reasons and per-week evidence. `status` is `complete` only when every
criterion is `met`.

- **consecutive_slots**: met whenever no refusal fired. Lists each slot's
  identity and receipt hash.
- **complete_attempts**: per week, all of the following must hold:
  - the receipt is `complete`;
  - attempts are `{126, 126, 0, 126}`;
  - the agent admission is complete with no stop;
  - `limit_assessment` shows 126 within the original limits, 0 failed and
    0 unknown, under `ORIGINAL_LIMITS`;
  - baseline custody is verified for 126 attempts;
  - `original_execution.observed` is present. A declared execution is never
    taken as observed.
  - the report shows zero provider-limit and authentication losses and none
    of the report's attempt reasons.

  A provider-cap stop shows as `receipt_incomplete:provider_cap_stop`.
- **second_grader_coverage**: per week:
  - changed-pair selection is complete: the join's `selection_completeness`
    is `declared`, with no `selection_undetermined`, `evidence_mismatch` or
    `grading_attempts_unavailable`;
  - `second_grader_reviewed` equals the changed pairs;
  - every changed pair has two determinate grades.

  Agreement is copied from the report, which takes it from the join. With zero
  changed pairs it is `{0, 0, null, not_applicable}`, and the bundle phase must
  show `no_grading_needed`. Unchanged pairs are never graded and 100% is never
  invented. A changed-pair week with no defined rate is unmet
  (`agreement_undefined`). Rates are reported per week and never pooled.
- **disputes_routed**: per week, any disagreement must have the routing
  receipt with destination `owner_only`, one dispute per disagreement.
  Disputes routed and pending the owner are listed in `pending_owner`. They
  satisfy routing and coverage only. The underlying report keeps `incomplete`
  with `disagreements_pending_owner` in `week_reports`. Missing grades
  (`occurrence_pending: awaiting_grades`) are kept separate from pending owner
  adjudication.
- **branch_request_without_manual_setup**: always `unproved`
  (`recorded_automation_absent`). A bridge receipt is recorded under
  `observed` as the run's origin. No existing receipt records that an ordinary
  branch request went from standing configuration to a run without per-run
  operator assembly. Today's path needs an operator-assembled bridge
  selection, external reviews and a per-slot live selection
  (`declared_unauthenticated`). Declarations and booleans cannot establish this
  criterion.

`remaining_live_evidence` lists what no source change can supply:

- two live consecutive runner receipts from an actual campaign;
- dedicated-account capacity evidence and a root-issued live selection for
  each slot;
- private conversion materials and a grader release authority;
- actual grades from fresh sessions;
- recorded automation of branch requests;
- grading custody attestation.

## Relation to `check_weekly_paired_eval.py`

That checker (18404cc, with owner-only routing in 5a6d3c1) stays unchanged.
Its `check_usage_precheck` is a pre-launch headroom gate.
Its `evaluate_dual_grader_agreement` computes its own agreement and kappa
from declared judgments. This verifier does not use it, because agreement here
must come only from the existing join. It imports only `WEEKLY_ATTEMPTS`,
which the runner also takes from that checker.

## Validation

```sh
cd scripts && python3 -B -m unittest test_trial_campaign_completion test_trial_legacy_occurrence
```

The tests run real occurrences, with the fake fabric and note store, for two
synthetic weeks of 126 attempts each. They cover these cases:

- a complete pair of weeks;
- zero changed pairs;
- a provider-limit loss;
- a declared but unobserved execution;
- a gap, a duplicate and a reversal;
- an off-schedule slot;
- a missing grade;
- a routed dispute pending the owner;
- an unrouted dispute (failed routing publication);
- an undetermined selection;
- tampering with each pinned input;
- a swapped snapshot;
- a forged report naming another slot, occurrence or agent;
- a forged routing that disagrees with the state, or that routes fewer
  disputes than there are disagreements;
- a bridge receipt for another run, or one that names this receipt but
  carries another run.

Targeted mutations confirm that the tests catch the removal of each guard:

- the consecutive-slot, schedule, slot-identity, agent-count and identity
  joins;
- the routing-pin, routing-count, collection-pin and bridge-link checks;
- the observed-execution, cap-stop, selection, second-grader and owner-reason
  checks;
- zero-changed agreement (never 100%);
- manual setup staying `unproved`.
