# Local recovery model report

`scripts/recovery_model_report.py` joins a local
`cairn.workflow-model-report/1` file with selected, payload-free recovery
observations. The `cairn.recovery-model-report/2` result requires exact event,
delivery **and attempt** identity to join a provider turn, and counts a joined
turn once per phase even when multiple observations share it. Missing provider
evidence remains unknown. The report does not infer a failure from a log substring,
attribute all turn usage to Cairn, or turn a refused wake into a zero-token run.

For a bounded, read-only cohort of helper-recorded refused settlement
operations, `scripts/select_refused_recovery_evidence.py` reads retained local
`*/intents/*.json` journals and requires their delivery, attempt and
completion-request identities to match the sibling inbox context. The context
supplies the event ID; the journal does not carry it. The selector emits a
private 0600 evidence file with unknown model, operator and recovery
classifications. Its output is a retained
state selection, not proof that a recipient model did or did not run:

```sh
install -d -m 700 "$HOME/.cache/cairn-recovery"
python3 -B scripts/select_refused_recovery_evidence.py \
  --state-root "$HOME/.local/share/cairn/coordination/state" \
  --output "$HOME/.cache/cairn-recovery/recovery-evidence-new.json"
```

The output directory must be private and the output file must not exist yet.
Use a current-format workflow report with attempt references before joining.

Prepare a private `cairn.recovery-evidence/1` JSON file from an authorized
exact operation, attempt, and state observation. For example:

```json
{
  "schema": "cairn.recovery-evidence/1",
  "observations": [
    {
      "event_id": "11111111-1111-4111-8111-111111111111",
      "delivery_id": "22222222-2222-4222-8222-222222222222",
      "attempt_id": "33333333-3333-4333-8333-333333333333",
      "phase": "settlement",
      "outcome": "failed",
      "code": "API_CONNECTION_FAILED",
      "recovery_state": "retry_same_intent",
      "operator_action": "manual_retry",
      "model_invocation": "started",
      "state_revision": 2,
      "reminder_emitted": false,
      "reminder_kind": "recovery_failure"
    }
  ]
}
```

Allowed phases are `before_admission`, `admission`, `grant`, `source`, `tool`,
`settlement`, `reply`, and `recovery`. Every row needs an observed outcome
(`refused`, `failed`, `held`, or `recovered`), recovery state (`pending`,
`retry_same_intent`, `held`, `committed`, or `unknown`), and operator action
(`none`, `manual_retry`, `self_drain`, `intervention`, or `unknown`). A reminder
observation needs its exact attempt ID and observed state revision. A repeated
emission for one attempt, revision and reminder kind is reported as a diagnostic.
Different reminder kinds may be retained as separate observations for the same
event, delivery, attempt, phase, and revision; repeating the same kind at that
identity is still rejected as a duplicate.
`model_invocation` describes the recipient native model turn for this exact
attempt, not any sender work. It is an explicit selected observation: `not_started`,
`started`, or `unknown` (the default). Only `not_started` with an observed
refusal proves a pre-model refusal in this report; missing native logs alone
leave model work unknown. `reminder_kind` is `turn_end`, `waiting_work`,
`recovery_failure`, or `unspecified` (the default). Use a null
delivery ID only for a phase with no selected delivery identity, such as a
pre-admission refusal. Never infer a pre-model refusal solely from absent logs.
For later phases, `attempt_id` is required. Old workflow reports without the
opaque attempt field cannot establish a provider join.

```sh
uv run --with tiktoken==0.12.0 python scripts/recovery_model_report.py \
  --workflow-report "$HOME/.cache/cairn-token-audit-20261023/workflows.json" \
  --recovery-evidence "$HOME/.cache/cairn-token-audit-20261023/recovery-evidence.json" \
  --output "$HOME/.cache/cairn-token-audit-20261023/recovery-report.json"
```

The output file is created with owner-only permissions and cannot overwrite an
existing file. It contains opaque event, delivery, attempt and turn references,
bounded machine codes and classifications, not payloads or native file paths.
Keep the input and output private. A phase's `observed_responses` is a count of
joined whole native turns. It is **null** when any selected observation in that
phase has unknown provider work; zero requires explicit no-model evidence or
an exactly joined turn with zero responses. Compare matched attempts, operation identities,
model route and task outcomes before making a cost or recovery-time claim.

The historical v1 classification panel is `trials/recovery-model/FROZEN.json`;
its bytes and hash are preserved for earlier receipts. The current v2 panel is
`trials/recovery-model/FROZEN-v2.json`; run
`uv run --with tiktoken==0.12.0 python scripts/eval_recovery_model_panel.py`.
The evaluator accepts either version and checks that each exact failure code,
recovery state, operator action and reminder observation survive into the
report. V2 adds synthetic cases for a transient provider failure and a joined
owner prompt. The provider-failure case exercises the exact synthetic
event/delivery/attempt join and keeps its two synthetic responses associated
with that attempt; its turn reference is a fixture value, not an observed native
turn. The owner-prompt fixture counts a separate synthetic native-turn
reference and leaves provider responses unknown, so it is not charged to the
refused delivery. The panel's expected values are controls in the same fixture,
not independent observations. Both versions make no provider call and do not
measure production reminder deduplication or accepted recovery. CAIRN-226 still
requires selected live failure evidence and the CAIRN-214 usage join before
making model-work or token-savings claims.

`scripts/check_codex_compact_native.py` also emits a bounded
`cairn.recovery-model-report/3` fixture report from a live disposable Store
event. After the observed Codex turn claims the delivery, the fixture submits
one acknowledgement with a deliberately invalid lease, verifies `STALE_LEASE`
and confirms that the delivery remains leased with one attempt, then handles it
through the valid compact handle. The adapter requires exact event, delivery,
Store attempt and observed native-turn identity across refusal and settlement.
It emits only opaque delivery/attempt/turn references, the bounded refusal
code and final disposition. `provider_turn.responses` and usage are null:
the Codex endpoint is a scripted loopback and does not establish provider
usage. The wrong-lease call is a fencing control, not an incident sample or a
measurement of recovery work. `scripts/test_codex_recovery_fixture_report.py`
checks mismatch refusal and null provider accounting without retaining payloads.
Reproduce it with a private, offline candidate stage and a disposable
PostgreSQL cluster:

```sh
python3 scripts/stage_token_efficiency_canary.py \
  --destination "$HOME/.cache/cairn-token-efficiency-stages/recovery-probe" \
  --codex-only --profile compact-inbox --snapshot-current-codex-config
python3 -B scripts/check_codex_recovery_fixture_disposable.py \
  --candidate-root "$HOME/.cache/cairn-token-efficiency-stages/recovery-probe"
```

The runner binds PostgreSQL to a private Unix socket with TCP disabled and
Codex to the loopback scripted Responses endpoint. It prints only the
redacted report. This probe exercises refusal and same-attempt recovery in the
Store and observes a real Codex turn boundary, but it does not make a provider
call, measure model response count, sample an incident, or assess task outcome.
Verified on 2026-10-24 with the installed Codex 0.164 app-server: the exact
`STALE_LEASE` refusal left one leased Store attempt; the same attempt then
settled handled once. The report contained the opaque join references and
unknown provider response/usage fields described above.

The broad `make test-integration` run did not pass: it stopped at or near the
notice-watcher leg. The retained test root was gone before the exact assertion
could be recovered, so the failure attribution remains unknown. That leg's
unconditional 60-second routine-notice pilot is under correction in CAIRN-220;
this CAIRN-226 result does not claim a broad integration pass.
