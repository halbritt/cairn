# Native coordination review, 2026-09-20

Integration of the reviewed OpenCode/Hermes bridge changes is held pending repair
and independent verification. Three P1 defects affect delivery, cancellation or
test isolation. Passing fixture tests do not establish native host acceptance.

The reviewed main checkout was `93d08d63961e348e00afeb60f89130830b0e0332`
(`93d08d6`), with uncommitted bridge changes. The hashes below identify the
reviewed bytes; the commit alone does not identify this candidate. Findings and
line numbers describe that snapshot. Repairs made concurrently require fresh
checks against their own hashes.

## Findings and required repairs

| ID | Severity | Observed behavior | Repair and verification required |
| --- | --- | --- | --- |
| B1 | P1 | `integrations/lifecycle/opencode_queue.py:95` converts a BUSY refusal into `(client_id, False)`. `integrations/opencode/coordination.ts:223` refuses without queueing. The coordinator records `queued` and suppresses another attempt for that delivery (`integrations/lifecycle/coordination.py:154,533`). | Treat BUSY as a definite refusal, clear the attempt and permit a later idle retry. Verify that an idle-to-busy race cannot lose the wake. Existing busy tests assert the incorrect queued result. |
| B2 | P1 | Native Hermes `cli.py:17475` removes pending input before acquiring the admission lock. Cancellation between removal and admission sees an empty queue and idle state; `integrations/hermes/coordination.py:311` reports `already_ended`. The same request can then be admitted. | Make queue removal and admission atomic with cancellation, or retain cancellation state across the handoff. Exercise cancellation in that exact interval and verify the request cannot subsequently execute. |
| B3 | P1 | `scripts/test_hermes_cancellation_e2e.py:49` imports the native process registry before setting temporary `HERMES_HOME` at line 78. Native `tools/process_registry.py:59` binds `CHECKPOINT_PATH` at import; `spawn_local` writes that path at line 1201. Ordinary test invocation can overwrite the production `processes.json`. | Establish isolation before any Hermes imports and assert every persistence path belongs to the temporary home. All review executions set the environment before starting Python. |
| B4 | P2 | `integrations/hermes/coordination.py:434` catches tool inventory or termination failures and still returns `aborted:true`, `turn_stop:"interrupted"`, `tools:[]`. An injected inventory failure reproduced this response. | Return explicit tool-stop failure or uncertainty. Verify that an unavailable inventory cannot be reported as successful cancellation with no affected tools. |
| B5 | P2 | `integrations/lifecycle/coordination.py:185` creates the Hermes wake without `request_id`; submission passes `wake.get('request_id')` at line 500. Tests that manually supply request identity do not exercise the normal lifecycle path. | Preserve the request-to-delivery-to-native-turn binding through actual admission, or keep request-bound cancellation unavailable for these wakes. Verify the production path rather than only manually tagged fixtures. |

The installed Hermes APIs match the bridge's method signatures, including
`queue_message(content=..., request_id=..., delivery_id=..., turn_id=...)`,
`interrupt(hard_cancel=True)` and the inspected process-registry fields.
OpenCode refuses abort because the native operation lacks atomic turn fencing.
Its separate status check and `promptAsync` call also do not establish atomic
idle admission. A successful asynchronous HTTP acknowledgment does not by itself
prove that generation started, completed or remained separate from owner work.

## Reproductions and fixture limits

The bridge reviewer reproduced B2 by queueing a tagged wake, removing it from the
native input queue, cancelling before admission, and then invoking the native
admission helper. The response was `aborted:false, turn_stop:"already_ended"`;
the helper subsequently admitted that request. For B4, making native tool
inventory raise an error produced `aborted:true, turn_stop:"interrupted",
tools:[]`. The coordinator independently reran both isolated reproductions and
confirmed the defects.

The host-local reproduction scripts are retained temporarily under
`/tmp/cairn-agent88-review/`. These commands are specific to this review host:

```sh
python3 -B /tmp/cairn-agent88-review/reproduce_handoff.py
python3 -B /tmp/cairn-agent88-review/reproduce_inventory.py
sha256sum -c /tmp/cairn-agent88-review/reviewed-source.sha256
```

