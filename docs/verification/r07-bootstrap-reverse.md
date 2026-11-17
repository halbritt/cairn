# R07 bootstrap exchange and retained reverse

`cairn-lifecycle unit-bootstrap` now has an effectful operator path for a
pre-staged complete watcher selection. It captures the landed selected-unit
fence, reads authenticated D1–D5 facts for both selections, takes the actual
watcher flock, rechecks D6 and exchanges the two directories atomically. Reverse
moves the original directory itself back, preserving original config and opaque
archive inodes, bytes, modes and owners. Rename changes directory ctime.

This is source engineering and disposable-fixture verification. It does not
admit a production operation, refresh a native client, establish installed
runtime/ABI adoption, or complete R07. The existing current-/3 `unit-control`
consumer is unchanged. A selected-file S04R staging utility supplies the actual
API launcher flags; starting or changing a managed API remains an admitted
operator step.

The initial hook/binding cutover conditionally keeps the raw Python presence
watcher. Its separate captured-watcher fixture must prove the complete `/3`
selection, same lock, owner wake and settlement across stopped forward/reverse
exchanges and restarts; a lock-held exchange must refuse. A partial or failed
fixture admits no watcher reliance. See the
[interim watcher contract](../developer/retained-lifecycle-package.md#interim-watcher-and-required-go-adoption).

Adopting the Go watcher is a mandatory later step with its own admission after
the initial R07 cutover, supported unit selection, byte-exact reverse, the same
lock identity and mixed-unit membership. R08 must include at least 24 hours
of affected-lane observation on the Go watcher before R08 completion and R09
removal of Python lifecycle code. No observation clock starts here.

## Current66 staged-data status

Current source66ca/treee23c6306/parent416a and its five accepted FINAL5 roles
are separate from historical5275/462/416a receipts. Root1496 accepted15
same-host imported role files and all10 member associations in16 actual
generations (8 disabled forward `/3`,8 retained installed `/2`;8 cohorts).
See the [current packet selection](r07-execution-packet-draft.md#current-66ca-staged-data-selection)
for counts, exact cache locators, OptionC mode ledger and the parked Rust state.
This completes lifecycle file-data preparation only. The three current assemblies
are nonexecutable metadata with actual pins; their nine future captures per host
remain unfilled. Current Go bootstrap inspect, exchange/adoption, native/runtime,
aggregate Stage1 and Stage2 are not established by these readbacks.

The first future post-layout inspect must select the **disabled target** and
return exit0/schema `cairn.unit-bootstrap-result/1`, `inspected=true`,
`rechecked=true`, `published=false`, before any exchange. Enabled-original `/2`
read-only refusal is intentional. Each exchange needs its fresh real<30s fence,
positive library/full-launch margins and final same-Scope Recheck before Rename.
Reverse and rollback select the SAME ORIGINAL transition-plan path and SHA.
Raw retained17 and guarded/aliased forward18 snapshots have different derivations;
the accepted stage receipts do not assert raw-source equality or execute hooks.

## Operator commands and selection data

The ordinary private frontend uses the concrete coordination profile and
FileHost. It accepts exactly an operation, a private pinned plan and its hash:

```sh
cairn-lifecycle unit-bootstrap inspect --plan "$R07_PLAN" --sha256 "$R07_PLAN_SHA256"
cairn-lifecycle unit-bootstrap switch --plan "$R07_TRANSITION_PLAN" --sha256 "$R07_TRANSITION_SHA256"
cairn-lifecycle unit-bootstrap reverse --plan "$R07_TRANSITION_PLAN" --sha256 "$R07_TRANSITION_SHA256"
cairn-lifecycle unit-bootstrap rollback --plan "$R07_TRANSITION_PLAN" --sha256 "$R07_TRANSITION_SHA256"
```

These are supported source interfaces, not authorization to run against an
installation. The same immutable transition plan selects both directions.
`reverse` expects the forward exchange to have happened. `rollback` recognizes
exact original/forward directory identities: it restores the original after a
published exchange, or confirms the unchanged original after an unpublished
failure. An unknown third placement refuses. It never retries the switch as a
recovery shortcut.

The historical `cairn.unit-bootstrap/1` plan remains read-only. An effectful
`cairn.unit-bootstrap/2` plan names the original directory/pins, exact opaque
archive and watcher-lock metadata, credential **selectors**, callback identities,
per-member associations and a transition. The transition selects another
private pinned `/2` plan, both directory device/inode/owner/mode identities, and
an exact `SelectedUnitFenceScope`. Nested keys are literal and case-sensitive;
unknown fields, duplicate keys and invalid Unicode refuse. No JSON field carries
a fence or drain receipt.

Prepare the disabled `/3` generation through the maintained
`scripts/stage_lifecycle_compatibility.py` and `cairn-stage-lifecycle` publisher.
A separate private sibling contains only exact immutable `0400` copies of its
selected coordination profiles and the watcher lock. The original and target
must contain the same binding names. Their binding/account/state/socket/repo/
credential identities, including memory state, must correspond. The `/3`
compatibility receipt, complete source closure, selected binary, profiles and
manifest are read and verified as selection data.

Both directories must be beneath one owned `0700` root with safe, no-follow
ancestry. Each state directory's ancestors are checked before walking it.
Existing `0700` and explicitly observed legacy `0755` directories are supported
inside that private root; the scoped config reader walks actual descriptors
without following links and uses the ordinary stable-FD reader/JSON decoder.
The shared compatibility reader's `0700` immediate-parent rule is unchanged.
The original PATH executable has a separate bootstrap-only reader. Each path
component is canonical and opened without following symlinks. Ancestors are
root/user-owned and have no group/other write (the existing sticky-root exception
applies); the final regular executable is user-owned, executable, singly linked,
and has no group/other write or special bits. An owned `0755` PATH parent passes;
`0775`, a foreign owner, replacement or a symlink refuses. Original role-profile
inputs may be selected outside the private Cairn root only as exact `0600`/`0400`
pins with that safe ancestry. Target images, credentials, source closures and
state retain the generic private-parent rules. No HOME chmod is performed.
Any installed writable ancestry needs its separately admitted narrow remediation;
fixture-owned `0775` refusals and `0755` preparation do not establish readiness.

The optional `/2` plan field `external_states` selects affected state resources,
not authority. Each array entry has exactly `member`, `role`, `directory`, and
`identity`. `member` is one selected coordinator JSON name; `role` is exactly
`coordination` or `memory`; `directory` is the actual canonical absolute
`state_dir`. `identity` has the same literal `name,type,uid,gid,mode,device,inode`
fields as other directory identities, with type `directory`, this UID, and mode
`0700` (decimal `448`). Both plans must select identical declarations and
directory identities. `/1` plans cannot use this field.

An external state root must be declared for exactly one member's role profile.
It cannot equal, contain, or lie inside the private plan root, an account home,
a pinned binary directory, or another affected state root. Every component is
opened without following symlinks; ancestors must be root/user-owned with no
group/other write, retaining the existing sticky-root exception. The leaf must
actually be the pinned, user-owned `0700` directory without special bits. An
unselected, duplicated, overlapping, replaced, foreign, indirect, or writable
resource refuses. This exception applies only to state-file inspection beneath
that verified root. Other profile/source/image/credential references retain
their existing rules.

For a declared external root, its parent uses the safe ancestor rule rather
than an immediate-parent `0700` requirement. The state-directory walk still
requires every visited directory to be `0700`; content pins, credential-alias
exclusion, lock ownership, grants, sessions and authenticated D1–D6 reads are
unchanged. External directory/ancestor identities are freshly checked around
the API observation and again during commit preparation and reverse. Existing
Hermes `~/.hermes/cairn/state` can therefore remain `0700` under its owned
`0755` parent, without moving state or changing HOME.

The runtime contracts permit that layout. Go coordination
[`ValidateConfig`](../../internal/lifecycle/coordination/cli.go) requires an
absolute `state_dir`; [`NewFileHost`](../../internal/lifecycle/coordination/file_host.go)
derives session paths from it and
[`AcquireSessionLock`](../../internal/lifecycle/coordination/locks.go) creates
its parent with `0700`. Go memory activation
[`DecideActivation`](../../internal/lifecycle/ledger/activation.go) checks an
absolute, owner-controlled directory without a plan-root containment rule.
Python coordination [`validate_config`](../../integrations/lifecycle/coordination.py)
also requires an absolute selector; Python memory
[`handle_event`](../../integrations/lifecycle/memory.py) creates its selected state
directory with `0700`. Neither runtime requires the existing external parent
to be private or the state to lie inside this bootstrap plan root. Their
runtime code and the generic compatibility reader are unchanged.

The disposable production-shape fixture records directory preparation and its
reverse explicitly. Proximal's Cairn-owned `coordination/state` starts `0775`
and archon's starts `0755`; their required prepared mode is `0700`, not `0755`.
Talos starts without that parent or coordinator leaf and without the OpenCode
memory state leaf; its `opencode-hooks` parent starts `0755`. Preparation creates
only the missing owned `0700` parent and changes the required private parent to
`0700`; reverse restores its exact original mode or absence after consumers
join. Original talos coordination/bindings `0755` remains supported and is not
normalized away. Shared `.local`/share/PATH ancestor preparation and reverses
are separately recorded. HOME is never chmodded. The production-shape fixture
selects explicit coordination-only roles for proximal `agy-one` and archon
`codex-archon`, without an inactive memory profile or memory directory. Other
members retain their paired-memory cases. Fixture material does not establish
installed selectors or authorize host preparation.

An existing lock must be the same actual inode in both selections, linked during
off-path preparation. Its exact exclusive flock is identified through
`/proc/self/fdinfo`; other owners, waiters and ambiguous inode observations
refuse. No original lock is created or repaired by observing it. A talos-shaped
original with an explicitly observed absent lock can use the prepared target's
new private lock. Reverse restores the original absence and directory mode;
it does not silently initialize the retained generation.

## Bound and unbound retained Python /2

An explicitly selected
[retained installed package](../developer/retained-lifecycle-package.md) also
supports original installed paths, separate complete closures, aliases and the
existing coordination facade/delegate. Its outer generation manifest authenticates
declared per-member argv and evidence without relocating original selectors.
Every declared closure separately pins its actual adjacent bundle sidecar.
Unknown installed sidecars remain a qualification gap; generated fixture
sidecars cannot fill it. Omitting the package selector keeps the existing
generation-local association below.

Every original member selects its actual `/2` manifest and literal
`retained-argv.json`. Bound profiles name their real installed manifest. A
previously unbound profile has no installed binding selector: its separately
staged, disabled off-path association describes the exact memory/coordinator
profiles, source bundle and literal commands. It is selection data, never an
invented installed binding or a `/3` receipt. All selected members remain in the
plan. No `/2` profile or command is normalized into `/3`.

An unbound member with no memory role declares exactly
`memory_config: {"absent": true}` in both its retained `/2` manifest and its
disabled `/3` binding/receipt. Its literal retained argv has only the
`coordination` key. This declaration is pinned selection data, not an omitted
association: every member still needs its explicit association and complete
source closure. Missing/contradictory declarations, bound or enabled no-memory
selections, and memory-role invocations refuse. Both sides preserve absence;
state/drain coverage includes the actual coordination state without inventing
a memory state. Paired-memory descriptors and validations remain unchanged.

The reader checks the engine/bridge, all 17 named source members and their bundle
digest. Each selected role's argv must select the actual source generation,
role and exact profile (`hook --config` for coordination). Shared bound groups
retain the existing profile validator: memory home is unnecessary, one
coordinator may omit its home, and multiple coordinators need distinct explicit
canonical homes. The source profiles, literal argv and Python files are never
rewritten. Archives and credential contents are not read.

An enabled installed `/2` manifest needs a real disable before namespace
publication. After a fresh stopped fence, lock and D1–D6 preflight, the consumer
prepares `.unit-bootstrap-legacy-<selection digest>` beneath the private root.
Its owner-only recovery journal binds exact selection paths/pins and original
file identities. For each enabled bound manifest it derives bytes that change
only `enabled`, fsyncs the complete preparation, then uses descriptor-bound
`RENAME_EXCHANGE`. The actual raw original inode moves to the recovery directory;
it is never replaced by a rewritten copy labelled "original". The complete
disabled selections are freshly checked before exchanging watcher directories.

Reverse verifies both resulting selections before exchanging each manifest back.
It restores the original bytes (including `enabled:true`), device/inode, mode,
owner, size and mtime. Rename ctime is not preserved. A fresh `rollback` reconciles
partial disable, completed namespace publication or partial raw restoration
from the actual names, bytes and identities; unknown or modified recovery data
refuses. An incomplete pre-publication preparation remains retained evidence.
A later `switch` does not replay an interrupted disable. Keep recovery artifacts
until separate owner-controlled reconciliation/cleanup; this command deletes no
retained original.

The journal is not a fence or drain receipt. Every recovery invocation captures
a new process-local fence and repeats plan, profile, state and authenticated API
checks. Neither a JSON `disabled`/`stopped` claim nor journal contents skip those
checks. `published` describes the watcher-directory exchange; a pre-exchange
error can leave legacy manifests disabled, so `reconciliation` and a fresh
rollback are required even when `published` is false.

## Account identity and genuine unbound /3

`config_home` is an account-directory identity selector on both sides. Bootstrap
opens its canonical absolute path without following any symlink component,
requires safe root/user ancestry and a user-owned directory with no `0022` or
special bits, and freshly checks its path/device/inode/mode/owner across the
observation and exchange boundaries. Owned `0755` and `0700` pass, including
outside `plan.Root`; replacement, foreign ownership, `0775` and symlinks refuse.
Original/target account identity must correspond. The consumer never reads
inside, chmods, relocates or copies an account directory or HOME. The private
Cairn root, state, credential, socket, image and source-closure rules remain.

This selector is compatible with the actual runtimes. Go's existing binding
predicate ([binding.go](../../internal/lifecycle/binding/binding.go):109–130,
210–222) resolves coordinator homes, requires distinct explicit homes only for
shared groups, and checks absolute paths when present. Go admission
([admission.go](../../internal/lifecycle/admission/admission.go):236–266,
[wake.go](../../internal/lifecycle/admission/wake.go):29,63) matches the
originating Codex/Claude account; transcript selection
([transcript.go](../../internal/lifecycle/transcript/transcript.go):601–614)
passes the selected home. None requires account mode `0700` or Cairn-root
containment. Python reverse retains
[inbox_binding.py](../../integrations/lifecycle/inbox_binding.py):39–51 and
[hook_admission.py](../../integrations/lifecycle/hook_admission.py):141–145:
coordinator home is optional for a single profile, memory home is not required,
and account matching resolves configured/active paths. No runtime code changes
are part of this bootstrap cut.

A target unbound `/3` branch is selected by its actual publisher-produced
receipt/manifest and all profile hashes, not merely an absent caller field.
The existing `CompatibilityContext.UnboundProfiles` verifies schema, binding,
descriptors and unbound role profiles. The bound branch stays strict. Removing
a bound field, inserting a binding selector in an unbound active profile, or
changing the receipt hash/schema/binding refuses. Agy/Hermes/OpenCode retain their
real harness kinds and absent account homes. An unknown retained activation
grant still refuses; staging a receipt does not grant native support or session
visibility.

## Stopped boundary, D6 and D7

The public operation captures `CaptureSelectedUnitFence(ctx, scope, nil)` in
its own process and invokes `Recheck` on that same opaque receipt. Database and
system-ID selections must be empty: `database_coverage` is `not-selected`.
The [landed fence contract](r07-selected-unit-fence.md) retains
`external_coverage=selected-units-only` and `outside_scope=unknown`. It observes
selected stopped/masked definitions, cgroups and process identities; it does
not issue stop/mask/start/restart operations.

A metadata-only pass checks both full layouts before opening the existing
watcher lock. Authenticated drain reads then observe both complete selections
while holding that exact flock. Immediately before
exchange, both pinned plans, layouts, statuses, local state and associations
are reread and actual directory identities are checked again. After all
callbacks, descriptor opens, state/operand checks and rename-argument
preparation, the same receipt performs a fresh same-scope `Recheck` immediately
before `Renameat2(RENAME_EXCHANGE)`, in both directions. No work intervenes
between the successful final recheck and the syscall. The final read/action
interval is not atomic with external
activity. Neither a quiet sample nor masks observed at two instants prove a
host-wide future-writer barrier.

A final recheck refusal performs zero namespace exchange and leaves the active
directory identity intact. Earlier raw-manifest disable and recovery-journal
effects can already exist; `published=false` does not mean zero earlier effects.
The result retains that reconciliation state and never automatically re-enables
hooks after writer reactivation. Re-establish the stopped boundary, then use
the documented fresh `rollback` and verify exact raw originals. Deterministic
tests inject late activation and generation changes after operand preparation
in both directions, including prior enabled-manifest effects and recovery.

Per-host fence inputs must preserve the API/relay transport used by D4. Do not
select generic `/usr/bin/python3*` executables or the PATH binary currently
serving proximal `cairn-api` or archon `cairn-relay`. Select exact retained
watch/hook command identities and complete stopped-writer coverage, or propose
dedicated private per-generation transport images before bootstrap. Those
transport moves remain unselected operator actions. Talos' zero-unit proposal
depends on the separately reviewed F4 fence change, nonempty executable
selection and actual manager/transient/process checks. Stop and disable every
selected activation trigger, retain previous enablement for reverse, and refuse
any reactivation/generation drift. Bind the actual composed fence/head, fresh
FINAL5 and final-image fixture/full11 receipts before R07 selection; older
native evidence remains labelled with its original `886262cf` head.

Linux `RENAME_EXCHANGE` publishes the pair, followed by parent fsync and complete
D7 readback of both resulting selections. Results keep `published` separate
from errors and report active/retained paths. A post-publication sync or readback
failure leaves both selections available and requires exact reconciliation with
a fresh `rollback` invocation (or `reverse` when the exact forward placement is
still present). A killed process's receipt cannot be
reused in a new process.

Lifecycle fencing performs no database connection or semantic writer query.
The explicit operator `pg_stat_activity`/`cairn.semantic_job` read and remaining
writer/restart graph are separate obligations in the R07 execution packet.
Their absence never becomes semantic coverage of this lifecycle receipt.

## S04R selected-file reverse staging

The same script has an explicit operator staging mode:

```sh
python3 -B scripts/check_r07_bootstrap_reverse.py --semantic-reverse-selection "$R07_SEMANTIC_REVERSE_SELECTION" --sha256 "$R07_SEMANTIC_REVERSE_SHA256" --publication-dir "$R07_NEW_PRIVATE_PUBLICATIONS"
python3 -B scripts/semantic_python_closure.py verify --publication "$R07_NEW_PRIVATE_PUBLICATIONS/payload.json"
python3 -B scripts/semantic_python_closure.py verify --publication "$R07_NEW_PRIVATE_PUBLICATIONS/controls.json"
```

The private pinned selection has schema
`cairn.r07-semantic-reverse-selection/1` and exactly: `payload` (path/hash of the
selected payload-reference JSON), `source_payload`, `original_config`
(path/hash), `stage`, `home`, and `expected_control`. It verifies the selected
source payload, copies it through the maintained immutable generation publisher,
then renders/verifies controls with `semantic_python_closure.py`. An existing
mismatched generation refuses rather than being repaired. Ordinary pending
publication failures use the maintained cleanup; a killed unpublished pending
generation is not selectable. A control-address mismatch leaves any immutable
staged artifacts unselected and changes no API configuration.

Owner source `63dffa76/v1` selects accepted payload
`c6c1bd58dccd51ae1b06c0213c94ef9f87206a80951fc198ff0674654b0c7221`,
original venv config hash
`7eacb9935ee06d946bcdcb29b6ef1767d22880c64c01c05dd980381105991aed`,
and the prepared production control
`05dd3ac9c7cd5f8552ecde0b2c4d97502997870ce8e87b1a1168968e98fbdb69`.
The separately owned empty `0700` HOME remains outside the immutable stage.
The pre-cutover live `semantic/worker-embeddings` launcher is not the accepted
S04R reverse. The stream launcher entry uses the descriptor's corrected
`73c0e89a6f1c310bfde2cb26080c2f3b67bac288b07f6310ef5e7c18a5870503`.

Output supplies exact `cairn serve` `--embedding-command`, `--semantic-command`
and `--semantic-stream-command` flags for the verified zero-argument launchers.
It does not invent an API JSON selector or rewrite a managed service definition.
The operator keeps the reviewed API's other arguments/credentials and applies
these flags only at its admitted stopped boundary. Staging executes no retained
interpreter or model. Production path qualification, loaded-byte/ABI checks,
model/effects evidence and actual API startup/readback remain R07 obligations.

## Local verification

### Final-image issued-inbox gate

The earlier `5275bd3d` full11 rehearsal covered eight bound and three unbound
members, but omitted issued inbox command execution for those unbound members.
Its maintained driver's `add_scale_peers`/`refresh_scale_catalog` supply the
extra profiles, while `run_session_sequence` drives the primary scripted hook
sequence. The retained full11 result SHA `4c7a10e5b8183e7be30fec9cdde65da2e7cbd61f7185b8d4290ce6a5c6e49355`
and `actual-api-bridge-calls.json` SHA `14b36314015c1a9c99ab622598fc8e90fc813815f2a973e08b1761cd919edf3f`
join to 31 read/presence calls and zero inbox-claim, ack, complete, publish or
drain calls. Member count and rehearsal PASS did not establish issued-command
coverage.

Root's current66 final-image issued-command gate is now accepted as finite
fixture evidence:96 unique selected keys (79old+17new),82 positive cases,
14 negative groups/64 unique refusals,97 retained records including both c045.
All80 old records stay preserved; the first24 are excluded. Applicable operations
ran through actual selected Go/Python frontends; unsupported cells preserve their
no-emission/refusal baseline. Native90s grants and ordinary900s drain remain
unchanged. Claude channel is synthetic. Old negative SQL snapshots are absent
and retain their live-collector equality limit; the new16 refusal pairs have
exact returned SQL bodies. This finite gate is not installed/native qualification.
Root's exact merged audit and current full11/R07/OpenCode limits are linked in
the [current packet](r07-execution-packet-draft.md#current-66ca-staged-data-selection).

The presence90 drop-in's raw custody under `0775` user-systemd/service.d parents
remains **OPEN and hard-excluded**. No alternative read or mode repair follows
from these fixtures. Later Go watcher adoption remains mandatory after initial
R07, with its **own admission** and at least **24 hours of Go R08 observation
before R09**. No fixture starts that clock or admits Stage2/production.

All test commands use mise and maintained host process ownership. The fixture
uses normal host HOME/CODEX_HOME/tools, disposable application CAIRN_HOME/state,
and a fresh PostgreSQL cluster. It stages actual `/3` material through the
maintained publisher and exercises the real FileHost/authenticated API transport.
Only systemd/cgroup observations are fixture data captured by the landed private
producer. No installed systemd or native client qualification is claimed.

```sh
mise run test:unit -- ./internal/lifecycle/coordination ./internal/lifecycle/transcript ./cmd/cairn-lifecycle -run 'Bootstrap' -count=1 -race -v
mise run test:unit -- scripts/test_r07_bootstrap_reverse.py
mise run host:exec -- --timeout 12m --weight 2 -- python3 -B scripts/check_r07_bootstrap_reverse.py --effects-only --evidence "$R07_NEW_PRIVATE_EVIDENCE"
mise run host:exec -- --timeout 20m --weight 1 -- python3 -B scripts/check_r07_bootstrap_reverse.py --production-shape-only --evidence "$R07_NEW_PRIVATE_PRODUCTION_SHAPE_EVIDENCE"
mise run check
mise run host:exec -- python3 -B scripts/verify_docs.py --tier 1,2,3,4
```

The `--effects-only` regression retains homogeneous bound/unbound cases.
`--production-shape-only` stages the actual proximal eight-member/17-archive
shape: one shared three-Codex bound group, two separate Claude bound groups,
and genuine unbound Agy/Hermes/OpenCode groups. Archon is one unbound Codex;
talos is one unbound OpenCode with an absent original state/lock and a `0755`
selected directory. Bound original memory profiles omit home, account selectors
use their real optional shape and `0755`/`0700` modes, original PATH parents use
`0755`, retained source members use `0700`, client images retain the full candidate bytes, and
every `/3` group comes from the
maintained publisher. No installed HOME or operational input is changed. It also
injects a separate actual owning API claim at D6 and joins SIGKILL consumers
before and after publication, recovering with fresh receipts. Protected store/
state snapshots and original member metadata are compared; child and PostgreSQL
cleanup are recorded. Source-fault unit tests cover copied/zero/foreign/drifted
fences, changed archives/bundles, writable targets, held locks, ancestor aliases,
strict selection fields and failures around exchange/fsync.

The production-shape fixture also checks each real unbound kind, bound-field
removal/unbound-field insertion, bad receipt hash/schema/binding, unsafe
PATH/source/share ancestry and exact raw reverse. It joins an actual SIGKILL
consumer after a partial multi-manifest disable, then uses a fresh rollback.
All selected coordination/memory state, API tables, account identities and raw
original manifest bytes/inodes/modes/mtime are compared. The test requires the
matching non-skipped Go test and its transition receipt; exit zero with no tests
is rejected.

The route-A workflow proof is labelled exactly
`SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE`. The public `RunUnitBootstrap` method
is the sole non-test caller of an unexported full CLI workflow helper and passes
literal `nil`. Nil captures the unchanged concrete default fence. Package tests
supply a genuine process-local capability from the existing private fixture
producer, exercising the same argument parsing, output, exit status and complete
transition workflow. No argv/environment/config field, exported constructor,
global hook or serialized value supplies that capability. An AST wiring test
fails on a second non-test reference/call or a non-nil production argument;
independent review must also check this capability boundary explicitly.

Each positive switch/reverse runs in a separately launched, joined test process
with a fresh `30s` workflow context. Receipts record workflow and launch-to-join
seconds and positive margins independently. The `90s` Go test / `100s` process
limits are outer cleanup limits, not operation acceptance. Direct library fault
injection remains separately labelled. The fixture hashes exact full candidate
API and hook images; it does not substitute padded or smaller clients. Test-only
API auditing forwards unchanged calls through the concrete FileHost transport.

The candidate's genuine default public command is also tested against the real
local systemd manager, using owned disposable plans and no service changes. It
must refuse the active shared Cairn workers before admission or publication;
the receipt is `DEFAULT_PUBLIC_REFUSAL_ACTIVE_WORKERS`. An actual live owned
callback supplies a later refusal guard if the manager changes unexpectedly.
The worker status and owned selections/API/state are compared before and after.
This refusal is not a default-fence positive. Public read-only inspect timing is
reported separately.

The first real default-fence positive and manager latency remain
**UNESTABLISHED until R07**: at each host's admitted stopped boundary, evaluate
the default fence before any exchange and measure the real operation against
its unchanged `30s` contract. On refusal, abort with no publication, retain the
current selection, report the exact refusing condition to the release owner,
and await a new admitted reconciliation; do not retry, stop shared workers or
weaken the default guard to force success. The execution packet must carry this
operator response. Route-A source/workflow proof does not qualify installed
manager latency or native adoption.

For coordinated fixture reuse, `production_shape_fixture(cluster, images, root,
host)` yields `(f, original_plan_path, original_plan_sha)` and owns only
`f.host.close()`/its API process. Caller owns cluster, root and images. It
initializes the actual `status_consumer.Fixture` before accessing its profiles.
`production-material.json` exposes exact target `directory`/`pins`, per-group
Go-produced receipts and all genuine unbound receipt references. These are
selection inputs for a caller's fresh census, never Go3 native session evidence.
`images` needs `api`, `control`, `stager`; the library consumer additionally uses
`tests`, mapped locally to the separately verified same-head packet `observer`
(coordination `go test -c`) when Root selects that candidate. This is no extra
native role. A growing Agy trace remains caller-owned: a live selected callback
must fail bootstrap D5; an unrelated trace is not bootstrap trace qualification.

Final command results and precise counts belong to the frozen candidate's
selected raw receipts. Earlier failures are retained separately; none are
converted into runtime qualification. No production transition, common release
image build, model/provider attempt, admission or observation-clock start is
performed by this slice.
