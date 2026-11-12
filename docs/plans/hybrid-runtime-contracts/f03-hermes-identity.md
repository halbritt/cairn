# F03 Hermes capture and tool identity refusal

The existing Hermes capture and tool handlers require an encodable session
label before converting it to Go text, hashing it, creating the state directory,
acquiring locks or reaching the API. Both guards use the existing `validText`
and `failure` helpers and return the fixed HookError message
`invalid Hermes session identity`. Shared admission, disable/account checks,
privacy, and unbound selection still run first. Other harnesses do not enter
these guards.

This is an owner class-B malformed-input refusal. Actual Source `memory.py`
hashes the UTF-8 session label before `mkdir`; a lone JSON surrogate raises
ValueError there. Source and Go both return exit 1 and empty stdout, with no
API or state effects. The corpus separately requires Source's ValueError
diagnostic and Go's fixed HookError diagnostic. It preserves every other trace
field and does not claim arbitrary-input CPython parity.

The reached cells are Hermes PreCompact and SessionEnd via the existing checked
H150 capture entry and bare memory entry, plus PreToolUse, PostToolUse and
PostToolUseFailure via the existing bare tool entry. Checked ordinary recall
continues refusing capture/tools and bound Hermes. This adds no admitted route
and changes neither the value model nor Source, shared admission, H/V/L/D,
binding selection or deadlines.

`scripts/test_f03_hermes_identity.py` runs actual installed Source/main and actual
Go processes. Capture selects Source H150; bare tools select Source H13 (the
installed PostToolUse/Failure rows require H13). Local APIs and the existing
selector fixture are the only external
engines. Each side starts from an independently reconstructed state snapshot.
Malformed high and low surrogates cover absent directories and preexisting
bytes/modes, including the replacement-hash collision path. Fresh state must
stay absent; seeded state and lock bytes/modes must stay exact. API calls must
stay empty. Successful non-UUID, Unicode, escaped supplementary pairs, DEL and
128-character controls preserve hashed state paths, raw-label API scope, status,
reports, request order/bodies and trace semantics. Missing, C0 and oversize
identity, privacy/disable precedence, other harness account variables, and
selection/deadline/bound-profile refusals have separate controls.

The new corpus compares decoded per-side keysets before normalizing any timing
values, including request tuple bodies. Generated capture status requires the complete status keyset and `at`;
report duration, trace timestamps/invocation IDs and exit duration are required.
Measured hint times must fall within each process interval. Only independently
validated timing/UUID/image-attribution fields vary; semantic stdout ordering,
state fields/modes, request order/bodies, locks and remaining trace data compare.
Existing test methods and normalizers remain unchanged.

Run from a clean committed candidate, with a fresh external receipt:

```sh
make test-isolated RECEIPT=/absolute/evidence/hermes-identity.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f03_hermes_identity.py'
```

The wrapper's `/tmp` is disposable namespace storage. Its self-test must pass;
the receipt identifies the clean head and the actual command exit. Retain the
base RED separately. This bounded repair requires the existing checked capture,
Hermes recall, applicable ordinary/tool and Go race regressions. It does not
establish full CAIRN-318/319 qualification, native admission or ticket closure.
