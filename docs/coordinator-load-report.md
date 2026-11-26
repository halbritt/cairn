# Report coordinator load and bookkeeping share

Use `scripts/coordinator_load_report.py` to measure how much of a coordinator
session's provider usage is spent on inbox wakeups and bookkeeping versus
decisions, verification, and worker dispatch.

The script is read-only and requires only the Python standard library. It reads
selected Codex or Claude native transcripts (single `--transcript` with
`--harness`, or `--manifest` JSON list of `{harness, path}` descriptors) over a
half-open ISO-8601 window (`[since, until)`), optionally joined with retained
coordination state (`--coordination-state`) and `cairn event-stats` JSON
(`--event-stats`).

The report JSON (`cairn.coordinator-load-report/1`) is payload-free and uses
opaque agent and sender hashes (`cairn.workflow/1`). It never contains message
bodies, prompts, shell arguments, native transcript paths, raw UUIDs, or
credentials. When `--output` is set, the report file is created exclusively
with `0600` permissions and stdout prints a minimal summary containing the
caller-supplied `output` destination and aggregate counts.

```sh
PYTHONPATH=scripts python3 scripts/coordinator_load_report.py \
  --since 2026-11-06T05:09:00Z --until 2026-11-09T19:25:00Z \
  --harness codex \
  --transcript "$HOME/.codex/sessions/YYYY/MM/DD/rollout.jsonl" \
  --coordination-state "$HOME/.local/share/cairn/coordination/state" \
  --output "$HOME/.cache/cairn-coordinator-load.json"
```

## Reported dimensions

- `turns.by_trigger`: completed turns classified as `inbox_wakeup`,
  `goal_continuation`, or `owner`, with `input`, `cached`, `write`, `output`,
  and `reasoning` token totals and shares. Incomplete boundary turns appear in
  `turns.incomplete` and are excluded from completed rollups.
- `wakeup_outcomes`: `inbox_wakeup` turns classified as `acknowledge_only`,
  `decision_or_action`, or `report`, with `acknowledge_only_share` and
  `tokens_per_decision` (`wakeup_tokens_per_decision`,
  `session_tokens_per_decision`, and `decision_turn_avg_tokens`).
- `tool_calls`: tool invocations classified as `bookkeeping` (with
  `inbox_lifecycle`, `memory_notes`, `agent_context`, `tracker`, `report`, and
  `coordinator_state` subcategories), `worker_io`, `verification`, or `other`.
- `deliveries`: deliveries in the window grouped by `kind` (`notice`,
  `response`, `request`) and opaque `sender_ref`, separating `self_sent` from
  `external`. Uses `coordination_state` when `--coordination-state` is supplied
  (requiring `--agent-id` or a unique observed Cairn session banner in the
  transcript), and falls back to `transcript` headers otherwise.

## Limits

- Outcome and tool-call classes use deterministic tool-name and command
  heuristics without reading model or user prose.
- Provider token counts represent whole-turn response usage. Missing usage
  fields or duplicate provider response IDs null affected exact `total`, share,
  and `tokens_per_decision` values while retaining diagnostic `known_sum`.
- Retained `coordination-state` inbox files do not store event publication
  timestamps and are filtered by file `mtime` and `agent_id`.
