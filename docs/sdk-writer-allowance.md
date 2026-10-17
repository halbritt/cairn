# SDK writer allowance

`SDKDirectWriter.advance(maximum=512)` bounds both the copied pending suffix
and the offered syscall view to a caller-selected integer from 1 through 512.
The default preserves the existing write ceiling. Invalid allowances refuse
before copying or writing and leave controller progress unchanged. Zero is not
an EOF or idle permission: a resource-aware caller must park without calling
the writer when it has no payload allowance.

`SDKControl.pending_write(maximum=None)` retains the existing whole-suffix
behavior when called without a bound. The direct writer always supplies its
bound. Neither retrieval form advances the controller; only actual positive
write counts advance its offset. EAGAIN reoffers the same bounded suffix.

This is a prerequisite for original-resource session integration, not that
integration itself. The caller's integer is not an original-ledger ticket or
proof of funding. SDK review preparation and its controller/input buffers still
need allocation custody before construction, and the session must attach the
actual owner and resource ports without replacing those original objects.
Full simultaneous raw/working storage, native origin and runtime admission
remain unproved.

Tests inspect the actual copied suffix at the syscall boundary with real
controller, writer and owner objects; syscalls are inert in those new tests.
They cover partial writes with changing allowances, no mutation on invalid
allowance and identical bounded reoffers after EAGAIN. Existing controller
regression tests retain their ordinary nonblocking-pipe coverage.
