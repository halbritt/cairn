# Stale native journal after reviewed reissue, 2026-10-24

The frozen `stale_execution` case requires the old execution's settlement to
fail after rotation while the current execution keeps a recoverable request.
The existing Store test exercises that transition directly. The two-host test
checks a pending journal after a relay outage and a restore fence after a
committed completion. `scripts/check_panel_stale_journal.py` covers their
intersection through the actual native command helper, authenticated CLI and
local API on a fresh disposable PostgreSQL database.

The probe publishes the fixture's exact `Check marker ST-6.` source, claims its
request into a synthetic native turn, and writes the hook's real owner-only
context. It hides the disposable API socket for one `complete` attempt. The
helper retains the original request ID, result input and command as a pending
journal entry. It then marks only this disposable session offline. A local
operator releases the exact hold, explicitly accepts uncertain prior effects,
and reissues the failed request. The same binding registers a new execution,
which claims the successor delivery.

Retrying the old context returns `STALE_SESSION`; the journal records a final
refusal under the original request ID. Neither the failed original nor the
leased successor gets a result from that retry. The current execution then
commits one synthetic result on the successor and reconciles its attempt.
The original has no result, and the Store has exactly the request source and
the current execution's result record.

Run through `make test-integration`, or run the script against a newly created
`cairn_panel_stale_journal` database in an invocation-owned disposable cluster.
The focused disposable run and `make check` passed on this change. The hook
admission and result are synthetic; no queue turn, model, provider usage,
independent task grade or accepted `stale_execution` actor outcome is present.
