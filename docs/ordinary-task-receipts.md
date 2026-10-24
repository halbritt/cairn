# Prospective ordinary-task native receipts

`scripts/ordinary_task_receipt.py` is a source-level seam for CAIRN-227. It
binds one `cairn.token-efficiency-panel-manifest/1` prospective workflow to
the exact actor task, setup, acceptance-check, arm intervention, whole source-record and
source-body hashes. The actor task hash is separate from the exact rendered
stdin hash that a future runner must compute after adding any context. Its
private stream parser accepts complete native Claude `stream-json` lines from
one process, requires zero process exit, one successful terminal result, one
resolved model, bounded terminal usage and at least one native assistant message
ID. Repeated stream blocks for the same message count once. It emits only
24-character opaque response references in the same convention as
`workflow_model_report.py` and counters; no prompt, answer or raw stream is
retained in the projection.

This does **not** establish a completed ordinary workflow. The entry point
currently stops with `native_launch_not_configured` after checking the frozen
binding. It has no stream-replay, `native_completed` or response-reference
arguments. A caller could fabricate JSON or replay bytes if a gate accepted
this parser's output alone. The token-efficiency gate therefore remains closed
for memory/capture cases without a real event join.

Before enabling a provider launch, a trusted local runner must receive a
reviewed, frozen panel and explicit account, subscription route, per-run and
campaign token budgets, model/effort, effective ordinary-hook and inbox pins,
plus fixture setup custody. It must own native subprocess creation, exact
stdin prompt bytes, streaming stdout, process exit, and receipt publication in
one execution. The receipt must link those observed facts to the frozen
workflow binding without accepting externally supplied completion or response
fields. A separate task reviewer must grade source use and useful outcome.
The runner must stop if subscription authentication or quota fails; OpenRouter
is not a fallback. Raw streams and private task content stay outside the repo.
