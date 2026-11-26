# CAIRN-227 synthetic paired-task panel

`FIXTURE.json` is the prospective source/task contract for the 22 situations in
the token-efficiency plan. Its `acceptance_check` values are private grading
criteria. They are not accepted outcomes. The fixture contains synthetic data
only. Source IDs, source order, task wording, event bodies, transition labels,
and acceptance checks are held identical across the four proposed arms.

Run the provider-free validator from the repository root:

```sh
python3 -B scripts/preflight_token_efficiency_panel.py
```

For an export, first create the private output parent with mode `0700`:

```sh
install -d -m 700 "$HOME/.cache/cairn-token-efficiency-panels"
python3 -B scripts/preflight_token_efficiency_panel.py \
  --output "$HOME/.cache/cairn-token-efficiency-panels/review-001"
```

The requested output directory must be new. Every generated directory is
owner-only (`0700`); every JSON file is create-new and owner-only (`0600`).
`actor/<case>.json` contains exactly `schema`, `case`, and `task`. It has no
setup, acceptance check, expected answer, or source body. `controller.json`
contains exact source bodies, source metadata, body and full record hashes,
actor/setup/check hashes, the
declarative setup and hidden checks, and 88 prospective case/arm workflow IDs.
The four arm rows for a case carry identical actor, setup, and check hashes.

The controller must materialize source records and event deliveries from
`controller.json` for each run and verify their exact hashes. Source order is
the array order. `events[].id` is a local fixture label, not a Cairn event ID.
Every source has one common synthetic Cairn repository scope. `project` is a
relevance label in the synthetic content, so the Trail and Cairn Atlas notes
can both be candidates in the shared collection. Ordinary sources use native
`create` or `edit` kinds; `record_family` and `logical_version` bind the two
same-record corrections. The two required conflict members are C instructions
issued under a symbolic root grant in a disposable store, both mandatory with
the same policy key; the controller must verify that the store opens the
conflict. No grant UUID or real authority is carried by this fixture. Event
bodies are prospective shareable ordinary note sources in that same disposable
scope before publication; a controller must bind each published event to its
created record/version and verify the receipt.
`transitions` name failure and timing conditions the future controller must
actually induce and verify; a label alone proves nothing. The `repeat_source`
transition asks the controller to place 120 copies of the historical relay
body before the current qualification. The actor receives only its task and
the native task/event delivery appropriate to that case. A runner must confine
the actor so it cannot read the repository fixture or `controller.json`.
`duplicate_send` declares one request event and a second publish attempt using
the same idempotency key. The symbolic key must map to one native request UUID
within that run. Event setup itself has no notice policy. The separate
`arm_interventions` map applies a version 1, 30-second routine notice deferral
only to the burst case's batch and combined arms; baseline and envelope publish
those notices without an opt-in deferral. Its intervention hash is distinct
from the shared actor/setup/check hashes. The urgent request is prioritized by
its native request kind.
`actionable_response` declares a response causally linked to its preceding
request; its symbolic causation label must resolve to that native event ID.
`unfinished_handoff` declares an ordinary resumed
task with saved source state; it does not invent a native handoff event kind.
The required-source conflict may correctly refuse before delivering either
body, so its acceptance check allows that bounded refusal.
`busy_owner_task` declares a small parser review in `setup.active_task`. The
controller export freezes the exact source body, task target, and pause
boundary. The actor export contains only the review instruction; a future
runner must stage `parser_review.md` in an isolated actor workspace without
the controller manifest or hidden `acceptance_check`. The sample has a
duplicate field, and the hidden check grades the review's conclusion as well
as its order relative to the routine notice. The preflight rejects a copied
grading clause in the actor instruction or source, but cannot detect every
paraphrase. The proposed pause is before the first source-read reply: a runner
must own that reply, observe the native turn, publish the notice while the
review is active, verify pending state and absent readiness, then release the
source and observe the review conclusion before notice handling. This runner
and pause are not implemented by the preflight or Store probe.