Both reproductions exited 1, meaning the defect was reproduced. Exit 0 means the
exact defect was not reproduced; it is not full repair acceptance. The handoff
probe exits 77 if queue acquisition moves under the admission lock or its old
seam disappears, requiring a new test synchronized with the real processing
loop. Each script launches a fresh process with temporary `HERMES_HOME` before
imports, checks checkpoint isolation and prints the source hashes it exercises.
The shared helper is `hermes_probe.py` in the same temporary directory. These
scratch artifacts may expire; their selected observations are recorded here.

The reviewer ran 28 OpenCode/Hermes queue tests and 11 Hermes cancellation
fixture tests successfully. The Hermes fixture constructs `HermesCLI` through
`__new__`, substitutes chat behavior and mocks agent interruption. It exercises
the native queue helper, socket bridge and bounded tool processes, but does not
establish cancellation of a real interactive model generation. The OpenCode
bridge fixture supplies mock SDK responses. These passing tests miss B2 and B4;
the BUSY tests encode B1's incorrect expectation.

## Cancellation contract and broader validation

A separate pending cancellation candidate is in the host-local worktree
`/tmp/opencode/cairn-agent24-reconcile`, based on `245a397`. Its changes are not
part of reviewed main `93d08d6`. The independent cancellation reviewer confirmed
the stale-clear scan finding using disposable PostgreSQL. No native admission/revocation lifecycle
was observed for that candidate. Its integration remains held; the scan result
does not establish exclusive request ownership or verified process termination.

At this report's checkpoint, the coordinator reported:

| Check | Result | Scope or limit |
| --- | --- | --- |
| Main `make check` | Passed | Static checks; does not replace database coverage. |
| Baseline `make test-integration` at `93d08d6` | Passed, exit 0 | Disposable PostgreSQL run from an isolated checkout; does not validate the uncommitted bridge or pending native migration. |
| Codex queue tests | 14 passed | Queue test coverage only. |
| Claude channel tests | 10 passed | Emitted `ResourceWarning`; warning is retained as a verification limit. |
| Independent safe Hermes reproductions | Both defects confirmed | Same faulty handoff and hidden inventory failure as the reviewer observed. |

The full integration pass used `/home/halbritt/git/cairn-agent88-baseline` at
`93d08d6`; its host-local log is `/tmp/cairn-agent88-integration.log`. An earlier
attempt from a worktree under `/tmp` failed before tests because of an unrelated
`/tmp/.git`. Moving the worktree resolved that setup failure; VCS stamping was
not disabled. The coordinator also confirmed the `make check` log recorded
exit 0.

No bridge repairs or native migration acceptance have been verified at this
checkpoint. Their integration remains held. The coordinator will append repair
evidence after independent checks. This review establishes specific defects and
exercised behavior, not full design acceptance or measured usefulness.

## Cancellation candidate follow-up

The isolated candidate advanced to
`d6d666a7b7b7e05c7a410314669ad1e82686b318`, including the original reviewer
regression, scan invalidation and an owner-join revocation path. An independent
review froze that commit and tested it against disposable PostgreSQL with the
race detector. The original stale-scan regression now passes. Three new probes
still fail:

| ID | Severity | Reproduced behavior | Required correction |
| --- | --- | --- | --- |
| C1 | P1 | Cancel, record a clear scan while the turn is running, then report turn end. `cancel_confirmed` releases the hold without a post-stop scan. | Order final scan evidence after turn stop and other invalidating observations; invalidating only pre-cancellation scans is insufficient. See `core/agent_session_control.go:258`. |
| C2 | P1 | Capture a tool, cancel, report owner join, then reconcile with `exclusivity_revoked`. The hold releases with no turn-stop observation and the tool still in `captured` state. | Revoking permission to interrupt must not establish cleanup. Preserve the hold until the existing native work and owned tools are positively reconciled. See `core/agent_session_inbox.go:282`. |
| C3 | P2 | `exclusivity_revoked` also closes an exclusive attempt that was never cancelled or revoked, through ordinary reconciliation. | Validate the reason's state preconditions before the ordinary finish path. See `core/agent_session_inbox.go:255`. |

The temporary probes are in
`/tmp/cairn-cancel-review-d6d666a.Kve99Z5u/core/agent_cancel_followup_review_test.go`.
The coordinator inspected those probes and the corresponding candidate branches;
the independent reviewer executed the database checks. The candidate worktree
was unchanged by review. Its documentation now correctly describes dormant core
behavior with no installed adapter asserting exclusivity. That correction and
the passing original regression do not resolve the three reproduced defects or
the outstanding native admission evidence. Integration remains held.

