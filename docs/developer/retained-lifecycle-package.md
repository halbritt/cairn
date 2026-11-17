# Retained installed lifecycle packages

`cairn.lifecycle-retained-package/1` describes original installed Python `/2`
associations alongside an immutable generation of byte copies. It supports
installed profile paths, renamed adjacent entrypoints, an explicit coordination
facade/delegate, multiple complete closures, and one declared shared Codex
cohort. Original binding, profile, argv and evidence files remain byte-for-byte
inputs. The generation-local retained format remains supported separately.

The maintained producer is
[`stage_lifecycle_compatibility.py`](../../scripts/stage_lifecycle_compatibility.py).
The independent Go checks are shared by the bootstrap association reader and
the native retained hook reader in
[`retained_package.go`](../../internal/lifecycle/coordination/retained_package.go).
These interfaces inspect selection data without executing the selected Python
source or interpreter. Passing them does not establish native refresh, runtime
adoption, reverse custody, authenticated drain, or permission to operate a host.

## Selection and publication

The producer accepts a plan with exactly `id`, `package`, and `material`.
`id` is a canonical nonzero UUID. `material` maps every declared generation data
filename to an original source pin. A pin has exactly lowercase `path` and
`sha256`: a canonical absolute path and lowercase SHA-256 of its actual bytes.
Inputs retain the existing bounded regular-file, ownership, no-follow ancestry
and stable-read checks. No credential content is a package input.
The outer generation remains inside the bootstrap plan root. Explicit original
profile and source pins can be external, retaining credential-alias exclusion,
safe ancestry and complete closure checks. Source/data parents remain private
`0700`; original `0600`/`0400` profiles retain the existing safe `0755` parent
support. This does
not widen the placement of the original binding, target images or state.

```sh
python3 -I -B scripts/stage_lifecycle_compatibility.py --stager "$STAGER" stage-retained --root "$GENERATIONS" < "$PLAN"
python3 -I -B scripts/stage_lifecycle_compatibility.py --stager "$STAGER" inspect --legacy --path "$MANIFEST" --sha256 "$MANIFEST_SHA256"
```

The selected package pin names the **outer generation `manifest.json`**, not
`retained-package.json`. The existing Go generation publisher authenticates
every generation member, including `retained-package.json`. Inner `files`
describes the data members and excludes this metadata member, avoiding a
self-hash. The producer validates the proposed data before publication, then
inspects the published generation. Publication and inspection do not activate
hooks or alter the original files. A publication error requires reconciliation
of the selected immutable destination before another attempt.

The generation contains precisely the 17 lifecycle source copies, `binding.json`,
`lifecycle-bundle.json`, `retained-argv.json`, the declared profile copies, the
declared evidence, and `retained-package.json`. Extra, missing, executable,
changed, mixed `/3`, or otherwise unsupported material refuses. Naming an extra
in `files` cannot make it admissible.

## Package fields

The metadata object has exactly the following fields. Map fields are nonempty
and bounded to 128 entries. Package, binding, sidecar and evidence metadata use
duplicate-reject JSON. Original profile bodies preserve the existing
duplicate-last policy; their complete raw bytes remain pinned and copied.

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `cairn.lifecycle-retained-package/1`. |
| `host` | Nonempty lowercase ASCII letters, digits and hyphens. |
| `harness` | `codex`, `claude`, `opencode`, `agy`, or `hermes`. |
| `multiplicity` | Integer `1`, or `3` for Codex only; equal to the member count. |
| `primary` | One declared member whose original vector remains in `retained-argv.json`. |
| `binding` | Pin of the original `/2` association, with matching `binding.json` copy bytes. |
| `profiles` | Generation profile-copy name to original profile pin. Each binding profile has exactly one matching copy. |
| `closures` | Absolute installed root to complete bundle descriptor plus an explicit `manifest` pin, as below. |
| `members` | Member name to exactly `config`, `argv`, and `runtime`. |
| `evidence` | Allowed evidence filename to exactly `sha256` and `members`. |
| `files` | Every generation data filename to its byte digest. |

