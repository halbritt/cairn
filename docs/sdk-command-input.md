# Command stdin rejection

A trusted downstream command builder may construct
`NativeProcessOwner(allow_stdin_epipe=True)`. The default is `False`, including
all existing SDK callers. This local policy is not authentication, command
authorization or runtime admission. It must never come from a producer payload.

The owner passes the selection to its actual `NativeInput` as
`NativeInput(fd, allow_epipe=True)`. Only a `BrokenPipeError` with `errno.EPIPE`
from the attempted write selects the known-rejection path. That syscall accepted
zero new bytes. The input owner records `input_rejected=True`, preserves every
previously known accepted byte in `known_written`, leaves `write_unknown=False`,
and raises `NativeInputRejected('native_input_rejected')`. Subsequent writes
raise that fixed refusal without another syscall. No unwritten suffix is counted
as delivered, and rejection does not close the descriptor automatically.

Import `NativeInputRejected` from `trial_sdk_native_input`. A command job may
catch that specific exception to stop offering stdin, retain its exact accepted
prefix and record the remaining input as rejected. It must still inspect owner
and input uncertainty, close stdin, collect both output streams, observe the
actual direct-child wait and release the owner. A rejection alone supplies no
return code, command success or completed result. A nonzero command result still
belongs to the separate engine decoder.

`NativeProcessOwner.snapshot()['input_rejected']` derives from its held input
owner; before construction it is false. The nested `native_input` snapshot also
contains the additive `input_rejected` field. Known rejection does not set the
process owner's `primary` failure, so read, wait and release remain available.
`stdin_closed`, `closed_known`, `close_unknown` and release keep their separate
meanings. An uncertain close remains uncertain and is not retried by raw number.

Default owners retain the existing strict behavior: EPIPE raises the original
`BrokenPipeError`, marks write uncertainty and poisons the process owner's input.
In either policy, other ambiguous write errors remain failures. In command mode,
the write-to-accounting interval is premarked unknown; only a known syscall
outcome with completed accounting clears it. Known would-block accepts zero and
can resume. Neither an arbitrary `BrokenPipeError` without EPIPE nor another
`OSError` number qualifies as rejection.

The 512-byte per-write limit, launch-once ownership, actual wait requirements and
all existing resource/admission limits remain unchanged. The pure regressions in
`scripts/test_trial_sdk_command_input.py` use real input/process-owner objects
with simulated OS/process boundaries. Their controlled PID, return code and FD
numbers do not attest kernel behavior. Ordinary-child EPIPE compatibility and
the complete downstream job/broker path require separate validation.
