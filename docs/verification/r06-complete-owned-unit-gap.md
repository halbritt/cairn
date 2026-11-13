# Complete owned unit: native selection readback gap

On pinned parent `31a554ae4f503ac1cada270d8bea32680bf034c4`, a complete
installed-unit roundtrip cannot pass the existing census. This is a product
interface finding for `REL-R06-COMPLETE-OWNED-UNIT`, not native qualification.

`members` calls `emptyHookLayer` on every selected `config_home`; `sessions`
calls the same function on every selected session's entire workspace.
In [shared_unit_census_files.go](../../internal/lifecycle/coordination/shared_unit_census_files.go),
`emptyHookLayer` refuses every nonempty directory with:

```text
account hook/config coverage is unknown: selected native settings/readback seam is unavailable
repository hook/config coverage is unknown: selected native settings/readback seam is unavailable
```

The refusal applies even to an account `settings.json`, repository
`.claude/settings.json`, or ordinary workspace `README.md`. These users cannot
be omitted or moved to empty stand-in directories to claim a complete unit.
The selected disabled binding and otherwise valid finite census do not supply
the missing native settings or refresh evidence.

The combined `unit-control/2` path calls this census in
`captureUnitCensusDrain` and returns the error before `CaptureWatchDrain`.
The accepted authenticated D2 join therefore cannot remove this refusal.
`WatchCensusCatalog` has no native effective-settings/hook-listing observation
input, and the census initializes `NativeRefresh` to `unobserved`.

The existing [Claude fixture](../developer/native-claude-hook-fixture.md)
observes actual `get_settings` and `get_hooks_listing`, then uses orderly
stop/join and `--resume` with the same native conversation, account and exact
`--settings`. It currently has no product census consumer for that evidence;
its existing two selections also use the same Go image and a synthetic API.
Adding its observations to a report does not make the existing combined
controller accept a complete installed unit.

The dedicated [Go counterexample](../../internal/lifecycle/coordination/shared_unit_native_unit_test.go)
starts from a valid two-binding finite census, adds each real filesystem
member, and verifies the exact standalone and combined-path refusals. It
checks file bytes, membership, modes, ownership and inode identity. It launches
no native client, simulates no authenticated drain, and does not consume a
native allowance. Run the focused counterexample and adjacent regressions with:

```sh
mise run test:unit -- ./internal/lifecycle/coordination \
  -run '^Test(OwnedUnitNativeLayersRequireReadback|WatchCensusClosedTwoBindingUnit|UnitCensusOwnPreparationStillPinsAllOtherFacts)$' \
  -race -count=1 -v
```

Root must assign the product seam that observes the selected account and
repository layers and ties real per-session native readback to initial and
final census facts. This helper lease does not change that consumer, invent an
authority or refresh Boolean, or relax unknown/change refusal. Once that seam
is reviewed and landed, the complete owned-unit driver still needs the real
API/PostgreSQL D1–D7 observations, complete Python `/2` to Go `/3` installed
forward/reverse transition, one executor, supported same-conversation refresh,
and Root's exact final candidate/image pins and native admission. No such native
run has occurred here. Quiescence, legacy writers, opaque attribution and
remaining hard-kill intervals still require explicit evidence and limits.
