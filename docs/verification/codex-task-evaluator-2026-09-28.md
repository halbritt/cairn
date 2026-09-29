# Native Codex task evaluator

The incident evaluator now supports native Codex alongside its existing Claude
route. This extends evaluation coverage; it does not establish retrieval
usefulness or accept the current recall candidate.

## Implemented boundary

- Fresh profile and workspace per run; only existing account auth is mounted.
  Codex uses its npm entry point, bypassing the host coordination launcher.
- Native recall hooks and a disposable-store MCP server for memory arms;
  none/direct arms omit both. Capture hooks are excluded.
- Native completion-event parsing, provider/version/configuration provenance,
  and explicit failure accounting. Partial answers cannot turn a nonzero exit,
  failed turn, timeout or unterminated stream into a successful run.
- Explicit per-arm semantic recall and isolated tool-free selector support.
  Hook rejection reasons and reported selector costs remain inspectable.
- Store cleanup registered immediately after acquisition, including when a
  later arm fails during startup. The shell wrapper owns the disposable PG
  lifetime.
- The exact previously ignored mid-wildcard fixture is now tracked. Frozen v2
  remains 102 files with SHA-256
  `3cc8e70b0cf6e82e5626d4eec3ff8b99265e7b1438f42f00c3f5adfa6d289f13`.

## Verification

Local parser, failure, fixture, calibration and sandbox tests passed. The
sandbox test actually starts bubblewrap and verifies that supplied auth is
read-only while the host profiles, repository and production Cairn directory
are absent. A regression test reproduces a second store startup failure and
checks cleanup of both stores. `make check` passed. No store implementation or
schema changed in this work.

Developmental native probes used Codex CLI 0.158.0, `gpt-6-astra`, high effort,
and the existing account. A tool probe received the hook marker, ran a command,
wrote a file and called the test Cairn MCP server. These observed event shapes
were used for the parser tests.

| Probe | Observed result | Wall time |
|---|---|---:|
| local-ci, none | Added GitHub workflow; frozen check marked mistake | 57.67 s |
| local-ci, direct note | Added local hooks; check marked correct | 156.68 s |
| local-ci, d7fba5d memory | Added local hooks after manual MCP search/pull; automatic hook delivered one distractor | 81.17 s |
| jev-model, repaired hook with selector | Expected note expanded; check marked correct | 45.24 s |

The local-CI memory probe borrowed a fully indexed disposable diagnostic store
with 1,900 distractors. The selector probe used a fresh disposable store with
zero distractors and no semantic worker, testing the explicit applicability
path with lexical candidates. Its four-candidate selector call took 2.434 s
and reported $0.0051676; the hook delivered 2,144 bytes and recorded one budget
rejection. It used Claude Code 2.1.284, Sonnet, with hooks/tools disabled. This
does not measure full-scale semantic recall latency or provider billing.

All probes completed and their owned stores were shut down. Raw events and
workspaces remain under `/tmp/cairn-retrieval-goal/codex-*-smoke`, outside Git.
The diagnostic store owner retained its dump before cleanup.

## Limits and next gate

These are small harness probes, not a repeated paired comparison or proof of
better automatic recall. Frozen grading has independently identified validity
problems; passing its checks is narrower than completing a real task correctly.
Agent 235 owns that correction. The recall candidate still needs better
candidate exposure, bounded applicability input and an end-to-end latency
assessment before deployment.

Codex exec turns differ from Claude turns. Only wall timeout bounds Codex;
`--max-turns` is rejected. Commands and tool calls describe attempts, not
successful execution or delivery. Hook metrics exclude manual MCP result bytes,
and selector diagnostics from state files describe the last recall per session,
not a complete series of selector calls. Read-only host filesystem access and
network access remain; reviewed fixtures must not contain secrets.

No provider configuration, production service or installed recall engine was
changed by this evaluator work. Usage is in
[the evaluation plan](../plans/task-evaluation.md#native-codex).
