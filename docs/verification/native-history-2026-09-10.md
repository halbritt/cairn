# Native retained-history access, 2026-09-10

`cairn_history` now exposes the existing history read in MCP and native OpenCode.
A native caller can compare earlier wording without shell access: metadata pages
list retained versions; an exact-version request returns the original body and
source digest. The prior implementation returned an unknown-tool error in the
MCP regression. The [contract](../record-history.md#native-tools) defines paging,
output limits, historical meaning and upgrade requirements.

The API/store retains ownership of repository, sensitivity, deletion and snapshot
checks. An optional API `repo` constraint lets each facade bind the read to its
configured repository; native arguments cannot override it. The first implementation
omitted that check and returned history despite a different configured repository.
The added regression failed before this was corrected. Existing CLI/API requests
without the field retain their behavior. No migration or new retained state is
required.

## Verification

Full disposable PostgreSQL/race integration, Go/40 Python, static and final MCP
checks passed. MCP and OpenCode verify exact earlier Unicode bodies and digests,
metadata-only pages, continuation after appending a new version, an empty terminal
page, missing versions, invalid paging combinations, local-only exclusion,
configured repository refusal, stale-edit refusal and oversized-body refusal.
OpenCode also checks permission denial and invalid arguments before execution.
A normal session with scripted loopback completions reads an exact older body
after successive edits. No answering model ran.

The first full native check failed while decoding debug stdout from a 64 KiB
capture. The [earlier note-transport report](note-transport-2026-09-09.md) had already
recorded this failure; it was consulted only afterward. The large history-budget
fixture now uses operator CLI setup, while native history still performs the read
and refusal. Existing normal-session tests cover large capture and edit behavior.
No product workaround or assumed root cause was added. The corrected complete
native suite passed. This repeated fixture mistake is retained as avoidable
maintenance cost.

## Contribution and limits

The current history procedure `cd6217ad-2f69-45f1-bcfb-a01066909855` version 1 was
searched and pulled before implementation. It confirmed existing read semantics
and the native access gap. Earlier retained-history use had helped recover omitted
instructions, as recorded in the [maintenance case](cumulative-maintenance-value-2026-09-09.md).
The new tool extends that ordinary access path; it does not itself demonstrate a
better model task, independent correction or net memory benefit.

Historical class and wording remain inspection material, not current authority.
The tool does not consume index expansion credits or create use/mutation receipts;
hosts must budget combined reads. Oversized bodies are refused, not truncated.
The Codex generator includes the new read tool, but existing allowlists require
an explicit update. Native Claude/Agy history execution and aggregate task budgets
remain unverified or incomplete.

[Metadata](native-history-2026-09-10.json) retains source, failure, check and decision
pointers. Installation and actual retained-guidance review are recorded separately.
