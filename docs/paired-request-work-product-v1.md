# Offline paired requests and selected work products, version 1

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
messages. Argument syntax errors use standard argparse help. Success exits 0;
refusal exits 2. Callers must inspect both exit status and selected JSON.

The closed `cairn.paired-eval.request/1` object accepts these fields only:

| Field | Contract |
| --- | --- |
| `schema` | Exact request schema above. |
| `request_id`, `campaign_id`, `occurrence_id` | Canonical UUID strings. |
| `scheduled_at`, `expires_at` | UTC timestamps; the current time must be in the interval. |
| `frozen_input` | Absolute input `path` and exact `manifest_sha256` of `FROZEN.json`; existing prospective input validation must also pass. |
| `plan` | Exact `instrument`, frozen case IDs, `arms`, unique integer `seeds`, `attempts`, canonical `sha256`. Arms must be CURRENT and CAND; attempts equal cases × two arms × seeds. |
| `interfaces` | CURRENT and CAND each specify `api_revision`, `actor_base`, `components`, matching the frozen memory-arm declaration and actor baseline. Component file pins are rechecked. |
| `dispositions` | Exactly task, oracle, comparator, supervisor, native and integration. Each has a pinned `artifact`, exact `subject_sha256`, `reviewer_id`, accepted `state` and unexpired `valid_until`. |
| `limits` | Selected input 9500 bytes, final answer 8192 bytes, two native searches, four pulls, five hook seconds, thirty seconds for the last native memory call. |
| `capacity` | Pin of the whole-plan capacity declaration described below. |
| `corpus_selection` | Pin of the source identity and exclusion artifact, checked against the frozen corpus. |
| `grading` | Primary and secondary agent/model declarations; excluded agents; pinned rubric, changed-pair definition, blinding policy and review budget; dispute owner. |
| `output` | Absolute fresh private preparation output path. |
| `reply` | Event, recipient and correlation UUIDs, in fields `event_id`, `recipient_id`, `correlation_id`. Actual inbox authentication belongs to the integrator. |
| `supervisor_source` | Pin of the supervisor source artifact reviewed for this preparation. |

A file pin has exactly `path` and lowercase hexadecimal `sha256`. Artifact JSON
is checked against the same bytes that are parsed. Canonical JSON hashes sort
keys, preserve Unicode and use compact separators. JSON/input files are limited
to 8 MiB each; the input tree has a 4096-entry and 64 MiB preflight limit. Other
pinned files use a streamed check capped at 512 MiB. Frozen inputs and their
external referenced files are trusted, quiescent operator sources: preflight and
subsequent existing loader reads are not a filesystem snapshot or a hard memory
containment boundary.

Review subjects bind task to input.json, oracle to rubric, comparator to the
plan and interfaces, supervisor to its source pin, native to the native binary
and integration to the frozen manifest. Grader agents must differ and cannot
be any declared source reviewer, excluded agent or dispute owner. This verifies
declarations, not fresh-session identity, author exclusion completeness or actual
blinding. The integration reviewer must establish those facts independently.

## Whole-plan capacity declarations

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

`prepare_workspace(source, base_commit, actor, binding)` creates a standalone
local clone at an explicit full base revision, removes origin and checks out the
base. It rejects committed symlinks and submodules. It returns a private control
manifest binding request ID, occurrence ID, request hash and supervisor source
hash to the initial file snapshot, baseline and Git configuration hash. Store
that returned manifest outside actor-readable mounts. The source repository is
trusted operator input; this function is not an arbitrary repository sandbox.

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
explicit allowed paths, including committed changes restored in the working tree.
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
python3 -m unittest -v test_trial_paired_request test_trial_work_product test_trial_task_prospective
```

Tests use owned disposable inputs and local Git repositories, with explicit
constructed terminal evidence. The provider-free access test runs the existing
runtime sandbox with network isolation, mounts only the actor workspace and
tries actual reads of evaluator/oracle/corpus-origin/reviewer sibling fixtures.
If the host refuses the namespace operation, the test reports a skip; it does not
establish campaign isolation. A passing fixture establishes only that tested
mount arrangement, not the eventual provider mount plan. No provider launch or
production database is needed for these checks.
