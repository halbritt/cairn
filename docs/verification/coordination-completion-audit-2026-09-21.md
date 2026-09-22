# Coordination delivery completion audit — September 21

The previous turn repaired and verified agent 65's automatic channel delivery.
This audit returns to the full delivery goal. It does not equate a clean main
branch or a successful narrow trial with full coordination acceptance.

## Reconciliation

At the initial snapshot, main and origin/main both named `b7d3eb8`, with no
uncommitted main changes. Agents 2, 24 and 67 received separate durable audit
requests and explicitly completed them once. Their results are evidence to
review, not automatic acceptance. Agent 2's report incorrectly repeated main
`b5aab96`; the coordinator's current Git observation establishes `b7d3eb8`.

Independent reconciliation inspected 96 dirty files across 11 older worktrees.
Forty-five files exactly match current main, 17 match historical main revisions,
and five scratch patches have patch IDs matching ancestor commits. Other old
snapshots contain superseded bridge, documentation and cancellation experiments.
No original worktree, index, stash or patch was deleted or reset.

The audit identified one ordinary unintegrated repair candidate: orderly
supervisor shutdown during an outer heartbeat/claim/control RPC. The separate
lease-renewal shutdown repair is already integrated. The outer-loop race was reproduced, repaired, independently reviewed, and
integrated as `8f4fb50`. Held Unix HTTP calls test cancellation during claim and
control; cleanup still runs with its independent budget, and a refused finish
remains an error. A successful live restart does not replace that deterministic
race evidence. Claim/commit ambiguity remains governed by existing startup
reconciliation; this patch does not newly prove that PostgreSQL race.

The two old native-control wiring patches differ materially: one asserts
exclusive ownership merely from queued wake binding, and the other does not.
Neither is accepted for deployment. The source-defined ownership and cleanup
requirements remain in force. Agent 24 is implementing a bounded Hermes slice
in isolated worktrees because that open native bridge has a request/turn-fenced
abort operation; model interruption and observed cleanup still require native
acceptance afterward.

The first candidate, `96a5b02` with follow-up `60505a1`, was rejected after
independent native-method and process tests. Owner steering can join the active
turn while the CLI admission lock is held, preserving its turn ID. The proposed
foreign-turn observer therefore does not establish revocation before an owner
join. Separately, the installed native process registry can report `killed` and
`exited` while the original process is still alive. A disposable SIGTERM-ignoring
child reproduced this with the supported zero-grace setting; the fixture then
force-killed and reaped its own child. Registry flags alone cannot establish
terminal cleanup. The candidate also used the wrong admission callback status
and referenced callback variables before assignment. Its fake-bridge integration
success did not cover these native contracts. A revision task requires repairs
and native evidence in isolated Cairn and Hermes worktrees; nothing from this
candidate was deployed.

## Standing tests

The full Python discovery run on `b7d3eb8` executed 272 tests, with one error and
14 skips. The error was a stale Hermes provider-observation fixture whose plain
mock fabricated an admission context manager. Hermes fixture teardown also left
an asynchronous logger pointing at a deleted temporary directory. Both were repaired in `130f2c9`: the provider fixture uses an explicit context
and real admission lock, and teardown drains and closes only fixture-owned
logging handlers. An actual asynchronous logging regression fails before repair
for three ownership scenarios and passes afterward. The coordinator independently
reran full discovery: 273 tests, exit zero, 14 optional numerical skips, and no
logging tracebacks. Existing resource warnings remain visible. The original
failed run is retained as regression evidence.

The 14 optional numerical tests passed separately, without skips, using the
already installed semantic-worker Python environment. No dependencies were
installed and no production database was used for testing.

## Live OpenCode draft preservation

The coordinator placed one clearly marked, unsubmitted canary in agent 67's
observed empty composer. Its native session was unchanged and unfocused. The
audit request had finished before the notice was published, so this trial tests
an idle draft, not a busy submission race.

A deployment-status notice was published at 17:08:57 PDT as event
`a92a930e-e5a1-41dc-84b0-da439d82cb99`. It arrived through the automatic native
queue, was read and acknowledged at 17:09:53 after exactly one claim, and the
composer still displayed the canary verbatim while and after handling. The
coordinator removed only that test text and verified the composer was empty.
A read-only native-store query found zero submitted user messages containing
the canary. No manual claim or wake replay was used.

