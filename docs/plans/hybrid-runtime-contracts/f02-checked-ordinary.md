# Receipt-checked ordinary memory entry

The selected construction interface is Release1270's88e057bc/v1, under Root
ad940a23/v1. It extends the private image's memory entry only when the leading
argument is --receipt. The existing bare --config entry is not receipt checked
and remains outside staged hook selection.

The actual Go vector is IMAGE memory followed by the eight R02 selection flags:
--receipt, --receipt-sha256, --role memory, --entrypoint, --config,
--hook-timeout13, --launch-root and --shell. The original bounded event bytes
remain stdin; public output is the ordinary hook result. V comes from os.Args,
not an input vector. Entry time is captured before parsing/verification, and
H13 establishes D once for verification and body/SDK work. No external D/fd or
fresh budget is accepted on this direct path. L uses the same shell quoting and
content-addressed script formula as R02, with the actual Go vector; the runtime
checks its private ownership, mode and exact bytes.

The receipt's complete closure, alias/role/config selection, selected and
executing image identity, disabled manifest and exact ordered descriptors are
checked before any profile is trusted. Every pinned profile is read and checked
against its exact digest and must already be unbound. The executing descriptor
is retained through the body. No activation is resolved, binding removed or
bound generation downgraded. OpenCode agent_tools Start/prompt are the only
new reached cells. The body receives the checked config snapshot and original
environment/cwd, retaining native/privacy/account admission and SDK attribution.

The differential fixture uses Release's committed6fe25e21 actual R02 installer,
UnboundFixture and complete Python verifier/closure, selected read-only outside
the consumer checkout. That producer is not copied into production source or
used as an uncommitted baseline. Python and Go receive the same issued receipt;
each uses its own actual V and L. The fixture's source_revision label is opaque
provenance; committed producer/source hashes identify what was executed.

The first finite process pair covers real installed unbound Start/prompt and a
changed-receipt refusal with no API/state effect. Broader mutation/fd/deadline
and attribution evidence remains required before author ready, same1279 review
and lead integration. Release owns switching staged rows after the reviewed
consumer lands. No staging/TS/Python verifier/Coordination constructor edit,
live apply, public enable or provider/capture/tool/bound expansion is implied.
