# R06 owned Agy Go /3 unit driver

`scripts/check_r06_agy_native_unit.py` implements a future, explicitly admitted
disposable Agy A→B→restored-A case. Authoring and review use no actual Agy,
model or provider calls. A runnable entry is separate from permission to run
it, commonRC qualification, and a native PASS. Three-role validation images
used for finite authoring checks are not a five-role native image packet.

The driver composes the maintained Go /3 producer, authenticated public unit
control, R04 stream-json protocol, local synthetic `GeminiFixture`, current
generation collector and descendant `connect(2)` audit. It uses no synthetic
status fixture, Claude-specific admission, pause helper, fabricated callback
or session state, or rewritten trace bytes.

## Native admission and one-attempt boundary

The only native entry is:

```sh
mise run host:exec -- --timeout 10m -- python3 -B scripts/check_r06_agy_native_unit.py \
  --native-unit --admission /absolute/private/admission.json \
  --admission-sha256 SELECTED_SHA256 --attempt-id SELECTED_UUID
```

The selector uses schema `cairn.r06-agy-native-admission/1`. Unknown, duplicate
or missing fields refuse. Its exact fields are `schema`, `source_head`,
`source_tree`, `manifest`, `index`, `build_complete`, `images`, `client`,
`provider`, `attempt_id`, `attempt_limit`, `issued_at`, `expires_at`, `ledger`,
`evidence`, `catalog_root` and `tools`. Pins contain exactly `Path` and `SHA256`.
The attempt UUID is canonical, its limit is one, and the UTC validity interval
is at most 15 minutes. The source must be the actual clean driver checkout.

The selected sealed packet must contain five distinct original API, control,
stager, helper and observer images; full source archive; successful real build,
buildid and buildinfo receipts; complete custody index; and unchanged native,
compiler, Go-runtime and mapped-runtime provenance. Go, embedded assets and
test compilation inputs must match the checkout. Driver and image commits are
separately recorded. Partial manifests, three-role validation packets, stale
inputs and aliased roles refuse.

The client is Root1383's frozen Agy1.5.7 image, SHA256
`5df525a2d6908989c57b897723caea66e00d7c4d2e24411f91ad306989574642`, under
`/home/halbritt/.cache/cairn301-release-1383/agy-receipt-filetype-20261112/`.
The driver copies its bytes to `catalog_root/client/native-agy`, an owned 0500 regular
file with a distinct inode, inside an owned 0700 client directory. The native
child receives `AGY_CLI_DISABLE_AUTO_UPDATE=1`. A background updater or second
session exec is rejected; only an exact child `[client, "--version"]` with one
exec and an observed zero exit is permitted.

The provider tuple is exactly local `GeminiFixture`, host `127.0.0.1`, port 0,
synthetic key `r04-synthetic-unused`, and `NO_PROXY=127.0.0.1,localhost`.
Normal-host Python and strace are explicitly pinned. Evidence and catalog
directories must be fresh, empty, private 0700 and disjoint from the admission
and ledger. The catalog path must fit the owned Unix socket limit. PID-fd
support, nonroot execution, executable PostgreSQL tools and selected
source/tools/images are checked before reservation or actual native startup.
A genuine callback size measurement is also a prerequisite before reservation;
its disposable API/PG and two Go callbacks confer no native admission.
Known missing prerequisites return75 without consuming an attempt.

An exclusive immutable consumption marker binds the entire admitted tuple.
After that boundary, any failure consumes the attempt. A complete fsynced
terminal result is published exclusively without replacing an earlier result
or consumption marker. A failed result includes the original error and cleanup
observations. Both `native-result.json` and the atomic native terminal precede
expensive custody hashing and archival. `archive-result.json` records archival
separately; a pending or failed archive cannot establish complete custody. An
archive failure preserves an earlier native failure and its first error. There
is no automatic retry or fallback route. An
admission selects already-authorized work; it is not authentication or an
expansion of the owner's authorization.

## Genuine producer and authenticated transport

`NativeUnitFixture` prepares genuine null/unbound, disabled Go /3 receipts,
profiles, source/runtime members, hook generations and exact settings bytes.
One private account layer is published by the actual stager; project hooks
remain absent. The native application alone receives a private HOME and
session settings directory. The parent keeps its normal HOME, PATH,
CODEX_HOME, tools, services and authentication. The child receives the local
synthetic key and provider URL; ambient provider keys, proxies, database DSNs
and Cairn wake context are excluded.