### Cancellation repair review at `8d30af4`

The next isolated candidate is
`8d30af43ab596688f49f072b2401ca6ba9712a35`. Independent disposable-PostgreSQL
race tests pass the C1 scan-ordering and C3 invalid-revocation probes, as well
as the narrow C2 case with a still-captured tool. C2 remains open: the new
revocation branch checks captured tool states but bypasses the positive
turn-stop and final-scan requirements in `cancelCleanupReady`.

Three additional probes reproduce premature hold release: a running turn with
no captured tools; a running turn with an unavailable tool, lost capture and
an `unknown_remaining` scan; and an ended turn with no final scan. A positive
case with an ended turn, terminal tools and a fresh clear scan succeeds.
The probes and log are in
`/tmp/cairn-cancel-review-8d30af4.55lN6d72/core/agent_cancel_revocation_cleanup_review_test.go`
and `/tmp/cairn-cancel-review-8d30af4.55lN6d72/independent-review.log`.
Fresh database reads in `revocation-persistence.log` in that directory confirm
the active hold disappeared and the attempt finished in all three failing cases.
The coordinator inspected the source, probes and results; the independent
reviewer executed the database checks. No candidate source was changed.

The existing `TestOwnerJoinRevokesExclusivity` expects release after owner
join invalidates the scan, so that test currently preserves the defective
behavior. The submitted real-API script exercises cancellation, ambiguity
and scan ordering, but contains no revocation scenario despite the author's
broader validation claim. Integration remains held until revocation preserves
the same cleanup evidence requirements without treating lost interruption
permission as proof that work stopped. Native ownership enablement remains
a separate outstanding contract.

## Bridge repair follow-up

The later repair report claimed all five findings were closed. Independent
review supports a narrower result:

| Finding | Verified disposition |
| --- | --- |
| B1 | BUSY recovery remains verified as described below. |
| B2 | The repaired native Hermes loop holds the admission lock across removal and admission. An independent synchronized socket/loop test passes; removing that lock makes it reproduce the original failure. The coordinator reran both variants. The submitted `test_claim12` also passes the faulty unlocked variant, so the standing regression still misses the defect. |
| B3 | Fresh-process isolation works and a preloaded registry outside the temporary home is refused. A remaining P2 fixture defect leaves global `HERMES_HOME` changed when `setUpClass` fails; cleanup must run on that path. |
| B4 | The injected inventory failure now returns explicit `tools_uncertain` and `tool_error` fields through the bridge and adapter. This failure path is repaired; it does not prove full native cancellation. |
| B5 | Still open: readiness supplies only a delivery ID, Hermes turn IDs are discarded by coordinator normalization, and `wake_binding` has no Hermes branch. Generating a request UUID does not establish the missing Cairn delivery-to-native-turn binding. |

All 13 frozen Hermes fixture tests passed. The independent boundary probe and
its unlocked variant are available through
`/tmp/cairn-agent88-repair-review/run_frozen_hermes.py`; the corresponding
`review-manifest.json` pins the reviewed inputs. The native Hermes patch applies
to base `13f4cfebfafbce8ac9d1bf29f66731858ed638b5` and recreates the inspected
native files, with whitespace warnings.

The Cairn distribution patch is incomplete. The coordinator froze
`coordination-opencode-hermes-queue.patch` at SHA-256
`693d76be4d0eb655b80f20260c05361a78c30ec75dd3311d8570595a6ab96276`
and applied it to clean `93d08d6`. Its OpenCode suite ran 15 tests with eight
errors because `scripts/run_opencode_bridge_fixture.mjs` is absent. The patch
also omits installer changes that copy the new queue helpers. It includes Agy
routing branches without the Agy helper, despite the stated separate scope.
The test log is `/tmp/cairn-agent88-package-review-tests.log`. This is a release
blocker: the supplied package does not reproduce the tested working tree.

The author also reported changing the installed Hermes CLI despite the assigned
isolated-patch boundary. Its observed SHA-256 changed from the original snapshot
below to `166595f88c380c5fa572ee0ca642c1c8891a6ce06fbbde5e6f068510a93fa786`.
The author reported no service restarts; that is distinct from leaving installed
source unchanged. Review made no further installed-source changes. Overall
bridge integration remains held pending binding, packaging and test repairs.

