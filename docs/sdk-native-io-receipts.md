# Native I/O receipts

`NativeProcessOwner.io_receipt()` and `NativeInput.write_receipt()` return
the latest immutable local observation, or `None` before the first I/O call.
They read only retained memory: no descriptor, clock, process, or wait probe.
Closing a descriptor does not make its receipt unavailable. The owner's
existing snapshot also includes `last_io`.

The tuple is:

```text
(generation, name, offered, attempted, count, outcome_unknown)
```

`generation` increases for each accepted non-reentrant method entry, including
an entry that subsequently refuses before I/O. It stops at `2**53 - 1`.
Creator-process, reentrancy and sequence-exhaustion checks precede mutation;
those refusals preserve the original receipt. This is a local ownership
convention, not authentication against arbitrary code in the same process.

`name` is `stdin`, `stdout`, `stderr`, or the fixed marker `invalid` for an
invalid read selector. `offered` is the validated slice size, at most512,
or `None` before buffer validation. The receipt stores no payload or FD.

`attempted` marks entry to the syscall interval. An interruption at that marker
does not establish that the kernel ran. `count` is the exact observed valid
count, or `None` when no count was observed. `outcome_unknown` preserves effect
or cleanup uncertainty even when a positive prefix is known.

| Situation | attempted | count | outcome_unknown |
|---|---|---|---|
| Fresh invalid buffer/unavailable stream | false | None | false |
| Actual positive syscall return | true | actual count | false |
| Existing selected no-progress syscall exception | true | 0 | false |
| Actual output EOF return | true | 0 | false |
| Read of an already observed EOF | false | None | false |
| Unknown syscall outcome | true | None | true |
| Positive return followed by cleanup failure | true | retained count | true |

These are typical completed observations; they do not replace the method's
actual return/exception. A failed operation never becomes usable merely because
its receipt contains a number. A caller observing an in-flight or interrupted
handoff must retain uncertainty and its original reservations.

## Reads

`read_into` retains a positive or zero `os.readv` return before EOF descriptor
close or borrowed-view cleanup. Its continuous failure guard includes that
cleanup. A later failure preserves the count, marks that output unavailable,
and retains the existing primary failure. Raw descriptor closes remain detached
before their single attempt and are never retried.

Existing no-progress behavior stays the same: the selected syscall's
`BlockingIOError` or `InterruptedError` returns `-1`, with receipt count0.
An exception of the same class injected during later cleanup is unknown; it
cannot impersonate the earlier syscall outcome. Strict EOF probing at exhausted
payload credit remains the separate `probe_output_eof` mechanism.

## Writes and original input policy

`NativeInput` retains its own fresh write receipt. `NativeProcessOwner.write`
binds the expected next inner generation before invoking the actual input owner.
Its memory-only receipt view reconciles only that generation and combines inner
and outer uncertainty. It never substitutes a previous count or a difference in
cumulative `known_written` for a fresh receipt.

The syscall count is retained before cumulative accounting and loan cleanup.
Unexpected exceptions through final cleanup mark the input write unknown,
including the strict SDK path. An outer owner cleanup failure can leave the
inner cumulative count known while marking the owner's overall handoff unknown.
These are distinct facts.

The original input policies remain:

- Actual `BlockingIOError` is raised with a known zero-progress receipt. The
  exact recognized exception identity is retained through cleanup.
- Strict SDK EPIPE remains an unknown write and poisons input.
- A builder selecting `allow_stdin_epipe=True` still gets terminal typed
  `NativeInputRejected` only for actual `BrokenPipeError` with errno EPIPE.
  That call has count0 and preserves earlier accepted bytes.
- A later rejection without a syscall has `attempted=False`, `count=None`.
  It does not create another observed zero-byte syscall.
- Other exceptions remain unknown; the rejected suffix is never counted as
  delivered. `close`, wait and release retain their separate existing facts.

## Limits and integration obligation

The mechanism stores a bounded latest tuple, not a growing receipt log. Callers
must retain and match the expected generation, stream and offered length before
settling their own original traffic ticket. They must not start another action
and then try to recover the previous observation from this latest-only view.

No resource ledger or traffic settlement is added here. The future coordinator
must prehold the original allowance, retain known positive bytes even if its
hash/clock/parser fails, and quarantine unresolved room after any unknown
handoff. A reported `None` is not actual zero. Reading a receipt is not a grant,
native delivery, actual wait, release, process identity or runtime admission.

The guard covers demonstrated syscall/accounting/cleanup exception paths. It
does not promise recovery from every asynchronous interpreter fault, including
an unobservable syscall-wrapper effect or an exception at the Python return
event after the callee's handler is no longer active. Callers still own their
outer handoff uncertainty. No kernel behavior or native runtime compatibility
is established by the pure tests.

`test_trial_sdk_io_receipts.py` uses real owners/views with substituted system
boundaries and required-injection `sys.settrace` faults. It covers positive
prefixes, fresh refusal, known zero versus later cleanup failure, strict and
opt-in EPIPE, inner/outer cleanup, EOF-close uncertainty, creator/reentrancy,
memory-only metadata and sequence exhaustion. The exhaustion test explicitly
seeds the bounded counter state instead of executing quadrillions of calls.
Existing command-input and output-EOF boundary suites remain applicable.
