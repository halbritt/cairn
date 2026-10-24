# Local recovery model report

`scripts/recovery_model_report.py` joins a local
`cairn.workflow-model-report/1` file with selected, payload-free recovery
observations. It groups exact failure phases and counts a joined provider turn
once per phase even when multiple deliveries share it. Missing provider evidence
remains unknown. The report does not infer a failure from a log substring,
attribute all turn usage to Cairn, or turn a refused wake into a zero-token run.

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
      "state_revision": 2,
      "reminder_emitted": false
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
emission for one attempt and revision is reported as a diagnostic. Use a null
delivery ID only for a phase with no selected delivery identity, such as a
pre-admission refusal. Never infer a pre-model refusal solely from absent logs.

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
joined whole native turns; compare matched attempts, operation identities,
model route and task outcomes before making a cost or recovery-time claim.

This first read-only analysis slice does not change wake, reminder or replay
behavior. CAIRN-226 still requires exact failure instrumentation, bounded
state-aware reminders, fixture coverage and accepted recovery outcomes.
