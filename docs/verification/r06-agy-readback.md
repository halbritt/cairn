# Unbound census association and the next Agy blocker

The finite census can associate genuine disabled `/3` receipts with unbound
profiles without editing their JSON. This removes the empty-binding-path
failure recorded by diagnostic `094393e2648d978998f8b00c59e469c03ffbc085` on
`7d22b663d6abb0295dda0263adc9033353e5cefa`. Its frozen packet remains historical
evidence. The typed Agy reader described below builds on that association without qualifying a fresh native session.

## Selected receipt evidence

`WatchCensusCatalog.UnboundReceipts` optionally supplies exact receipt path/hash
pins. It selects observations, never liveness, refresh, drain or authority.
Every selected unbound coordinator must match exactly one receipt descriptor's
actual bytes. Missing, duplicate, ambiguous, mixed or omitted users refuse.
The existing bound `/2` and `/3` paths retain their profile checks.

The reader first registers the receipt, manifest, binary, every role profile
and every source member through the existing path/alias/inode, credential
exclusion and pin checks. All JSON association inputs reject duplicate fields
and invalid Unicode. The accepted `CompatibilityContext.VerifyReceipt` checks
source closure, aliases, content address and binary pins using the observed
receipt. It cannot discover paths from a replacement receipt. All validator
disk reads are already in census facts and final readback. The manifest/profile
predicates from `UnboundProfiles` run against tracked bytes, avoiding that
method's separate `controlledData` reads.

The manifest must be private, explicitly disabled `/3`, match its selected
binding pin and retain the receipt's exact binary and role descriptors. Every
role remains unbound, with agreeing harness/client/socket/credential/repository
selectors. State and account paths stay in the declared universe. Coordinator
homes are distinct; memory belongs to a selected coordinator account and has a
separate state directory. Selected configurations are never given an inserted
`inbox_recall_binding`. Unknown and unsupported associations refuse.

Members and session memory enumeration share that verified association. Receipt,
source and manifest roots participate in resource closure. Memory grants,
retained attempts and authenticated D2 requirements keep their existing checks;
the disabled manifest is not proof of a current owning turn's settlement.
The inspector still rereads facts and refuses change or unknown coverage.
These point observations are not a global atomic barrier.

## Consumed proof and limits

The maintained [probe](../../scripts/check_r06_agy_readback.py) constructs actual
R02/R04 Agy material and invokes the existing process helper that calls public
`CLI.InspectWatchUnitCensus`. Agy's actual profiles remain null/unbound, and the
producer still emits only coordination `PreInvocation`/`Stop`, with ordinary
memory unsupported. No hooks, native client, registration or API execute.

Three independent owned fixtures distinguish the claims:

- With the ordinary nonempty account, receipt association succeeds and the
  current account-layer reader returns the exact next refusal:
  `account hook/config coverage is unknown: selected native settings/readback seam is unavailable`.
- With explicitly empty account/state layers, finite association and resource
  enumeration can succeed. The report says `ASSOCIATION_ONLY`, never native or
  authenticated D2 qualification. External coverage stays unknown and native
  refresh unobserved.

- With genuine emitted settings copied into both cumulative Agy layers, a strict
  `cairn.watch-agy-hooks/1` packet reaches the Agy producer reader. Both layers
  select actual generation/settings pins and exhaustive inventories. The explicit
  `code-preparation` readback contains no native session, PID, client or trace.
  The exact result is `Agy native session/file-read/exec provenance is unobserved; code preparation is not native qualification`.

The zero-byte mode-0000 credential selector is a metadata-only fixture sentinel,
never authentication or a content read. Before/after snapshots preserve profile,
source, settings and unrelated bigint/Unicode bytes and metadata. Consumer traces
check no credential opens, owned writes, locks, renames, unlinks, signals or
network connections.

The private Go fixture uses that genuine produced selection for public positive
association and missing, duplicate, ambiguous, tampered, external-source,
mixed-profile, enabled-manifest, omitted-user and source-pin refusals. It also
checks retained positive grants and changed tracked bytes at final readback.
Derived negative inputs are unit fixtures, not new receipts for native use.

Build and pin `control` (`cmd/cairn-lifecycle`), `stager`
(`cmd/cairn-stage-lifecycle`) and race-instrumented `observer`
(`go test -c ./internal/lifecycle/coordination`). The fixture image schema is
`cairn.r06-agy-readback-images/1`, with `images.ROLE.path/sha256`.

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/check_r06_agy_readback.py \
  --images /absolute/private/images.json --evidence /absolute/private/new-evidence
