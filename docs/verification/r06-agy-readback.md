# Agy native readback: blocked consumer interface

The Agy reader cannot be made reachable by changing only the hook dispatcher
on base `7d22b663d6abb0295dda0263adc9033353e5cefa`. The setup probe reproduces
the current consumer's refusal with genuine installed R02/R04 producer material.
It adds no product reader or native qualification.

## Profile association blocks hook inspection

The [actual producer](../../scripts/stage_lifecycle_hooks.py) requires Agy's
memory and coordination profiles to be unbound. It rejects a nonempty
`inbox_recall_binding`, emits only coordination `PreInvocation` and `Stop`
rows, and records `ordinary memory hook` as unsupported. Its `/3` compatibility
receipt still pins the disabled manifest and the exact unbound profiles; it
does not insert a runtime binding into those profiles.

`CLI.InspectWatchUnitCensus` calls `loadHookSelections`, then `members`, then
`inspectHookSelections`. In
[member loading](../../internal/lifecycle/coordination/shared_unit_census_files.go),
every selected configuration unconditionally selects its manifest through
`config.inbox_recall_binding`. An actual unbound profile therefore attempts
`jsonFile("")` and returns the exact error
`census reference outside observation universe: `, before hook inspection.
`sessions` later repeats that same bound-profile assumption when finding memory
state. Adding a field to the actual producer profile would violate its contract.
A hooks-only dispatcher cannot repair either association.

The existing catalog can name binding pins, but the member reader does not use
them to associate an unbound profile with its independently selected receipt.
Supporting that association requires a separately scoped change to the member
and session readers. It must preserve exact profile bytes, ambiguity refusal,
resource closure and authenticated D2 correspondence. This report introduces
no alias, inferred authority, alternate binding, or fabricated registration.

## Native observation boundary

The [current hook reader](../../internal/lifecycle/coordination/shared_unit_census_hooks.go)
accepts only `cairn.watch-claude-hooks/1`, harness `claude`, layer
`flagSettings`. Its role/event coverage, hook JSON, four-element coordination
tail and effective-listing checks describe Claude. Actual Agy rows have the
additional `--event EVENT` tail and cumulative account/project groups.

The retained [Agy native fixture](../../scripts/check_r04_native_agy.py) observes
real client writes, literal launcher/Go execs, callback stdin, conversation and
workspace identities, selected file opens and command multiplicities. Its
`hook_layers` list comes from the fixture's declared active layers. It is not
a native `get_hooks_listing` response or a general effective-settings API.
Identical commands across both layers run twice; individual identical-command
PIDs are not attributed to one file. These are finite, versioned, prompt-interval
observations with synthetic API registration, not authenticated D2 or exhaustive
native coverage of unknown groups. A future typed reader needs explicit native
file-read/dispatch evidence and complete declared inventories. This audit does
not establish that all needed client observables are unavailable, nor invent
positive coverage from the current report shape.

## Reproduce the exact interface refusal

Build and independently pin `control` (`cmd/cairn-lifecycle`), `stager`
(`cmd/cairn-stage-lifecycle`) and race-instrumented `observer`
(`go test -c ./internal/lifecycle/coordination`). The image JSON uses fixture
schema `cairn.r06-agy-readback-images/1` and `images.ROLE.path/sha256`.

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/check_r06_agy_readback.py \
  --images /absolute/private/images.json --evidence /absolute/private/new-evidence
mise run test:unit -- scripts/test_r06_agy_readback.py
```

The probe uses existing fixture construction and actual stagers. It copies the
unmodified produced coordination profile into the selected config directory,
declares its exact original binding/image/credential selectors, and invokes the
existing private process helper that calls the public census. No hooks or native
client execute, and no session, registration or journal is created. The zero-byte
mode-0000 credential selector is a metadata-only fixture sentinel, never submitted
as authentication or read for content. The API fixture is never called.

The expected error is checked exactly, with `FixtureReady=false`, external
coverage unknown and native refresh unobserved. Protected bytes/metadata and
membership are compared before and after. The trace checks no credential content
opens, owned writes, locks, renames, unlinks, signals or network connections.
Passing this probe means the blocker was reproduced, not that Agy is ready.

Strict install-3/install-5 remain open: transient both-active settings with two
journals, twenty post-rename NEW-active hard-kill observations, legacy writers
and opaque attribution retain their existing limits. No old Agy baseline,
receipt-fault series, hard-kill series, API/D2 or native run is repeated here.
D6 retains existing read facts, reread and refusal on change/unknown; this work
adds no barrier or global atomicity. Native9 belongs only to agent-1468's separate
frozen-7d admission. Live/installed/production actions remain outside this cut.
