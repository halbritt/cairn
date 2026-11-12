# Checked Claude reserved-wake state parity

The checked bound memory entry must retain the coordinator snapshots resolved
by activation. `RunBound` now forwards them to both ordinary `runMemory` calls,
as the Codex `RunStart` branch already did. This lets the existing wake check
return before constructing ordinary memory state. It adds no wake protocol,
profile policy, authority API or state-file cleanup.

The regression uses `HookFixture.wake()` and a native-shaped `prompt_id` with
an enabled Claude `agent_tools` binding. The real R02 installer issues the
receipt, profiles and launchers. The retained Source frontend is compared with
the actual eight-flag, 13-second checked Go memory entry, not a direct
`RunStart` call. Only the external API and fail-fast provider are fixtures.

Before the repair, the unchanged `BoundMemoryCase.pair` parent-key assertion
fails: Go creates `SESSION.json` and makes recall API calls; Source does not.
The common `SESSION.inbox-recall.initialized` marker and
`SESSION.inbox-recall.json` remain in both sides' exact state comparison.
The repair prevents the extra work; it does not remove the extra state after
creating it or filter keys, streams or markers from the comparison.

The [focused regression](../../scripts/test_f02_claude_wake_state.py) checks
first and repeated wake streams, all state/file names, retained activation
state, released locks, zero API/provider calls and matching invocation traces.
Only validated trace invocation IDs, PIDs and clock fields follow the existing
trace nondeterminism convention. Privacy/account exclusions are exercised with
the binding unavailable. A leaf `load_tests` selects only this module's two
new methods under ordinary unittest discovery.

```sh
mise run host:exec -- python3 -B -m unittest discover -s scripts -p test_f02_claude_wake_state.py -v
```

Existing bound-memory and Claude recall checks retain ordinary owner prompts,
grants, locking, disabled generations and enabled ambient/default refusals.
The immutable original provider-corpus RED, including its two separate
disabled-fixture errors, is not rewritten or reported as fixed by this repair.

This is checked-entry Source parity. It does not qualify the hook-settings
producer's Claude prompt selection, native/provider execution, a common-image
release, deployed sessions or production adoption. Those joins retain their
existing owners and holds.
