# Hermes request control: implementation review, 2026-09-21

This is candidate verification, not deployment or coordination-v1 acceptance.
Cairn candidate `83a0430` connects its recoverable controller to the native
Hermes candidate `d27b8d55`. The installed Hermes remains `131e95b3` and native
cancellation is disabled. Ordinary delivery remains available.

## What the candidate establishes

Native admission creates an immutable request/delivery/turn token before the
queue callback. Owner steer, redirect and interrupt input share its admission
lock. An owner join revokes exclusivity before cancellation; an accepted
cancellation closes admission before interruption. Native generation and tool
workers retain the original token across child execution, timeout and reuse of
the agent instance. An executor submission exception can occur after enqueue;
it therefore retains unknown work rather than reporting zero workers.

The retained token owns a local process ledger. Foreground commands, code
execution, background jobs and PTYs enter a unique systemd user scope before
arbitrary command execution. A handshake records the unit invocation and cgroup
identity before releasing the command. Cleanup checks that identity and uses a
verified directory descriptor for `cgroup.kill`. Recursive `cgroup.events`
population, rather than a parent PID or nonrecursive `cgroup.procs`, establishes
whether descendants remain. Missing permissions and changed identities remain
unknown. This is a lifetime boundary on the trusted single-user host, not a
sandbox against deliberate escape or a claim about remote service effects.

Inventory scanning performs OS work outside the admission lock and checks that
no launch or record publication invalidated its snapshot. A scan started before
the native gate closed cannot establish final clearance. Original tokens remain
queryable when a newer owner turn starts. Caller-provided registry labels do not
create native containment ownership.

The bridge selects exact request and captured process identities. Signalling
acknowledgements are separate from fresh termination evidence. Post-effect
errors remain uncertain. The controller records cancellation/cleanup intent
before dispatch and does not replay uncertain native mutations. Its durable API
mutations retain exact request UUIDs and payloads across lost responses.
Revocation waits for natural quiescence rather than signalling joined owner work.

## Bounded verification

- The composed native slice passed 255 tests across ownership, nested executor
  accounting, process containment, coverage policy, background/PTY registry and
  code execution; four Windows-only cases were skipped on Linux.
- The Cairn controller, bridge, coordination and native cancellation suites
  passed 49 tests, including a fresh-process native CLI composition check.
- An independent actual Unix-socket probe used native request tokens and real
  contained lifecycle processes. It observed a detached child, cancelled the
  exact request, cleaned its captured scope, and obtained fresh clear evidence.
  A separate actual hook-timeout probe verified parent and detached-child
  termination. That probe explicitly initialized fixture coverage; it did not
  validate a live provider/configuration profile.
- The full Cairn Python run executed 301 tests with 14 optional NumPy skips.
  Its only error was a test-class build affected by the same VCS discovery
  issue; that class's four tests passed when rerun with test-only VCS stamping
  disabled. The composed native dependency path was supplied explicitly.
- `make check` and the full disposable PostgreSQL integration run passed,
  including the controller's real authenticated API regressions for revocation,
  invalidated scans and lost committed-response recovery. Native evidence in
  that database check is an explicit double; actual native process probes are
  separate. The first invocation stopped at Go VCS discovery of an unrelated
  `/tmp/.git`; the successful test-only rerun disabled VCS stamping. No
  production database was used.

Private selected review artifacts are in
`/tmp/cairn-agent88-bridge83-review/review.md`,
`/tmp/cairn-agent88-native-api-review-2ca48e0d/review.md`, and
`/tmp/cairn-agent88-native-executor-review-e03c8418/review.md`.
These paths identify local evidence; they are not a substitute for the checked-in
regressions or a durable external acceptance record.

## Explicit limits before activation

Coverage validation runs before each owned native conversation. Its initial
profile is direct OpenRouter chat-completions with a static credential, stock
HTTP transport, local tools, reviewed synchronous plugin callbacks, and no
uncovered auxiliary provider or subprocess helpers. It preserves ordinary tool
availability: unsupported paths run with a permanent coverage gap. Remote/shared
services, delegation setup, unknown plugins, smart-approval auxiliary routing,
optional document converters and shell hooks are not certified by local process
containment. Configuration is inspected; the policy does not silently disable
owner features.

The actual CLI performs credential refresh, routing, agent initialization and
some image preprocessing before that conversation-entry boundary. This startup
coverage gap is under review. A constructed-agent mock HTTP canary will be
labelled accordingly; it cannot establish full interactive CLI acceptance.
Completed scope ledgers also currently prevent token retirement indefinitely;
a bounded terminal-receipt repair is being reviewed.

Still required: composed profile/CLI startup review, actual native mock HTTP trials, bounded real-model interruption and
failed-cleanup trials, subsequent owner-turn preservation, release installation,
and activation verification. OpenCode needs its separate atomic require-idle
admission repair. Source integration and local fixture success do not close
these acceptance requirements.
