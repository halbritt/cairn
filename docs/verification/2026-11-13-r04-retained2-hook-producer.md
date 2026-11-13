# Retained Python /2 hook selection, 2026-11-13

The hook settings producer can now explicitly select a retained Python `/2`
generation. Previously, `definitions` always inspected a `/3` installation and
read `selection.json`; a genuine retained generation has no such member.

This is a Python host-adapter change based on frozen recovery commit
`0c67d838b89000a7f6604899e29ddc4de22d8450`, tree
`0ba272f16dd4a54bbd00027629d7178cb79c5632`. The original recovery checkout and
evidence remain separate. Product Go code, recovery publication, runtime code,
profiles, credentials, and installed hooks were not changed.

## Explicit retained selection

The existing hook plan accepts the Boolean field `legacy`. Omission or `false`
keeps the current `/3` path. `true` selects `inspect_retained`; it does not infer
a compatibility receipt or bypass current installation inspection. The public
`definitions(stager, plan)` interface still returns `(rows, profiles)`.

The retained path checks the actual Go `inspect-generation` result, including
its independently selected manifest digest and all installed member bytes and
modes. It then checks the `/2` binding, the complete 17-member bundle manifest,
both original engine/bridge bundle descriptors, profile descriptors, and
`retained-argv.json`. Descriptors must select the same verified generation.
Missing, extra, mixed, ambiguous, and unknown selections refuse. Selected code
and selected interpreters are not executed during these checks.

There must be one retained literal argv for each of `memory` and `coordination`.
The coordination vector must select exactly one verified coordination profile;
the plan's index must select that same profile. A retained Python invocation
may have no interpreter flags or distinct `-I`, `-S`, and `-B` flags. The
producer preserves their order and bytes. Other launch grammars refuse rather
than acquiring a new meaning. Agy retains its existing event suffix and its
restriction to unbound profiles.

The existing `cairn.lifecycle-hook-settings/1` record gains `retained_python`
only for this explicit path. It contains the original binding pin and `/2`
schema, engine and bridge descriptors, bundle-manifest pin, and retained-argv
pin. Existing `lifecycle_selection` and `role_configurations` identify the
actual retained inputs. Each row reports `implementation: python` and reason
`verified retained Python/2 hook`. Its `argv`, `checked_launcher`, and
`retained_launcher` contain the full retained role vector, including the
coordination `hook --config` tail. Consumers must not append that tail again.

The interpreter remains an external retained launch anchor. A Python source
digest does not authenticate a Go image. These records make no native trust,
refresh, connection, or activation claim; those fields remain `not-performed`.

## Local validation

All invocations used repository mise tasks, the shared `host:exec` admission
and cleanup, the normal host HOME/tools, and disposable application state.
The new fixture uses the maintained retained-generation writer and the real
Go stager. It stages and reads hook settings without starting a native client,
model, API, or production process.

Development invocation 1 ran seven methods and retained one failing subtest
and one fixture error: OpenCode plugin pins were in an unsafe worktree ancestor,
and the current `/3` fixture lacked a cwd attribute. The fixture now copies
plugin inputs into controlled scratch state and supplies its owned cwd. No
production guard changed. Development invocation 2 passed all seven methods.

The final focused invocation passed **14 tests, zero failures, zero skips**:
the seven new retained tests and seven maintained `/3` staging/refusal controls.
It ran through `mise run test:unit -- ... -v`; the complete argv and raw logs
are retained in the handoff packet. The controls cover unrelated settings and
group positions, exact previous bytes, repeated rendering, invalid profiles and
pins, malformed/ambiguous hooks, changed launchers, cross-role replacement,
and refusal to downgrade compatibility inspection.

The retained cases cover actual Codex, Claude, Agy, and OpenCode settings,
literal launcher readback, exact raw previous settings, Unicode and large
integers, unchanged profile bytes with duplicate-last configuration fields,
file modes, and an unreadable synthetic token selector. They also exercise
wrong hashes, missing/extra members, inconsistent bundle and role descriptors,
invalid/duplicate argv, unknown schemas, wrong accounts, ambiguous hooks, and
refusal of `/2` without explicit selection or `/3` with retained selection.

Before each producer invocation the fixture records the actual producer,
compatibility adapter, stager, plan, argv, exit, stdout, and stderr. The packet
retains fixture copies, actual Go images and build information, interpreter
identity, and all development failures. Its `original-disposable-root.txt`
files map retired scratch paths to the retained copies; a copied descriptor
does not imply that its original scratch generation still exists.

The final stager SHA-256 is
`71f852136cb7f92e6e51b10b1f263f4df4332fc2cc06c8dbe4e4e84c9be71cdc`.
Build information reports Go 1.25.0, `github.com/google/uuid` v1.6.0, and
`golang.org/x/sys` v0.41.0. The fixture uses `-buildvcs=false`; this is not a
VCS build-identity attestation. `mise run check` and documentation validation
are separate required pre-handoff gates, with their own packet receipts.

## Scope and remaining limits

The original recovery review remains on its exact frozen candidate. Its Go
publisher, recovery implementation, and recovery fixtures are unchanged in
this child; the authorized hook-producer delta preserves `publish_agy`.
The earlier recovery SIGKILL series was not repeated. Exact source equality
supports carrying that earlier evidence within its recorded scope; it does
not establish new recovery or native qualification.

This change does not implement the Go selected-hook consumer. It does not
close the 20 immediate active recovery snapshots, the both-active concurrency
interval, install3/5, full-unit, or actual native readback gaps. No installed
profile, live service, production database, native client, or model was used.
Production hold `98d46758` v1 remains. Release owns exact review, composition,
landing, and cleanup of this deliberately frozen child.
