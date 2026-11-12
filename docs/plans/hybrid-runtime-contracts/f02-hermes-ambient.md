# Checked unbound Hermes lexical ambient recall

Receipt-checked Hermes `SessionStart` and `UserPromptSubmit` can use the existing
ordinary lexical ambient recall path. `recall_mode` may be absent or literal
`ambient`; `semantic_fallback` must be absent or literal `false`. The checked
binding guard admits this cell alongside OpenCode ambient. The existing
`agent_tools` admission and recall engine are unchanged.

The complete selected receipt generation is still verified before recall or
state effects. Every profile must be pinned and unbound; the actual executing
image descriptor, receipt closure, selected entrypoint/alias, argv, launcher and
H13 deadline remain checked. No equality rule for harness labels across profiles
is added. Bare Hermes recall, bound/mixed profiles, provider-selected ambient,
malformed fallback, tool/capture and unsupported checked events remain refused.
No Source, provider, activation, stager or installed lifecycle behavior changes.

`scripts/test_f02_hermes_ambient.py` uses the existing real Source and checked-Go
frontends and Unix API fixture. Its eight methods cover both events, omitted and
explicit ambient mode, omitted and literal-false fallback, selected and optional
context, no-match/taskless deferral, seen/reset and literal Hermes session labels.
API scope retains `hermes/` plus the literal session label and the raw run label;
only state filenames use the session digest. The retained-state case uses the
existing Hermes test method, including its opaque state and binding assertions.
The existing malformed-surrogate case requires safe stable Go refusal separately
from exact producer-shaped comparisons.

The new corpus is committed before the guard repair. Its clean baseline records
actual Source success with selected/optional context versus the old Go checked
preflight refusal for all eight event/mode/fallback combinations. The same
corpus is retained through the repair. Raw process records retain input/digest,
commands, launcher bytes/modes, receipts/configuration, stdout/stderr, state,
API tuples/headers, trace and post-lock reports. Existing keyset, UUID, timing,
origin and normalization checks are reused; Hermes stdout timing fields are
validated before normalization. No probe callback or existing collector changes.
Trace comparison binds the event; full-input custody also depends on the actual
subprocess call using the fixture's recorded input.

The two old negative inputs are reconciled with this intended expansion. The
Hermes blanket ambient negative explicitly selects provider fallback, then
restores the prior fallback presence and value before its original mixed-profile
and timeout cases. The ambient suite keeps every other-harness tuple member;
only the Hermes member selects provider fallback. Its other three members and
later OpenCode negatives retain literal false. All existing assertions, test
names and their order remain.

Run the new leaf from a clean committed head with a fresh evidence receipt:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  make test-isolated RECEIPT=/absolute/evidence/hermes-ambient.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f02_hermes_ambient.py'
```

The host executor preserves normal agent tools, HOME, PATH, authentication and
services; the existing campaign fixture supplies disposable application state.
Final validation also includes the existing Hermes22/OpenCode ambient16
regressions with only the stated input deltas, focused binding/memoryhost/
memoryhook Go race checks, project `mise run check` and documentation tiers1,3
plus manual source/example review.

These are constructed lexical ambient cases through H13 checked argv, not
all-input, provider, installed/native, writer, full-gate or campaign acceptance.
Other modes/harnesses and the paused Source/runtime/provider/material/native/
production work remain outside this slice. The owner318/319 closure bar and
retained historical failures remain open.