A member name matches `[a-z][a-z0-9-]{0,63}`. `config` is exactly one original
coordination profile descriptor from the binding. Every coordinator occurs
once. `argv` maps `memory` and `coordination` to their original literal string
vectors. For an explicitly absent memory descriptor (`{"absent":true}`), only
`coordination` occurs. `runtime` has the same role keys. An absent memory role
cannot be enabled. The binding's existing profile agreement, account, state,
selector and enabled-state checks still apply in their respective consumers.

Profiles may continue to name the original installed binding. Selecting the
generation's binding copy in their place is not a relocation rule. A separately
selected disabled unbound association remains unbound; package metadata does
not install a selector into its original profiles.

Each closure has exactly `schema`, `sha256`, `files`, and `manifest`. The first
three fields are the existing `cairn.lifecycle-bundle/1` descriptor: the complete
17-file name/digest map and its canonical map digest. `manifest` separately pins
the actual adjacent `lifecycle-bundle.json` bytes at that root. Its decoded
descriptor must equal those first three fields. A derived map digest is never
the sidecar's byte digest. Unknown, missing, changed or disagreeing sidecars
refuse; a fixture-generated sidecar does not attest to an installed original.
All declared closure files are independently read and checked, including
closures used only for preserved source custody. The original engine and
bridge descriptors must select their own complete declared roots and roles.

Each runtime role has exactly `entry`, `delegate`, and `closure`. `entry` pins
the actual installed script named by that role's argv. `closure` selects a
declared root. With `delegate: null`, the entry is adjacent to that root's
loader/sidecar and its bytes equal the declared `memory.py` or
`coordination.py` role. Its filename may be an alias such as `lifecycle.py`.
Equal script bytes at a different root do not establish the same import closure.

For the existing coordination `runpy` facade, `delegate` pins that root's exact
`coordination.py`, and the facade bytes must equal the supported literal
`run_path` template naming that exact absolute delegate. Other wrappers refuse;
there is no Python evaluation or inferred fallback. Facade paths containing a
quote, backslash or newline are outside this template's grammar.

Literal vectors retain their interpreter spelling and distinct `-I`, `-S`, and
`-B` flag order. Memory ends with `--config ORIGINAL_MEMORY`; coordination ends
with `hook --config ORIGINAL_COORDINATOR`. The original `retained-argv.json`
equals the primary member's vectors; another member selects its own declared
vectors through its original configuration and `argv-MEMBER.json`.

## Evidence is checked against associations

The only evidence names are `native-representation.json`, `provenance.json`,
and one `argv-MEMBER.json` per declared member. Global evidence declarations
contain the sorted complete member names; each argv declaration contains only
its own member name. Their byte digests must match the authenticated generation
data. There is no wildcard or ignored-extra rule.

Each argv record equals its member's association vectors. Native evidence
membership is the corresponding `host/member` list; provenance membership is
the member list. Provenance's native representation equals the preserved native
record. Its four original-rewrite flags must be false, and profile correspondence
must exactly cover the declared original-path/copy-name/digest associations.
Recognized native source pins and literal or derived vectors must agree with
the selected associations, including role and member variants. Agy's existing
event suffixes remain supported. Opaque historical evidence stays byte-pinned;
it does not select execution or resolve disagreements. Any disagreement refuses.

Hermes internal-engine evidence can describe the retained in-process provider.
This schema does not introduce a Hermes hook CLI, change provider selectors,
or add Hermes to the hook-settings stager. Watcher policy and issued installed
helper/context commands are separate contracts and are not rewritten here.

## Current66 installed-data evidence

