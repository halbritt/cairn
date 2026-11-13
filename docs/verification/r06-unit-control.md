# Private R06 unit control process

The private `cairn-lifecycle` image exposes this finite selection command:

```sh
cairn-lifecycle unit-control inspect --plan /ABSOLUTE/plan.json --sha256 LOWERCASE_SHA256
cairn-lifecycle unit-control switch  --plan /ABSOLUTE/plan.json --sha256 LOWERCASE_SHA256
cairn-lifecycle unit-control reverse --plan /ABSOLUTE/reverse.json --sha256 LOWERCASE_SHA256
```

These are literal positional forms. There is no force, skip-drain, clear-hold,
enable, installation, refresh or service-management option. This entry is not
the public Store dispatcher and does not change public lifecycle registration.
It uses the existing coordination source profile and authenticated file host.
The whole control operation has a 30-second deadline; unknown/error/timeout
refuses. An older API without a required read is unknown.

## Pinned plan

The plan is one UTF-8 JSON object, at most 65,536 bytes, with this shape:

```json
{
  "schema": "cairn.unit-control/1",
  "current": {
    "directory": "/DISPOSABLE/unit/bindings",
    "pins": {"one.json": "CURRENT_CONFIG_SHA256"}
  },
  "next": [
    {"name": "one.json", "path": "/DISPOSABLE/release/one.json", "sha256": "NEXT_CONFIG_SHA256"}
  ],
  "retained": "/DISPOSABLE/unit/retained-old",
  "callbacks": []
}
```

Replace the digest placeholders with actual lowercase 64-character SHA256
values. Pin the complete plan bytes in the command's `--sha256`. The plan must
be owned by the current unchanged UID, mode0600, in a private mode0700 directory.
Paths must be canonical absolute paths with the existing owner-controlled,
non-symlink path checks. Fields use the exact lowercase names shown. Unknown
fields, duplicate JSON keys (including case aliases), trailing values,
unsupported schema and unbounded membership refuse. One to 32 binding names
and at most 256 callback references are accepted.

The current directory must contain exactly its pinned binding JSON files and
the optional existing `.watch.lock`. Every next member must retain the same
name, be data rather than an executable, and be an owner-controlled mode0400
file with the selected digest. The retained destination must be a distinct,
absent sibling. Both current and next configs must validate with their pinned
disabled `/2` or `/3` binding descriptors and configuration profiles. Binding,
harness, repository, coordination/memory state roots, account configuration home,
socket and token-file selector cannot silently change. Token contents are never
plan fields, output fields or inspector evidence.

`callbacks: null` and an omitted census refuse. `[]` means an explicitly
observed empty census, not discovery that every external callback has stopped.
References contain exactly integer `pid`>1, positive uint64 `start`, and
nonempty string `boot`. Invalid or additional fields refuse before API reads.
The command performs no process or process-group signaling. Full census and
native refresh remain release qualification work; no placeholder for another
author's census interface is installed here.

## Receipt and control boundary

### Explicit finite census adoption (`/2`)

The same literal command also accepts `schema: cairn.unit-control/2`. It has
the existing `current`, `next` and `retained` fields, and replaces `callbacks`
with an explicit `census` object. `/1` remains compatible and retains its
standing 19-case bounded proof. There is no fallback between the two forms.
The census object uses the existing census API's literal field names:

```json
{
  "Catalog": {"Path": "/DISPOSABLE/catalog.json", "SHA256": "CATALOG_SHA256"},
  "Proposal": {
    "Bindings": [{"Directory": "/DISPOSABLE/selections/unit", "Pins": {"one.json": "CURRENT_SHA256"}}],
    "Sessions": [],
    "Callbacks": []
  },
  "Retained": []
}
```

This example is the `census` value; use actual digests and complete retained
session/callback paths. All arrays are explicit. The proposal must match the
actual current directory/pins exactly. Only one selected directory can be
exchanged atomically; a connected user in another directory blocks the switch.
The `/2` path requires the unit's existing owned mode0600 `.watch.lock` before
inspection and never creates/acquires it during `inspect`.

`Retained` names complete immutable sibling copies already kept by a preceding
exchange, using `Directory`/`Pins`. Each has all binding names, the same unit
selectors and the same watch-lock inode. They are data retained for reconciliation
or reverse, not additional selected executors. Every copy is read and pinned;
unknown entries, aliases, changed selectors or missing coverage refuse. Catalog
and proposal are input bytes, never authority, a serialized receipt or proof of
external closure.