`DisposableHost` and `OwnedCluster` own a real local API and fresh PostgreSQL.
The traced launcher uses a private Unix transport to the pinned API CLI outside
the traced descendant tree, so credential contents do not enter raw strace.
The transport forwards unchanged stdout, stderr and exit status for selected
operations. The actual API authenticates the issued worker profile and owns
all status, session and permission responses. Selector mismatches and
transport failures refuse; the transport invents no API envelope or response.

After an actual A turn completes, a fresh disabled B generation is staged while
the same native generation remains live. The actual stager publishes B; the
same client produces the B turn. PID/start/boot/image/literal argv,
conversation, workspace and native history must remain equal. The driver
collects actual emitted owner state and callback facts. It creates no
native-looking state to satisfy the census.

The owner-state reader requires both `registration.native_session_id` and
`agent.native_session_id` to be nonempty strings equal to the client's actual
conversation. An optional legacy outer `native_session_id` must also match;
it cannot replace either nested identity. File pins must remain equal across
the state read, and exactly one matching owner state is required.

Public A→B and B→retained Go /3 A use literal TargetReceipt, actual session
paths, maintained `plan`/`invoke` and unchanged authenticated reads. Reverse
examines current B while B remains installed. Both publications preserve
protected filesystem and API state and the same watch-lock inode. The stager
restores exact A settings; a third actual turn must use the same native
generation/history. The local provider's final request must contain all three
actual prompts. Shutdown must be orderly, zero and joined.

The scripted preparation entry `--prepare-stubbed --rehearse-sequence` uses
this same maintained sequence with a clearly labelled nonnative stream-json
client, genuine Go /3 callbacks and the real disposable API and PostgreSQL.
It must finish public forward and reverse, retain the watcher lock, restore A,
verify all three prompts in actual history, close with zero exit and complete
the final descendant audit. Its result always has `NativePASS=false` and
zero actual Agy/native attempts. Only the complete sequence can set
`RehearsalPASS=true`; a partial capture or a successful preparation stage
cannot set it. Cleanup or result-publication failure clears a prior rehearsal
success before the atomic terminal is published. Candidate image inputs must
match the composed source checkout;
an old packet cannot be relabelled as a new candidate.

Client executable identity, mode, size and digest, the 0700 parent identity and
its exact single-entry inventory are checked at each phase, before public
control and after joining the client. A genuine rename to another image or a
new sibling refuses. During public forward and reverse, all Cairn files,
account/workspace/state data, profiles and credentials remain protected,
together with unchanged API database snapshots. Only appends to the same raw
trace and stderr files, and the explicitly selected client-internal subtree,
may change. Rewrites, truncation, replacement, symlink escapes and hardlink
aliases refuse. The explicit full-sequence tests inject concurrent file
mutation and a real disposable-API `remember` write independently during
forward and reverse control. Each must refuse qualification and retain the
actual command and database snapshots. The selected fault and phase are
recorded in the separate nonnative rehearsal selection.

The parent and owned API/control children retain the normal host runtime
options. The fixture does not set `GORACE=atexit_sleep_ms=0` or disable race
instrumentation. Its runtime receipt records the inherited value, including
an unset value, and the unchanged public 30-second deadline. Exit-sleep tuning,
smaller inputs or a longer deadline cannot establish the rehearsal gate.
Each public operation records elapsed wall time from process launch through
joined exit, including inherited runtime exit behavior, and its remaining
margin against 30 seconds. A nonnative rehearsal requires a positive margin.

## Kernel network restriction and measured trace budget

The fixture uses the normal host UID, user and mount namespaces, HOME,
CODEX_HOME, tools and authentication. A fresh Python child bootstrap installs
`PR_SET_NO_NEW_PRIVS`, Landlock and seccomp before execing strace. Both the
tracer and native client are observed with NoNewPrivs=1 and Seccomp=2; all
fork/clone/exec descendants inherit the restrictions. No Python preexec_fn runs
in the fixture's threaded parent. The local provider listener and the private
AF_UNIX API bridge remain owned by that parent.

Landlock ABI4 or newer handles CONNECT_TCP only. Its rules permit only the
actual selected fixture ports, all above 1024, normally the provider's ephemeral
port. No filesystem or BIND_TCP right is handled. Landlock filters ports,
not IP addresses: a non-denied external address on an allowed port still fails
the descendant audit. Missing support or installation failure refuses; there
is no unrestricted or audit-only fallback.

The x86_64 seccomp program rejects alternate syscall architectures and x32,
masks SOCK_CLOEXEC/SOCK_NONBLOCK, and permits inet sockets only for SOCK_STREAM
with protocol 0 or IPPROTO_TCP. Inet datagram/raw/non-TCP stream sockets and
other socket domains, including AF_PACKET and AF_NETLINK, return EACCES.
AF_UNIX remains available. io_uring setup/enter/register are refused to close
its socket-operation route. The exec bootstrap closes ordinary inherited FDs
and refuses retained network sockets and io_uring descriptors. Failed policy
validation never execs the target.

