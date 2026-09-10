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

## Installation and actual guidance comparison

Installed clean `9b888e11c601d07c67e4cc1a2b5e0a9fffed04a2` in the CLI/API and
native adapter. PostgreSQL remains at schema 033. The project Codex allowlist now
includes `cairn_history`; other parsed configuration is unchanged. The existing
OpenCode permissions were not edited.

A fresh Codex 0.153.4 app-server client loaded the actual project configuration,
discovered six tools and read validation lesson
`104519b5-82cd-44b3-ba73-1def2ad42e8c` versions 2 and 3. Native OpenCode 1.18.21,
with only history allowed and no shell tool, read identical history responses.
The two bodies are 1,935 and 2,964 UTF-8 bytes. Their reported source digests match
the returned text. The later body preserves the complete earlier wording and
appends the prior turn's Unicode finding and source pointers.

This completed the intended comparison using existing retained guidance. No
missing earlier wording or new correction was found in that append. Direct native
client calls are not independent model-selected use, and this comparison alone
does not establish an efficiency gain or net task benefit.

The saved history procedure was revised from version 1 to 2 to replace the obsolete
CLI-only limitation, describe native access and preserve the known large-debug-input
failure and correct test route. Fresh ordinary search/pull verifies the body and
unchanged metadata. Earlier versions remain retained. The metadata manifest records
build, configuration, client comparison and guide-update identities.

[CI 34464102538](https://github.com/halbritt/cairn/actions/runs/34464102538)
passed PostgreSQL/race, Python, static/build, use-report and authenticated CLI
checks for the installed commit. Native client checks ran locally.
