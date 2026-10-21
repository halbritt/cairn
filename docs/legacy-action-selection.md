# Selected legacy action traces

`trial_legacy_blind_selection.convert` accepts the original
`cairn.legacy-blind-conversion/1` unchanged and an explicit `/2` extension. `/2`
adds one required field, `action_parser`:

```text
schema: cairn.legacy-action-parser/1
task_eval:        {path, bytes, sha256}
task_permissions: {path, bytes, sha256}
```

The pins select the exact source files for the already imported
`trial_task_eval` parsers and their `trial_task_permissions` helpers. The selected
bytes must equal those modules' current source bytes. `plan.evaluator_sha256`
must also match the selected task-eval source. Caller-selected code is never
imported or executed. These checks require trusted quiescent module/source files;
they are not Python runtime/import attestation. The legacy plan does not separately
attest historical helper bytes: the helper pin identifies this conversion's
selected dependency. Source drift requires an explicit compatible selection;
the converter never substitutes a newer parser silently.

The existing ordered `cairn.blind-case-contract/2` decides which channels appear.
`/2` adds support for `action_trace`; `/1` still refuses it. Both still refuse
`artifact_bundle` and `evaluator_observation` because original final-workspace/
evaluator custody is absent. Task statement and grading instructions remain
mandatory under the unchanged draft contract. An action-only evidence contract
can select those two plus action_trace, without final_answer. An answer-only
contract gets no action files. No changed pairs produce only receipt.json.
`/2` validates its parser selection even when no action channel is emitted.

## Exact projection and private subject

For each changed attempt the converter verifies the retained stream hash against
the original result record and verifies that record against the pinned agent.json.
It calls the existing `parse_stream(text, harness)` and, only for action channels,
`parse_tool_outputs(text, harness, require_completed=False)`. The public JSON is:

```text
schema: cairn.legacy-action-trace/1
commands:     the parse_stream commands list
tool_outputs: the parse_tool_outputs list of {command, output, is_error}
```

This preserves the original legacy grading input semantics. Commands can describe
attempts without successful completion. Failed outputs, repeated commands and
background results are not removed. In Codex, command deduplication and output
shell unwrapping differ between the two original parsers; the projection preserves
that difference. Unsupported malformed value types refuse instead of being coerced.
The canonical projection must fit the existing 32000-byte action/tool ceiling;
overflow refuses without truncation. No runtime limit or grading rule changes.
An otherwise valid attempt can exceed this allowance once commands, outputs and
the JSON envelope are combined. This slice does not prove every original case's
selected action channel fits.

Conversion retains `actions/<sample-id>.json`, `subjects/<sample-id>.json`, and
`observations/<sample-id>.json`. The private subject binds:

- exact blind-source attempt/pair identity, agent/plan/binding pins and result pin;
- stream path, bytes and digest; prompt bytes/digest and harness;
- selected parser-source pins and `legacy-retained-utf8/1` representation;
- custody explicitly labelled `harness_recorded_hash`.

The observation uses the existing `cairn.blind-evaluator-observation/2` shape,
binding the same source, action_trace channel, projection pin and subject pin.
Its required `evaluator_observation` role is a schema label for the projection,
not proof that an independent evaluator ran. The receipt retains these private
pins. Converter replay recomputes the subject/projection and checks exact output
bytes; the existing bundle repeats that verification. The draft additionally
checks its ordinary evidence/subject pins. No blind-selection/2, draft, bundle or
join schema was changed.
`trial_legacy_occurrence` already passes its pinned conversion_request through
to this converter; it does not construct or silently upgrade a `/1` request.

Only commands and command outputs are projected. Parsed memory events, write
paths, permission metadata, final answers, check logs and verdict bodies are not
added to an action channel. Answers appear separately only when selected. Raw
command/output text can itself contain sensitive or identifying information; the
owner's existing selected-material/release review still applies. This converter
does not redact command text or manufacture a privacy guarantee.

## Custody limits

Legacy run_agent retains stdout after UTF-8 replacement decoding and re-encoding.
The converter hashes the exact retained bytes, then uses the existing replacement
decode policy. Even a retained invalid-UTF8 synthetic stream uses that policy.
Neither the stream digest nor the derived JSON is claimed to be an original
subprocess-byte digest. No parsed JSON is substituted for the verified stream hash.

Pins establish correspondence to harness-recorded evidence, not independent
capture, actor containment, true command execution, or source authenticity. No
live workspace is inspected and no post-hoc artifact/evaluator evidence is made.
The legacy reader/draft retention bounds remain in force; this offline converter
does not establish full runtime physical fit. Original 21/126/1900, deadlines,
131072 raw bytes, 512 MiB combined working and 4 MiB traffic remain unchanged.
Actual per-case contracts/material selection and fresh independent grading remain
root-owned; implementation engineers are not graders.

## Synthetic validation

`test_trial_legacy_action_selection` covers action-only, answer-only and mixed
ordered channels through actual conversion/draft; Claude failures, repetition,
background and incomplete calls; Codex duplicate/output semantics; parser/plan,
stream, attempt and subject mismatch; overflow and missing channel refusal;
no-change receipt-only output; and 126 synthetic attempts through the unchanged
bundle. It neither starts actors/graders nor reads real cases. Run it alongside
`test_trial_legacy_blind_selection`, `test_trial_legacy_grading_bundle`, and
`test_trial_blind_packet_draft`. Do not run the broad task-eval calibration suite
as synthetic validation: it loads original cases.