The command freshly calls the census, derives selected callback process refs,
then runs the existing authenticated D1-D5 inspection. Its private receipt binds
the full census digest and selected directory/file/lock identities. After the
final API reads, the command freshly inspects that same pinned catalog/proposal
and invalidates change/unknown/error before publication. The exchange's own
complete preparation is validated against its live private swap, next pins,
unit selectors and shared held lock inode. Raw final facts retain it; only that
known preparation's additions are projected out of the baseline comparison.
No caller can supply this private exception. Public ordinary census still
refuses an unexplained extra selection directory. Reverse uses the same path.

Outputs include initial and final census observations when reached. `FixtureReady`
remains a declared finite-fixture result, with `ExternalCoverage: unknown` and
`NativeRefresh: unobserved`. Owner join receipts retain same-UID fixture trust;
a dead/unknown process alone is not clearance. Retained native obligation fields
that the census cannot establish remain conservative blockers, including when
the legacy `/1` drain could independently prove historical correspondence.
Per-fact/composite final-read-to-rename windows, native producer/refresh/readback,
external coverage, grants, common-image qualification and full R06/R07 remain open.
No new lock/barrier, authority, settlement or inspector cleanup is introduced.

The composed process proof is `scripts/check_r06_census_control.py` through the
existing host task. It reuses both existing fixture families with actual API/PG
sessions and a real two-binding CLI inspect/switch/reverse. Separate standing
19-control and nine census process controls remain required. A fixture-owned
forwarder returns actual API responses and mutates catalog/join bytes after the
last final API read; publication must refuse and preserve the complete old
selection/preparation. There is no production injection option. The known
disjoint enabled callback remains alive and untouched; only its owner joins it.

### Real watcher handoff fixture

