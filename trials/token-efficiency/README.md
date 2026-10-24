# CAIRN-227 synthetic paired-task panel

`FIXTURE.json` is the prospective source/task contract for the 22 situations in
the token-efficiency plan. Its `acceptance_check` values are private grading
criteria. They are not accepted outcomes. The fixture contains synthetic data
only. Source IDs, source order, task wording, event bodies, transition labels,
and acceptance checks are held identical across the four proposed arms.

Run the provider-free validator from the repository root:

```sh
python3 -B scripts/preflight_token_efficiency_panel.py
python3 -B scripts/test_preflight_token_efficiency_panel.py
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

Two Go integration tests exercise source and event setup for six cases under
`make test-integration`'s disposable PostgreSQL cluster.
`core/token_efficiency_panel_materialization_test.go` checks A record
creation/edit with retained history for `corrected_note`, governed C issuance
and required-conflict refusal for `required_conflict_group`, and one request
source/event with an identical publish retry and one delivery for
`duplicate_send`. `core/token_efficiency_panel_events_test.go` checks a pending
`single_notice`, exact request/response causation and responding writer for
`actionable_response`, and four isolated publication setups for
`routine_burst_urgent_request`. The burst test applies only the fixture's
routine-notice policy per arm and checks availability. It checks urgent
readiness in the batch and combined setups, where routine notices are deferred;
it does not run batch transfer or handling. These tests use store-owned writer
identity, a disposable test operator grant where needed, and fixture source
and event fields.

Other Go tests check selected Store behavior for fifteen more fixture IDs.
`core/token_efficiency_panel_reply_test.go` follows native session claims,
request settlement, and a result response for `small_request_result_reply`;
its `completion_reply_lost` and `relay_uncertain_response` checks exercise
idempotent Store operations without inducing transport loss or running journal
recovery. `core/token_efficiency_panel_ownership_test.go` checks distinct
recipient ownership for `concurrent_receivers` and a failed source read after
operator deletion for `source_forgotten_before_read`.
`core/token_efficiency_panel_memory_cases_test.go` creates and whole-pulls
exact sources for `semantic_unavailable`, `wrong_project_memory`, and
`meaningful_owner_correction`. These memory checks use a separate disposable
database for each case so another case's required C conflict cannot alter
retrieval under the fixture's shared repository scope.

`core/token_efficiency_panel_recovery_test.go` checks an expired lease under
its current native hold, an offline presence with operator release and explicit
reissue, and execution fencing for `lease_expiry`, `process_crash`, and
`stale_execution`. Expired test presence is an observable offline boundary,
not proof that a recipient process crashed.
`core/token_efficiency_panel_session_boundary_test.go` checks that a reported
busy session has no ready routine notice until it reports idle, and that two
notices keep their durable state across fresh client views for
`busy_owner_task` and `compaction_before_after_settlement`. The busy check does
not run the synthetic parser review, and fresh views do not invoke a harness
compaction hook. `core/token_efficiency_panel_memory_followup_test.go` stores
and whole-pulls the historical and current relay sources with a pending
request for `long_source`, and the exact saved source for
`unfinished_handoff`. The 120-copy exposure and actor pause/resume transitions
remain unrun.

`unchanged_capture` has a separate controller-only synthetic dialogue and
first selection in `setup.capture`. The Python preflight test runs the real
local capture decision twice with a stubbed first selector and local create
response: the first call confirms a saved snapshot and the identical second
call skips selection and another write. This selection is test setup, not a
provider response to impose on a future actor. The hook's persisted state,
actual provider selection, and task outcome remain untested.

The Go tests check setup or selected Store transitions for 21 fixture IDs;
the Python hook probe covers the remaining capture setup. None grades an
actor task, completes a paired model arm, or measures provider usage. The
original six have source/event setup checks; the other fifteen Go checks are
partial source, scheduling, or recovery checks.

**Current preflight boundary:** the Python validator checks the fixture and
export as data. It never inserts records, issues authority, applies arm
interventions, executes transitions, launches a provider, measures tokens,
joins native turns, or grades task outcomes. A separate disposable-only
controller in `scripts/token_efficiency_panel_controller.py` materializes five
baseline setups (`single_notice`, `actionable_response`, `duplicate_send`,
`small_request_result_reply`, `concurrent_receivers`) and all four `routine_burst_urgent_request` arm
setups through the authenticated
local API, each in a separate disposable database. It registers synthetic Codex
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
`unrun`; no actor handled the request or acknowledged a notice. All other
case/arm setups still lack an executable controller path.

The [relay uncertain response probe](../../scripts/check_panel_relay_uncertain_response.py)
uses a separate disposable Store to drop one successful child receipt after
commit and retry the same journaled request ID. It exercises recovery from
receipt loss, with no native actor or model outcome.

The Go probes above write only to disposable integration databases and make
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
