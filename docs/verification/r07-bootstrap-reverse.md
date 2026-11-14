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
Unsafe installed HOME/ancestor modes still require the separately admitted
operator remediation; a private fixture is not installed readiness.

An existing lock must be the same actual inode in both selections, linked during
off-path preparation. Its exact exclusive flock is identified through
`/proc/self/fdinfo`; other owners, waiters and ambiguous inode observations
refuse. No original lock is created or repaired by observing it. A talos-shaped
original with an explicitly observed absent lock can use the prepared target's
new private lock. Reverse restores the original absence and directory mode;
it does not silently initialize the retained generation.

## Bound and unbound retained Python /2

Every original member selects an actual disabled `/2` manifest and its literal
`retained-argv.json`. Bound profiles must name that manifest; unbound profiles
must remain unbound. The memory/coordinator profiles must select the exact
current member and agree on shared selectors, accounts and state. The reader
verifies the original engine/bridge, all 17 named lifecycle bundle members and
the bundle digest. Literal memory and coordination vectors must select their
own source entrypoint and exact profile, including `hook --config` where the
source requires it. No `/2` observation becomes a `/3` compatibility receipt.

The original config/manifests and Python bundle are never rewritten or
normalized. The complete original directory, including all 17 proximal opaque
`.before-*` members, is retained during the forward exchange. Archive bodies
and credential contents are not opened by the metadata reader. Returned facts
and association labels are retained observations, not permission to replay the
process-local receipts.

## Stopped boundary, D6 and D7

The public operation captures `CaptureSelectedUnitFence(ctx, scope, nil)` in
its own process and invokes `Recheck` on that same opaque receipt. Database and
system-ID selections must be empty: `database_coverage` is `not-selected`.
The [landed fence contract](r07-selected-unit-fence.md) retains
`external_coverage=selected-units-only` and `outside_scope=unknown`. It observes
selected stopped/masked definitions, cgroups and process identities; it does
not issue stop/mask/start/restart operations.

Authenticated drain reads are performed independently for both complete
selections before and while holding the watcher flock. Immediately before
exchange, both pinned plans, layouts, statuses, local state and associations
are reread, the selected-unit fence is rechecked, and actual directory identities
are checked again. The final read/action interval is not atomic with external
activity. Neither a quiet sample nor masks observed at two instants prove a
host-wide future-writer barrier.

Linux `RENAME_EXCHANGE` publishes the pair, followed by parent fsync and complete
D7 readback of both resulting selections. Results keep `published` separate
from errors and report active/retained paths. A post-publication sync or readback
failure leaves both selections available and requires exact reconciliation with
a fresh `reverse`/`rollback` invocation. A killed process's receipt cannot be
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
mise run check
mise run host:exec -- python3 -B scripts/verify_docs.py --tier 1,2,3,4
```

The focused effect fixture covers proximal eight-member/17-archive, archon
one-member/existing-lock and talos one-member/absent-lock/0755 shapes, each with
bound and unbound `/2` originals and a complete `/3` forward generation. It also
injects a separate actual owning API claim at D6 and joins SIGKILL consumers
before and after publication, recovering with fresh receipts. Protected store/
state snapshots and original member metadata are compared; child and PostgreSQL
cleanup are recorded. Source-fault unit tests cover copied/zero/foreign/drifted
fences, changed archives/bundles, writable targets, held locks, ancestor aliases,
strict selection fields and failures around exchange/fsync.

Final command results and precise counts belong to the frozen candidate's
selected raw receipts. Earlier failures are retained separately; none are
converted into runtime qualification. No production transition, common release
image build, model/provider attempt, admission or observation-clock start is
performed by this slice.
