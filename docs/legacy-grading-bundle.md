# Legacy grading bundle

`scripts/trial_legacy_grading_bundle.py` connects the
[legacy conversion receipt](legacy-blind-selection.md) to the existing
[grading join](grading-custody-join-v1.md) and to `trial_run_report`.
CAIRN-107, assigned by agent-341.

```text
conversion receipt -> cairn.legacy-grading-join-input/1 bundle
  -> trial_grader_delivery_plan / trial_grader_delivery (changed pairs only)
  -> trial_grading_custody.join_results -> trial_run_report --join
```

The tests use synthetic full-shape runs only: 21 tasks, 3 seeds, 2 arms,
126 attempts and 63 pairs. Grades come from the fake event fabric through
the unchanged delivery plan, `dispatch` and `collect`. Passing the tests does
not show that any real grader was reached or that any real run was graded.

## Interface

```python
bundle, result = build(conversion_receipt_pin, policy, draft_receipt=None)
join = report_join(bundle, result, collection=None)
```

`build` reads the conversion receipt. It re-runs the idempotent converter on
the receipt's own request, so every converter check runs again against the
current files: coverage, identity, provenance, binding and output bytes. The
receipt must come back exactly. `build` then returns the bundle and a result:

- `status`: `no_grading_needed`, `awaiting_grades`,
  `incomplete_incomplete_all_pairs_indeterminate` or
  `incomplete_blocked_indeterminate_pairs`
- `delivery_pairs`: the `pairs` list of a `cairn.grader-delivery-selection/1`
- `bundle_sha256` and `binding_sha256`
- `grades_created`, `delivery_performed` and `runtime_admitted`, all false
- `disputes: owner_only`

Changed pairs need the blind draft's `private/receipt.json` pin. Its
`selection_pin` must be the conversion receipt's selection. Each packet hash
is the hash of the draft's public comparison file. A run with no changed pair
refuses a draft (`legacy_bundle_draft_unexpected`). A run with changed pairs
refuses without one (`legacy_bundle_draft_required`).

`report_join` never passes through a cached join. It recomputes the join
from the bundle with the existing `join_results`:

- **No collection:** the join with no grades. It is returned only if every
  pair is `unchanged` and none is changed or `undetermined`. Otherwise it
  refuses `legacy_grades_awaited`.
- **With a `trial_grader_delivery.collect` result:** the join is recomputed
  from the collection's own retained submissions, with no dispositions. The
  collection's join must equal it exactly. Otherwise it refuses
  `legacy_collection_mismatch`.

In both cases the bundle must hash to `result.bundle_sha256`
(`legacy_bundle_mismatch`). Agreement, coverage and denominators come only
from the existing join. The adapter never creates a grade or a disposition.
Disagreements stay `unresolved`. `trial_grader_delivery.route_disagreements`
sends them to the configured owner destination only, which since 5a6d3c1
cannot be the coordinator's or an excluded identity's inbox.

## Legacy join branch (design decision, 2026-10-21)

The SDK path in `trial_grading_custody._schedule` requires the closed
`cairn.weekly-schedule/1` with source and execution sections, and the
original 7-field plan with a workload pin. Legacy output has neither. The
runner's `schedule_sha256` (`trial_weekly_run`, spec `planned`) hashes
something different: the sorted list of planned identities
`{run_id, case, arm, seed}`. Making up source or execution data, or treating
one hash as the other, would weaken both. The join therefore has a separate,
explicit branch for `cairn.legacy-grading-join-input/1`. The SDK branch is
unchanged: the only edit to it is the one-line dispatch before its schema
check.

The legacy bundle has the same top-level fields and the same closed binding.
Each field hash in the binding is still `canonical_sha` of its section, so
any change to any field breaks the binding.

- **`schedule`** is exactly the runner's planned-identity list. Its
  canonical hash is `binding.schedule_sha256`. It must equal the declared
  pair binding's `schedule_sha256`, which is the value the converter put in
  every blind `pair_key`. The delivery plan's occurrence check therefore
  holds unchanged. There must be 126 rows covering cases x seeds x arms
  exactly, with each `run_id` equal to `case.arm.sSEED`, sorted by `run_id`.
- **`plan`** (`cairn.legacy-weekly-plan/1`) contains `cases` (21), `arms`
  (`CURRENT`, `CAND`), `seeds` (3), `attempts` (126), `pairs` (63),
  `distractors` (1900) and a self-hash. It keeps declared and observed values
  in separate fields:
  - `declared`: the runner's `cairn.legacy-pair-binding/1` exactly as
    issued, and its file pin. The arm roles must be the identity mapping, as
    the runner emits them.
  - `observed`: what the adapter read. That is the `agent.json` and
    `plan.json` hashes, the conversion receipt hash, the distractor count
    from `agent.json`, and the 126 shuffle positions from `plan.json` (0 or 1,
    unique per pair).
- **Checks in the branch:**
  - declared agent and plan hashes equal the observed ones
    (`legacy_declared_observed_mismatch`);
  - request, campaign, occurrence and schedule identities equal the binding
    (`legacy_pair_binding_mismatch`);
  - every inventory row's `run_id` is its planned identity
    (`legacy_attempt_identity`).
- **`inventory`** has one completed row per attempt, with the record's
  outcome. Its `evidence_sha256` is the harness-recorded `stream_sha256` that
  the converter checked.
- **`selection`** is the receipt's `pair_classification`.
- **`packets`** contain one row per changed pair from the draft.
- **`policy`** is the caller's grading policy.

The pinned runner plan may include finite nonnegative decimal diagnostics at
`memory_backends.{CURRENT,CAND}.readiness.cold_seconds`. The bundle preserves
the exact plan digest and validates the rest of the plan in the strict JSON
domain used for blinded documents. These readiness values are not copied into
the bundle or used as grading evidence.

The join checks consistency only. It performs no I/O, so the file pins are
checked by `build`, not by the join. A matching hash is a declaration, not
authenticated custody. The join's own `custody: unknown` and `admission:
false` are unchanged.

## Validation

```sh
cd scripts && python3 -B -m unittest test_trial_legacy_grading_bundle
```

The tests cover:

- **Zero-change full shape:** no grades are needed, and the report renders
  `declared_complete` with 0 changed pairs and a null agreement rate.
- **All-indeterminate and mixed shapes:** they stay incomplete, and the
  no-grade join is refused.
- **Two changed pairs through the real delivery plan, `dispatch` and
  `collect`:** the existing join reports agreement 1/2, and the one
  disagreement goes only to the owner destination.
- **Missing grades:** the report stays incomplete.
- **Identity and file changes:** a wrong schedule hash, non-role arm labels,
  and changed binding, plan, agent or attempt files all refuse.
- **Grades for unknown or wrong pairs:** they refuse.
- **Join branch:** a change to any field breaks the binding, and resealed
  identity mismatches still refuse.
- **Path separation:** the SDK path refuses legacy-shaped bundles and the
  legacy path refuses SDK-shaped ones.
- **Cached joins:** an empty bundle with an arbitrary cached join, a stale
  cached join, a changed bundle and a mutated collection join all refuse.

Remaining work, owned by the root:

- the real case-material inventory
- actual grader sessions
- the owner's release authority and grader choice
- live delivery
