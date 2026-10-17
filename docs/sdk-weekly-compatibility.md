# Original weekly compatibility correction

Status: source-only candidate; DO_NOT_ADOPT for runtime. The selected source is
b1e16415's trial_task_eval.py, Git blob 6655a423fbfb16e438eb6bf39ef24c8890dca17e,
SHA256 4ad15e6a32765b96f842b7a3f1815e765b60a85ea771fa59ec1c31b325b0b087.
Complete retained run_agent, cmd_agent, prepare_workspace, TrialStore,
import_provenance and distractor units establish the following contract.

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
are not an owned setup execution capability. A source-owned workspace constructor
must consume the selected case/root/version, copy excluding root setup.sh and
__pycache__, run the selected setup with original fixed Git identity/date,
check requested workspace_commit, tag eval-base, and retain the exact initial
snapshot. It must bind that actual workspace result to SDK preparation; a
workspace_sha256 declaration alone does not do so. No setup/bash/git was run.
Memory instruction parity and selected native binary/arm engine/hook/bridge and
embedding configuration identity still need actual held launch/source joins.
Model/effort binding does not discharge these obligations.

An actual corpus owner must preserve TrialStore.remember's fixture-ID to actual
record-ID maps (both directions), entity-association verification, provenance,
seed_corpus's import-then-supersede transitions, distractor growth, and shared
embedding readiness before warm timing. It must own actual service load results
and relate selected bytes to those IDs and same-store pre/post observations.
corpus-fingerprint/2 contains generated IDs/times and compares a DB to itself;
it is neither a selected-overlay digest nor a cross-import equivalence proof.
TrialStore.stop kills on timeout without waiting again; preserve that as legacy
behavior to correct in an owned lifecycle, not containment-release evidence.
FixedServiceLaunch/RuntimeBinding currently own policy/config/processes, not
this corpus loading contract. This is authored implementation work, not an
owner-permission barrier or justification for another SDK acquisition.

The next critical source lane is that corpus-load owner and its workspace/setup
handoff, tested with synthetic service responses at the actual import/supersede/
entity/readiness boundary. It must retain uncertainty and real cleanup authority;
no caller success boolean or flat seed-only loader can stand in for it. Supported
native launch, complete physical bounds/standing admission, containment exporter,
blind comparison and both grader joins remain additional full-goal requirements.

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
by the current physical proof. All 56 phase-table physical bounds remain unknown;
transitions stay disabled. Raw reservations remain 136353/153325 against 131072.
