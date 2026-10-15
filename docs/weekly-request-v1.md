# Offline weekly request and preparation

`scripts/trial_weekly_request.py` implements the separate, closed
`cairn.weekly-eval.request/1` source boundary. It rebuilds the original weekly
bundle through [`trial_weekly_input`](weekly-input-bundle-v1.md). Existing
`cairn.paired-eval.request/2` continues to load prospective input, including
when its plan declares `weekly-frozen-cases`; it is not silently redirected.

The weekly request has exactly these 17 fields:

```text
schema request_id campaign_id occurrence_id scheduled_at expires_at
weekly_input plan schedule dispositions limits capacity grading
output reply supervisor_source permission_profile
```

`weekly_input` is the closed `cairn.weekly-input-reference/1` selecting the
original root, positive label version, exact FROZEN bytes and execution sidecar
pin. `plan` must equal the entire rebuilt seven-field capacity plan, including
its canonical hash. `schedule` is a `{path, sha256}` file pin; its entire strict
JSON value must equal the rebuilt weekly schedule. Merely updating its self-hash
does not permit different rows, ordering, source identity or fields.

The bundle preserves all 21 ordered cases, all three wordings, private grading
fields/assets, original corpus provenance, local-only/cross-collection and
supersession controls. The selected actor wording is `task`. The 126 rows bind
three explicit distinct seeds and both arms. The original corpus identity is
separate from the 1900 generated distractors, fixed generator seed 20260928,
generator source pin and generated digest. Both arms declare the same embedding
worker, configuration and model identity pins. These are source declarations.

`dispositions` has the existing six review names. Each entry has exactly
`artifact subject_sha256 reviewer_id state valid_until`, with a selected artifact
pin, canonical reviewer UUID, accepted state and sufficient validity. Subjects
are the canonical SHA-256 of the following objects, each with a distinct
`schema: cairn.weekly-NAME-review/1` field:

| NAME | Remaining subject fields |
| --- | --- |
| task | `reference`, full derived `source`, selected `wording` |
| oracle | full `source`, complete `grading`, `legacy_grader_source` |
| comparator | complete `plan`, `schedule_sha256`, derived `interfaces`, derived `corpus` |
| supervisor | `supervisor_source`, `limits`, `permission_profile` |
| native | entire sidecar `native` (binary/model/effort), `permission_profile`, `limits`, `supervisor_source` |
| integration | `request_core`: every request field except `dispositions` |

Here `interfaces` contains the sidecar's `actor_api_base` and complete
`memory_arms`, including each arm's binary/hook/bridge, API/recall declarations
and all three embedding pins. `corpus` contains full derived `source` plus the
sidecar's entire distractor declaration. `legacy_grader_source` is the pinned
evaluator source also used by the generator. Actor API base and weekly case
baseline commit remain distinct. `review_subjects(request, bundle)` computes
review subjects without I/O; it neither validates nor accepts caller inputs.
The public check and prepare paths rebuild the bundle themselves.

Both paths require externally selected companion and companion-review pins.
The unchanged mapper/spend logic validates the complete workload and declared
capacity/account/price routes. The companion binds the full request including
dispositions; its independent review binds the whole companion. Companion pins
are not fields of the weekly request, so this graph has no self-hash cycle.
The helper's required keyword-only `native_model` comes from the validated
dispatcher's native declaration: `bundle.execution.native.model` for weekly,
`bundle.document.native.model` for request/2. It derives both actor roles from
that value and both grader roles directly from validated `request.grading`.
There is no four-role caller override, default model or fallback.

Check and preparation reuse the exact original limits, implementation permission
profile, grader independence/exclusions, grading artifact pins, reply UUIDs and
freshness checks. Companion evidence must cover the full selected interval,
including the spend projection end. No implicit calibration, absent auxiliary
work or zero cache/call assumption is introduced.

```text
python3 scripts/trial_weekly_request.py check REQUEST.json \
  --companion COMPANION.json --companion-sha256 SHA256 \
  --review REVIEW.json --review-sha256 SHA256
python3 scripts/trial_weekly_request.py prepare REQUEST.json --registry PRIVATE_DIR \
  --companion COMPANION.json --companion-sha256 SHA256 \
  --review REVIEW.json --review-sha256 SHA256
```

`check(request, *, now, companion, review)` performs no writes. Preparation
validates before writing exclusive occurrence/request holds into an existing
private registry. It refuses output/registry overlap with the weekly source
tree, every selected component/control/evidence pin (including transitive
provider evidence), and each other. The CLI additionally protects its selected
request filename. The in-memory API has no request filename. Sources must be
trusted and quiescent; these path/pin checks do not establish atomic snapshots,
arbitrary concurrent filesystem custody or hardlink provenance.

Preparation retains the canonical request/reference/plan/source, exact pinned
schedule and sidecar bytes, selected review/grading/permission/supervisor
artifacts and companion evidence/projection. These files plus the receipt must
fit one aggregate 8 MiB retention limit before registration. Existing per-file,
source-tree and component hashing bounds remain in force. Files use mode 0600,
the output directory 0700, exclusive creation and file fsync. A write/fsync
failure leaves partial holds/output for operator reconciliation; preparation
does not undo a hold or manufacture a retry. This is not a transactional commit
or a claim of directory-entry durability after power loss.

The public receipt contains identities, hashes, filenames and declared provider
projection evidence, not private case/corpus bodies or grader assets. Private
source stays evaluator-side; selected control artifacts may themselves be
sensitive and remain in the private preparation directory. No corpus filter,
prospective conversion, legacy launcher or fake workspace override is used.

`launch` (API or CLI) refuses with `provider_execution_unavailable` before input
reads. Source preparation does not import stores or map original IDs to store
UUIDs. It proves no model/account/runtime identity, delivery witness, capacity
availability, settlement, fresh grading or campaign outcome. Native integration
and both complete original campaigns remain separate, unimplemented work;
all admission/witness flags remain false.
