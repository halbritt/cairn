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
contains exact source bodies and body hashes, actor/setup/check hashes, the
declarative setup and hidden checks, and 88 prospective case/arm workflow IDs.
The four arm rows for a case carry identical actor, setup, and check hashes.

The controller must materialize source records and event deliveries from
`controller.json` for each run and verify their exact hashes. Source order is
the array order. `events[].id` is a local fixture label, not a Cairn event ID.
`transitions` name failure and timing conditions the future controller must
actually induce and verify; a label alone proves nothing. The `repeat_source`
transition asks the controller to place 120 copies of the historical relay
body before the current qualification. The actor receives only its task and
the native task/event delivery appropriate to that case. A runner must confine
the actor so it cannot read the repository fixture or `controller.json`.

**Current preflight boundary:** the validator checks the fixture and export
as data. It does not insert records into a disposable or live native store,
execute transitions, launch a provider, measure tokens, join native turns, or
grade task outcomes. The existing gate requires native workflow evidence for
observed sender/recipient turns. An ordinary-memory case has no proven native
join yet; a fabricated inbox event would change what that case measures.
Future admission must solve the carrier and join, freeze the same account,
model, effort, route, arm pins, and limits with the gate, then run and review
all four arms. The subscription route rule remains in force. CAIRN-227 release
status remains **Defer**.