The bounded `scripts/check_r06_watch_unit_roundtrip.py` driver exercises actual
watcher processes against disposable API/PostgreSQL state:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- python3 -B scripts/check_r06_watch_unit_roundtrip.py --evidence /NEW/PRIVATE/evidence
```

It stages a complete immutable retained Python `/2` bundle with the generation
installer and a disabled Go `/3` selection with the compatibility installer.
The old and retained watchers execute the copied, pinned Python interpreter and
retained `coordination.py`; the Go watcher executes the selected private image's
`coordination --receipt … -- watch` command. The installed Python compatibility
launcher is not a Go watcher entrypoint. Each watcher serves the same complete
two-binding directory, lock inode and custom state paths. Running executable,
argv, PID/start/boot, actual lock ownership, both configuration pins and real
session heartbeats are read back. The fixture owner stops and joins each
watcher before the next executor starts. The control process independently
inspects, switches and reverses through `/2` census plans.
Both fixture profiles use the required `native_delivery: true` and omit the optional
`idle_wakeup` key before staging. These are fully synthetic profiles; before each
watcher launch the driver checks every selector, session identity and API endpoint
against its owned disposable roots and server. The disjoint unit also selects that
server and its own synthetic session. No installed profile or native client is selected.
A Boolean false is invalid for the retained Python idle-wakeup setting.
The observed watcher cycle is real authenticated presence against the two retained sessions.
The fixture owner initializes ordinary `owner_turn_required` delivery diagnostics
through the real disposable API before the cycles. This carries no settlement or
activation authority and leaves the existing strict state-preservation oracle intact.
The read-only drain still checks all principal/session claim and wake scopes.

The fixture owner archives only its exactly joined watcher reference after
publication, retaining the immutable configuration, issued argv, join receipt
and reference bytes. This permits the next finite catalog to describe its
current selection; the inspector never retires a reference. Unjoined, delayed
or running callbacks remain blockers. Separate cases cover omitted connected
bindings, changed configuration/runtime pins, incomplete retained reverse, and
a real authenticated claim admitted by the existing forwarding injector between
initial and final API reads. The two-session fixture has three initial wake
reads; its actor is admitted before the fourth, at the first final wake read.
Refusal must keep the active selection; an admitted
claim must invalidate the receipt. Existing 15/19/9 driver assertions are unchanged.

The enabled disjoint unit stays alive and unchanged. State preservation checks
allow only the watcher's presence timestamps to advance and retain session and
execution identity, journal selectors, ledger bytes and synthetic credential
bytes/selectors. A nonzero result with `exchange.Published: true` still requires
owner reconciliation; the driver never replays or cleans an uncertain exchange.
This proves a finite disposable watcher handoff, not native per-conversation
refresh, native layer coverage, positive-grant disposition or a final common
image. Those joins, external coverage and production remain held.

`inspect` validates the complete plan and captures the accepted D1-D5 facts
without acquiring the watcher lock or creating a preparation. Its output is
an observation, not an activation capability. `switch` and `reverse` each
capture a new private drain receipt inside their own process and retain it in
memory through `ExchangeWatchBindings`. Serialized fingerprints, `complete`
flags or an old inspect result cannot become that receipt. The plan and next
selection are checked again after capture. The accepted exchange performs its
own immediate final D1-D5 re-read and refuses any changed or unknown fact.

Run an ordinary independent control process outside the affected fixture/unit.
The invoking native conversation must not retain an affected unresolved
attempt. No exemption, borrowed session, replacement conversation or
re-registration is provided. Every affected known native attempt/hold, including
an owning control conversation's affected attempt, remains subject to the same
read-only checks. The disposable driver includes a real active owning-attempt
negative. Unknown external association/coverage does not become a full R06
qualification claim.

The inspector never claims, renews, acknowledges, completes, cancels or settles
an inbox, and never deletes a positive grant. Owning sessions settle their own
existing contexts. Null control does not establish historical terminal close;
the exact historical read and all protected correspondence still apply. Missing,
foreign, unavailable, stale and serialization errors refuse. Lease redaction
does not establish clearance.

## Switch, reverse and reconciliation

`switch` uses the existing whole-directory exchange and hardlinks the existing
watch-lock inode across the active and retained selections. It does not start
either executor. `reverse` uses the same drain and exchange; its next files
must identify a complete retained directory, with exact member names/pins and
the same watch-lock inode as the current directory. It retains the outgoing
selection in a new absent sibling. There is no rollback bypass.

One JSON result is written to stdout with schema `cairn.unit-control-result/1`.
It includes the selected plan digest, operation, current selection and directory
identity, observed fingerprints, exchange selections/preparation, post-operation
directory/lock identities, and the exact error when one occurs. `inspected`
means a receipt was captured, not that publication succeeded.

Exit0 means the requested operation succeeded. Invalid literal syntax exits2;
other refusals/errors exit1. Always inspect `exchange.Published`. Once rename
occurred, a later durability/readback failure retains `Published: true` and
requires reconciliation. A nonzero exit, broken output delivery or leftover
preparation is never an instruction to replay effects. Preserve the plan and
both directories, compare their complete pins and lock identities, and let the
release owner reconcile. No automatic cleanup or retry occurs in the command.

This is the accepted detection/invalidation form of D6. Each fact has its own
final-read-to-rename interval, and the composite file/API read is not atomic.
Activity after a fact's last read, or leaving no retained observable, remains
a limit. It does not establish a universal prevention barrier across refresh
and readback. Full R06, unsupported refresh, unknown external coverage, grant
disposition, final predecessor/image joins and production R07 remain held.

## Disposable command proof

Use the repository's maintained host process task:

```sh
mise run host:exec --timeout 10m --weight 1 -- python3 -B scripts/check_r06_unit_control.py --evidence /NEW/PRIVATE/evidence
```

The driver reuses `check_r06_status_consumer.Fixture`, `OwnedCluster` and
`DisposableHost` without editing them. It builds and pins the actual API and
new control image, preserving normal HOME/tools/auth and creating only
application-owned state. It runs the actual command for inspect/switch/reverse,
checks protected API/state snapshots and shared lock identity, exercises strict
input/census refusals and real active attempt/hold/unavailable/stale failures.
A fixture-owned forwarding executable returns the real read responses and
injects a separately recorded actual claim during the final re-read. No
production command fault/skip option exists. That claim must invalidate the
receipt, preserve old active bytes and retain the complete preparation.
Another fixture actor moves its owned parent after the final read. The command
must preserve `Published: true` on post-rename readback failure, complete active
and retained bytes, and their shared lock identity. It must not replay or repair
the selection automatically.

This proves the command seam with synthetic fixture membership/callback census
and a real API/PostgreSQL backend. It does not run an installed native client,
qualify native refresh, discover complete production membership or grant R07.
Terminal run counts, raw commands/streams/exits, image/compiler/module pins,
RED corrections and cleanup receipts belong to the exact candidate's retained
evidence packet; this document alone is not a runtime PASS receipt.

The watcher roundtrip fixture initializes its initial null callback collections
once, before adding references. It requires the declared disposable fixture
scope, an owned callback directory observed empty, explicit collection keys,
and no nonempty membership in either catalog or proposal. Existing lists and
their members are preserved; missing fields, unexpected types and null with
observed or declared members refuse. This fixture setup repair changes neither
the production census schema nor the runtime preservation/refusal assertions.
The retained real roundtrip PASS and four failed full runs remain evidence for
their exact inputs; the repaired input needs its own complete nine-case result.

The incomplete-reverse fixture keeps both retained source files, their paths and
digests, and two valid `next` descriptors. It changes only the required
`two.json` descriptor name to `other-two.json`, leaving current pins unchanged.
The required-name set is incomplete while cardinality remains valid, so the
unchanged refusal assertion targets the membership guard after plan parsing.
