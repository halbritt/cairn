# Disposable inbox resume probe (2026-10-24)

[CAIRN-224's probe](../../scripts/check_inbox_resume_disposable.py) loads
private, pinned copies of the inbox recall bridge and lifecycle engine through
the hook subprocesses against a newly migrated PostgreSQL database and
authenticated local API. A synthetic Codex hook owner holds one native request
attempt. The probe invokes
synthetic `PreCompact`, compact `SessionStart`, and resume `SessionStart` events;
no Codex process performs compaction.

The first compact callback returns the issued mandatory instruction and the
exact whole unfinished checkpoint note. Before resume, the disposable operator
retracts that instruction and issues its replacement, while the note is revised
to version 2. The resume callback returns only the current mandatory source
and the current whole note. Its grant ledger records required and checkpoint
refresh bytes separately, and the same request delivery remains leased with
one attempt throughout both callbacks. The owner then runs the original
context's `finish_reply` command twice with identical input; the Store retains
one result and one causally linked reply.

The probe uses a local selector that cannot contact a provider. It tests hook,
API, Store, source-currentness and journal wiring. Model context retention,
task acceptance, real automatic/manual compaction, provider usage, and an
installed canary remain unobserved. Same-ID reply loss after a committed
publication is covered separately by the
[relay response probe](../../scripts/check_panel_relay_uncertain_response.py).

A follow-up failure case now commits the native request result, hides the local
API socket before its reply, and observes `completion=committed` with the exact
reply request pending. It runs the synthetic compact and resume callbacks while
that reply remains pending, confirms the completion and reply intent are
unchanged, then restores the socket and retries the original `finish_reply`
intent twice. The Store retains one handled delivery, one result, and one
causally linked response; the delivery attempt count stays one. This adds
cross-layer source/API/hook evidence for a partial completion/reply failure
across resume. It does not exercise a real native compaction or establish model
retention or accepted task outcome.

Validation: a fresh disposable PostgreSQL run of this probe, `make check`,
and `bash -n scripts/test-postgres.sh` passed locally. The repository-wide
`make test-integration` suite was not rerun for this test-only addition.

## Native Claude compaction/resume boundary

Source review found two complementary but unjoined local fixtures. The native
Claude lifecycle check in [`check_claude_lifecycle.py`](../../scripts/check_claude_lifecycle.py)
resumes a session, sends the real `/compact` command, observes `PreCompact` and
`SessionStart:compact`, then verifies a later `--resume`. Its stored checkpoint
is a lifecycle handoff; it does not include an outstanding agent delivery or
attempt. The native compact-inbox check in
[`check_claude_compact_native.py`](../../scripts/check_claude_compact_native.py)
uses an exact Store-backed notice/response source and verifies native read and
settlement, but it completes that path in one CLI run without issuing `/compact`
while the delivery is leased.

The disposable CAIRN-224 resume probe verifies an outstanding native attempt,
current mandatory source and checkpoint across PreCompact/compact/resume hooks,
but those callbacks are synthetic. None of these fixtures establishes all of
those properties in one actual Claude compaction/resume sequence. No native
provider request was started for this follow-up, and no source, attempt, or hook
state was injected to simulate the missing join. The next useful fixture must
start from the real local API/Store, leave a native attempt unresolved while
Claude processes `/compact`, resume the same session, and verify the exact
source ref and attempt settle at most once. CAIRN-224 remains open pending that
joined evidence and accepted task/provider/canary results.

### Bounded joined native attempt probe

A loopback-only composition was attempted with Claude Code 2.1.310, fresh
private hook configuration, fixture-only API identities, disposable PostgreSQL,
and a scripted localhost Messages endpoint. The existing native notice and
response compact-inbox scenarios passed. In the added sequence, the first
bounded CLI run (`--max-turns 1`) exited with status 1 before the fixture could
verify an outstanding attempt. A single adjusted run (`--max-turns 2`) exited
normally, but the Store event did not satisfy the required `leased` plus one
attempt precondition. The probe stopped there; it did not invoke `/compact`,
resume, or settle that event. The disposable cluster and fixture configuration
were removed by the runner. No provider route was contacted and no raw stream
or credential was retained.

This is an inconclusive joined-probe setup result, not evidence that native
compaction cannot occur. The existing native lifecycle check exercises `/compact`
and `--resume` separately; the joined source/attempt sequence remains unverified.
The experimental harness changes were discarded. Keep the issue open until a
bounded fixture can establish a leased attempt before compaction and carry its
exact source/ref across native resume without synthetic callbacks.
