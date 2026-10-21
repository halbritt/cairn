# Original weekly compatibility correction

Status: source-only compatibility history; not runtime admission. The original
review selected source is
b1e16415's trial_task_eval.py, Git blob 6655a423fbfb16e438eb6bf39ef24c8890dca17e,
SHA256 4ad15e6a32765b96f842b7a3f1815e765b60a85ea771fa59ec1c31b325b0b087.
Complete retained run_agent, cmd_agent, prepare_workspace, TrialStore,
import_provenance and distractor units establish the following contract.

Current-source reconciliation at `04ba225` (2026-10-21): the workspace and
corpus owners described below now exist. The earlier selected source remains
historical evidence, not the current implementation. See
[original attempt custody](original-attempt-custody.md) for source locations,
remaining final-artifact/evaluator joins and missing per-case channel metadata.

## Implemented joins

The legacy run ID is case.arm.sSEED, including on failed/not-started rows. It
names the MCP run and task-eval/<run> task. It is not a UUID. The owned schedule
now retains that ID separately from the occurrence/schedule/index-derived SDK
session UUID. The exact mapping is sealed and rechecked before move and export.
The same row checks copied prompt, actual compiled model and effort, and held
MCP arguments against service policy. Failure consumes selection and keeps the
original ticket/cleanup owner. SDK review accepts weekly ID syntax; only the
selected owner establishes membership. Generic native supplemental syntax stays
unchanged, as do paired request /2 and original limits.

The original cmd_agent writes resolved notes alone using json.dumps(indent=2,
ensure_ascii=False) plus final LF before calling grow. The candidate retains
these exact selected bytes separately from the canonical notes+distractors
selected overlay, canonical resolved-corpus digest and original frozen digest.
None is an actual load observation. The live observed digest remains null.
Supersede_with and other note fields survive serialization unchanged.

The weekly loader already uses explicit root/version for cases, notes and
workspace paths. Its 21-case, three-seed, two-arm schedule has 126 rows; each
pair uses the original seeded shuffle. Its explicit seed list is a selected
interface distinct from the legacy CLI's contiguous range. Distractor generation
is exactly 1900 with seed 20260928; grow(target,0) means start index zero, not
seed zero. The original seven-field provider plan is unchanged.

## Remaining actual execution seams

Do not call legacy cmd_agent/run_agent directly as the weekly adapter. Their
nonprospective load_cases/load_corpus calls select default latest, and
prepare_workspace resolves default TRIAL. The loader's selected workspace_paths
alone are not an owned setup execution capability.
`WeeklyWorkspaceOwner.prepare` in `trial_sdk_weekly_owners.py` now consumes the
held selected case/root/version, checks its manifest before and after setup,
and calls `prepare_workspace` with an explicit `source_root`. That function
copies excluding root setup.sh and __pycache__, uses the fixed Git identity/date,
checks optional workspace_commit, tags eval-base and retains the initial snapshot.
The owner also retains HEAD and original refs; `verify_preparation` and
`WeeklyEnvironmentOwner.bind_run` join the workspace digest and actual cwd to
SDK preparation. These source joins have synthetic tests, not original setup or
campaign evidence. Final artifact/index/ref capture and evaluator custody remain
separate from the initial workspace binding.
Memory instruction parity and selected native binary/arm engine/hook/bridge and
embedding configuration identity still need actual held launch/source joins.
Model/effort binding does not discharge these obligations.

`WeeklyStoreOwner.prepare` now preserves TrialStore.remember's fixture-ID to actual
record-ID maps (both directions), entity-association verification, provenance,
seed_corpus's import-then-supersede transitions, distractor growth, and shared
embedding readiness before warm timing. It retains service load results
and relates selected inputs to those IDs and same-store pre/post observations.
corpus-fingerprint/2 contains generated IDs/times and compares a DB to itself;
it is neither a selected-overlay digest nor a cross-import equivalence proof.
The historical baseline killed TrialStore on timeout without waiting again.
Current `TrialStore.stop` waits after kill; if that second wait also times out,
it raises and retains the server handle and socket for recovery. The SDK weekly
store owner also performs a second wait and retains unknown cleanup. These
checks observe the selected service process, not complete descendant closure.
`WeeklyEnvironmentOwner` owns cluster/store loading and joins its actual store
policy into the fixed run. These source mechanisms and synthetic observations
do not establish actual original corpus import or containment release.

`test_trial_sdk_weekly_owners.py` exercises the import/supersede/entity/readiness
and workspace handoff boundaries with synthetic service/process responses,
including partial setup/import and cleanup uncertainty. Do not implement a
second flat loader or constructor in place of these owners. The remaining
attempt-content lane needs selected per-case release contracts and final
workspace/action/evaluator custody. Selected native compatibility, complete
physical bounds, containment export, blind comparison and both grader joins
remain full-goal requirements.

## Validation and resource scope

Synthetic 21-case inputs exercise the real _checked and companion validation,
source selection, publication, SDK preparation, owned move and evidence export.
Native edges are inert, process/network/signal effects denied. No actual cases,
oracles, provider calls, store or grading runs are used. Tests compare the exact
legacy corpus bytes, preserve the future-overlay exclusion, keep all 126 rows,
and reject model, effort and MCP task substitution before move. They do not
prove actual weekly task completion or store/runtime compatibility.

The faithful JSON serializer is not bounded per token: Unicode input, escaped
string token and UTF-8 encoded token can overlap before FILE_LIMIT refusal.
There is no new per-note limit and no fixed-scratch claim. Complete validation
precedes destination creation; interrupted output still belongs to the snapshot
and original preparation failure charge. Parsed notes/metadata, source snapshots,
retained exception frames and allocator/kernel/internal storage remain unbounded
by the current physical proof. The historical 56-row phase table describes its
selected fixture, including the caller-selected 65536 preparation raw quote
and 136353/153325 totals. Those totals are not a universal current launch
refusal: `trial_sdk_source_profile.py` supplies the current named preparation
profile, and integrated synthetic native launch/dispatch is exercised under the
original caps. See [SDK launch and hosted retrieval](sdk-native-launch.md) for
the current allocations and limitations. Logical resource admission and these
synthetic tests do not establish physical fit or weekly campaign acceptance.
