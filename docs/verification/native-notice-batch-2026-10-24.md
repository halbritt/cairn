# CAIRN-221 native notice batch, 2026-10-24

`scripts/check_native_notice_batch.py` runs against a fresh PostgreSQL database
and local Cairn API created by `scripts/test-postgres.sh`. It registers a
synthetic existing-session Codex owner and publishes four distinct routine
notices before claiming the first. The test process is the synthetic native
owner. It writes a private coordinator state and calls `inbox_context`
directly to issue the first compact handle; child processes then run the real
`inbox compact read`, `settle`, and `advance` commands. This does not simulate
a Codex hook invocation or a model turn.

The probe checks the exact source and delivery for each item, one root and
native turn, successive batch indices, distinct leases, and separate handled,
failed, and ignored settlements. It repeats each handoff with the same
request ID against the Store API and checks the same successor attempt.
The third advance reports `batch_limit`; the fourth notice remains pending
and becomes ready after explicit final hold reconciliation. No provider was
called and no actor task outcome was graded.

The first live probe exposed a coordinator defect: `advance_batch_intent`
selected a session-wrapped CLI client even though `AdvanceSessionInbox`
requires the base authenticated profile with the session in its request body.
The corrected call retains the exact session JSON and omits the CLI session
selector. The disposable API regression now exercises that boundary.

The focused fresh database probe and `make check` validate source behavior.
They do not establish that the installed coordinator or a live canary has
processed a batch.

A follow-up focused disposable API probe adds the missing urgent-arrival
transition: after a later native turn claims its first routine notice, an
explicitly urgent notice is published while another routine notice is already
waiting. The real compact helper explicitly settles the claimed item as
handled, then `advance` returns `priority_waiting`. Store inspection confirms
the waiting routine and urgent deliveries both remain pending with zero
attempts; final reconciliation closes only the explicitly handled attempt.
This closes the source-fixture gap for mid-batch urgent preemption through the
authenticated API/helper path. It remains a synthetic native owner without a
provider turn or accepted task outcome.

## Staged Codex first-admission readiness

The Codex staged-candidate fixture also has a `batch-admission` scenario. It
copies the disabled, full-profile candidate into a private test home, enables
its batch manifest only in that copy, and registers a real native Codex thread
against a disposable PostgreSQL/API fixture. It publishes the same three
routine bodies used by `routine_burst_urgent_request`, then starts a fresh
`UserPromptSubmit` turn. The loopback Responses endpoint refuses the first
request with HTTP 503 before returning model output or tool calls.

The probe verifies the copied binding pins compact inbox and notice batch v1;
the first admitted delivery, exact source and compact handle; the request's
native turn ID against the observed `UserPromptSubmit` hook; and the presence
of that item's durable advance request ID and bounded continuation cue. At
admission, only the first notice is leased (attempt 1); the two neighbors stay
pending with zero attempts. No actor tool call, source read or disposition is
scripted. After the fixture refused the request and stopped, the first delivery
was `failed` and both neighbors remained pending. That state is not an
actor-selected per-item outcome.

This closes only the staged binding and first-admission readiness check. The
compact-resume `SessionStart` path has no native turn ID, so it does not qualify
for this batch admission; the probe deliberately starts a fresh prompt turn.
The fixture provides no actor choice to advance, accepted task grade, provider
usage, response reduction, latency result or live deployment observation.
CAIRN-221 remains open for those acceptance gates and a canary.
