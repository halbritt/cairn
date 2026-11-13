# Unbound census association and the next Agy blocker

The finite census can associate genuine disabled `/3` receipts with unbound
profiles without editing their JSON. This removes the empty-binding-path
failure recorded by diagnostic `094393e2648d978998f8b00c59e469c03ffbc085` on
`7d22b663d6abb0295dda0263adc9033353e5cefa`. Its frozen packet remains historical
evidence. This association does not implement Agy native hook readback.

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

Two independent owned fixtures distinguish the claims:

- With the ordinary nonempty account, receipt association succeeds and the
  current account-layer reader returns the exact next refusal:
  `account hook/config coverage is unknown: selected native settings/readback seam is unavailable`.
- With explicitly empty account/state layers, finite association and resource
  enumeration can succeed. The report says `ASSOCIATION_ONLY`, never native or
  authenticated D2 qualification. External coverage stays unknown and native
  refresh unobserved.

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

The current native hook reader continues to recognize Claude `flagSettings`.
Actual Agy account/project groups are cumulative; identical commands in both
layers execute twice. The retained Agy report's layer list is fixture-declared,
not a native `get_hooks_listing` response. A future typed reader requires the
correct native observations and exhaustive declared inventories. This cut does
not deduplicate commands, invent effective settings, or bypass that reader.

Strict install-3/install-5 remain open, including transient both-active settings
with two journals, twenty post-rename NEW-active hard-kill observations, legacy
writers and opaque attribution. No old Agy baseline, receipt-fault/SIGKILL series,
real API/D2 replay, native/model run or full suite is part of this cut. Full
R06/HC05/native/live R07 remain separate and open.
