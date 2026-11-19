# Workstream status board

`CAIRN-466` adds a bounded per-repository workstream status board so leads and
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
cairn status authorize --operator --repo /home/halbritt/git/cairn --stream foundation --transfer-owner agent/SUCCESSOR_UUID
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
  total (`current`, `last_landed`, `blocker`, and `decision_needed` at most 512
  bytes each; `detail` at most 1,024 bytes). Oversized updates refuse with
  `BUDGET_REFUSED`.
- **Ownership and authorization**: the initial writer becomes the stream's
  `owner`. Subsequent updates from other principals refuse with
  `AUTHORITY_DENIED` unless the owner or a local operator explicitly authorizes
  them via `status authorize`.
- **Sensitivity isolation**: new streams default to `shareable`; updating an
  existing stream without `--local` or `--shareable` preserves its current
  sensitivity. `--local` requires a local destination, is hidden from hosted
  profiles without leaking stream existence, and a `shareable` stream cannot be
  downgraded to `local`.

## Deterministic Kanban rendering

`scripts/render_kanban_board.py` combines `cairn status get` with `plane issue list`
to render the campaign `leaf-done-v1` completion metric and Markdown Kanban
table with zero model calls:

```sh
python3 -B scripts/render_kanban_board.py --project CAIRN --campaign CAIRN-301
```