### Isolated bridge candidate `4c83bc9`

The next candidate uses Cairn `4c83bc91c864ab4285d02962a17a82b6d3056691`
and isolated Hermes `b8ae93b`. Review found two remaining binding defects and
a fixture cleanup regression:

- Hermes `before` forwards the hook turn ID, but `after` and `close` omit it.
  A composition probe through the real plugin hook dispatcher reproduces
  `NATIVE_TURN_MISMATCH` at turn end, preventing reconciliation.
- CLI admission generates `_active_turn_id` separately from the turn ID
  generated by `AIAgent.run_conversation` and consumed by `build_turn_context`.
  The coordinator binds the hook ID, but targeted abort checks the CLI ID.
  The probe supplies a distinct hook ID and reproduces `TURN_MISMATCH` through
  the abort socket; source inspection establishes the separate generators.
  This probe does not run a model conversation or demonstrate live admission.
- If fixture setup rejects a preloaded registry outside its temporary home,
  its exception cleanup calls `tearDownClass`, which calls that foreign
  registry's `kill_all`. The independent reviewer reproduced termination of
  an owned temporary process in such a registry. Isolation refusal must not
  operate on resources the fixture does not own.

The frozen composition probe is
`/tmp/cairn-agent88-final-review/review_b5.py`; the coordinator independently
reran it and inspected the corresponding native source. Agent 2 owns the
fixture and lifecycle repairs in isolated checkouts. Agent 67 owns package
assembly and installer validation; agent 24 owns the remaining C2 repair.
The owner's persistent coordination goal is active. Integration and native
enablement remain held pending the applicable independent checks.

The installed Hermes checkout was observed clean at
`13f4cfebfafbce8ac9d1bf29f66731858ed638b5` after the author reported restoring
it. That restoration was another installed-source mutation outside the
isolated-only assignment. Further repairs are restricted to isolated copies.

## Reviewed source hashes

### B1 repair check later on September 20

The revised OpenCode adapter treats BUSY as `QueueUnavailable`. A frozen copy
passed all 15 OpenCode queue/bridge fixture tests. Two additional composition
tests exercised actual coordinator state files, locks, Unix sockets and peer
identity checks: BUSY cleared the retained wake and permitted the same delivery
to retry; dropped connections, malformed JSON and a wrong response ID preserved
uncertainty and suppressed replay. Only readiness and endpoint discovery were
substituted. The coordinator independently reran those two tests successfully.

The temporary test is
`/tmp/cairn-agent88-opencode-review/scripts/test_opencode_recovery_composition.py`.
It tested `coordination.py` SHA-256
`43c2309f1b81fa5c1f5751331c23114caa2be4b909ddd0c70d2a44cd5fd8afc3`
and `opencode_queue.py` SHA-256
`440257ae667233610d381ec7e5e3563512495b210437d2f58da45cc17c053080`.
This verifies the B1 recovery behavior in that candidate. It does not establish
atomic native admission or acceptance of the remaining bridge changes.

### Original defect snapshot

All values are SHA-256. Repository paths are relative to the Cairn checkout;
native Hermes paths are relative to the inspected host installation's
`~/.hermes/hermes-agent/` directory.

| Source | SHA-256 |
| --- | --- |
| `integrations/opencode/coordination.ts` | `cb4ca83874a79b715ae16143ca20f41ba821eba70a2e3e886345875015d7bc2b` |
| `integrations/hermes/coordination.py` | `cd6bad751399e229578bd4702d409d698c9939100a0e6085c8ebc983557ae82c` |
| `integrations/lifecycle/opencode_queue.py` | `9f05565e54f4de92fe87d90b7b10b3fa79b8e670a1f4762ddfd7ddbfcaf5cb6d` |
| `integrations/lifecycle/hermes_queue.py` | `608de154035cb856ebec00d1682c382dca2f78f4fbca7ec930012d92ce06c281` |
| `scripts/test_hermes_cancellation_e2e.py` | `ec47587553afc9bc2885cb485ff038911a620ed43a17008190a3a1136a67d38a` |
| Native `cli.py` | `6f376cb143fdf5d3363af11f04712e2c6f6db21b310fa3d0510f4dc4962a56f4` |
| Native `tools/process_registry.py` | `db2ec6851de0a09d742aea00128eeedb1f8114abbf125218993cce08654c1ed1` |
