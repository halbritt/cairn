# Local workflow model report

`scripts/workflow_model_report.py` joins retained Codex or Claude provider usage
to Cairn native inbox contexts and committed command journals. It is read-only.
It writes an owner-only JSON report (`cairn.workflow-model-report/1`) with
opaque event, delivery, and turn references. It does not emit source bodies,
prompts, credentials, native file paths, or model output.

Run it with a private frozen native log manifest. This is the same list of
`{home, harness, path}` entries accepted by `audit_model_overhead.py`. An
optional `binding` and `pin` on each entry make attribution more precise. The
report matches an inbox context to a provider turn only when its turn ID is
unique in the selected logs. A binding match is recorded separately from a
unique turn ID without a binding match. Ambiguous and missing matches remain
unjoined. The selected coordination tree is read as it exists when the command
runs; the provider window applies only to native logs.
Each retained delivery carries an opaque attempt reference when its context
contains a canonical native UUID or `drain-` UUID. Missing or malformed
attempts remain null and are counted in `coverage.attempt_unknown`. The
recovery report requires the same event, delivery and attempt before joining
model work. Older reports without attempt references remain usable for other
dimensions but cannot prove that join.

Each delivery also reports `intent_journal` as `present` or `missing`, with
matching `coverage.intent_journal_present` and
`coverage.intent_journal_missing` counts. A present journal with no completion
data remains distinct from an absent journal; neither state supplies evidence
of provider work or a zero-response turn.

```sh
uv run --with tiktoken==0.12.0 python scripts/workflow_model_report.py \
  --since 2026-10-21T00:00:00Z --until 2026-10-24T00:00:00Z \
  --manifest "$HOME/.cache/cairn-token-audit-20261023/sample-manifest.json" \
  --coordination-state "$HOME/.local/share/cairn/coordination/state" \
  --max-deliveries 10000 \
  --output "$HOME/.cache/cairn-token-audit-20261023/workflows.json"
```

The optional `--event-metadata` file supplies selected **metadata only** from an
authorized source. It can add sender publication state, an exact sender native
turn, response causation, workload label, bounded refusal code, joined owner
input, wake state, retry count, and an explicit task assessment. These optional
classifications remain unknown when no selected evidence supplies them. This
file does not authorize access to another principal's events.
Use `cairn.workflow-links/1` with `events` and `assessments` arrays:

```json
{
  "schema": "cairn.workflow-links/1",
  "events": [
    {
      "event_id": "11111111-1111-4111-8111-111111111111",
      "kind": "request",
      "publication_state": "committed",
      "sender_turn_id": "native-turn-id",
      "sender_binding": "codex-three",
      "workload": "small_request",
      "owner_input_joined": false,
      "wake_state": "started",
      "retry_count": 0
    },
    {
      "event_id": "22222222-2222-4222-8222-222222222222",
      "kind": "response",
      "causation_id": "11111111-1111-4111-8111-111111111111",
      "publication_state": "committed"
    }
  ],
  "assessments": [
    {"event_id": "11111111-1111-4111-8111-111111111111", "outcome": "accepted"}
  ]
}
```

Keep both input files and the report private. A committed `ack` or `complete`
proves only that the command was retained; the report marks task acceptance
only from an explicit assessment. Provider usage belongs to a whole response
or native turn, not necessarily Cairn alone. A turn shared by deliveries is
marked `shared_turn`; its tokens must not be summed once per event. Each token
field has `known_sum`, `missing_records`, and `total`, where `total` is null
until all observed response records contain that field. Codex response coverage
comes from usage records only. Unknown Claude thinking remains null. Unsupported
harnesses and paths without a joined trace appear in `unsupported`.
If the selected log files repeat a provider response ID, affected token totals
are null and coverage reports the duplicate. Their `known_sum` fields remain
diagnostic only.
`coverage_by_dimension` groups sender and recipient observations by harness,
account, model, effort, pin and selected workload. Its counts describe coverage;
they do not add shared-turn tokens into an event cost.

This is a measurement input, not a deployment or savings claim. Compare paired
manifests with fixed workload, model, effort, route, pins and task assessment;
preserve failed and missing-usage runs. The report does not recover complete
Claude request allocation from missing blobs, infer owner-input joins from
turn proximity, or attribute remote relay costs without selected evidence.
