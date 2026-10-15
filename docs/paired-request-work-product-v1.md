# Offline paired requests, version 2, and selected work products, version 1

This source slice prepares reviewed requests and exports selected local work.
It has no provider launcher, scheduler, deployed admission path or supervisor
implementation. A successful preparation always reports `runtime_admitted: false`.
Declarations of review, account capacity and reservation are pinned inputs;
this code does not authenticate their authors or attest their truth.

## Request entry point

From the repository root, with an operator-owned request JSON and an existing
private registry directory, the commands are:

```sh
python3 scripts/trial_paired_request.py check /absolute/path/request.json
python3 scripts/trial_paired_request.py prepare /absolute/path/request.json --registry /absolute/path/private-registry
```

Those commands without companion arguments apply to `supplemental-fresh-tasks`.
Weekly requests additionally require explicit companion and independently
selected review file pins on **both** check and prepare; see the closed
[weekly companion contract](paired-companion-v1.md). Request/2 itself is unchanged.

`check` validates without registering or creating output. `prepare` snapshots
the supplied JSON, validates it, exclusively registers the occurrence and request
IDs, then creates the requested private output directory. The registry must have
no group or other permission bits. Input, registry and output must be separate
paths; none may contain another. Output must be fresh. Ancestor symlinks and
`..` are refused. Registry writes remain held if a later write fails: the operator
must reconcile the partial preparation; the script does not manufacture a retry.
Registration is preparation identity only, distinct from the supervisor's actual
execution intent. No actor is launched or marked consumed by this registry.

Provider execution has a fixed refusal, even without a readable request file:

```sh
python3 scripts/trial_paired_request.py launch /absolute/path/unavailable-request.json
```

It exits 2 and writes:

```json
{"code": "provider_execution_unavailable", "status": "refused"}
```

Malformed JSON, duplicate keys, nonfinite numbers and unavailable files are
refused. The CLI reports fixed codes without paths, source text or exception
messages. Argument syntax errors also use a fixed refusal code. Success exits 0;
refusal exits 2. Callers must inspect both exit status and selected JSON.

Prospective preparation requires `cairn.paired-eval.request/2` and the
[version 2 provider capacity contract](paired-capacity-v2.md). Version 1
capacity is retained only in `capacity_check_v1` for historical source inspection;
neither request preparation nor the current `capacity_check` calls it.

The closed `cairn.paired-eval.request/2` object accepts these fields only:

| Field | Contract |
| --- | --- |
| `schema` | Exact request schema above. |
| `request_id`, `campaign_id`, `occurrence_id` | Canonical UUID strings. |
| `scheduled_at`, `expires_at` | UTC timestamps; the current time must be in the interval. |
| `frozen_input` | Absolute input `path` and exact `manifest_sha256` of `FROZEN.json`; existing prospective input validation must also pass. |
| `plan` | Exact `instrument`, frozen case IDs, `arms`, unique integer `seeds`, `attempts`, pinned `workload`, canonical `sha256`. The hash includes the workload pin. Arms must be CURRENT and CAND; attempts equal cases × two arms × seeds. |
| `interfaces` | CURRENT and CAND each specify `api_revision`, `actor_base`, `components`, matching the frozen memory-arm declaration and actor baseline. Component file pins are rechecked. |
| `dispositions` | Exactly task, oracle, comparator, supervisor, native and integration. Each has a pinned `artifact`, exact `subject_sha256`, `reviewer_id`, accepted `state` and unexpired `valid_until`. |
| `limits` | Selected input 9500 bytes, final answer 8192 bytes, tool room 32000 bytes (`tool_room_bytes`), two native searches, four pulls, five hook seconds, thirty seconds for the last native memory call. |
| `capacity` | Pin of the [whole-plan capacity v2 declaration](paired-capacity-v2.md). |
| `corpus_selection` | Pin of the source identity and exclusion artifact, checked against the frozen corpus. |
| `grading` | Primary and secondary agent/model declarations; excluded agents; pinned rubric, changed-pair definition, blinding policy and review budget; dispute owner. |
| `output` | Absolute fresh private preparation output path. |
| `reply` | Event, recipient and correlation UUIDs, in fields `event_id`, `recipient_id`, `correlation_id`. Actual inbox authentication belongs to the integrator. |
| `supervisor_source` | Pin of the supervisor source artifact reviewed for this preparation. |
| `permission_profile` | Exactly `name`, pinned source manifest and pinned settings; reviewed implementation profile described below. |

A file pin has exactly `path` and lowercase hexadecimal `sha256`. Artifact JSON
is checked against the same bytes that are parsed. Canonical JSON hashes sort
keys, preserve Unicode and use compact separators. JSON/input files are limited
to 8 MiB each; the input tree has a 4096-entry and 64 MiB preflight limit. Other
pinned files use a streamed check capped at 512 MiB. Frozen inputs and their
external referenced files are trusted, quiescent operator sources: preflight and
subsequent existing loader reads are not a filesystem snapshot or a hard memory
containment boundary.

