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
python3 -m unittest scripts.test_inbox_recovery
make check
```

## Retained refused-settlement convenience cohort

At 2026-10-24 11:08 UTC, a read-only selector inspected the retained local
coordination journals and their sibling inbox contexts. It selected exactly 13
helper-recorded `completion.status=refused` operations. Each journal's
delivery, attempt and completion request matched its sibling inbox context,
which supplied the event ID: eight `HOLD_RELEASED` and five `STALE_SESSION`.
The selection is an owner-host convenience snapshot, not a sampled population
rate or a count of every provider failure. It contains twelve request
deliveries and one response delivery. The selected private evidence is SHA-256
`66a9517ddc58ee49233471a5117785414073ff2eda21a71d79a59fe765e7a3bd`.

A current-format workflow report was regenerated from the frozen 40-file
2026-10-21 through 2026-10-24 native-log manifest and the current retained
coordination tree. Its SHA-256 is
`a0048e9a8291e64b302ad7f6b32ebf865580e936c1153d071f6593970f8b333b`.
The recovery report joined all 13 selected deliveries and attempts, but no
provider turn for those attempts in that convenience log sample. It reports
`provider_turns=0`, `observed_responses=null`, and model work, operator action
and eventual recovery as unknown for all 13. Zero joined turns does **not**
mean zero model work. The private report SHA-256 is
`926e6b19d62b5bfffb686f29bca0b38e4fe1c781d2d69410bc9419a651d8807a`.
All three artifacts remain owner-only under
`~/.cache/cairn-token-audit-20261023/`; no IDs, bodies, commands or raw logs
were committed.

The selector is `scripts/select_refused_recovery_evidence.py`. It fails closed
on a missing or mismatched sibling context, ambiguous JSON, noncanonical IDs,
duplicate selected identity, malformed refusal code or unsafe output
directory. It exports only exact IDs and bounded codes to a create-new 0600
private file.
The unknown recovery/operator/model fields are intentional: the refused
journal does not establish those facts. Reproduce with the frozen private
manifest and commands in `docs/workflow-model-report.md`, then run the selector
and `docs/recovery-model-report.md` command with fresh private output names.

## Acceptance still open

The panel uses synthetic selected classifications. It does not join real
operation/attempt/phase transitions to native response IDs, prove actual
reminder ingestion, measure recovery time or token savings, or establish a
correctly settled live outcome. The existing production turn-end reminder
uses a persisted `inbox_ack_reminded` attempt marker and has one-reminder/no-loop
tests. A follow-up disposable lifecycle fixture verified the transition from an
unavailable compact batch source to an explicit `failed --code source_unavailable`
settlement, and the absence of a reminder after a stale lease. It caught and
fixed a reminder that previously suggested plain acknowledgement for the
unavailable source. The original retry or failed-settlement instructions now
appear in the single permitted reminder. Normal notice and response
acknowledgements remain covered by the existing tests. There is no general
per-revision runtime policy: attempt source versions are pinned, and
`state_revision` in the report is selected observation metadata. The relay
and provider failure paths still need selected exact evidence. CAIRN-226
remains open.
