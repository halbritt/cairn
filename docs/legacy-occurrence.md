# Legacy occurrence orchestration

`scripts/trial_legacy_occurrence.py` runs the post-attempt phases of one
completed legacy weekly occurrence. It calls only existing functions and does
no grading arithmetic of its own. It starts after the weekly runner has
finished. It never runs an actor, never admits anything, and never builds the
bridge from candidate requests to weekly runs or any aggregation across
weeks. CAIRN-107, assigned by agent-341.

The tests use synthetic full-shape occurrences (126 attempts, 63 pairs), the
fake event fabric and a fake note store. Passing them does not show that any
real grader, store or note was reached.

## Seam and CLI

```python
state = run_occurrence(input_pin, out_dir, *, fabric=None, config=None, publisher=None, now)
```

```sh
python3 scripts/trial_legacy_occurrence.py --input IN.json --input-sha256 HEX --out DIR \
  --now 2026-10-15T12:00:00Z [--delivery-config FILE] [--note-config FILE]
```

- `fabric` and `config` are a `trial_grader_delivery` fabric and its closed
  config. The CLI builds `CliFabric` from `{"delivery", "cli"}`.
- `publisher(report)` returns `{request_id, record_id, version}`. The CLI's
  `--note-config {binary, token_file, repo}` binds it to
  `trial_run_report.publish_note`. Tests pass that function a fake runner.
- The CLI exits 0 only when the state is `finished`, and 3 while it is
  pending.

The input `cairn.legacy-occurrence-input/1` is closed. Every value is a pin
or null:

- `conversion_request` (required): the `trial_legacy_blind_selection`
  request. It names the runner's `agent.json`, `plan.json` and
  `pair-binding.json`, plus the case materials, which are root-owned.
- `grading_policy`, `draft_approval`, `grader_sessions`, `release_authority`
- `run_date`

The approval, sessions and authority are issued after the occurrence starts.
They are added to a new input document as they become available.

## Phases

| Phase | Existing function | Output under `out/` | Pending code when missing |
|---|---|---|---|
| conversion | `trial_legacy_blind_selection.convert` | `conversion/` | `case_material_missing` |
| draft | `approval_subject`, `draft` | `draft-subject.json`, `draft/` | `draft_approval_missing` |
| bundle | `trial_legacy_grading_bundle.build` | `bundle/` | `grading_policy_missing` |
| plan | `trial_grader_delivery_plan.build_plan` | `delivery-selection.json`, `delivery-plan/` | `grader_sessions_missing` |
| dispatch | `trial_grader_delivery.dispatch` | `delivery-run/`, `dispatch.json` | `release_authority_missing`, `grader_fabric_unavailable` |
| collect | `collect`, `route_disagreements` | journal in `delivery-run/` | `awaiting_grades` |
| report | `report_join` or `join_results`, then `build_report`, `render_markdown`, `note_draft` | `report/<snapshot>/` | none |
| publication | `publisher`, `trial_run_report.publish_note` | `phases/publication-<snapshot>.json` | `note_publication_unconfigured` |

The draft, plan, dispatch and collect phases run only when there are changed
pairs. A run with no changed pair is `not_required` for all four and needs no
delivery. Uncertain fabric or store responses are pending, not refusals:
`dispatch_uncertain_retry`, `collect_uncertain_retry`,
`dispute_routing_uncertain_retry` and `note_publication_uncertain_retry`.
Any other refusal from an existing function propagates with its own code.

## Resume and changed inputs

Each completed phase is recorded once in `phases/<phase>.json`. The record
holds `phase_id` (the hash of its exact inputs), the inputs and the output
pins. A repeat invocation verifies both:

- A changed input refuses `occurrence_<phase>_input_changed`.
- A changed output refuses `occurrence_<phase>_output_changed`.

So no downstream phase is silently reused. Phase inputs chain through
upstream output pins. The bundle is rebuilt on every run, which re-runs the
converter's checks, and must be byte-identical to the recorded one.

Some phases can be interrupted after writing their output but before
recording it. A draft or plan directory left that way is adopted only if its
receipt names the same selection, and approval or delivery selection.
Dispatch reuses the delivery journal and its derived request IDs, so a retry
after `grader_fabric_uncertain` publishes nothing twice. Collect only reads.
Dispute routing uses derived request IDs.

## Reports, observed grading status and publication

Every occurrence that reaches a bundle keeps report evidence. Each run
writes a snapshot under `report/<first 20 hex of the join hash>/`
(`join.json`, `report.json`, `report.md`, `note-draft.json`). The snapshot is
keyed by the exact join input and written once, so later grades produce a
new snapshot and earlier ones are kept. The join is always recomputed by the
existing join:

- `no_grading_needed`: `report_join(bundle, result)`
- `collected_grades`: `report_join(bundle, result, collection)`
- `no_grades_incomplete`: `custody.join_results(bundle, [], [])`, used for
  indeterminate runs and for changed pairs before delivery. Such a report
  must render `incomplete`; it is never labelled no-grading-needed.

The renderer's output is unchanged. While a dispute is open it truthfully
reads `incomplete` with `disagreements_pending_owner`. Alongside it, the
state's `grading` field records observed status, so pending owner
adjudication can be told apart from missing grades:

- `all_graded`: every changed pair has both determinate grades. It is vacuously
  true with 0 changed pairs.
- `agreement` and `disagreements`, from the existing join
- `disagreements_routed`, with the routing receipts (`destination:
  owner_only`)
- `delivery_coverage` and `resolution` (`owner_pending` or
  `not_applicable`)

Disputes are never resolved here. Unanimity is never required.

Only a terminal occurrence is published: no prerequisite or grade is pending.
An occurrence that can still advance keeps its snapshot unpublished. The
note's request ID is derived from the exact report by `note_draft`. An
interrupted publication is replayed with the same request ID, and the
publication is recorded once per snapshot.

The state's `complete` is true only when the occurrence is finished, its note
is published and the report is `declared_complete`. A note draft or missing
grades never count. `runtime_admitted` and `grades_created` are false, and
`custody` is `declared_only`.

## Interface needed from elsewhere

None for this module. It consumes the runner's existing outputs through the
conversion request. Someone with access to the private case material must
assemble that request: case contracts, task statements and grading
instructions, plus the blind policy and review measurement. This remains
root-owned.

## Validation

```sh
cd scripts && python3 -B -m unittest test_trial_legacy_occurrence
```