Review subjects bind task to input.json, oracle to rubric, comparator to the
plan and interfaces, supervisor to its source pin, native to the canonical object
containing `binary_sha256` and the complete `permission_profile` declaration,
and integration to the frozen manifest. Grader agents must differ and cannot
be any declared source reviewer, excluded agent or dispute owner. This verifies
declarations, not fresh-session identity, author exclusion completeness or actual
blinding. The integration reviewer must establish those facts independently.

`mcp_arguments(binding)` builds the paired facade arguments with explicit
`--tokens 32000` and `--max-tokens 32000`. Its closed binding has `socket`,
`token_file`, `repo`, `task` and `run`; no argument extension or limit override
is accepted. The first three paths must be absolute without symlink ancestors;
task/run are nonempty bounded UTF-8 strings. It starts no process. The integrator
must use these arguments with the pinned arm binary and authenticated disposable
trial binding; this helper does not prove that any loaded process used them.

These CLI flags are named tokens but preserve the tool response room measured in
bytes in this workstream. They are separate from the 9500-byte selected-input
verdict and from provider token/quota estimates. Both must be explicit: the
inspected facade defaults `--max-tokens` to 1000000. Pinning the server ceiling
does not repair a reader that drops invalid tool arguments before retaining the
server error evidence. Root's native-profile integration must retain that
evidence and its invalid/unknown disposition independently.

The implementation permission profile is pinned to root's reviewed source
commit `6fb0fcd07f9cd7f8959190088bb9d070ff863764`. `permission_profile.name`
must be `implementation/1`. Its pinned source manifest must declare that commit
and the SHA-256 of `scripts/trial_task_permissions.py`
(`43a097332584a501b752e8fe15ded91d204bb8a62af34180ec5694b763b188b2`).
Its pinned settings must have canonical JSON hash
`98b58eec1a3554ec8604c9508867684bd63a78ab482c68acab1e90af2a09261d`,
generated by the reviewed root constructor. Changed names, source identities or
settings are refused. A new reviewed source version requires a deliberate
contract update; this preparer never imports request-supplied Python or defaults
to a weaker profile. Root owns that constructor and its later bridge.

The source manifest is a declaration, not verification of a loaded file or its
author. The runner must check its actual constructor hash and the selected
settings/prompt/native-profile agreement before admission. Configured allow/deny
rules do not prove effective native permission matching. Actual denials and
control failures must remain evidence. These source declarations never enable
runtime admission.

## Historical whole-plan capacity declarations, version 1

The following describes preserved source behavior, not a usable prospective
capacity contract. It permits omitted token demand and assumes one call per
attempt. `capacity_check_v1` preserves those semantics for historical inspection.
New requests must use v2; there is no automatic conversion, fallback or inferred
provider-request/token estimate.

`cairn.paired-capacity-evidence/1` has exactly `schema`, `plan_sha256`, `state`,
`observed_at`, `valid_until`, four pins (`account_binding`, `route_binding`,
`quota_policy`, `reservation`), `resources` and `demands`. State must be supported,
the plan hash exact and the UTC observation fresh.

The account artifact is `cairn.paired-account-binding/1`, with `accounts` entries
having `account_id`, `dedicated: true`, `interactive_use: false`. The route artifact
is `cairn.paired-route-binding/1`, with the plan hash and `routes` for CURRENT,
CAND, primary and secondary; each route declares account ID and exact model.
Actor models match the frozen native model; grader models match grading.

The quota policy is exactly `cairn.paired-quota-policy/1`, `units: ["calls",
"tokens"]`, `percent_conversion: false`. Each resource has `account_id`, `model`,
`window_id`, `unit`, nonnegative integer `remaining` and positive integer `required`.
Duplicate resource identities are refused. Remaining must cover required.

`demands` covers all four roles. Each demand names `resource_index` and positive
integer `max_units_per_attempt`; calls must use one. Every role contributes
cases × seeds attempts, so both graders are budgeted for every possible changed
pair. Required must equal the sum of these contributions for each resource.
Token bounds are declared upper bounds whose adequacy still needs independent
review. This contract does not establish that all applicable quota windows were
listed, or that provider quota can be reserved by these artifacts.

The reservation artifact is `cairn.paired-reservation/1`, with `plan_sha256`,
`resources_sha256`, held `state`, unexpired `valid_until`. It is a declaration;
a cached percent remaining, account alias or unsupported provider observation
cannot alone supply a trustworthy full-run admission decision.

## Corpus source preservation

`freeze_corpus(openings, policy)` accepts explicitly supplied whole, sanitized
source openings. It never searches or copies an operational memory store.
Each opening carries record UUID, positive version, exact UTF-8 body and body
hash, kind, shareable flag, active lifecycle, repository and file/symbol entities.
The result preserves accepted source IDs, versions and bodies. Caller objects
are copied; the output does not borrow their mutable lists or dictionaries.