The [memory lifecycle probe](../../scripts/check_panel_memory_lifecycle.py)
checks the real CLI/API search and pull surface on a disposable database for
wrong-project source ranking, then checks stale-handle refusal and exact
retained history after the `corrected_note` and `meaningful_owner_correction`
edits. Project labels are fixture metadata, not Store-enforced project scope;
actor decisions remain unrun.
The disposable controller materializes `wrong_project_memory`,
`corrected_note`, `meaningful_owner_correction`, and `unfinished_handoff` in all four arms. It creates
the exact shareable sources through the authenticated API, applies each
correction under the same Store record, verifies exact version history, and
retains only source identities and hashes in its receipt. Correction receipts
mark the verified lineage as source setup only and set the actor runtime
transition to unrun. These are self-claimed A records; setup coverage does not
authenticate the owner or grade project distinctions, current endpoints, or
correction applicability.
The controller also materializes the exact `lexical_anchor` for
`semantic_unavailable` in all four arms. It verifies source identity and
history, but carries `before_search/disable_semantic` as unrun and marks
semantic fallback and whole-source pull unrun in those per-arm receipts. The
separate local-worker probe below remains the only authenticated search/fallback
and whole-pull evidence; neither setup establishes actor acceptance.
For `unfinished_handoff`, the worker's registered session searches for the
exact source, performs a whole-source pull, and reads its retained history.
The receipt stores only record/version identifiers, hashes, extent, and writer
identity. It explicitly marks the frozen `before_resume/pause_after_source_check`
transition unrun and keeps actor acceptance unassessed. This prepares source
authority and recall only; it does not pause or resume a native model turn or
prove the remaining report was written. The controller launches no actor and no
provider; the release gate remains deferred.

The [stale journal probe](../../scripts/check_panel_stale_journal.py)
exercises a disposable CLI/API and native helper path for `stale_execution`: a
pending old-execution completion is refused after reviewed reissue and execution
rotation while the current execution can settle the successor. Its host turn
and result text are synthetic, so it does not grade an actor task.

The [process-crash child probe](../../scripts/check_panel_process_crash_child.py)
kills a disposable native-claim child with `SIGKILL` after lease acquisition
and verifies explicit operator reconciliation of its attempt.

The [two-notice compact probe](../../scripts/check_panel_compaction_settlement.py)
uses the actual bound hooks and compact helper around two synthetic compact
callbacks. It checks a leased first notice through compaction, commits its
settlement once, then restores and settles a different notice without replaying
the first. Host queue admission, model compaction and task outcome remain unrun.

`unchanged_capture` declares a separate controller-only synthetic dialogue and
first selection in `setup.capture`; the controller does not yet execute it, and
the hook's persisted state, actual provider selection, and task outcome remain
unrun.

**Current preflight boundary:** the Python validator checks the fixture and
export as data. It never inserts records, issues authority, applies arm
interventions, executes transitions, launches a provider, measures tokens,
joins native turns, or grades task outcomes. A separate disposable-only
controller in `scripts/token_efficiency_panel_controller.py` materializes 37
workflows across 13 cases through the authenticated local API (`--list-workflows`
prints the full set; `scripts/check_token_efficiency_panel_controller.py`
exercises selected setups on disposable databases): five baseline-only setups
(`single_notice`, `actionable_response`, `duplicate_send`,
`small_request_result_reply`, `concurrent_receivers`) plus all four arms of
`routine_burst_urgent_request`, `wrong_project_memory`, `corrected_note`,
`meaningful_owner_correction`, `unfinished_handoff`, `required_conflict_group`,
`semantic_unavailable`, and `source_forgotten_before_read`. It registers synthetic Codex
sessions for setup; these are
not selected live harness or deployment evidence. It persists an immutable
intent before source creation and publication, then verifies exact Store-owned
source, session, event and
pending-delivery identities. For `actionable_response`, the controller
publishes a synthetic response whose causal ID is the preceding request's
observed event ID; no actor handles the request. For `duplicate_send`, it
repeats the identical publication with one native request UUID and verifies
one durable event and delivery; no task effect is exercised. Its private
receipt states that no provider call, native turn join or task outcome occurred.
For `small_request_result_reply`, the controller uses a synthetic registered
session claim, completes the exact request with a Store-owned digest result,
and publishes one causally linked reply to the registered sender inbox. A
retry uses the same claim, completion and reply UUIDs; changed result bytes
under the completion UUID are refused. The synthetic claim is not reconciled
(`attempt_reconciled: false`), and the stored result is not an actor answer or
evidence of a live native turn.
For `concurrent_receivers`, the sender publishes two requests to separately
owned synthetic worker sessions. The controller checks each exact native
readiness hint, verifies that worker A's authenticated profile receives
`NOT_FOUND` for worker B's exact session, and rechecks B's pending delivery.
The third profile token is bound into the durable intent; exact retry retains
the same two events and deliveries, while a changed token is refused. This
proves pending delivery ownership in the disposable Store, not concurrent
actor execution, request settlement, or task outcome. The fixture sources are
shareable and this check does not establish source secrecy.
For the burst, the sender publishes three routine notices followed by one
request. The batch and combined arms apply the sender-owned version 1 routine
policy with a fixed 30-second availability delay; baseline and envelope leave
all notices immediate. A selected native `session-inbox-ready` hint is saved
once with its observed time before the first routine deadline. An exact retry
after that deadline rechecks durable IDs and availability times without claiming
that the earlier priority still holds. The receipt labels native batch transfer
`unrun`; no actor handled the request or acknowledged a notice. The remaining
case setups (`long_source`, `busy_owner_task`, `lease_expiry`, `process_crash`,
`stale_execution`, `compaction_before_after_settlement`, `completion_reply_lost`,
`relay_uncertain_response`, `unchanged_capture`) remain unimplemented in this
controller.