`kernel-policy.json` records the actual installed ABI, rights, selected ports,
BPF digest and kernel readback. Audit acceptance uses the owning tracer's
retained observation: exact process capture, receipt hash/inode/owner/mode and
timestamps, unchanged installer, trace-file identity and unchanged raw prefix.
A decoded caller JSON flag cannot establish enforcement. The receipt selects
no principal and grants no authority.

Both measurement and production use the same `OwnedKernelTrace.command`:
single-line `strace -f -xx -s 2048` and the existing selected syscalls, without
multiline `-e read=` dumps. Every raw reader retains its 268435456-byte cap.
The nonnative `--measure-trace-budget` entry verifies the sealed five-role
packet, stages genuine Go /3 material, and runs its PreInvocation and Stop
callbacks under that restricted child. It uses an explicitly synthetic
conversation and an actual owned controller PID; the genuine API creates only
local disposable fixture registrations. No Agy, Gemini fixture or model runs.
The real common control image must be at least 24 MB. Exact image hash/size,
producer argv, callback stdin bytes/EOF, zero joins, raw pins, installed policy
and cleanup observations are recorded. Input frames exceed 1024 bytes, covering
the measured native payload lengths, while staying below the 2048-byte bound.

The gate requires `max(measured callback bytes) * 6 + 67108864 < 268435456`.
The 64 MiB client reserve is an explicit allowance for client-only traffic,
not a new client measurement. The result reports the remaining byte margin. This projection does not replace the live cap, full raw audit or
exact hook-frame proof. A smaller synthetic binary cannot replace the genuine
control image. Native preflight remeasures its selected packet before the
exclusive reservation.

## Current readback and raw trace contract

Private `agyTraceCallsLive` is called only by `agyCurrentPhase` for
`cairn.watch-agy-readback/2`. It permits at most one pending syscall, exactly
named `read`, with `TrimSpace(body) == "read(0"`, on the captured native PID
or an actual kernel-derived CLONE_THREAD descendant. Forked helpers and
callbacks cannot supply that wait. Other descriptors, extra arguments,
overlapping calls, multiple waits, unknown framing and incomplete
creation/exec/connect coverage refuse. `readback/1` and all closed-trace
decoders continue to reject every pending syscall.

The allowance does not substitute for callback facts. Both actual emitted
roles need exact outer and inner literal argv, complete stdin bytes, actual
EOF, zero exit and matching raw native write frames. Stop must carry Boolean
`fullyIdle=true`. The current owner/process tuple must remain live. Current A,
private verified target B and D1–D6 final rereads retain their existing checks.
The historical held-image prefix is decoder evidence, not a new native run.

The collector waits for callbacks to finish, then seals a complete raw prefix
verbatim. It never trims an unfinished record or appends a synthetic resume,
wait or exit. A boundary is the length of the exact complete byte snapshot
that was validated; later file growth cannot change that selected offset.
BEFORE/AFTER byte offsets and exec/join/frame references locate
those exact bytes. AFTER ends before the later restored-A turn; the full final
trace retains that turn and must contain the sealed AFTER prefix unchanged.
Extra A callbacks within the B phase refuse.

Native ELF extraction, wire chunks and builtin Markdown are not hook frames.
Only `{`-prefixed JSON objects with valid conversationId, workspacePaths and
hookEventName undergo strict complete hook I/O validation. Malformed or
truncated purported hook JSON refuses. Only failed ENOENT hooks.json probes in
the selected account's owned builtin-plugin subtree are exempt from layer
inventory; successful undeclared opens and foreign paths still refuse.

## Finite descendant connect audit

Normal-host strace includes the selected literal native exec and every observed
clone/clone3/fork/vfork descendant, recursively. Every PID needs a causal
kernel-derived birth. PID reuse, events before the root exec or after exit,
unknown records, partial lines, overlapping calls and unfinished
connect/exec/creation coverage refuse. The final interval requires every
observed descendant to exit.

