# Original weekly attempt custody: source review and integration boundary

Reviewed against `04ba225af14e806207e5b0924bec08c10e1c3b65`, 2026-10-21.
This is a source-bound integration design, not a new channel implementation or
campaign qualification. No private cases, labels, oracles, retained actor output,
provider, namespace or store execution was inspected or performed.

## What the public contract establishes

The public [task plan](plans/task-evaluation.md#what-a-case-is) says deterministic
evaluation reads final workspace files, Git state, executed commands and the final
answer. It identifies 21 cases by group. It does **not** publish a per-case
`cairn.blind-case-contract/2`, ordered `required_channels`, or artifact inventory.
The source establishes these mechanisms, without establishing their incidence
in the selected original cases:

| `trial_task_eval.evaluate` check | Original observation needed by that check | Blind-channel consequence |
|---|---|---|
| `answer` | Final answer from the native stream | May require `final_answer`; not a per-case channel declaration |
| `command`, `tool_output` | Parsed executed command, correlated output/error state | A command digest alone cannot reproduce the check; a released action projection needs its own contract |
| `exists`, `file` | Final workspace paths/content | A final answer is insufficient; selected artifact coverage must be declared |
| `added`, `unchanged` | `eval-base` diff including untracked additions, or initial snapshot digest | Needs the original baseline as well as final state |
| `commit_files`, `staged` | Commit comparison or index state | A worktree-only snapshot loses evaluated state |
| `shell` | Actual grader command/result under the selected runtime | Requires evaluator custody; an actor assertion is not that result |
| `all`, `any` | Recursive composition of the above | Preserve check order and short-circuit behavior |

`grade` also evaluates `review_checks` and `descriptive`, beyond `correct` and
`mistake`. `validate`'s check-type loop only traverses the latter two. A future
metadata projection that examines only that loop is therefore incomplete.
This table describes supported mechanisms, **not** a requirement that every
case release every channel or that evaluator checks be exposed to a grader.

The blind draft's `_contract` permits six channels and checks an explicitly
supplied ordered contract. `_render` requires exact coverage, artifact metadata,
and a separately pinned observation/subject for `action_trace` and
`evaluator_observation`. It does not derive a contract from the private checks.
The converter's `_materials` currently accepts only `task_statement`,
`grading_instructions`, and `final_answer`; additional channels refuse
`legacy_channel_unbound`. The tracked schema examples in the blind draft and
converter tests are synthetic. They do not establish original-case requirements.

## Existing custody to reuse

There is already an SDK weekly workspace constructor. In
`scripts/trial_sdk_weekly_owners.py`, `WeeklyWorkspaceOwner.prepare`:

1. Verifies the held weekly selection and selected root/version manifest.
2. Checks the selected case's workspace path, creates a private root, and calls
   `trial_task_eval.prepare_workspace(..., source_root=root)`.
3. Rechecks the manifest, reads HEAD and all refs, verifies `eval-base`, and
   retains the initial snapshot with the selected schedule row and request IDs.
4. Seals that preparation into `workspace_sha256`; `verify_preparation` compares
   it with the SDK preparation binding.

`WeeklyEnvironmentOwner.bind_run` additionally binds the actual workspace cwd,
selected binaries and actual store policy to the run. Its workspace owner stays
reachable through `run._weekly_workspace`. Setup failure consumes the row and
retains the partial workspace. The current owner tests exercise these boundaries
with synthetic inputs and substituted process/store effects. They are not actual
weekly setup or physical-bound evidence.

The legacy `prepare_workspace` already checks optional `workspace_commit`, uses
fixed fixture Git identity/date, excludes only root `setup.sh` plus
`__pycache__`, creates `eval-base`, and writes `.eval-snapshot.json`. However,
legacy `run_agent` invokes its default source root and resolves the default
corpus; the legacy command path is not the explicit weekly selection owner.
An initial snapshot and optional commit check alone do not bind final artifacts
to a stopped original attempt or its grading observation.

`SessionStreamRetention` and `EvidencePublication` retain actual SDK stream
identity/hash/count and source evidence. They do not export final workspace,
index/ref state or original task-grader observations into a blind selection.
`trial_attempt_actions` supplies bounded action/denial metadata, not the full
command/output input consumed by the original checks. Its caller-declared
required commands and timestamps are not authenticated by that projection.
`trial_work_product` has selected export machinery, but its prepared workspace
contract is the prospective detached/no-refs contract. The weekly owner test
explicitly refuses that substitution. Preserve weekly refs and `eval-base`.

Legacy `stream_sha256` currently binds the retained `stream.jsonl` bytes:
`run_agent` decodes stdout with replacement, then encodes it for retention.
Thus it is not a hash of the original subprocess bytes when stdout has invalid
UTF-8. Do not describe it as independent or byte-exact native capture. Existing
legacy records without these hashes still cannot gain retrospective custody.

## Missing evidence and smallest coherent integration

No per-case channel incidence is established by this review. The missing input
is a version-bound, owner/evaluator-selected metadata inventory for all 21
original cases: selected frozen identity; opaque case key; complete check-family
coverage (including review/descriptive checks); ordered released channels; and
artifact names/kinds/representations or an explicit rule for their selection.
It also needs the selected grading-instructions pin and the rule for deriving
public observations without leaking checks, labels, arm identity or memory.
Opaque shell checks may require evaluator-authored metadata rather than any
automatic inference. Such metadata must be chosen before outcomes. Engineers
need not open private check bodies to receive it.

Until that inventory exists, the minimum coherent result is this integration
design and correction of stale claims. A new generic collector would choose
unestablished retention/privacy semantics; a second workspace constructor would
duplicate an existing owner. No new attempt-record field is introduced here.

After the metadata is selected, implement one original-attempt owner in a new
`scripts/trial_attempt_custody.py` (proposed, not present):

1. **Preparation.** Consume the selected row and original workspace owner, not
   an arbitrary workspace hash. Keep the actual root/version/source identity,
   setup outcome, initial snapshot, HEAD, index and refs attached to the same
   attempt. Reuse the existing weekly constructor. A legacy adapter must pass
   an explicit selected source/corpus instead of silently resolving latest.
2. **Termination and capture.** Capture only after the original actor and its
   writers have actually stopped, through an owned wait/release boundary. Retain
   unknown cleanup as unknown; no caller success flag substitutes. Capture the
   contracted file bytes/modes/deletions, index and refs under held directory
   custody, with changes during capture causing refusal. Bound counts and reads
   before allocation. Preserve partial output on interruptions; never retry an
   uncertain capture as a fresh attempt.
3. **Actions.** Feed original stream bytes and their existing timing/correlation
   custody into the selected action projection. Keep error, repeat, incomplete
   and denied actions distinct. Bind the projection to original stream digest,
   byte count, attempt identity and parser source. Do not manufacture raw-line
   custody by re-encoding parsed JSON or substitute command hashes for original
   `tool_output` semantics.
4. **Evaluation.** An authorized evaluator consumes the captured original state
   and original selected checks through `grade`/`evaluate`. Bind its source,
   exact input snapshot, actual execution/exit and outcome before projecting the
   permitted public observation. `shell` currently mounts default `TRIAL`
   revision assets: an explicit selected grader-root join is needed alongside
   the workspace-root join. Preserve unknowns and the original mistake/review
   precedence. This engineering lane neither invokes those checks nor grades.
5. **Publication.** Persist a private attempt-custody record before the attempt
   result; the result may reference its digest and size. Final field names and
   schema require runner coordination. Runner inclusion is declared binding,
   not independent observation. Only after this path exists should the owner
   assign a converter extension that emits exact contracted artifact entries
   and action/evaluator observation pins into the unchanged blind draft. Keep
   `legacy_channel_unbound` until then; never attach freshly observed files to
   old attempts and call them original custody.

The integration points are `WeeklyPreparationOwner.prepare_workspace` /
`bind_loaded_run`, `WeeklyEnvironmentOwner.bind_run`, original run terminal
custody, and legacy `run_agent` immediately around preparation, capture and
`grade`. The evaluator path must remain separate from source-ready SDK input
qualification. A passed 9500-byte observation does not certify task artifacts.

## Validation required before channel support

Use synthetic cases only. Exercise the actual setup and publication boundaries,
not a standalone manifest checker:

- Select an older root/version with a different newer default; verify original
  setup bytes, optional commit mismatch refusal, fixed Git identity/date, nested
  setup preservation, original refs and `eval-base`. Test interrupted setup and
  consumed-row replay refusal.
- Change, delete, rename, stage, commit and leave untracked synthetic files;
  verify exact selected final bytes, modes, index and ref differences. Refuse
  traversal, indirection, replacement during read and output overflow. No
  workspace-only projection may satisfy a staged/commit observation.
- Bind interleaved/repeated/error native actions to original stream custody;
  test incomplete capture, invalid UTF-8 policy, altered same-length bytes,
  mismatched IDs and missing wait/release.
- Run an inert synthetic evaluator against the exact captured input, preserving
  timeout/nonzero/unknown and review precedence. Refuse changed evaluator source,
  different selected revision assets or altered observation subjects.
- Compose attempt record, runner binding and blind draft; reject missing or
  additional contracted channels, wrong artifact inventory, mismatched subject
  pins, partial persistence and arm/private-check leakage. Retain indeterminate
  pairs as blocking complete changed-pair coverage.

All work must preserve 21 cases, three seeds, two arms (126 attempts), 1900
distractors and two consecutive weekly runs. Original selected-input 9500 bytes,
final-answer 8192, tool-room 32000, search 2 / expansion 4, hook 5 seconds and
last-memory 30 seconds stay unchanged, as do raw 131072, combined 512 MiB and
traffic 4 MiB. Existing work-product storage ceilings are not spare runtime
allocations: any future capture must name its overlapping backings and owners
inside the original resource plan. This review does not expand physical-bound
work or claim that these extra backings fit.

## Coordination

Agent501 owns runner correction, pair-binding emission, `TrialStore` partial
construction/stop and `cmd_agent` store cleanup. Agent504 owns this source review
and proposed workspace/custody integration; new helpers require the established
seam. Runner lands first before adjacent shared-file integration. No converter,
SDK or runner implementation changed in this review. Root owns admission,
channel selection, evaluator access, campaign execution and fresh grading.
