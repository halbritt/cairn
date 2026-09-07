# Contract repair verification — 2026-09-07

Baseline: `e3b47c7`. Requirements and remaining acceptance work are tracked in
[the roadmap](../roadmap.md), R1–R5.

| Contract | Before repair | Exercised result |
| --- | --- | --- |
| Default capture | Synthetic prompt appeared in stored semantic bytes, replay and context file | `TestQueryIsTransient` and the CLI `check-capture.py` canary pass; task still reaches the process |
| Advisory B evidence | All-unavailable B omitted for context | Advisory delivery includes divergent/dangling evidence states; consequential reads still require one resolvable reference |
| Blocked consequential demand | Matching A excluded with no docket item | Protected candidate event commits with receipt; current demand is grouped by record/version; retry does not duplicate it |
| Protected explanation | Only selected content and aggregate counts retained | Owner/repo checks, hidden destination exclusion, ranking/packing features and fixed census checks pass |
| Retraction preview | Retraction committed without impact query | Missing/foreign/expired/stale previews refuse; fresh preview succeeds and retry returns original mutation; concurrent new exposure invalidates preview |
| Historical replay | Existing v1 semantic bytes | Frozen synthetic v1 fixture still reproduces its original seal and query; explanation version 0 explicitly reports no historical detail |

`make test-integration` passed on disposable PostgreSQL 17 with Go's race detector,
including the CLI capture check. `make check` passed. `make test-lifecycle` passed
startup, backup, restore, saved-package replay, stop and restart on its own cluster.
No test used the operational database.

After those checks, the dedicated local store was backed up and migrated through
005. The installed `~/.local/bin/cairn` was replaced. A local search returned a
semantic v2 package with a query digest, one selected record, and an explanation
covering three candidates. This is installation smoke evidence.

Retraction preview covers direct uses across all versions and refuses above 1,000
uses. Transitive dependencies are unfinished. Hard compilation refusals still lack
a durable explanation. Existing v1 text and backup copies remain retained; the new
capture path does not purge them. No real model call, Striatum build, measured
usefulness trial or full Stage 1–2 acceptance occurred in this repair slice.
