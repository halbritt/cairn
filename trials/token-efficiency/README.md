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

`core/token_efficiency_panel_materialization_test.go` exercises a bounded
native store slice under `make test-integration`'s disposable PostgreSQL
cluster. It reads this fixture and checks A record creation/edit with retained
history for `corrected_note`, governed C issuance and a required-conflict
refusal for `required_conflict_group`, and one request source/event with an
identical publish retry and one delivery for `duplicate_send`. The test uses
store-owned writer identity, the disposable test operator grant, and the
fixture's source/event fields. It does not materialize the other 19 situations
or any four-arm intervention.

**Current preflight boundary:** the Python validator checks the fixture and
export as data. It never inserts records, issues authority, applies arm
interventions, executes transitions, launches a provider, measures tokens,
joins native turns, or grades task outcomes. The three-case Go test above
inserts only into its disposable integration database and makes no claim about
full panel execution or accepted outcomes. The existing gate requires native
workflow evidence for observed sender/recipient turns. An ordinary-memory case
has no proven native join yet; a fabricated inbox event would change what that
case measures.
Future admission must solve the carrier and join, freeze the same account,
model, effort, route, arm pins, and limits with the gate, then run and review
all four arms. The subscription route rule remains in force. CAIRN-227 release
status remains **Defer**.
