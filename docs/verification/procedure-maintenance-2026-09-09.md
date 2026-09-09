# Procedure maintenance — 2026-09-09

The operational Codex and OpenCode setup notes now present current instructions
without successive release updates interrupting them. Their combined body size
fell from 13,044 to 7,820 bytes (5,224 bytes, 40.0%). Their previous versions remain
retained. This is selected ordinary memory maintenance using existing interfaces.

| Procedure | Revision | Before | After | Setup-query rank |
| --- | --- | ---: | ---: | --- |
| Codex | v6 → v7 | 5,270 bytes | 3,466 bytes | 1 → 1 |
| OpenCode | v8 → v9 | 7,774 bytes | 4,354 bytes | 1 → 1 |

## What changed and why

The notes had accumulated paragraphs for initial integration, configuration
repairs, native tools, editing, browsing, semantic discovery and setup generators.
Repeated verification caveats and historical command assembly competed with
current instructions. The consolidation puts current setup first, keeps the
native/MCP distinction and important failure paths, and uses source links for
implementation chronology and detailed contracts.

Setup commands, profile/destination choice, tool permissions, conversation/session
scope, retrieval modes, full-note inspection before edits, compare-and-swap and
retry behavior remain. The OpenCode note retains the observed string-command
configuration pitfall and the warning that process exit zero does not prove
connection. Historical performance anecdotes were removed from current guidance.
Neither note gained authority, scope or sensitivity changes.

## Verification

The docs-guard pass checked claims against current source. The documented Codex
and OpenCode configuration commands produced parsed TOML/JSON; the native installer
wrote its matching adapter into a temporary existing project with owner-only
files. Existing project settings and the running services were untouched.

Queries `codex setup memory` and `opencode setup memory` were retained before the
edits. Each target ranked first before and after. Fresh task/run scopes pulled
the exact revised bodies through the ordinary hosted profile. Earlier handles
returned `STALE_HANDLE`. A read of the two records' stored history verified both
old and new body checksums; scope, kind, class, lifecycle, sensitivity and declared
attribution were preserved. No new software tests or model trials were needed for
these body-only changes. This did not rerun native client conformance checks.

The [manifest](procedure-maintenance-2026-09-09.json) retains source hashes, record
IDs, versions, body hashes and artifact hashes. Operational note bodies and raw
responses remain outside the checkout under `/tmp/cairn-procedure-maintenance/`.
The running implementation remains `3e08c18` with migration 030.

## Judgment and limits

The coding agent's editorial judgment is that the current procedures are easier
to follow because release history and repeated caveats no longer interrupt the
instructions. The byte comparison establishes smaller bodies. An independent
reader or task comparison has not established better comprehension, decisions,
attention use or net task benefit. Two setup queries provide limited retrieval
coverage, and maintenance itself required source review, setup checks and memory
operations whose full costs were not measured.

This illustrates a maintenance obligation that accumulates across turns: adding
features and appending instructions can make retained guidance harder to use.
Selected consolidation belongs in ordinary upkeep; it does not justify a new
grooming service, an automatic ranking reward or a target note length. Review
future notes when current instructions become hard to find, while preserving
necessary context and earlier versions.
