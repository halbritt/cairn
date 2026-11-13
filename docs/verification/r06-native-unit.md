# Complete owned unit build: real routing and remaining interfaces

The dedicated [driver](../../scripts/check_r06_native_unit.py) and private
[Go helper](../../cmd/cairn-lifecycle/native_unit_fixture_test.go) implement a
real local API/PostgreSQL prerequisite for `REL-R06-COMPLETE-OWNED-UNIT-BUILD`.
They are a child of diagnostic `fffc55d5` on production-input parent
`31a554ae`. They do not qualify a complete installed native unit.

The driver starts a disposable cluster and authenticated API with the existing
`OwnedCluster` and `DisposableHost` helpers. Its adapter forwards existing agent
API calls and issued `complete`, `publish`, `ack` and `renew` client forms to
the pinned actual API image. Socket and token selectors must be the owned
fixture's exact paths; profile/endpoint overrides are refused. Responses,
streams, exit status and input are retained without replacement API results.

The Go helper checks the selected program hash, launches it as an owned child,
waits for its actual result, and records its PID/group and streams. The setup
keeps one observed helper ancestor alive across callbacks. It sets the actual
`CLAUDE_CONFIG_DIR` matching its profile rather than declaring a host PID.
The actual installed Go callback produces the registration, session state,
claim and context. Only its issued read and `finish_reply` commands are used;
the driver never constructs session records, journals or API identities.

The setup verifies actual completion and reply commits, raw product-written
journal correspondence, and ordinary SessionEnd retirement. The old reference
must then receive `STALE_SESSION`. A source-driven reopen with the same setup
native ID must return the same agent and a new current execution; that actual
reference authenticates the historical attempt read. The direct returned
`SessionInboxAttempt` must name the old claim reference and be finished.
Raw state bytes, hashes, modes, owners and inode identities are retained before
assertion. Used images are hashed before and after. Owned API processes are
joined and the private cluster is stopped without process-group signals.

Run only the non-native prerequisite, through the maintained wrapper and mise,
using explicit image pins with schema `cairn.r06-unit-images/1` and `api`,
`control`, `helper`, `stager` entries containing `path` and `sha256`:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 scripts/test_hybrid_runtime_operations.py --timeout 480 \
  --receipt "$R06_EVIDENCE/isolation.json" \
  --ro-bind "$R06_IMAGES:/opt/r06-unit-images" -- \
  python3 -B scripts/check_r06_native_unit.py --auth-setup \
  --images /opt/r06-unit-images/images-wrapper.json \
  --evidence /tmp/r06-owned-auth-evidence --archive-stdout
```

This uses native-shaped fixture events without starting Claude, a model or a
provider. Its generated setup ID is not evidence of a real native conversation.
The eventual complete-unit driver must take the actual native identity and
history from the supported client transport; this setup cannot replace it.

Two concrete interface dependencies remain on the pinned parent:

1. Actual Go producer state has its native ID in
   `registration.native_session_id` and the actual returned
   `agent.native_session_id`. It has no top-level `native_session_id`.
   `shared_unit_drain_local.go`, `shared_unit_census_files.go` and
   `shared_unit_census_d2.go` currently consume the latter fixture-only shape.
   The driver preserves actual state rather than injecting an alias. Root owns
   coordination of that product repair with the native hook consumer carry.
2. `--legacy-stage` stages and inspects a complete retained Python `/2` bundle
   with its actual engine/bridge descriptors and retained literal argv. The
   existing `stage_lifecycle_hooks.definitions` operation then calls
   `stage_lifecycle_compatibility.inspect` without its legacy option, and
   requires `selection.json`, which a genuine legacy generation lacks.
   The actual operation raises `KeyError: 'selection.json'`. Its raw inputs,
   inspected material and refusal are retained. The held native hook consumer
   also validates new hook-generation metadata and a `/3` compatibility
   context. A `/2` pin must not be relabeled as a `/3` receipt to pass it.

The supported legacy installers can emit Python hooks; that alone does not
provide the current consumer's complete selected-generation/readback input.
The owned-unit implementation needs an admitted legacy selection path or a
specific existing alternative from Root. No shared producer, census, Source,
runtime, authentication or store code is changed by this helper lease.

All original missing-selection refusal controls remain intact. The whole
watcher/controller/config-dir/memory/callback/account/project/state unit,
installed Python `/2` to Go `/3` forward/reverse, same real native conversation,
D1–D7 and supported refresh/readback remain unqualified. Every native, client
and model invocation is held for Root's final composed source and before-use
image pins and explicit admission. Existing native allowances are untouched.
Quiescence, legacy writers, opaque attribution, hard-kill and read-to-rename
limits remain explicit; production and full R06 acceptance are not granted.