Root1496's `20a933da/v1` selection accepts8 installed `/2` retained packages
paired with8 disabled `/3` forward generations:8 cohorts/10 distinct members
on3 hosts, with all10 actual associations reviewed. Shared Codex remains one
cohort/three proofs; proximal Agy and Archon Codex memory are genuinely absent.
The [current packet selection](../verification/r07-execution-packet-draft.md#current-66ca-staged-data-selection)
records counts, actual outer-generation pin locator and exact custody limits.
The raw17-file installed closures, original config/native argv and facade/delegate
bytes were preserved. Forward18 uses the maintained guarded-source transform
and aliases, not raw-source equality. Raw sidecar SHA remains distinct from its
decoded map digest. These data readbacks do not execute a retained runtime.

Talos's exact two source-parent0755-to0700 changes and recorded, unexecuted
same-inode0755 inverse leave the current0700 source/data contract intact;
bindings0755 is unchanged. Broader0755 source-parent support is not implemented.
Actual staged pins do not fill strict operator plans, owner/callback/Scope or
future runtime captures. Rust remains parked; aggregate Stage1, final1415
adequacy, BOTH cutover-split decision, Stage2 and all observation clocks stay open.

## Interim watcher and required Go adoption

The initial R07 hook/binding cutover conditionally retains the current raw
Python presence watcher selector and bytes. This boundary requires the actual
captured watcher to pass
[`check_r07_captured_watcher.py`](../../scripts/check_r07_captured_watcher.py)
against a complete target `/3` selection. That fixture uses real disposable
API/PostgreSQL authentication and clients, eight member profiles in six cohorts,
shared Codex member distinctions, and the same watcher-lock inode. The terminal
host is scripted; the owner invokes the selected current Go `/3` hook and the
captured Python `/2` hook after reverse. There is no native/model call. The
captured 17-file closure uses its byte-preserved selected sidecar; its file-table
digest remains distinct from the sidecar byte digest.

Step A stops the watcher before either exchange. The fixture checks stopped
forward exchange and restart on `/3`, stopped reverse and restart on the
original selection, owner wake and settlement, and refusal to exchange while
the watcher holds the lock. A held-lock refusal cannot be converted into a
positive by weakening the fence or forcing the exchange. Any named watcher
contract failure stops qualification and returns to both gatekeepers for a
Go-selector decision before further implementation or widening. A partial run
cannot qualify this boundary.

Go watcher adoption is a required later step with its own admission, after
the initial lifecycle cutover. It must select the supported Go unit, retain
byte-exact reverse and the same lock identity, and preserve mixed-unit
membership. The affected lane must then complete at least 24 hours of R08
observation on the Go watcher before R08 completion and R09 removal of Python
lifecycle code. Go adoption cannot be omitted or silently deferred into R09.
This contract starts no R08 clock and admits no host operation.

The presence90 drop-in's raw custody under the `0775` user-systemd/service.d
parents remains OPEN and a hard exclusion. Existing unit/show observations do
not replace the missing raw fragment/drop-in bytes and metadata. This package
admits neither an alternative read nor a parent-mode repair.

## Issued inbox commands and final-image coverage

Issued context and compact commands validate the selected configuration through
the same profile route as hooks. An explicitly unbound profile, including a
coordination-only selection, checks the disabled manifest and every unbound
profile pin. An opted-in profile keeps the full checked-binding validation.
Disabling a manifest cannot downgrade an opted-in sibling. State-directory,
context-path, binding, receipt, client, socket and credential-selector checks
remain in force.

The issued operations are context `ack`, `complete`, `respond`, `finish-reply`,
compact `read`, `inspect`, `settle`, `advance`, and the tool-cued `drain`.
`acknowledgement` is the context field that issues `ack`, rather than another
CLI spelling. The focused process tests execute those tails through independent
Go and Python `/3` frontends for bound, unbound-with-memory and coordination-only
profiles. They assert journal commitments, exact API arguments/results, separate
neighbor delivery, and refusals before store effects. This proves frontend
behavior with disposable protocol fixtures, not final-image roster adoption.

The earlier 5275 full11 rehearsal did **not** execute issued inbox commands for
its three unbound members. Its pinned driver
`scripts/check_r06_agy_native_unit.py` at `5275bd3d` runs the primary scripted
Agy hook sequence and includes two additional unbound profiles in producer/census
inputs. The retained `actual-api-bridge-calls.json` contains 31 calls: registration,
heartbeat/context, wake-attempts, event-watch and session-inbox-control; no claim,
ack, completion, response publication or drain. The whole-case PASS therefore
did not cover issued commands. The later current66 gate is now accepted as a **finite issued-command fixture**:
Root's merged96 selects79 old+17 new keys,82 positive cases and14 negative
groups/64 unique refusals. All80 old records and both c045 records remain in97
retained records; the first24 are not added. Unsupported compact/batch remains
valid no-emission/refusal coverage, not manufactured positives. Original90s
native grants and900s ordinary drain remain unchanged. Claude channel state
is synthetic. Old negative SQL equality remains collector testimony without
raw snapshots; the new16 refusal snapshot pairs are retained. This closes the
named finite fixture gate, not native adoption or full-design qualification.
See the [R07 gate](../verification/r07-bootstrap-reverse.md#final-image-issued-inbox-gate).

```sh
mise run test:unit -- scripts/test_issued_inbox_compatibility.py -v
```

## Consumer adapters and validation scope

In a `cairn.unit-bootstrap/2` association, `package` optionally selects the outer
generation pin. As with existing bootstrap pins, its keys are uppercase `Path`
and `SHA256`. The association still includes the original `binding` and its
member-specific `argv` pin. `package` and a `/3` `receipt` are mutually exclusive.
Omitting `package` keeps the generation-local retained reader. The original
five binding/profile selector comparisons are unchanged.

The hook-settings producer selects the package using `legacy: true` and the
existing `coordination_index`. Its `retained_python` proof retains the six
existing fields and adds lowercase `package` and `member`. The native reader
authenticates the whole package, original binding/configuration, exact generation
member set and member proof before reading that member's vectors. A different
primary member cannot choose its commands.

The public finite Claude census authenticates an explicit package before its
member/profile reads. Only that package's pinned original profiles and exact
selected copies use duplicate-last source JSON and the existing controlled
`0600`/`0400` profile reader with safe `0755` parents. Metadata keeps its strict
private reader. Original profile identity and ancestry are checked again at
readback. The five client/socket/token/repository/harness comparisons and
state/account agreement still apply. A package can select different complete
role closures; hook rows must match its exact installed entry, delegate,
original profile and literal argv. Standalone launch lookup independently
checks the selected generation, package membership and member proof.

This does not widen the native observation boundary. Every selected original
profile, closure and entry stays inside the finite catalog root, and hook
selections still require `disposable-fixture`. External original inputs supported
by the explicit bootstrap/package helpers do not grant native census coverage.

The focused checks use the real Go publisher, maintained Python producer and
inspector, independent Go validator, full bootstrap association and native
retained reader/launch selection. Disposable three-host shapes include all five
harnesses, shared Codex multiplicity, bound/unbound and coordination-only roles,
multiple closures, aliases and facade/delegate. Refusals cover undeclared and
declared extras, evidence hashes/disagreement, wrong member/binding/argv,
unsupported multiplicity, changed source, wrong closure/delegate/alias root,
and absent, changed or inconsistent sidecars.

The public census regression uses two Claude package selections with a 284-byte
coordination facade and delegate, a memory alias, distinct complete role
closures, separated original bindings, safe `0755` profile parents and
duplicate-last profile bytes. The maintained session producer uses synthetic
registration. Effective settings and listing are declared fixture inputs;
owned Python exec/exit frames are collected with `strace` under the existing
disable guards. Full public census results and file facts are asserted, with
wrong package/member/entry/closure/source/observed argv refusals. This is source
and process unit coverage, not a native client run, supported refresh or host
admission. Malformed native evidence also refuses through the public staging
and inspection CLIs without publication, traceback or inspection effects.

```sh
mise run test:unit -- scripts/test_retained_lifecycle_package.py scripts/test_r04_retained2_hook_settings.py -v
mise run test:unit -- ./internal/lifecycle/coordination -run '^TestRetainedPackage' -count=1 -v
mise run test:unit -- scripts/test_r06_retained_hook_census.py -v
```

The optional test-only `CAIRN_RETAINED_PACKAGE_SOURCE_FIXTURE` selects a pinned
three-set JSON file (`base`, `hotfix`, `other`) of complete public code-copy
descriptors. It enables parity checks over captured source bytes without
executing those bytes or reading installed originals. The optional
`CAIRN_RETAINED_PACKAGE_SIDECAR_FIXTURE` selects raw sidecar-copy pins labelled
`base`, `hotfix`, `other`, and `canonical`. It preserves their bytes, and adds a
refusal case pairing the canonical sidecar with a different captured closure.
Without it, sidecars are explicitly generated fixtures. Fixture paths remain
synthetic; the selected data ledger preserves original path/hash correspondence.
These results remain local data-contract evidence, not application/native host
qualification.