The policy declares a description, excluded UUIDs, forbidden text and pinned
excluded artifacts. Exclusions match UUIDs and case-insensitive text. The
selection artifact records accepted metadata and excluded IDs/versions/reasons,
without excluded bodies. Its corpus hash and accepted identities must match the
frozen prospective corpus. Limits are 1024 openings, 65536 UTF-8 bytes per body,
8 MiB total body bytes and sixteen entities per opening.

Text matching is not a semantic contamination audit or proof of natural origin.
Authenticated source openings, eligibility, corpus-origin review and oracle
separation must come from independent reviewers. An empty synthetic test corpus
is not an admitted natural campaign corpus.

## Runner-owned workspace and export

`prepare_workspace(source, base_commit, actor, binding)` creates an empty private
repository and fetches only the exact full base revision from the local source,
without tags or FETCH_HEAD. It checks out a detached base and refuses any named
refs. It independently enumerates all Git objects and requires exact equality
with the base's reachable commit/tree/blob history. Other branches, tags,
unreachable objects, source configuration and worktrees are not cloned. It sets
`core.hooksPath=/dev/null` before the actor can run Git. It rejects committed
symlinks and submodules. It returns a private control
manifest binding request ID, occurrence ID, request hash and supervisor source
hash to the initial file snapshot, baseline and Git configuration hash. Store
that returned manifest outside actor-readable mounts. The source repository is
trusted operator input; this function is not an arbitrary repository sandbox.
The manifest records `initial_refs: {}`, `initial_objects_sha256` and
`initial_objects_count`; export rechecks the base object identity and ancestry.
Preparation may leave an incomplete private directory after a failure and
returns no successful control manifest. The controller owns reconciliation;
the function does not publish or start an actor while preparing files.

`export_work_product(prepared, output, allowed_paths=..., test_log=...,
verify_terminal=...)` requires trusted runner code as the terminal adapter. There
is deliberately no CLI that accepts terminal JSON as authentication. The adapter
receives the request binding and must verify actual wait status, containment
release, retention closure and its source proof, then return the closed
`cairn.work-product-terminal/1` shape checked by `closure` in the source. Zero
actual exit, released containment and closed retention are required; unknown,
failed or mismatched evidence prevents creation of a success export.

The supervisor adapter and actual process/cgroup/sink custody remain external.
Returning a test dictionary from a callback demonstrates validation only. It
never proves that a real actor stopped or that containment was released.

Export checks baseline ancestry, committed tree modes, initial Git configuration,
extra Git state, symlinks, hardlinks and special files. It refuses work outside the
explicit allowed paths and fails closed on directory-scan errors. It catches
committed changes restored in the working tree.
DESIGN.md and the selected test log must exist and be allowed. It records base and
final commit IDs, selected files including untracked products, added/deleted paths,
net base-to-working diff, base-to-HEAD committed patch and HEAD-to-working patch.
Each has a digest; DESIGN, test log, terminal and workspace digests are explicit.
Full Git configuration, author metadata, raw native streams and private control
artifacts are not exported. File contents and patches are operator-selected task
products and must themselves be reviewed for private content before publication.

Workspace files are at most 8 MiB each, total 64 MiB and 4096 entries. Git tree and
diff output is checked against 16 MiB after subprocess capture; Git operations
have a 20-second timeout. Git metadata has a 20000-entry guard. Selected files,
patches and metadata allowance must fit 64 MiB. These are logical refusal limits,
not hard peak-memory, disk, CPU or process-containment guarantees. The external
supervisor must supply the hard worker boundary. Actors must already be quiescent;
two snapshot checks detect some mutation but do not authenticate an immutable
filesystem snapshot. Partial export after an I/O failure is not success.

Success has schema `cairn.work-product-export/1`, status `exported_for_review`
and `task_acceptance: false`. Technical correctness, oracle validity and usefulness
are later independent dispositions. Fresh task evaluation supplements the weekly
CAIRN-107 instrument; it does not replace its complete paired runs or two-week
acceptance requirement.

## Verification

From the scripts directory:

```sh
python3 -B -m unittest -v test_trial_paired_capacity test_trial_paired_request test_trial_work_product test_trial_task_prospective
```

Tests use owned disposable inputs and local Git repositories, with explicit
constructed terminal evidence. The provider-free access test runs the existing
runtime sandbox with network isolation, mounts only the actor workspace and
tries actual reads of evaluator/oracle/corpus-origin/reviewer sibling fixtures.
If the host refuses the namespace operation, the test reports a skip; it does not
establish campaign isolation. A passing fixture establishes only that tested
mount arrangement, not the eventual provider mount plan. No provider launch or
production database is needed for these checks.
