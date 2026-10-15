# Offline weekly input bundles

The Python API is `trial_weekly_input.load_weekly_input(reference)`. It returns
private evaluator data, a seven-field capacity plan and a deterministic
126-row schedule. There is no CLI
launcher, request/2 change, paired-request dispatch, store or runtime operation.

## Closed declarations

`reference` has exactly these fields:

| Field | Meaning |
|---|---|
| `schema` | `cairn.weekly-input-reference/1` |
| `root` | Absolute, plain, canonical spelling of the existing weekly source root |
| `label_version` | Explicit positive integer, less than 2**31; booleans refused |
| `frozen_sha256` | Exact original FROZEN.json bytes hash for version 1, or FROZEN-vN.json for N |
| `execution` | `{path, sha256}` for the sidecar below |

The sidecar has exactly `schema`, `weekly_source`, `actor_api_base`, `native`,
`arms`, `memory_arms`, `seeds`, `wording`, `workload`, and `distractors`.

- `schema`: `cairn.weekly-execution/1`.
- `weekly_source`: exactly the reference's root, label_version and frozen_sha256,
  with JSON type-sensitive equality.
- `actor_api_base`: explicit full 40-hex revision. This is independent of the
  frozen fixture's baseline_commit and each case's workspace/source identity.
- `native`: exactly binary `{path,sha256}`, model string, effort enum
  low/medium/high/xhigh/max. These are declarations, not an observed build,
  effective model, account assignment or provider attestation.
- `arms`: exactly `["CURRENT", "CAND"]`; `wording`: exactly `task`.
- `seeds`: three distinct explicit integers in 0..2**31-1, in caller order.
- `memory_arms`: exactly CURRENT and CAND. Each declares binary, hook, bridge,
  api_revision, recall_revision, recall_mode, semantic_fallback,
  embedding_worker, embedding_config, embedding_model_identity. All six file
  fields use `{path,sha256}` pins; API revisions are full 40-hex identifiers.
  Recall mode is ambient or agent_tools; semantic_fallback is a strict boolean.
  All three embedding descriptors must be exactly equal across both arms.
  An identity file pins a declaration; it does not verify adjacent model files,
  a running backend or compatibility. Legacy embedding storage is independent
  of the hook selector switch. Literal route declarations are retained without
  coercion or prospective route admission. Later dispatch must validate their
  executable combination and any selector/provider workload.
- `workload`: an explicit caller `{path,sha256}` pin. Its bytes are checked;
  workload semantics and capacity are deliberately left to the companion. No
  calibration workload, zero-cache or zero-call premise is manufactured here.
- `distractors`: exactly count=1900, seed=20260928, generator_source file pin,
  generated_sha256. The source pin must identify the loaded evaluator module
  file, including its existing distractor implementation. The generated digest
  is canonical SHA256 of `{"notes": [distractor(0), ..., distractor(1899)]}`.
  This remains separate from the frozen original corpus hash.

The complete synthetic declaration in `scripts/test_trial_weekly_bundle.py`
is an executable format example with inert component files. It is not capacity
evidence or a template containing real cases.

## Identity, output and bounds

The loader reuses the actual versioned readers, relative-path/component ordering
in label_manifest, and original filenames. It compares every selected manifest
identity field: schema, labels_sha256, files, cases_sha256, corpus_sha256,
baseline_commit and label_version. Optional frozen_at is retained only as
metadata, not evidence of time or custody. Changing it still changes the exact
manifest byte pin and therefore the selected schedule/source identity. With
the same ordered case IDs, arms, seeds and workload pin, capacity plan identity
is unchanged.

All selected JSON is duplicate-key checked. Future overlays are not read or
hashed into a past version; their directory entries still undergo the bounded
source-tree metadata check. Source has at most 4096 entries, 64 MiB total regular
file bytes, 8 MiB per file and 4096-byte path spellings. Existing plain-path and
regular-file validators reject indirection and special files. Sidecar reads use
the existing 8 MiB limit; component/workload pins use its 512 MiB streaming limit.
These are source read limits, not an all-process memory or queue guarantee.

This API requires trusted, quiescent source, including the imported code and
declared component files. It repeats some reads and does not hold a filesystem
snapshot or prove arbitrary concurrent filesystem custody. Pins verify selected
bytes; they do not certify that the caller selected the owner's original fixture.

The returned bundle includes the original resolved ordered 21 cases, all three
wordings, private grader fields and source provenance, original corpus controls,
separate workspace_paths, execution declarations, source identity, generated
1900 notes, plan and schedule. No `_prospective_workspace` override is injected.

`bundle.plan` has exactly the existing provider mapping contract's seven fields:
`instrument`, `cases`, `arms`, `seeds`, `attempts`, `workload`, `sha256`.
Its canonical hash excludes only its own sha256 field, as before. This is a
capacity/workload-count identity; it is not a complete source/execution identity.

`bundle.schedule` is a closed `cairn.weekly-schedule/1` object with exactly nine
fields: `schema`, `plan_sha256`, `source`, `execution`, `actor_api_base`, `wording`,
`distractors`, `runs`, `sha256`. It binds the capacity plan's hash, complete source
identity and exact execution-sidecar pin. The remaining execution declarations
are transitively bound by that pin. Its own canonical hash excludes only its
sha256 field. Future request dispatch must bind the schedule/source/reference,
in addition to the capacity plan. No request/2 field meaning is changed here.

The schedule
orders caller seeds, then original cases, then the legacy deterministic pair
shuffle using `random.Random(f"{seed}:{case_id}")`. Each row carries case, arm,
seed and pair position. The 126 rows select task wording; paraphrase and direct
remain in private source identity.

The result is ordinary mutable evaluator-owned Python data. Do not pass this
whole private bundle to an actor, persist it as public output, or mutate it and
reuse old plan/schedule hashes. Later admission must revalidate both selected
identities and their binding.
The loader always returns runtime_admitted=false, witness_valid=false and
admission=false. The bundle is not a native origin witness or full107 acceptance.


## Validation

The synthetic bundle tests exercise original source and private-label preservation,
the exact provider-plan contract, the separately hashed schedule, fixed baseline
pair ordering and declaration failures. They perform no store, setup, embedding
or provider operation. Run these local tests and the repository checks:

```sh
python3 -B -m unittest discover -s scripts -p 'test_trial_weekly*.py'
make check
```

The mapper compatibility test calls the existing `original_plan`; it validates
shape, counts and hash, rather than workloads, accounts or runtime admission.
