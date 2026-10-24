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

Validation: a fresh disposable PostgreSQL run of this probe, `make check`,
and `bash -n scripts/test-postgres.sh` passed locally. The repository-wide
`make test-integration` suite was not rerun for this test-only addition.
