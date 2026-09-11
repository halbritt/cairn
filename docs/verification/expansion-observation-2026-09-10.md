# Preserve observed expansion in use reports

The use report selected one usage observation by the accepted usage ladder and
then by recency. After an actual service expansion, a later reported expansion
could change the displayed witness to testimony. A citation took precedence over
expansion entirely. Those classifications were accurate for the selected event,
but the report no longer showed the separately retained instrumented expansion.

Each row now includes `expansion_observed`. It is true if any retained usage event
for that exact record/version/receipt records an instrumented expansion. The
existing usage label, witness and method retain their meaning and ordering.
Testimony does not become an instrumented event, and the report does not assign
witnesses a scalar trust rank. No observation or historical outcome is rewritten.

The core computes the additional fact across the matching observations before
selecting the existing representative event. It retains one row per exposure,
scope/record filters, pagination, report access and the existing transaction.
CLI and API share that projection. No endpoint, schema migration, ranking rule,
automatic utility update or memory-value score is added.

## Verification

A disposable PostgreSQL regression follows one record through no usage, reported
expansion, actual authorized expansion, later expansion testimony and citation.
The first check failed because the new output field was unpopulated; a refined
check reproduced the relevant later-testimony state. The completed check verifies
that the instrumented fact survives, testimony alone leaves it false, another
record stays unknown, a repeated pull does not multiply rows, and a different
retrieval does not inherit the observation.

The actual authenticated CLI fixture pulls a body, records citation testimony and
checks both facts in the JSON report while task outcome and usage coverage remain
unknown. The direct CLI history check verifies false for exposures without an
instrumented expansion. The focused PostgreSQL race check, full
`make test-integration` and `make check` passed. The
[manifest](expansion-observation-2026-09-10.json) retains source and log hashes.

## Meaning and limits

This field establishes a retained service-side expansion operation. Body and
supporting-evidence pulls, including excerpts, qualify. It does not assert that a
response reached the model, that the entire source was read, or that memory
influenced the task. False means no qualifying event is retained; absent telemetry
remains unknown. Old servers omit the field, which is not a false observation.

The [report contract](../use-outcome-loop.md) preserves citation testimony,
inferred usage, coverage and task assessments as separate evidence. This repair
improves visibility of existing evidence; it does not establish improved review
decisions, complete U4/U5, or add a new memory-task value case.

Leaving the projection unchanged would keep hiding the observed expansion in
the demonstrated sequence. Reordering the usage ladder would change its accepted
meaning, and ranking witnesses by trust would conflate distinct channels. A
separate boolean answers this specific review question without a new event-history
API or a larger reporting subsystem. The owner's delegated Cairn improvement
scope authorizes this additive report change. API and direct-store CLI adoption
are separate from checkout verification.

Pincite packet `pkt-dcbb492306167cc5` supported live-interface compatibility,
placement in the existing owner and preservation of behavior. Its bounded
decision receipt validates, and both consumption records are closed. Thirty
unmet obligations concern unrelated data/interface/configuration changes,
stronger external/monitoring claims or a separate production incident; each
nonmaterial classification is retained in the manifest. Installation remains
separate from this checkout verification.

## Installed verification

[CI for `4e75ed1`](https://github.com/halbritt/cairn/actions/runs/34560678618)
passed. Clean CLI/API builds from that commit are installed with matching
executable hashes. The API restarted at PID 386925; the PostgreSQL service stayed
at PID 163669. The inventory digest of all 88 ordinary record versions is
unchanged. No migration ran, and the prepared semantic worker and five-minute
idle configuration retain their hashes. The worker remains from `ed29cbc`.

An ordinary hosted-profile search and pull recovered the expected current value
evaluation decision v1 and body hash. The installed API still refused that
profile's protected use-report request. Report-field behavior was verified on
the disposable authenticated fixture; production protected report contents were
not read. The manifest separates those checks from installed build identity and
normal retrieval. No memory record was created or revised for this change.