This closes the observed idle-draft preservation case for the installed
OpenCode route in this existing conversation. It does not establish all-account
coverage, the native check-to-submission race, or cancellation safety.

## Remaining acceptance

| Requirement | Current evidence | Remaining work |
| --- | --- | --- |
| Commit, push and deploy intended release | Clean `8f4fb50` CLI/API, scheduler and seven supervisors deployed and independently hash-verified after full disposable integration; schema 049 unchanged | Finish remaining native acceptance work |
| Reconcile agent deliverables | All three named agents completed audit requests; 96-file independent worktree comparison; shutdown omission repaired | Keep unsupported prototypes separate; Hermes cancellation slice underway |
| Automatic current-session delivery | Agy boundary handling, OpenCode native queue and selected Claude channel observed | Older unactivated clients remain distinct from these targeted checks |
| Draft preservation | Live OpenCode idle canary passed; native fixtures cover additional cases | Busy submission races and broader harness/account live coverage |
| Per-request native cancellation | Dormant 049 core; Hermes request/turn-fenced bridge | Exclusive admission/revocation, real-model interruption, confirmed owned-tool cleanup and failed-termination drill |
| Unsupported native seams | No verified Agy idle RPC/server; OpenCode installed abort lacks expected-turn fencing; Claude has shared prompt joins | Preserve explicit refusal; do not invent capability or silently drop acceptance requirements |

Private evidence is retained under the release's `completion-audit` directory,
plus `/tmp/cairn-agent88-worktree-reconciliation.json`. Operational message
bodies, credentials and raw native transcripts are not committed.

Follow-up inspection found agent 65's initially functioning channel had stalled
after several messages because idle cleanup skipped a completed-delivery latch.
The repair was reproduced, independently reviewed, pushed and deployed as
`ce17d4c`; see the [delay follow-up](agent-delivery-delay-2026-09-21.md#follow-up-delivery-latch-stopped-the-backlog).
Initial successful messages were insufficient evidence of sustained delivery.

## Supervisor repair rollout

`make check` and the full `make test-integration` target passed on clean
`8f4fb501bc6d95394627f6fd7d32862c8d6ed367`. The integration target used its own
disposable PostgreSQL cluster and real managed-worker probes. Source was pushed
before deployment. The candidate binary reports that clean revision and SHA-256
`80e02d6a5bfdd314bedf287a8bc504fc1515799bb6585197d4c34e1c13726942`.

At 17:20:23–17:20:24 PDT the coordinator stopped idle managed supervisors and
the scheduler, atomically installed the candidate, restarted the API, and started
the supervisors/scheduler. No schema migration, presence watcher restart, native
conversation restart, manual claim, or wake replay occurred. There were zero
managed attempts and one native inbox hold before and immediately afterward.
The prior binary and selected state snapshots are retained in the private release
`~/.local/share/cairn/releases/20260922T001608Z/`. Independent post-deployment
verification at 17:21 confirmed matching installed/running executable hashes for
the API, scheduler, and all seven supervisors, healthy services, all 28 captured
native session identities/executions unchanged, and the exact original native
request hold retained. Its host process predated deployment. No new shutdown
failure was observed; the deterministic regression remains the race evidence.

Agent 2's follow-up found Agy's remote-control command, so its earlier absolute
claim of no control seam was narrowed. The installed client has a remote-control
feature, but this review has not established a supported local programmatic
queue/cancellation contract or preservation guarantees for it. Symbol names alone
do not prove runtime routing, authentication requirements, or draft behavior;
those stronger report claims are not accepted as native verification.

## Codex cancellation boundary

Independent offline schema export from installed Codex 0.155.1 confirms that
`turn/interrupt` accepts thread and turn IDs, without an ownership-generation
fence. The retained controller prototype checks an earlier Cairn exclusivity
observation before issuing that RPC and has no owner-join capture handler. Idle
queue admission therefore does not establish continued exclusive ownership.

A separate cached 0.154 source snapshot shows owner steering into the active
turn and a generic interrupt operation after an app-server turn check. That
source is not a verified match for 0.155.1 and is not accepted as a current
provider-race test. The precise missing contract is native serialization of
admission, owner-join revocation, and expected-ownership interruption. Codex
interactive cancellation remains unavailable; the earlier agent-24 feasibility
claim is not sufficient to enable it. No native session was launched or changed
for this offline audit.
