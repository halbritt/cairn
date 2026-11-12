# Checked ordinary early-return trace stage

Receipt-selected unbound ordinary memory calls initialize their trace stage to
`activation`, matching Source `_main` before its shared handler. This includes
already-admitted Claude/OpenCode agent_tools SessionStart/UserPromptSubmit calls
that return for disabled/child environments, foreign account home or invalid
session identity before setting `validated`. Their final phase stays `handle`.

The change is one condition in `memoryhost/checked.go`. It changes no admission,
cell, public result, diagnostic, state/API behavior, H13/V/L/D/input selection or
body. All unbound checked cells get the same initial stage. Later stages still
overwrite it. Existing bound tool initialization and the bound branch's own
activation assignment remain unchanged. Checked Hermes, OpenCode ambient and
unbound capture keep their existing paths; capture has a separate checked entry.

`scripts/test_f02_checked_early_exit.py` runs actual installed Source/main and
actual checked Go processes through the original H13 vectors and issuer. Each
side independently restores absent state or exact seeded file bytes/modes and
directory mode. The finite paired corpus covers both harnesses and both events:

- Disabled/child guards with valid and invalid identities.
- Foreign account variables, including Claude's applicable account-home guard
  and OpenCode's absence of that policy.
- Scalar invalid/missing identities that reach the Source handler.
- Privacy returns and identity-before-privacy precedence.
- Healthy calls whose later `saved` stage survives initialization.

Every Source and Go invocation must have entry/parsed/exit traces ending in
`phase=handle`; preflight refusal is not handler parity. Complete trace keysets,
types and stage values are checked before timing/UUID/process normalization.
Stage is required and never synthesized. Request tuples, field sets/types,
UUIDs, SDK origins, state/report timings and lock observations are validated
before comparing semantic streams, state, request order/bodies and traces.
The new collector copies a plain list of API calls, preserving real lock probes
without replaying observer callbacks. Existing helpers and corpora are unchanged.

Run from a clean committed head with a fresh external receipt:

```sh
make test-isolated RECEIPT=/absolute/evidence/checked-early.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f02_checked_early_exit.py'
```

Keep the actual baseline RED, receipt, raw streams/traces/state/API data and
image identity. Require namespace self-tests and actual terminal exits. This is
bounded software trace evidence, with no native-writer or all-input parity claim;
the existing full host-test and campaign acceptance bars remain separate.