mise run test:unit -- scripts/test_r06_agy_readback.py
```

For the focused race invocation, set `CAIRN_R06_UNBOUND_FIXTURE` to the resulting
`association-only/go-unit-fixture.json` and select
`TestR06UnboundCensusAssociationProcess` in the pinned observer, alongside the
affected bound census/Claude/D2 unit tests. Without that explicit material the
private fixture entry is inactive and does not count as coverage.

## Private drain capture and receipt recheck

The combined census/drain reader now carries its verified unbound association
privately into drain capture and `WatchDrainReceipt.recheck`. It repeats the
existing complete census validation before and after authenticated status reads,
then compares the original file, directory, resource and identity observations.
Receipt, manifest, profile, binary and member drift refuses. Credential selectors
are checked by metadata; the verifier never opens their contents. Public
`CaptureWatchDrain` has no association injection parameter and continues to
refuse an unbound selection supplied without the private census context.

The disabled flag does not settle grants or retained attempts. Existing D1–D5
checks and authenticated D2 correspondence still run, followed by the existing
D6 census/D2 checks before exchange. The only preparation exception covers the
swap's independently verified retained copy and its directory membership change.
An unbound retained copy must keep the exact selected profile bytes. Other
changes and unknown coverage still refuse; the final read and rename remain
separate observations.

`TestR06UnboundDrainAssociationProcess` uses genuine maintained R02/R04 material
for capture, independent recheck, lost or missing associations, duplicate
receipts, byte and inode replacement, aliases, resource drift, changes during
status reads and unknown API refusals. Its status doubles establish finite
plumbing only. Activate it with `CAIRN_R06_UNBOUND_FIXTURE`, along with
`TestR06UnboundCensusAssociationProcess`. Activate the existing typed refusal
entry with `CAIRN_R06_AGY_TYPED_FIXTURE`.

`TestR06UnboundDrainAuthenticatedProcess` uses a separate NEW disposable API/PG
fixture and genuinely produced unbound profiles. Set
`CAIRN_R06_UNBOUND_AUTH_FIXTURE` to its selected input. Capture and receipt
recheck each execute the real `wake-attempts` and `event-watch` readers. This
empty-session fixture proves authenticated reads; it supplies no retained D2
success or native qualification. The maintained census D2 process fixture
separately covers bound authenticated joins and their refusals. All gates run
through mise with pinned images and explicit fixture inputs; inactive entries
and skips do not count as coverage.

The Agy reader and its preparation/historical blockers are unchanged. Public
unit-control's preflight configuration-pair reader also remains bound-only;
this private capture/recheck component does not extend that separate interface.
Current Agy qualification, refresh and full R06/HC05 acceptance remain open.

## Typed Agy observations

The public census dispatches tagged Claude and Agy inputs to separate readers.
Claude `flagSettings` keeps its existing contract. Agy selects exactly the
ordered account `hooks.json` and project `.agents/hooks.json` layers. A missing
layer must be explicitly absent on disk, with no generation/settings claim.
The reader walks both declared inventories, refuses missing or extra pins and
ancestor project layers, and registers each observed generation/member through
the existing path, inode, credential exclusion and file-fact checks.

A present layer must equal its actual emitted settings. Its producer must select
Agy, the same account, a selected unbound receipt/binding, immutable role profiles,
and only coordination `PreInvocation` and `Stop` rows at timeout 15. Ordinary
memory remains unsupported. The six-element event argv, canonical content-addressed
launcher, receipt image, source entrypoint/alias, retained launcher and emitted
anchor must agree. Unrelated settings bytes remain observed. Foreign, extra or
partial hook coverage refuses rather than inferring command ownership.

`cairn.watch-agy-readback/1` distinguishes `code-preparation` from
`retained-native-strace`. Preparation cannot carry native-looking fields. The
retained path requires an existing catalogued session, matching native identity,
workspace/config, pinned client, literal client argv and a pinned complete raw
`strace -f -xx` prefix with an explicit prompt interval. This is a finite codec,
not an Agy settings or listing API. Unsupported, truncated and unfinished traces
refuse. Trace bytes have a separate 256 MiB bound; the second hook inspection
rereads their pins after the normal final census reread.

The raw codec requires successful native file opens, complete bytes through EOF
and close, exact native event/session/workspace writes, and matching literal
callback program argv with distinct PIDs greater than one and later zero exits.
Native threads come from observed `CLONE_THREAD` calls. Commands and native event
frames are matched as multisets. Identical commands in two layers require two
executions; there is no per-PID layer attribution or deduplication. Unknown hook
paths, extra callbacks, stale identities and wrong bytes refuse.

Retained provenance still adds a blocker for current image/session qualification
and refresh. It cannot make `FixtureReady` true or satisfy authenticated D2.
The historical native report's layer list is fixture-declared. It is never
recast as a native `get_hooks_listing` result. Old trace paths and sessions are
not rewritten into a current positive packet.

The focused tests use explicitly synthetic in-memory codec bytes and selected
exact frames from a pinned retained Agy 1.5.7 trace. The latter controls verify
original byte offsets and hashes and test decoding only. Neither creates native
settings, registration, authentication or session state. Set
`CAIRN_R06_AGY_RETAINED_FRAMES` to the selected codec corpus. Set
`CAIRN_R06_AGY_TYPED_FIXTURE` to `typed-agy-preparation/go-typed-fixture.json`
for genuine producer controls via `TestR06AgyTypedCensusProcess`. These private
entries are inactive without explicit inputs; a skip is not coverage.

Strict install-3/install-5 remain open, including transient both-active settings
with two journals, twenty post-rename NEW-active hard-kill observations, legacy
writers and opaque attribution. The earlier typed-reader cut included no old Agy baseline, receipt-fault/SIGKILL
series, real API/D2 replay, native/model run or full suite. The private drain
component above adds only its stated disposable authenticated evidence. Full
R06/HC05/native/live R07 remain separate and open.
