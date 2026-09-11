# Native assessment history and writes — 2026-09-10

MCP and the custom OpenCode adapter now expose the existing assessment workflow
as `cairn_assessments` and `cairn_assess`. Recent documentation and guide-maintenance
tasks needed separate CLI scripts to record and inspect reviews. These native
tools remove that interface gap while retaining qualitative accounts and unknown
acceptance. They do not add a new evaluator or infer benefit from tool contact.

## Contract

The [workflow](../use-outcome-loop.md#native-review-tools) uses an existing owned
receipt, a caller-chosen request UUID and the latest reviewed assessment version.
The API owns authentication, repository/destination binding, evidence validation,
version conflicts and idempotent writes. Native callbacks reuse those operations;
there is no new store or evaluation layer. Agent reviews remain testimony. A
linked retrieval does not grant access to the host's task assessment.

Write responses retain receipt/version/request identifiers and observer/witness,
without echoing potentially long reasons. History returns the full retained
narratives and evidence IDs, without evidence bodies. Existing per-call response
limits apply, and history refuses more than 1000 versions without pagination.
The schema and stored assessment format are unchanged. A response failure can
follow commitment; the exact saved request remains the retry mechanism.

## Checks

- The original MCP write check failed with an unknown tool. With the implementation,
  the real API check preserves Unicode reasons, testimony, exact retries,
  conflicting-intent and stale-version refusals, corrections and foreign ownership
  refusal.
- The shared stdio/native check cites a selected evidence ID, reads the retained
  narrative, appends a correction and confirms an old request still retries to its
  original response. Accepted-without-evidence refuses.
- The old OpenCode adapter from clean `bb600c5` reports the history tool missing.
  The new adapter passed 13 calls in OpenCode 1.18.21, including explicit permission
  refusals for reads and writes. API read-back confirms the refused write did not
  add a version. The isolated fixture copied only installed plugin 1.18.21 package
  dependencies; it used no user configuration or answering model.
- The full disposable integration suite and `make check` passed. The native
  fixture's first bootstrap stopped before tool execution because its helper
  expected `data` in a successful migration envelope; the helper was corrected
  before the old/new tool comparison.

The [manifest](native-assessments-2026-09-10.json) records source and log hashes.
Production data was not used for tests. These checks establish interface behavior,
not review quality, saved time, cross-task benefit or human acceptance. The wider
OpenCode opt-in suite was not rerun; this check exercises the changed tools and
permission paths with the actual installed runtime.

## Adoption

Upgrade the MCP executable or install its matching OpenCode adapter and restart
the harness. Keep the [assessment binding repair](assessment-binding-2026-09-10.md)
in the API. No migration is needed. Installation does not alter permissions or
the existing memory-only search/pull allowlists. The installed adoption follows.

[CI for `2ef6e04`](https://github.com/halbritt/cairn/actions/runs/34565632417)
passed. Clean CLI/MCP `2ef6e04` and its matching native adapter are now installed.
The API remains on clean `bb600c5`; neither the API nor PostgreSQL restarted.
The exact 91-version note inventory, Cairn connection settings, Codex/OpenCode
configuration and semantic service settings retained their hashes. CLI and
adapter hashes match the clean build and its source, respectively. Existing
harness processes need a fresh session to load the added tools.

## Operational review

A fresh MCP process at the installed revision recorded a selected review of this
work against owned retrieval `f9110acb-dda5-45bf-8bb7-b9fd8af4b78b`, then read it
back. Version 1 retains method `qualitative-self-review/1`, witness `testimony`,
observer `agent:cairn-hosted` and outcome `unknown`. The review describes the
actual guide v3 pull, the API repair and native interface work. It explicitly
notes that source and handoff already contained the recalled rules; memory's
incremental contribution and net savings remain uncertain.

This is an implementing-agent account through the installed interface, without
an independent reviewer or new answering-model run. It demonstrates recording
that uncertainty during actual work. It does not establish task benefit. The
manifest retains its receipt, request, version and reason hash; the narrative
remains in the owned assessment history.
