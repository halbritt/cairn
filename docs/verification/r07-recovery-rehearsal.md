# Disposable recovery rehearsal

This driver rehearses the existing backup metadata and restore-admission
contracts using synthetic records, two newly owned PostgreSQL clusters, and
real authenticated local API profiles. It does not perform a production
checkpoint, export, backup, restore, service change or session operation.

The source baseline is landed `2ba3b11a129c27e980677984b9585401e83bf81d`,
tree `c8ba8915721cd109fcd342aff9bddf3baabb1e29`. The implementation changes only
the driver, its pure unit tests and this document. The current store, schema,
API, lifecycle, Source and shared fixture implementations are unchanged.

The selected-dependency correction is a child of original candidate
`6fd18dd718a132ecd200fcfd2869b225923f05ba`, tree
`c02bee7f9ad43eff77fcefcc59025af6bb3b9836`. Its initial author prerequisite
stopped on embedded offline module errors. The dated STOP below describes
that retained failure, before the later operator metadata repair.

The linked-worktree correction is a child of
`3ed4327ac68c40b7652bfc7d2629006067bc60bc`, tree
`dbecf6d7890984aec44e4b53baf9bef9e1ecfb48`. Owner decision
`01a83f82-3108-46c5-8daa-745ebefcf065` version 1, dated November 13 at 11:57
PST, permits absent VCS stamps only in the linked-worktree file context. It
also clarifies that metadata stops before any PostgreSQL, API or recovery
step do not spend recovery-rehearsal caps. Earlier failure packets remain
unchanged; the correction itself claims no successful recovery run.

## Authority and scope

The owner's November 13 standing grant, Cairn record
`a3065785-ae68-4f8e-9148-57ba1468a768` version 1, supersedes the earlier
interactive human-wait text. Its engineering and independent-review conditions
remain mandatory. This rehearsal does not admit live R07 step 1.

Live admission still requires accepted and landed R06 and HC05, independent
review of the concrete phase packet, Coordinator/Owner Supervisor admission,
verified retained `/2` and S-04R semantic rollback closures, and a fresh
production checkpoint and recovery export before Rust or `/3` selection.
This driver neither reads nor creates that production evidence.

The reviewed plan input is the backup/restore/reverse sequence in
`~/.cache/cairn301-coordinator/r07-rollout-plan.md`, snapshot SHA-256
`57bb7b65c477b57e1bf3bd65497a8e86cde00b3a03619965d4801042a4469212`.
Its older human-wait statements are superseded by the standing grant; its
isolation, external-expectation and reverse-closure requirements still apply.

## Implemented method

Run only through the existing host task, with a new evidence child under an
owned `0700` parent. The caller pins its Python interpreter, this source/tree
and the exact command before invocation:

```sh
mise run host:exec -- --timeout 15m --weight 2 -- \
  python3 -B scripts/check_r07_recovery_rehearsal.py \
  --directory /absolute/private/parent/new-rehearsal
```

The driver requires a clean checkout. It records exact source/module hashes,
resolves and pins the Git, Go, compiler, PostgreSQL and compiled Cairn images,
and records their version/build metadata before their workload use.
In particular `initdb`, `pg_ctl`, `postgres`, `createdb`, `psql`, `pg_dump`
and `pg_restore` are pinned before either cluster starts. The same compiled
Cairn image runs the operator CLI and both API processes; actual API executable
hashes and owned PIDs are checked after startup.

Dependency inventory selects exactly `go list -deps -json ./cmd/cairn` under
the existing offline policy. The parser requires the complete target closure
and rejects package errors, embedded module errors, duplicate identities and
malformed or truncated JSON. It records participating module replacements,
selected source and embedded files, module manifests and toolchain inputs
before compilation, then checks source hashes and the resulting image's Go
build metadata against that selection.

If either `vcs.revision` or `vcs.modified` is present, both must match the
frozen commit and `false`. When both are absent, a regular `.git` file is
required; a primary checkout directory, missing marker or symlink refuses.
The retained `cairn-build-verification.json` receipt records `vcs_stamped`
as `true` or `false`. An absent stamp remains unknown provenance, as described
in [build identity](../build-identity.md); it does not establish source
cleanliness. Clean Git status, HEAD/tree, executable pins, full selected
module/replacement correspondence and source hashes before and after build
remain separate requirements. The actual build and recovery checks belong
to the Foundation Lead.

Cluster initialization, database/profile creation, API startup and shutdown use
the maintained `OwnedCluster`, `DisposableHost` and `ProvidedDatabase` fixtures.
There is no alternate PostgreSQL lifecycle or test runner. Short, fresh
`~/.cache/cairn-r07-*` runtime directories avoid Unix socket path limits.
Application routing variables and libpq destination/credential selectors are
cleared inside the driver process; the fixture supplies both database URLs,
private application homes, sockets and generated profiles. The agent's actual
`HOME`, tools, skills and subscription environment remain intact.

The scenario:

1. Creates a root only in the owned source database, a grant and instruction
   that will later be withdrawn, a source note, its exact `derived_from`
   dependent, and a disjoint retained compiler control. A real API search must
   select the control.
2. Runs actual `checkpoint --header`, saves its external expectation, and
   creates an actual custom-format `pg_dump`. An external catalog pins the
   archive checksum and checkpoint identity before restore use.
3. Revokes the grant, retracts the instruction and forgets the source after
   the archive. Actual `recovery-export --directory` creates a private bundle.
   The driver binds the returned manifest digest, exact descriptor positions,
   file hashes and original record digests without inventing an import API.
