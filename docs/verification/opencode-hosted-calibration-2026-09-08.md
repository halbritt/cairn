# Hosted OpenCode calibration — 2026-09-08 UTC

Neither hosted calibration produced a repair. Both timed out after 300 seconds
with empty patches, and the independent cache-lifetime condition failed. These
are repository-only calibrations; no memory benefit can be inferred.

The binding was the operator's existing OpenRouter
`deepseek/deepseek-v4-flash-0731` configuration through OpenCode 1.18.21. The
historical source, task, write scope, 131,072-token configured context,
8,192-token output limit and 20-step setting were the same in both runs.
The broken/reference preflight distinguished the original defect in each fresh
trial. No live Striatum graph or source worktree was changed.

| Trial | Request byte limit | Forwarded requests | Relay refusals | Duration | Patch | Reported cost |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| G | 262,144 | 15 | 2 | 300.103 s | Empty | $0.021010 |
| H | 1,048,576 | 22 | 0 | 300.091 s | Empty | $0.034588 |

Costs sum the last numeric usage report on each request. Interrupted responses
can omit usage; these totals are not invoices. G had one failed relay response;
H had two. Exact status, failure type, duration and provider identities are in
the [metadata report](opencode-hosted-calibration-2026-09-08.json).
H's nineteenth request reached the relay's 120-second response limit; its last
response hit a broken pipe after the model process deadline. Thus removal of
request-size refusals did not remove every execution constraint.

G's first request bound was too small for accumulated tool results. The relay
refused two requests; compaction was observed in a partial native inspection.
That is an experimental restriction to preserve, not evidence of model
incapability. H raised only the request byte allowance as a model-facing
experimental setting. It also used a reporting fix that publishes request
snapshots under a lock rather than sharing actively mutated response metadata.
Frozen copies and hashes distinguish both executed controllers and relays.

H completed 17 reads, four glob calls, seven grep calls and two shell calls.
Five shell calls were denied by the trial policy, including attempts to read Git
history and searches piped through an unallowed `head` command. A partial native
inspection observed 20 completed steps, a further active step and one compaction.
No edit tool event or changed source file was observed. A larger step/time
allowance is a possible next calibration; this result does not prove that the
binding cannot repair the task. Repeating a memory comparison at this budget
would still lack a demonstrated successful repair baseline.

The controller attached the failed necessary condition to each actual receipt as
operator testimony. Task rejection here means the trial did not produce the
required behavior. It does not diagnose capability or confer Striatum acceptance.
The memory scopes were empty and no citation or usage influence was recorded.

The new opt-in relay keeps the existing provider credential outside the model's
filesystem and environment. It exposes a disposable loopback token, fixes the
upstream model and HTTPS destination, restricts forwarded fields, and enforces
request count, byte and output limits. Provider price and data-collection filters
use the documented [OpenRouter routing controls](https://openrouter.ai/docs/guides/routing/provider-selection).
The relay retains metadata and digests; it is not network confinement, a
production credential service or an account billing cap. The
[experiment protocol](../experiments/opencode-recurrence.md#bounded-hosted-calibration)
describes these boundaries and the opt-in command.

Four local HTTP fixture tests pass for streaming, credential separation, request
refusal/exhaustion, large tool history, HTTP failure without relay retry and
connection refusal. They run under `make test` and CI without hosted requests.
`make test check` passed; production Go code, migrations and runtime installation
were unchanged. No database suite was required for this experiment-only change.

Both native session homes and caches were removed, both PostgreSQL clusters were
stopped, and both relay listeners closed. Private patches, source snapshots,
trial dumps, frozen scripts and metadata remain at the report's named `/tmp`
locations. Derived Go build caches were removed. No raw native session or
provider credential is included in the committed report.
