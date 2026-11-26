# Workstream status board

Cairn provides a bounded per-repository workstream status board so leads and
workers can publish routine progress, checkpoints, blockers, and decisions
needed without sending inbox notices or waking a coordinator model turn.

## Commands

Update or create a stream entry (`cairn status` or `cairn agent ... status`):

```sh
cairn status set --stream foundation --state active \
  --current "CAIRN-304 Remaining F01 qualification" \
  --last-landed "73865776 Go process/framing helpers" \
  --next-checkpoint 2026-11-19T08:30:00Z \
  --blocker "" --decision-needed ""
```

Read the current board as JSON (`cairn.response/1`) or as a Markdown table:

```sh
cairn status get
cairn status get --stream foundation
cairn status get --format markdown
```

Inspect retained revision history for a single stream:

```sh
cairn status history --stream foundation --limit 20
```

Grant or revoke co-writer permission or transfer stream ownership (local
operators may pass `--operator --repo REPO`):

```sh
cairn status authorize --stream foundation --writer agent/WORKER_UUID
cairn status authorize --stream foundation --writer agent/WORKER_UUID --revoke
cairn status authorize --stream foundation --transfer-owner agent/SUCCESSOR_UUID
cairn status authorize --operator --repo /projects/shared-memory --stream foundation --transfer-owner agent/SUCCESSOR_UUID
```

## Guarantees and limits

- **Zero wakes**: `status set` and `status authorize` write only to
  `cairn.workstream_status` and `cairn.workstream_status_history`. They emit no
  agent events, deliveries, organic notices, or wake signals.
- **Full-entry replacement, latest entry on board, full history retained**:
  `status set` replaces the complete stream entry (omitted optional text or
  checkpoint flags clear those fields on the new revision). Each `(repo, stream)`
  keeps only its latest entry on the active board while appending every revision
  (including permission and ownership updates) with its authenticated `writer`,
  `owner`, and timestamp to `cairn.workstream_status_history`.
- **Fixed byte budget**: each stream entry is capped at `2,048` UTF-8 bytes
  total across `stream`, `state`, `current`, `last_landed`, `blocker`,
  `decision_needed`, and `detail` (`current`, `last_landed`, `blocker`, and
  `decision_needed` at most 512 bytes each; `detail` at most 1,024 bytes).
  Oversized updates refuse with `BUDGET_REFUSED`.
- **Ownership and authorization**: the initial writer becomes the stream's
  `owner`. Subsequent updates from other principals refuse with
  `AUTHORITY_DENIED` unless the owner or a local operator explicitly authorizes
  them via `status authorize` (capped at `16` authorized writers per stream;
  exceeding this refuses with `BUDGET_REFUSED`).
- **Sensitivity isolation**: new streams default to `shareable`; updating an
  existing stream without `--local` or `--shareable` preserves its current
  sensitivity. `--local` requires a local destination, is hidden from hosted
  profiles without leaking stream existence, and a `shareable` stream cannot be
  downgraded to `local`.

## Overdue checkpoint and `expect_by` notices

When a non-`done` workstream entry declares `next_checkpoint`, a notice declares
`expect_next_by`, or a request declares `expect_by` (`--expect-by RFC3339`),
`cairn schedule-serve --repo COLLECTION [--overdue-grace-seconds N]` (or one-shot
`cairn checkpoint-overdue-sweep`) evaluates missed deadlines after `deadline + grace`
(default grace `60` seconds):

- **Subscribers**:
  - Workstreams: active topic subscribers of `workstream-overdue`, `overdue`, or
    `workstream-overdue.<stream>` (`cairn subscribe --request-id UUID --topic workstream-overdue`).
  - Notices (`expect_next_by`): each addressed notice recipient (`agent/...`).
  - Requests (`expect_by`): the request publisher (`agent/...`), plus active
    subscribers of `overdue.<topic>` for topic requests (or `request-overdue`
    for operator-published requests).
- **Satisfaction before `deadline + grace`**: no overdue event is emitted if the
  workstream reaches `done`, clears or advances `next_checkpoint`, receives a
  board revision at/after `next_checkpoint`, or receives a newer event on that
  stream from an authorized writer; if a newer notice/event from the sender on
  that topic arrives; or if a request is completed (`handled`), cancelled, or
  receives a `response` or progress notice referencing its `event_id` or `correlation_id`.
- **Deduplication and coalescing**: `cairn.agent_overdue_checkpoint` persists
  `(repo, subscriber_consumer, source_kind, source_key, deadline_at)` so at most
  one urgent (`action=review`, `urgency=urgent`) overdue notice is emitted per
  missed deadline across host restarts. Additional missed deadlines for the same
  subscriber coalesce in `coalesced_pending` while an earlier overdue notice
  delivery remains `pending` or `leased`, and re-verify staleness before emitting.

## Deterministic Kanban rendering

`scripts/render_kanban_board.py` combines `cairn status get` with `plane issue list`
to render the campaign `leaf-done-v1` completion metric and Markdown Kanban
table with zero model calls:

```sh
python3 -B scripts/render_kanban_board.py --project CAIRN --campaign CAIRN-301
```
