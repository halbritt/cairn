# F-02 Inbox Recall Caller and Post-Lock Report Scheduling

## Scope

This contract documents the `/3` Coordination caller seam in
`integrations/lifecycle/coordination.py` and
`integrations/lifecycle/lifecycle_compatibility.py` paired with the Go private
claimed-inbox consumer (`internal/lifecycle/inboxipc`).

## Producer-Consumer Seam (`lifecycle inbox recall`)

When `compatibility` (`InvocationContext`) is active on a bound Coordination
`UserPromptSubmit` wake turn:

1. **Read-only activation reuse:** The parent hook invocation has already
   resolved `/3` admission, receipt digests, and the one-shot activation
   decision via `compatibility.effective_config(...)`. `inbox_recall_output`
   reuses that checked state (`self.checked_binding()`, `self.enabled`) without
   making a second activation decision or loading `memory.py` / `inbox_recall.py`.
2. **Checked private child invocation:** `compatibility.inbox_recall_output`
   executes the verified Go binary descriptor (`/proc/self/fd/<binary_fd>`) with:
   ```text
   [binary_path, "lifecycle", "inbox", "recall",
    "--entrypoint", "coordination",
    "--config", config_path,
    "--config-sha256", config_sha256,
    "--binding-sha256", binding_sha256,
    "--receipt", receipt_path,
    "--receipt-sha256", receipt_sha256,
    "--deadline", str(limit)]
   ```
   where `limit = min(self.deadline, float(outer_deadline))` preserves the
   parent's `bridge_deadline`.
3. **Bounded 5-member producer stdin (`<= 1 MiB`):**
   ```json
   {
     "event": <dict>,
     "observation": <dict>,
     "state": <dict>,
     "control": <str>,
     "started_monotonic": <float>
   }
   ```
   `state` is the live locked Coordination session state after wake binding and
   inbox claim (`inbox_attempt`), and `control` is the composed Coordination
   control string. No Python callables (`revalidate`) cross the boundary.
4. **Bounded stdout reply (`<= 256 KiB`, `exit 0`, empty `stderr`):**
   - Success: `{"ok": true, "message": "...", "trace_report"?: {...}, "meter_report"?: {...}}`
   - Refusal: `{"ok": false, "error": {"class": "...", "message": "...", "code"?: "...", "errno"?: 11, "cairn_inbox_refusal": {"refusal_stage": "...", "refusal_reason": "...", ...}, "cairn_recall_failure"?: {...}}, "trace_report"?: {...}, "meter_report"?: {...}}`
   - Any non-zero exit code, non-empty `stderr`, oversized stdout, or malformed
     JSON frame raises `subprocess.SubprocessError('invalid inbox recall reply')`.

## Refusal Reconstruction and Settlement Ordering

On `ok: false` (or `subprocess.SubprocessError` / `TimeoutError`),
`inbox_recall_output` raises the reconstructed exception (`ValueError`,
`KeyError`, `TypeError`, `OSError` / `BlockingIOError`, `JSONDecodeError`,
`UnicodeDecodeError`, `UnicodeEncodeError`, `CoordinationError(code, message)`,
or `SubprocessError`) with `cairn_inbox_refusal` and optional
`cairn_recall_failure` attached.

Still inside `with session_lock(path):`, `coordination._handle` runs:
1. `_grant_ledger.annotate_inbox_refusal(exc, refusal_stage)`
2. `settle_refused_claude_wake(config, state, path, observation.get('native_turn_id'), exc)` when `state.get('inbox_intent')` is present
3. `raise NativePromptRefused(...) from exc`

## Post-Lock Report Scheduling (`lifecycle inbox report`)

Whenever `trace_report` or `meter_report` is present on the `recall` reply (both
success and post-required-search refusal paths), `inbox_recall_output` appends
`(self.inbox_recall_flush, {...})` to `recall_reports`.

`coordination.handle` flushes `recall_reports` in its `finally:` block **after**
`with session_lock(path):` has unwound (and after Go's `recall` child has
already released the memory session lock). `inbox_recall_flush` invokes:
```text
[binary_path, "lifecycle", "inbox", "report",
 "--entrypoint", "coordination", ..., "--deadline", str(limit)]
```
with the 3-member JSON payload `{"trace_report": ..., "meter_report": ..., "started_monotonic": ...}`.
Report failures are caught and ignored by `handle`'s `finally:` block so
telemetry never alters hook output, refusal status, or grant state.