Each non-denied connect attempt must name an explicit owned
loopback port at exactly `127.0.0.1` or `::1`, or a selected owned private Unix
socket inside the catalog. The sole outside-root exception is exactly
`/var/run/nscd/socket`, sockaddr length 110, returning `-1 ENOENT`.
Selected ports must be unique, sorted integers above 1024. Every inet address
must have its exact sockaddr size (16 for IPv4, 28 for IPv6). With a verified
capture-bound kernel policy, a valid unselected inet connect returning exactly
`-1 EACCES (Permission denied)` on a port outside the allowlist is retained with
`kernel_denied=true`. A selected port always requires the selected loopback
address, including when its result is EACCES. Without
that enforcement observation even that result refuses. Every other result,
including EINPROGRESS, ECONNREFUSED and ENETUNREACH, must target 127.0.0.1 or ::1
on an explicit selected port. This also covers TCP DNS fallback and literal
HTTPS attempts without erasing denied calls. Unknown or partial addresses and
results, wrong sockaddr sizes and foreign Unix sockets refuse. Collection of
an in-flight prefix does not make a pending connect auditable.

ExternalCoverage covers observed `connect(2)` attempts in that raw interval.
It excludes sendto/sendmsg, unconnected UDP, inherited/preconnected descriptors,
tracer/controller traffic, future intervals and global coverage. Kernel
enforcement is recorded separately and remains port-based; this audit does not establish Go currentness or global coverage.

## Validation, custody and remaining scope

`scale_selection_facts` observes the actual pinned JSON selection inventory:
eight profiles, including exactly three null/unbound profiles. Credential
inode aliases, symlinks, changed pins and omitted or extra JSON members refuse.
This input observation always reports `NativePASS=false` and
`complete_unit_qualified=false`. It does not establish genuine producer
receipts or runtime success. Scale qualification still needs the real
candidate control image, complete producer material, a live growing trace,
both public unit-control and unit-bootstrap measurements under their unchanged
30-second contracts, positive measured margins and owned cleanup. No input
observation, small synthetic image or omitted member can substitute for them.

The source adapter uses B's actual
`production_shape_fixture(cluster, images, root, 'proximal')` contextmanager.
It yields the initialized fixture, original plan path and SHA256. Its API
cleanup is `f.host.close()`; the caller owns the cluster, root, images, client
and live trace. `production_fixture_plan` checks a fresh short path without
creating it. The future fixture socket is
`root/account-home/.local/share/cairn/api.sock` and must fit the Unix socket
limit. Entering `production_shape_adapter` requires the complete five-role
packet through the unchanged `checked_packet(..., native=True)` validation.
B's local `tests` image is the independently pinned `observer` image; this
mapping adds no manifest role and never substitutes the control or helper.

`production_material_inputs` reads B's owned `production-material.json` and
the pinned original/target plans. It preserves literal lowercase
`original_current`/`target_current` `directory` and `pins`, and uppercase
plan `Path`/`SHA256` descriptors. Only the selection field names are adapted
to `Directory`/`Pins`. All eight members, three unbound receipt selections
and the empty target archive list remain required. Bounded input reads
exclude credential inode aliases before opening contents. This is selection
data, never fabricated current A/B, native session, generation or producer
receipt verification.

There is a known source boundary in the selected production shape: B
preserves missing `config_home` in the three unbound coordination profiles
and all memory profiles. The unit census requires account coverage in
`shared_unit_census_unbound.go` and `shared_unit_census_files.go`.
The adapter reports the observed coordination omissions as
`census_boundaries`; `require_scale_census_inputs` refuses those inputs.
It does not supply homes, change account modes, drop members or relax Go
checks. An empty Python boundary list still does not qualify a census:
fresh actual Go producer/census/readback and complete runtime gates remain
required. Memory/account and catalog-root coverage need their actual Go
checks. Source inspection establishes this mismatch; it is not a runtime
failure reproduction.

The bootstrap workflow proof is scoped exactly as
`SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE`. Each switch and reverse requires
the matching non-skipped `TestUnitBootstrapOwnedTransition` RUN/PASS output,
semantic transition receipts, a fresh unchanged 30-second deadline, and
separate positive library and launch-to-joined-exit margins. The adapter's
receipt parser checks consistency and keeps `NativePASS=false` and
`complete_unit_qualified=false`; parsed JSON alone cannot establish execution
or raw reverse/state preservation. Actual B execution receipts and protected
state/raw reverse checks must accompany later qualification.

`DEFAULT_PUBLIC_REFUSAL_ACTIVE_WORKERS` is a separate mandatory real public
negative. No shared worker may be stopped or masked to get a positive here.
True default-public positive behavior and its actual manager latency remain
UNESTABLISHED until a separately admitted R07 stopped boundary on each host,
before any exchange. A fence refusal must abort without changes, be reported,
and be reconciled before further action. The production caller still uses
the real default fence; the fixture capability is process-local and cannot
come from a CLI, environment, configuration or serialized value. A selected
live callback still refuses D5; an unrelated growing trace does not qualify
bootstrap trace handling.