4. Stops and joins the source API, initializes a distinct owned destination
   cluster, checks the archive digest and restores it with actual `pg_restore`.
   Distinct database/system identities are recorded. Current migrations and
   `begin-restore` run before any ordinary consumer connects to the destination.
5. Starts a real destination API while paused and requires ordinary search
   to refuse with `RESTORE_PAUSED`. Recovery inspection must detect revived
   state and missing newer audit. Each original bundle part is reapplied with
   its original digest and a stable request UUID; identical retries must agree.
6. Checks grant revocation and record tombstoning, purges recoverable deletion
   effects, pages the existing rebuild operation, and checks the exact
   dependent-version deletion exclusion. Inspection must retain original
   `AUDIT_MISSING` gaps rather than fabricate lost events.
7. Requires changed and missing checkpoint identities, an omitted external
   expectation, and an unsupported resume policy to refuse. Successful
   verification must leave admission paused, including through the real API.
   Only explicit `resume-restore` under `local-restore/1` admits fresh ordinary
   API search; restrictions and dependency exclusions are checked afterward.

Driver-issued commands retain exact argv, request JSON, raw stdout/stderr,
status/exit and timing receipts. Fixture token values are excluded from those
receipts. Lifecycle internals retain the maintained fixture's bounded startup
diagnostics and private PostgreSQL/API logs, rather than inventing raw command
receipts the shared helper does not expose. Owned API processes are joined and
owned clusters stopped on success or failure. A failed or uncertain run retains
its private runtime and failure result. A successful run removes runtime homes,
profiles and database files, retaining the archive, external expectations,
command evidence and cleanup results in the selected evidence directory.

## Verification boundary

The author's development unit command is:

```sh
mise run test:unit -- test_r07_recovery_rehearsal -v
```

They cover external manifest and part mutations, missing parts, private-file
and alias refusal, exact checkpoint expectations, routing isolation and its
restoration on failure, executable pin changes, and response/exit disagreement.
Selected-dependency tests cover incomplete and malformed streams, command
failure receipts, package errors, source changes, replacements and
build metadata mismatches. The full environment equality invariant uses a
fixed diagnostic; an induced restoration failure checks that values cannot
appear in its assertion output.
Linked-worktree tests also cover the retained false-stamp receipt, absent
stamps in other checkout contexts, matching stamps and rejection of partial,
conflicting or modified stamps even in a linked worktree.
They do not start PostgreSQL, an API, a compiler, a native client or a model.
Synthetic bundle bodies in these unit tests are parser inputs, not evidence of
real recovery acceptance.

Historical STOP in the selected-dependency child: on November 13, 2026, its
development invocation passed 25 tests. The
single permitted actual selected-target prerequisite then returned 314
package objects. Although Go exited zero with empty stderr, 15 package objects
carried module errors, all `module lookup disabled by GOPROXY=off`, across:

- `github.com/klauspost/cpuid/v2@v2.0.12`
- `github.com/segmentio/asm@v1.1.3`
- `golang.org/x/oauth2@v0.35.0`
- `golang.org/x/sys@v0.41.0`
- `golang.org/x/time@v0.15.0`

The inventory refused this output. This establishes unavailable error-free
module metadata under the observed policy; it does not establish missing
package source or a compiler failure. `GOPROXY=off`, `GOSUMDB=sum.golang.org`,
`GOFLAGS=-mod=mod`, the existing Go cache and the normal home were unchanged.
Raw output, policy and command receipts are retained under
`~/.cache/cairn-r07-recovery-rehearsal-inventory-1381-20261113/builder-1466`.
The author stopped without a retry, compilation, API or PostgreSQL startup.
The final focused unit gate was not run after this prerequisite stop.

The earlier `go list -m all`, offline module-metadata and missing VCS stamp
stops all remain retained. Under the later owner decision, those stops before
any PostgreSQL/API/recovery step do not consume recovery-rehearsal caps.
The Foundation Lead alone owns the next actual two-cluster rehearsal after
this correction's frozen-unit gate. Any genuine PostgreSQL, API, recovery or
safety failure stops for bounded disposition, without a silent retry or
weakened assertion. Check, documentation and independent-review gates remain
pending; no recovery pass or production admission is claimed.

## Remaining evidence

A checkpoint header covers emitted audit metadata. It does not establish all
record bodies, unknown newer commits, serving safety, truth or a usable
production backup. This synthetic dump/restore is not a pgBackRest chain,
retention, integrity, WAL or point-in-time recovery qualification. Newer
external expectations still need an independently trusted freshness decision.

The deletion scenario covers stored payloads and a retained dependency edge;
it does not create post-backup external file custody or prove physical erasure.
Named `not_possible` deletion residuals and missing original audit remain
visible under the existing policy.

Admission protects transactions that enter its boundary. It does not globally
fence old processes, recall returned bytes, stop tools, or cover direct-pool
helpers. The retained lesson `d196a57f-dc77-4249-8e0b-67c69c76f32b` version 2
and current [restore admission](../restore-admission.md),
[fencing](../restore-fencing.md) and
[maintenance](../operator/maintenance.md) contracts require external isolation
and process control. This lease does not repair or relax those boundaries.

The dependency-free retained `/2` lifecycle and S-04R/Python semantic reverse
closures, production checkpoint/export and actual backup-chain restore remain
separate engineering evidence. This cut establishes no R06/R07 acceptance,
production admission or observation-clock start.
