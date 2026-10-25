# CAIRN-226 live recovery metadata snapshot, 2026-10-25

This is a read-only operator observation, not a recovery experiment. At
13:27 UTC, `cairn coordination-review` paged every delivery in the production
Cairn repository with `limit: 100` and a strictly advancing `next_after`.
It returned 7,288 deliveries: 6,605 handled, 642 failed, and 41 pending.
No delivery exposed a `recovery_transitions` row. These state counts are a
snapshot, not an incident rate or a cohort of recovered tasks.

The failed rows comprised 451 `consumer_retired`, 96 `operator_cancelled`,
and 95 `processing_failed`. Each of the 95 `processing_failed` rows had an
exact `latest_native.attempt_id`. A private, owner-only
`cairn.recovery-evidence/1` selection recorded the event, delivery, and
attempt from those rows with settlement outcome `failed`; model invocation,
operator action, and recovery state stayed `unknown`.

`scripts/workflow_model_report.py` rebuilt a private
`cairn.workflow-model-report/1` through 2026-10-25 13:30 UTC using the frozen
40-session native manifest from the token-efficiency baseline, the retained
coordination state, and no event-metadata input. The manifest SHA-256 is
`5b6c77aa7506ad74feac27fd2cdff1eacdc533d9830d061ebb3b1b74c1087213`.
The rebuild selected 6,226 delivery workflows; only 1,099 had a unique native
turn ID in those sampled sessions. The sample is not all native sessions.

`scripts/recovery_model_report.py` then joined the 95 selected failed attempts
to that workflow report. All 95 event, delivery, and attempt identities matched.
Four joined a provider turn from the frozen native-log sample; 91 had unknown
provider work. The settlement phase's `observed_responses` is therefore **null**,
not zero or a total for the 95 attempts. No recovery action, recovery time,
accepted task outcome, marginal token cost, or savings follows from this join.

## Exact-session filename follow-up

A second private manifest used the registered agent directory's native session
ID only to find log filenames for the 95 selected attempts. It found 33 files:
17 Codex and 16 Claude. The current directory entry can outlive an earlier
execution, so the filename is a discovery hint, not an attribution claim.
`scripts/workflow_model_report.py` still requires the native turn join, and
`scripts/recovery_model_report.py` still requires the exact event, delivery,
and attempt. This 33-file manifest has SHA-256
`ae8eb89d63f0ff52991ccee44f1ca960b2bd1e60562aea40825f9a6609797e48`.

With those 33 files, 11 of the 95 failed attempts joined provider turns and
84 remained unknown. Two of the 11 joined projections carried the report's
`duplicate_response_id` flag. The report again left settlement-phase
`observed_responses` null. Other harnesses, missing logs, historical execution
changes, and unselected provider records remain outside this measurement.
The two manifest results are coverage checks for this selected cohort, not a
before/after effect of a Cairn change.

The private manifest, native logs, selected event identities, and report remain
under the owner's cache and are not committed. This observation uses source at
`78941c0`, the installed local operator review path, and report schema
`cairn.recovery-model-report/4`. It supports a narrow coverage finding:
current Store metadata can identify failed native attempts, while the frozen
and exact-session log selections cannot account for most of their provider
work. CAIRN-226 stays open for live recovery outcomes and complete usage
attribution.
