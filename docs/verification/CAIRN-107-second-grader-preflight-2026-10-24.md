# CAIRN-107 second-grader preflight, 2026-10-24

This check prepared the offline grading handoff for a weekly paired run. It did
not inspect campaign outputs, contact graders, or call a model provider.

## Verified handoff

The existing post-attempt path is in
[`legacy-occurrence.md`](../legacy-occurrence.md): conversion selects the
changed pairs, blind packet drafting produces their comparison packets, the
delivery plan binds those packets to two distinct grader sessions, and the
delivery collector retains both submissions for the existing grading join.
Unchanged pairs are not sent for grading. Disagreements route to the owner and
remain unresolved by code. The join and report keep declared custody separate
from verified execution.

The path is implemented by `trial_legacy_blind_selection`,
`trial_legacy_grading_bundle`, `trial_grader_delivery_plan`,
`trial_grader_delivery`, `trial_grading_custody`, and
`trial_legacy_occurrence`. The full contracts and limits remain in
[`legacy-grading-bundle.md`](../legacy-grading-bundle.md),
[`grader-delivery-plan-v1.md`](../grader-delivery-plan-v1.md), and
[`grader-delivery-v1.md`](../grader-delivery-v1.md).

## Local verification

Ran from `scripts/`:

```sh
python3 -B -m unittest test_trial_grading_custody test_trial_legacy_grading_bundle \
  test_trial_grader_delivery_plan test_trial_grader_delivery \
  test_trial_grader_release_issuer test_trial_legacy_occurrence \
  test_trial_campaign_completion
```

Result: 142 tests passed. These use synthetic fixtures and fake delivery
fabric. They establish the local handoff checks, not a live grader session,
real grade, or campaign completion.

## Remaining acceptance evidence

For the active campaign, this preflight establishes no result about its changed
pairs or grades. After its attempt outputs are available to the campaign owner,
the occurrence must still receive the selected case materials and grading
policy, blind-draft approval, fresh eligible grader sessions, and a separate
release authority bound to the exact plan and packets. Actual grades must then
be collected for every changed pair; the report must give the agreement rate
and route every disagreement to the owner. No provider-limit loss and complete
planned attempts must be established from campaign evidence.

CAIRN-107 acceptance also needs a second completed consecutive weekly slot and
evidence that a candidate branch can request a run without manual setup. The
current completion checker continues to mark that automation criterion
unproved. Synthetic tests and this preflight do not close any of those gaps.
