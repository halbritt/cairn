# Withdraw an instruction after its source is forgotten

The lifecycle review reproduced a withdrawal failure in a disposable store:

1. Create an ordinary source and an authorized mandatory instruction derived
   from it.
2. Forget the source. Fresh compilation correctly refuses because the instruction
   depends on forgotten content.
3. Obtain a current impact preview and retract the instruction with a live grant.

Before this change, step 3 failed with `PAYLOAD_UNAVAILABLE: cannot add a citation
to forgotten content`. Retraction copied the original draft's relations into its
new inactive version, treating withdrawal as a fresh citation. This left the
invalid instruction active and continued to block compilation.

Retraction now creates its inactive version without new dependency citations.
The previous versions keep their original relation rows, so impact/history is
not erased. The record's body, audit transition and old/new version link retain
their existing behavior. Authorization, compare-and-swap, impact preview and
open-conflict checks still apply.

The extended `TestForgettingSourceRefusesMandatoryDependentInstruction` fails
against the original behavior and passes with the repair. It verifies successful
withdrawal, idempotent retry, fresh compilation after withdrawal, and preservation
of the original dependency row. `make test-integration check` passes with
disposable PostgreSQL and the race detector, including existing authority,
conflict and retraction tests. No operational instruction or source was changed
for this verification. No schema migration is needed.

Implementation `f6d64c65a4259f8933cb6016eec8296e85c91a2f` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34205110979). A clean Go 1.25.0
build (`vcs.modified=false`) is installed, with SHA-256
`5544677a6a2914678a90f370281b6e4c42409ad91818772f2829d7413ca3141d`.
The API process runs those same bytes, authenticated existing-record reads pass,
schema 020 remains current, and the operational record-version digest is
unchanged. A pre-upgrade backup and the previous binary remain local. These
installation checks establish deployment health; the withdrawal behavior was
tested only in disposable stores.