Source-adapter checks use only Python and owned unit files. They do not start
B's contextmanager, an API, PostgreSQL, any Go image or a full case. Candidate
packet-dependent full 8+3 control/bootstrap, live trace, protected-file and
database drift cases in both directions, restored A, cleanup and final audit
remain unrun in that phase. Overall C qualification stays open.

### Unit-control scale and installed bootstrap shape

The corrected unit-control qualification is explicitly
`UNIT_CONTROL_SCALE_FIXTURE_SHAPE`: **eight bound coordinator bindings plus
three unbound profiles, eleven total**. It uses the maintained R02/R04
producer with a three-coordinator unbound receipt group and eight bound
groups. Every coordination and memory profile declares a private owned 0700
`config_home` inside `catalog.Root`. These are observed synthetic fixture
inputs. They are independent of B's installed-host material. The bound-profile
contract remains Codex/Claude with `native_delivery=true`. The eight inactive
bound peers declare fresh empty homes: no native settings/readback proof is
available for them. Original factory settings assets remain untouched outside
those declared homes. The live Agy account retains its actual selected hook
and current readback checks. The real Go publisher supplies the /3 receipts
and the live scripted sequence supplies
fresh census/readback A/B facts; count observations alone remain unqualified.
Both public directions exchange all eleven selected members under the
unchanged 30-second contract, with the complete growing trace and real
candidate images. The fixture preserves opaque Unicode and large integer
values rather than reducing input contents for timing.

The same scale selection is available to all thirteen nonnative cases:
one complete A/B/restored-A sequence, two client immutability refusals,
eight protected-file mutation cases and two real disposable-API database
drift cases. The latter ten exercise forward and reverse separately.
Expected refusals retain failed atomic results and cleanup evidence; they
never acquire a passing rehearsal outcome. A genuine positive still needs
all actual public/census, trace, history, preservation and cleanup checks.

The separate bootstrap qualification retains B's real installed shape:
proximal eight total profiles (three unbound) and seventeen opaque
archives, plus the one-profile archon and talos shapes. It preserves absent
homes, external 0755 account directories, enabled raw /2 manifests and the
separate legacy binary. It uses the scoped shared-workflow and default-public
negative labels above. Bootstrap material never supplies unit-control
native A/B facts. A selected live callback must still refuse D5.

**Product gap:** selected-unit census / live unit-control on the real
installed roster, with absent coordination or memory `config_home`, external
0755 account identity directories and non-fixture Scope, is
**UNSUPPORTED / FIXTURE_ONLY**. After R07, a production live generation
switch cannot use unit-control until that contract exists. The supported
production transition and reverse use stopped-boundary unit-bootstrap.
Qualifying the eleven-member disposable fixture does not close this gap.

Use `mise run test:unit` for focused Python and Go checks. Run bounded process
checks and documentation tiers1–4 through `mise run host:exec`; run
`mise run check`. Retain failures and exact source/image/tool receipts. Refusal
tests cover expiry, mutated/aliased tuples, malformed packets, consumed
attempts, concurrent reservation, custody, callback I/O and connect coverage.
A real owned nonnative root, child and grandchild exercise TCP/Unix attribution
and cleanup without Agy or a model.

Finite authoring checks use genuine producer material and the real disposable
API/PG for forward/reverse control and an independent target-memory mutation
at final D6. They establish those tested finite claims. They do not establish
actual native callbacks, refresh, a future admitted run or full design
acceptance. Separate authenticated and fixed nonnative stub preparation
entries retain their own exclusive local ledgers; neither consumes native
admission or makes a native PASS claim.

Cleanup targets owned PIDs greater than one. Exact pidfds preserve generation
identity for bounded driver cleanup. The driver sends no process-group
signals; the outer host:exec timeout is a separate cleanup mechanism and this
claim does not attest its signal scope. Evidence
records API, client/tracer/descendant and PG joins. Credentials, every inode
alias and API-home contents are metadata-only in custody snapshots and are
excluded from hashes and archives. PG data stays outside the catalog.
Non-private files enter a private archive only with matching pins and no raw
fixture DSN. PG scratch is removed only after cleanup succeeds. Unresolved
cleanup fails the attempt.

Retained Python /2 reverse through this unit-control driver remains unsupported:
`loadUnitTarget` requires a Go /3 receipt and the old binding/retained-argv shape
cannot be normalized into one. Ordinary Agy memory remains unsupported.
Authoring checks, BUILD-ONLY images and finite audits do not qualify commonRC,
production, HC05/R07/clock,
held runtimes, legacy reverse, global budgets or future runs.
