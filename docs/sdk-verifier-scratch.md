# Verifier lifetime boundary

This candidate extends dac8a6e without changing any reservation, cap, ticket or
selected source/config check. It is not physical-fit or runtime admission.

Snapshot.verify_bindings performs the same live path/FD/file-byte verification
as bindings, then compares one metadata row at a time. It avoids constructing an
unused complete metadata graph. SDKSessionCustody.verify_held_sources executes
all prior instruction/control JSON, control identity, source, user declaration,
permission, canonical argv and instruction equality checks. It returns no graph.
held_source_bindings remains an explicit export: it verifies, deep-copies the
initial metadata and returns the same projection. Its backing remains charged to
the original ticket; no caller alias is declared ended or refunded.

SDKPreparationAllocation.verification serializes these materializations under the
actual original standing allocation. It checks the exact original row on entry
and normal exit. Reentry or interruption poisons/freezes the original allocation.
The guard is NOT an allocated fixed-size scratch buffer, an allocator bound or a
new resource capability. It releases no accounting on exit. Legacy offline
unfunded custody retains its old verification-only behavior; fixed composition
requires its existing actual allocation.

Session first-step and terminal checks, and fixed service preparation/checks,
use the void interface. Service verification first returns from the session
verification frame, then takes a separate scope for service source/config checks.
JSON parsing and equality are retained, including comparison of reconstructed
runtime/config against the actual held values. No cached success substitutes for
mutation detection. Per-file metadata mismatch now reports
snapshot_bindings_changed internally; outer session failures remain sanitized.

Named logical raw-payload envelopes, not complete physical bounds:

- noncollecting snapshot read:8192;
- text read plus decoder concatenation and conservative carry copies:16393;
- canonical hash:three logical8192 slots,24576; old/new encoded pieces can
  overlap on assignment, or buffer/encoded/yield can overlap;
- suspended producer yield:three logical8192 slots,24576, under its existing
  callback ticket, not a second preparation charge;
- first SessionStart may retain controller user<=8500 and permission705, so
  verifier+these persistent named bytes<=33781. Later callbacks require actual
  full user write; ending only the queue alias is insufficient.

Buffer allocated capacity/terminators, encoder/decoder internals, Unicode/JSON
working storage, tracebacks and compiler/kernel backing are not established by
those length sums. Source code generation/load remains unchanged. A suspended
producer's buffers can coexist with verification through held read custody;
original callback phase guards, not callback start, govern their retirement.
Terminal result graph overlaps the whole source recheck before input closure.

Current full reservations still refuse136353/153325 versus131072. No new source
acceptance or synthetic pass changes that refusal. The next actual raw
materialization correction is a direct canonical size/hash sink that consumes
existing JSON pieces with explicitly cleared encoded scratch, avoiding the
bytearray and yielded-byte copy used only to compute a digest. It must preserve
exact canonical bytes, validation/error behavior and full selected semantics,
and be reviewed with all child source pins if the shared encoder changes.
Snapshot text decoding and producer compiler backing remain separate obligations;
even a proven direct sink is not a complete quote or automatic row reduction.
