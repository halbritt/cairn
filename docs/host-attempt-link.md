# Link a wrapped execution to its host attempt

A host may pass `--attempt-id ATTEMPT_UUID` to `cairn run` or `cairn agent ... run`
when that attempt has already been observed through Cairn's service channel.
The wrapper includes the ID in its binding and result. Local-profile run reports
include `attempt_id` on linked receipts. Existing unlinked runs remain supported;
Cairn does not infer or backfill an attempt from matching task/run strings.

The host owns spawn and terminal observations. It must observe the actual spawn
before recording `spawn`: a planned dispatch is not an observed attempt. The
observer that creates the memory receipt must also own the attempt, with exactly
matching repository, task and run. An agent token, another observer's attempt,
a wildcard scope, and a missing attempt cannot establish this linkage.

This supports a host-observed wrapper invocation that subsequently launches one
memory-wrapped child. It does not change Striatum's sealed-input contract. A host
that must compile context before dispatch sealing still needs a permitted,
sealed context input and a matching execution route; this flag alone does not
supply that integration.

## Host sequence

1. Start the host's wrapper invocation with its existing startup coordination.
   Record the actual spawn through `spawn`, using the host's attempt UUID,
   dispatcher/delegate identities and exact scope. Only release the wrapper to
   prepare its child after this observation is confirmed. Keep exact pending
   request JSON if the response is ambiguous; do not start a second invocation.
2. Invoke `cairn agent --token-file OBSERVER_TOKEN run --attempt-id ATTEMPT_UUID`
   with the same repo/task/run and ordinary run flags. The server validates the
   link during `bind-run` and checks the attempt again during `claim-run`.
3. Retain the returned receipt, attempt ID and process outcome beside the host's
   own result. The host records `terminal` from its actual terminal observation,
   including the corresponding result reference when reporting `completed`.
   Retry exact observation JSON under the same observer after a lost response.

Library/API callers may supply optional `attempt_id` in `RunBindingRequest` or
`bind-run`. Empty/omitted means no declared link, retaining the legacy request
encoding. A binding cannot be replaced under another request. Multiple prepared
receipts can name an attempt, permitting recompilation before execution; only
one receipt can claim a wrapped launch for that attempt. A later receipt cannot
turn a prior ambiguous claim into permission for another process.

The claim checks and terminal observation serialize through the attempt row.
A committed terminal state causes `ATTEMPT_TERMINAL` (HTTP409, CLI conflict exit)
on new claims and linked binding retries. A prior claim causes
`RUN_ALREADY_STARTED`, including claims against other prepared receipts for the
same attempt. This is authorization at claim time, not a lease or asynchronous
process cancellation mechanism. Hosts retain responsibility for cancellation.

## Separate process and host outcomes

The wrapper never records a host spawn or terminal observation on the caller's
behalf. Process exit zero does not finalize the attempt, reconcile a delegate's
completion claim, or accept the task. If the host closes the task while the
attempt remains open, the existing `OPEN_DELEGATE_AFTER_TASK` finding remains
visible until the host supplies its terminal observation.

Late process outcomes and host terminal observations remain separate and can
arrive in either order. Historical reports retain the link after policy changes
or restore fences. Existing payload/delivery gates remain in force. No raw
command, prompt, result text or model identity is added by the link.