The [relay uncertain response probe](../../scripts/check_panel_relay_uncertain_response.py)
uses a separate disposable Store to drop one successful child receipt after
commit and retry the same journaled request ID. It exercises recovery from
receipt loss, with no native actor or model outcome.

The [completion/reply-loss probe](../../scripts/check_panel_completion_reply_lost.py)
uses a hook-generated context for a synthetic turn. It commits one result,
then makes the first journaled reply fail while its disposable API socket is
unavailable. The pending reply intent succeeds with the same UUID after the
socket returns; completion, result, claim and reply counts remain singular.
This is pre-publication unavailability, not loss of a committed reply receipt.
It does not redeliver the handled request or measure an actor task.

The [source-forget probe](../../scripts/check_panel_source_forgotten.py)
uses another disposable Store to forget an admitted source through the
operator workflow after a synthetic claim, then settles that exact delivery
as `source_unavailable`. The authenticated controller also executes the frozen
sequence in four separate arm databases: it does not read source history
before admission, forgets after claiming, verifies `PAYLOAD_UNAVAILABLE` on
the first scoped read, and repeats the failed settlement under the same request
UUID. Receipts retain no source body and mark actor acceptance unassessed. A
requester explanation and actor task remain unrun. The controller also
materializes all four arms of `source_forgotten_before_read`.

The [required conflict probe](../../scripts/check_panel_required_conflict_group.py)
issues the exact `conflict_24` and `conflict_48` mandatory instructions through
the operator CLI in a separate disposable database. Operator history returns
both complete bodies. An authenticated agent's index and search return coded
`OPEN_CONFLICT`; neither returns an answer or any source body. Search retains
diagnostic metadata with no selected source or index entry. No actor judged
the evacuation threshold or produced a response.

The [semantic-unavailable probe](../../scripts/check_panel_semantic_unavailable.py)
creates the exact `lexical_anchor` source in its own disposable database. A
private local semantic worker exits without a score; authenticated semantic
search reports `DEGRADED_NO_EMBEDDINGS` with `discovery.state: unavailable` and
lexical ranking. A whole pull recovers the exact RAVEN-42 source, and ordinary
lexical search finds the same source without calling the worker. The frozen
case has no mandatory instruction, so this probe does not measure required
context preservation or an actor answer.

The disposable probes above write only to fresh integration databases and make
no claim about full panel execution or accepted outcomes. The existing gate
requires native workflow evidence for observed sender/recipient turns. An
ordinary-memory case has no proven native join yet; a fabricated inbox event
would change what that case measures. The capture gate freezes a separate
subscription-routed Claude selector and per-arm capture client pin; the hook
does not configure selector effort, so missing effort remains explicit. Its
seed stage requires selected provider response evidence, while the repeat
skip remains incomplete until its selected hook testimony can be verified.
Future admission must solve the carrier and joins, freeze the selected
accounts, routes, pins and limits, then run and review all four arms. The
subscription route rule remains in force. CAIRN-227 release status remains
**Defer**.

For a provider-free preflight of one fixture, see
[the single-workflow dry run](../../scripts/token_efficiency_single_workflow.py).
It supports only `baseline.single_notice`, checks the selected Codex account and
current installation pins, and records fresh caller-status samples. It never
starts an actor turn; its task grade stays unassessed and the 22-case paired
gate stays incomplete. Capture, concurrent receivers, other arms and all other
case setups remain refused by this narrow helper.
