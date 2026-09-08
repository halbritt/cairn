# Restore admission verification, 2026-09-08

An isolated restore now has a persistent pause for ordinary core and local API
transactions. Beginning drains existing transactions and fences old delivery;
resume requires a current-root operator action that recomputes recovery proof
under the same exclusive lock. The [operator contract](../restore-admission.md)
defines the input, bounded checks and `local-restore/1` residual policy.

## Observed behavior

The regression suite demonstrated three failures before their fixes: fencing
alone still allowed fresh compilation, an old cached begin response appeared
successful after resume, and mutually consistent corrupt class flags allowed a B
promotion to appear as a C instruction. The last regression uses an independent
valid compiler fixture, so its seal cannot hide the damaged record. Removing the
transition-type predicate makes that regression fail; the predicate is restored
in the implementation.

`core/restore_session_test.go` covers ordinary fresh/cached refusal, operator
reads, explicit resume and stale delivery, stale sessions and retries, unknown
missing audit, changed evidence after a green verification, draining an existing
transaction, rebuilding a missing exact dependency exclusion, retaining known
gaps despite older external input, scoped/different-root refusal, and an empty
store without invented fixtures. The focused final suite passed all ten tests.

`localapi/restore_session_test.go` exercises existing and newly opened clients:
cached create, get and fresh compile return HTTP 503 `RESTORE_PAUSED`; an agent
restore-resume route does not exist. An initial fixture bug used its disposable
parent database because changing pgx Config.Database did not change the retained
connection string. Explicit database replacement and a `current_database()` check
now isolate the fixture before any mutation. This was confined to the owned test
cluster and was not an operational incident.

`scripts/check-recovery.py`, run by `make test-lifecycle`, takes an actual earlier
custom dump, then revokes a grant, retracts an instruction, forgets a record and
captures a post-backup context file. In the older restored database it:

1. Confirms read-only inspection detects revived restrictions and missing custody.
2. Begins a restore and observes refusal of ordinary reads and fresh compilation.
3. Reapplies withdrawals with new provenance while preserving original audit gaps.
4. Refuses verification/resume with outstanding deletion effects and remains paused.
5. Purges the file through imported custody without fabricating its lost receipt,
   drains other fixture effects and rebuilds known dependency exclusions.
6. Verifies the expected checkpoint, external recovery record and an independent
   positive compiler fixture, then explicitly resumes and compiles fresh work.

Final local checks passed: `make test-integration`, `make check`,
`make test-lifecycle` and all 12 Python script tests. Integration/check ran after
the final production authority type/actor/kind checks; the subsequent stronger
test fixture passed the focused suite. Lifecycle was rerun after those checks.
All corruption, pause/resume and restore probes used owned disposable PostgreSQL
clusters. No operational memory was corrupted or restored for this verification.

## Decision and limitations

The selected core transaction boundary prevents direct callers from bypassing an
API-only pause. Private recovery contexts preserve narrowly scoped administrative
and late-observation paths; they cannot be chosen through request JSON. A shared
row lock adds work to every ordinary transaction; no latency benefit is claimed.
Known derived exclusions are rebuilt from retained exact relations. Historical
impact, receipt and authority observations are not reconstructed from today's
state. Fixture selection is explicit and bounded, not complete compiler coverage.

Missing original withdrawal audit can support admission only through exact
reapplication provenance and current restricted state. It remains missing in
inspection and subsequent exports. Unknown audit gaps still refuse. Missing
subjects can remain an admission problem even when reapplication reports absent;
this workflow does not silently treat every absence as recovered.

The current-root action selects `local-restore/1` and affirms external isolation,
source freshness and intended restore point. Cairn does not independently prove
those facts from a restored database. Raw restoration outside the procedure is
not automatically detected. Named ordinary `not_possible` deletion residuals
remain explicit; pending/running/failed effects refuse. This is not complete
historical reconstruction, physical erasure, full L9 completion, or measured
memory usefulness.

Pincite packet `pkt-7e4717968f627ff0`, content SHA256
`7e4717968f627ff0181e0a39b6446d9ff2c79b0c93df37bbb6171719a135c787`,
supported the boundary decision through repository-contract precedence,
authorization embodied in action, population scoping, complete identity keys and
authoritative gate signals. Two evidence passes were used. The late authority
regression is separately observed repository evidence after that packet.
Twenty-three generic interface/UI/ranking/configuration/named-procedure and
pre-release serving-parity obligations were retained as nonmaterial to this
implementation claim. Runtime release parity requires separate installation
verification. Private receipts retain each obligation and its rationale.

## Release state

Implementation and local verification are complete for this bounded slice.
Commit, exact-revision CI and operational installation are pending. Migration028
is not yet applied to the operational store; no operational restore session has
been declared. This section must be replaced by observed release evidence after
installation.
