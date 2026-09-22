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
lease-renewal shutdown repair is already integrated. The outer-loop candidate
requires reproduction and independent review before integration.

The two old native-control wiring patches differ materially: one asserts
exclusive ownership merely from queued wake binding, and the other does not.
Neither is accepted for deployment. The source-defined ownership and cleanup
requirements remain in force. Agent 24 is implementing a bounded Hermes slice
in isolated worktrees because that open native bridge has a request/turn-fenced
abort operation; model interruption and observed cleanup still require native
acceptance afterward.

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
| Commit, push and deploy intended release | Core `b5aab96`, schema 049, installed repaired adapters; main `b7d3eb8` pushed before this audit | Finish and deploy newly identified repairs after validation |
| Reconcile agent deliverables | All three named agents completed audit requests; 96-file independent worktree comparison | Review shutdown candidate and keep unsupported prototypes explicitly separate |
| Automatic current-session delivery | Agy boundary handling, OpenCode native queue and selected Claude channel observed | Older unactivated clients remain distinct from these targeted checks |
| Draft preservation | Live OpenCode idle canary passed; native fixtures cover additional cases | Busy submission races and broader harness/account live coverage |
| Per-request native cancellation | Dormant 049 core; Hermes request/turn-fenced bridge | Exclusive admission/revocation, real-model interruption, confirmed owned-tool cleanup and failed-termination drill |
| Unsupported native seams | No verified Agy idle RPC/server; OpenCode installed abort lacks expected-turn fencing; Claude has shared prompt joins | Preserve explicit refusal; do not invent capability or silently drop acceptance requirements |

Private evidence is retained under the release's `completion-audit` directory,
plus `/tmp/cairn-agent88-worktree-reconciliation.json`. Operational message
bodies, credentials and raw native transcripts are not committed.
