# CAIRN-226 failure and reminder classification, 2026-10-24

## Implemented slice

The local workflow report now emits an opaque attempt reference from canonical
native or self-drain attempt IDs. The recovery report joins a provider turn
only when event, delivery and attempt all match. A delivery that matches but
belongs to another attempt stays unjoined. The report also requires an actual
turn reference before it counts a provider projection.

Selected recovery evidence may say `model_invocation=not_started`, `started`,
or `unknown` for the recipient turn of the exact attempt. A selected refusal
with `not_started` is classified as a recipient pre-model refusal. This does
not classify sender work. If model invocation is unknown and no exact provider turn
is joined, phase response count is null, not zero. An explicit no-model claim
contradicting an exactly joined provider turn is refused. Whole native turn
responses are counted once per phase, never allocated to the marginal Cairn
failure cost.

Repeated reminder emissions are grouped by exact attempt, state revision and
kind. A new revision is separate from a repeat of an unchanged revision.
These are retrospective diagnostics. No production wake, reminder, retry,
replay or ownership behavior was changed.

## Frozen local evaluation

`trials/recovery-model/FROZEN.json` fixes ten synthetic payload-free controls:
refusal before admission, refusal after grant, model-mediated source failure,
stale execution on a different attempt, unchanged and changed turn-end
reminders, uncertain relay reply, missing acknowledgement with self-drain, and
expired lease hold. Panel SHA-256:
`305c87dd267df559afa6d853c417de07a96d1b475f264c81c861c164f3b7024f`.

The local evaluator passed all ten expected exact-join and work-class checks.
It reported one repeated emitted reminder at an unchanged revision. Its
phase response counts were: before admission 0, grant 0, source 2, and null
for tool, recovery, reply and settlement because those phases contain
unjoined or unknown model work. The `source` value is a whole synthetic native
turn's two responses, not a failure-specific token charge.
The panel also fixed and passed native-turn and operator-action counts: one
joined turn in `source`, one in `settlement`, none in the other phases;
intervention for both refusals, stale execution and expired lease; manual
retry for source failure and relay uncertainty; self-drain for the recovered
acknowledgement; and no operator action for the three reminder observations.

Validation commands:

```sh
uv run --with tiktoken==0.12.0 python scripts/eval_recovery_model_panel.py
uv run --with tiktoken==0.12.0 python -m unittest discover -s scripts -p test_recovery_model_report.py
uv run --with tiktoken==0.12.0 python -m unittest discover -s scripts -p test_workflow_model_report.py
uv run --with tiktoken==0.12.0 python -m unittest \
  scripts.test_inbox_recovery.InboxRecovery.test_unacknowledged_turn_end_reminder_surfaces_exact_ack_and_avoids_looping \
  scripts.test_inbox_recovery.InboxRecovery.test_acknowledging_after_turn_end_reminder_completes_cleanly
make check
```

## Acceptance still open

The panel uses synthetic selected classifications. It does not join real
operation/attempt/phase transitions to native response IDs, prove actual
reminder ingestion, measure recovery time or token savings, or establish a
correctly settled live outcome. The existing production turn-end reminder
uses a persisted `inbox_ack_reminded` attempt marker and has one-reminder/no-loop
tests; this slice did not verify a new state transition, install a per-revision
runtime policy, or change its operator action. The relay and provider failure
paths still need selected exact evidence. CAIRN-226 remains open.
