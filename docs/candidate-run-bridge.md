# Candidate preparation to weekly runner

`scripts/trial_candidate_run_bridge.py` connects one retained original-weekly
candidate preparation to the separately selected weekly runner. It does not
change `trial_weekly_run.py`, `trial_task_eval.py`, request/2, grading, report
policy or two-week aggregation. The default runner still owns executor
availability, original limits, capacity, account selection and live selection.

The CLI consumes an explicitly pinned selection document:

```json
{
  "schema": "cairn.candidate-run-bridge/1",
  "entry": {"path": "/private/queue/REQUEST/entry.json", "sha256": "..."},
  "prepared": {"path": "/private/queue/REQUEST/prepared.json", "sha256": "..."},
  "runner_profile": {"path": "/private/selected-runner.json", "sha256": "..."}
}
```

These are structural placeholders, not executable credentials or an admission
example. The operator supplies real pins from the already prepared candidate
and selected runner profile. The latter explicitly pins `harness_commit`:
candidate request/1 has no harness-commit field, so the bridge cannot infer one
from the branch or current main. These held inputs remain declarations; matching
them does not authenticate a reviewer or create owner authority.

```sh
python3 -B scripts/trial_candidate_run_bridge.py \
  --selection /private/bridge-selection.json --sha256 SELECTED_SHA256 \
  --output /private/bridge-run
```

The output's parent must exist. Inputs must be private, owner-held and quiescent,
as required by the preparation APIs. Output cannot overlap the candidate entry,
weekly source root, runner output root or selected control files. Existing Git
replacement refs and an alternative replacement namespace refuse: the runner's
ordinary Git export must not substitute another tree behind a reviewed OID.

Before writing an intent, the bridge verifies the selected entry/preparation
pins, all recorded draft and retained preparation files, the exact prepared
request/dispositions and their referenced control pins. It binds the selected
companion/review to the retained provider evidence, including each retained
input's source hash. It
checks campaign, original request/occurrence IDs, the exact active slot and
expiration, current branch resolutions, arm pins, plan, frozen-source identity,
actor model and embedding worker. A stale ref or wrong slot refuses before
bridge or runner mutation. An existing runner claim must also match the selected
commits, harness, executor, capacity receipt and live selection.

Expiry is inherited from the verified preparation contract, not a fresh review.
`trial_candidate_request.prepare` calls `trial_weekly_request._checked`, which
calls `validate_companion`. That validator requires companion, review, mapping,
capacity, spend binding and price quotes to satisfy
`observed_at <= scheduled_at <= preparation_now < spend_end <= valid_until`,
with `request.expires_at <= spend_end`. Each disposition must also cover
`spend_end`. Thus unchanged properly prepared evidence and the bridge's exact
`scheduled_at <= now < expires_at` slot imply current interval validity. The
bridge does not rerun provider projection or preparation, open case contents to
rebuild review subjects, or claim that these caller-held receipts authenticate
the preparation process or reviewer. A substituted selected review cannot
borrow the retained preparation's validity. Changing all pins and fabricating
a preparation remains outside this owner-held input contract.

The two namespaces remain explicit in `binding`:

- `candidate` holds the original request/occurrence IDs, request hash, full
  weekly-schedule hash and its file hash.
- `runner` holds the runner slot/request ID, its occurrence ID and the hash of
  its planned `{run_id, case, arm, seed}` list.
- The full candidate schedule is projected to that list and compared exactly;
  the original hashes are never equated or replaced. All126 identities remain.

The call to `trial_weekly_run.run` uses immutable commit OIDs after verifying
the original named refs. Default executor construction receives the selected
`account` from the runner profile; missing accounts and mismatched capacity
accounts retain the runner's refusals. The bridge delegates runner checks unchanged. A guarded
executor rechecks retained inputs, full planned identities and the harness
commit and selected account, then verifies the rebuilt binary/hook/bridge hashes against the reviewed
candidate artifacts before calling the actual selected executor. A changed
compiler output refuses execution; the runner conservatively retains its
execution-started marker and incomplete result.

Output contains an exclusive `intent.json` and, when the runner returns a
retained receipt, `receipt.json`. The latter binds that exact receipt, the
runner spec and, for a complete result, the pair-binding request/occurrence and
schedule hashes. It never grants runtime admission. Complete cached results
are reverified against the retained runner receipt and spec; editing a bridge
status cannot upgrade an incomplete result.

The bridge serializes its output through the existing directory-lock helper.
The runner remains the execution owner: a lost return after it writes a receipt
can recover that receipt without re-executing; an execution with no terminal
receipt remains held. The bridge never removes runner state or automatically
replays an uncertain executor. Resuming still requires the original valid
selection window; a historical receipt can be read without running the bridge.

## Original execution contract

The runner profile's `execution` pin must equal the reviewed request's
`weekly_input.execution`. Otherwise the bridge refuses
`bridge_execution_mismatch` before it writes anything. A stored claim must
carry that same pin. Before the effect, the runner spec's declared native
binary pin, model and effort must equal the reviewed declaration. A complete
runner receipt must report observed native SHA-256 and effort equal to the
declared ones (`bridge_runner_execution_mismatch`). The recall and
embedding-config controls stay named runner refusals. See
[the runner reconciliation](weekly-runner-reconciliation.md).

## Boundaries requiring later integration

This binds selected identities and retained artifacts. It does not establish
the selected native executable/protocol, fresh grader execution, actual account
capacity, original-case channel compatibility or full physical fit. At runner
revision5b52a0f, original limits, retained baseline verification and selected-account routing are implemented;
their actual observations and account capacity still belong to the runner's
checks. This bridge does not upgrade declared account identity to authenticated
dedicated-account evidence. Agent501 owns runner changes.

The retained original `execution` pin also includes native binary/effort,
embedding config/model and recall controls. The current runner spec exposes only
part of that contract. The bridge preserves the pin; it does not silently claim
that the legacy executor consumes every field. If root selects exact original
execution equivalence, the precise runner seam is `_prepare`'s spec and the
selected executor's `check`/`execute`: carry and enforce those selected fields
there, using the existing owner machinery. Do not add caller booleans or use
this bridge receipt as evidence that this remaining runtime join is complete.
No runner/evaluator edit is included here.

All original limits and21/126/1900 criteria remain unchanged. The bridge is
control-plane source code and does not establish a new runtime allocation.
Two-week evidence binding and post-attempt orchestration are separate lanes.
Observed grader coverage and owner routing remain separate from unresolved
adjudication; no renderer policy is changed.

## Synthetic verification

The tests use the real candidate request/preparation and real weekly runner
with an inert compiler, synthetic frozen fixtures and a fake harness/executor.
They cover a complete126-attempt handoff, separate identity/hash namespaces,
ref/slot/artifact/preparation drift, substituted Git objects, a mismatched
existing slot, changed compiler output, lost returns, unknown completion,
receipt tampering, selected-account construction and spec drift, and the exact
retained-review expiry contract. The shared fake harness emits retained
synthetic baselines: all126 must verify, and tampering retains the runner's
terminal rejection without a pair binding. No provider or native vendor is used. Run only this class:

```sh
cd scripts && python3 -B -m unittest test_trial_candidate_run_bridge.BridgeTests
```

The fixture module is imported as a module, not as a TestCase class, so unittest
does not also discover its optional PostgreSQL smoke tests.
