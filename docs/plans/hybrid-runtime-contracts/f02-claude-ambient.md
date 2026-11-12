# Checked Claude lexical ambient recall

The receipt-checked unbound memory entry admits Claude `SessionStart` and
`UserPromptSubmit` with `recall_mode` omitted or literal `ambient`, and
`semantic_fallback` omitted or literal `False`. Only the existing ambient harness
term in `binding.VerifyOrdinary` changes. The ordinary recall engine and
`agent_tools` behavior are unchanged.

The same receipt selection still checks every profile is unbound, the image and
Source closure, entrypoint/alias, descriptors, actual launcher bytes/mode and H13
deadline. This does not add cross-profile harness equality. Bound or mixed
profiles, provider-selected recall, unsupported events, tools and capture remain
outside this checked unbound cell. Existing bare-Claude behavior is unchanged;
the leaf adds no bare case or policy.

Claude's producer-shaped positive input uses a UUID session identity, as required
by [Source admission](../../../integrations/lifecycle/hook_admission.py):231–244.
Absent explicit configuration overrides, the literal index task is
`claude/` plus that session UUID, and the run is the session UUID itself
([Source memory.py](../../../integrations/lifecycle/memory.py):859–860).
Overrides retain their existing semantics. Claude uses the raw UUID for its
`.json` and `.lock` state filenames; only Hermes hashes its label
(memory.py:3351–3356,3366). Hermes arbitrary-label acceptance is not a Claude
positive case. Scope assertions apply to index only; expand and report bodies
remain fully compared.

The new leaf uses individual existing ordinary/ambient fixture helpers and real
installed Source/main and checked Go processes against the fixture's Unix API.
It does not use the bound, controlled-UUID Claude optional fixture as its new
unbound frontend. Every cell in the two-event × omitted/ambient-mode ×
omitted/literal-False-fallback matrix requires exit zero and nonempty selected
and optional context on both frontends in the base fixture environment. A foreign
account-home or privacy-marker early return cannot satisfy those assertions.

Separate cases preserve Source outcomes for no-match/taskless recall,
seen/start/binding resets, opaque retained state, UUID state names and explicit
scope overrides. Provider/type/event/bound/deadline and tampered receipt/profile/
image/closure/launcher controls retain the existing refusals. Non-UUID identities
are separate safe-refusal controls, not exact Class A comparisons.

The parent collector compares full normalized streams, state and request bodies,
with original keysets/tuples, UUIDs, timings, authentication/protocol headers and
reported implementation origins checked before permitted normalization. Actual
process records retain input/digest, H13 vectors/launcher bytes/modes,
receipt/profile/member/image/alias data, streams, state, API tuples and traces.
The trace binds the input event; full-input custody also rests on the actual
subprocess input argument. Existing post-lock report assertions are recorded
checks, not a new physical-probe counter. No observer refinement is included.

The complete new leaf was committed and pinned before changing the guard. Its
focused eight-cell RED retains actual Source success with selected and optional
context versus the old checked-Go preflight refusal. Inputs, assertions, member
declarations and normalization are frozen from that commit. The existing
OpenCode other-harness negative retains all four harnesses and every assertion;
only its fallback setup now selects `True` for Claude as well as Hermes, keeping
the negative meaningful and leaving `False` after the final Codex iteration.

Run the leaf from a clean committed head with a fresh receipt:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  make test-isolated RECEIPT=/absolute/evidence/claude-ambient.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f02_claude_ambient.py -v'
```

The shared executor preserves the outer agent environment and bounds the
existing approved campaign fixture. Qualification also runs the existing
Claude20, OpenCode ambient16 and Hermes8 leaves, focused binding/memoryhost/
memoryhook Go race checks, project `mise run check`, and docs tiers 1,3 with
source/example review. UID/filesystem skips remain explicit and do not pass.
Retained artifacts distinguish unittest durations, outer wall times, inner
exits, self-test results and historical failures.

These constructed cases do not establish every input, native writer, installed
route, production behavior, measured usefulness or full design acceptance. All
campaign holds and the CAIRN-318/319 owner closure bar remain. Independent review,
lead qualification/integration and native/full-gate/campaign acceptance are
separate from this bounded caller implementation.
