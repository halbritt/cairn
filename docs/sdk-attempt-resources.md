# Attempt resource accounting candidate

`AttemptResources` is one parent-owned ledger for the existing 128-KiB raw queue,
512-MiB working-memory allowance and 4-MiB cumulative traffic ceiling. It creates
no backing, process, transport, credentials or extra allowance. Construction and
tests are inert accounting only. A runtime must construct it once for the whole
attempt and bind every relevant allocation/transfer; one instance per child would
be independent budgets and would violate the contract.

Raw backing and decoded/working quotes are disjoint allocations. Their combined
known reserved total must also fit the SAME 512-MiB aggregate. Both component
counts and the simultaneous combined peak remain visible; the raw allowance is
not another 128 KiB added above that aggregate. This arithmetic covers only the
known quoted subset and does not stand in for interpreter/process peak coverage.

The local domain is minted by the ledger. `bind` associates another opaque domain
with an exact retained `NativeProcessOwner`, including before its launch. Neither
that object reference nor the returned domain authenticates a connected process.
The real broker must bind its actual owned peer/lifetime and selected source
before mapping a wire receipt to a held domain/ticket. A supplied PID, ordinal,
success Boolean or equal-looking ticket cannot replace that association.

Each domain has two allocation lifetimes, `standing` and `operation`, and two
traffic lanes, `control` and `data`. Quotes combine all simultaneous backings,
aliases and copies within that lifetime. This is the selected serialized service
profile, not a verified bound on native concurrency or callbacks. At most 32 live
domains bound bookkeeping; this does not admit 32 extra tasks or replace the
original process controls. Workload compatibility still needs actual composition.
Released tickets are removed; ordinals never repeat, and public diagnostics copy
their state. No request text, raw results or caller-controlled labels are retained.

`allocate` debits before returning a ticket. A known capacity denial returns
`None` without allocating or spending capacity. `release` consumes that exact held
ticket once. The trusted owning handler may call it only after every allocation
and alias ended or its actual charge transfer completed. This method performs
accounting; it does not prove a remote free, producer consumption, actual wait or
kernel close. Domain retirement likewise only ends the ledger association. The
runtime must retain separate real wait/EOF/release facts. Those proofs are not
implemented by a counter or an acknowledgment flag.

`grant` reserves unspent traffic before external effects; control headroom can be
reserved before a CLI data turn. `settle` accepts the exact grant's known actual
count, commits those bytes and returns only unused commitment. Spent bytes never
return. An ambiguous receipt uses `quarantine`: known positive progress is kept
separate while the entire remaining commitment stays unavailable. Accounting
uncertainty is sticky, so catching an exception cannot issue/replay more grants.
The mutation guard includes final cleanup and return, without promising universal
recovery from arbitrary asynchronous interruption.

`quarantine(ticket, known)` is a terminal parent-only observation port. It also
works after the shared ledger is frozen, so a CLI data failure does not prevent
recording separately observed RPC prefixes from both original live credits.
`known` is the cumulative actual count for that exact still-held grant, bounded
by its original maximum. Equal repeated observations are idempotent; a smaller,
invalid, foreign, forged or stale observation refuses without changing counters.
No amount is inferred from an exception or supplied role/identity field. Only
the trusted source-owned observer may map its actual receipt to the held ticket.

The port freezes the ledger before updating the held row's cumulative prefix.
Public spent traffic is settled traffic plus the sum of those live prefixes;
public reserved traffic is gross live commitment minus the same sum. Thus each
increase reclassifies only the difference, keeps spent+reserved unchanged, and
leaves all unobserved remainder unavailable. Prefix assignment has one scalar
linearization point; there is no separate aggregate increment to half-apply and
then double-apply on the next observation. A one-shot interruption retains the
previous or increased prefix, and repeating the actual cumulative observation
can recover idempotently. This permits no traffic effect or general retry.

Normal grant, settlement, memory release/allocation, retirement and close remain
unavailable after freeze. Even a fully observed prefix never consumes its ticket
or restores a slot. Wrong-process, closed-ledger and reentrant prefix updates
refuse. Settlement is premarked before its counter changes: if an interrupted
settlement still retains its ticket, prefix recording refuses that ambiguous row
instead of counting its already-mutated settlement again. This does not repair
or certify every interrupted legacy settlement; its uncertainty stays terminal.
Repeated asynchronous interruption inside recovery or arbitrary hostile field
mutation is outside the trusted serialized contract.

`freeze` records higher-layer handoff uncertainty even after a traffic ticket
was already settled. It is parent-process-bound, sticky and idempotent, including
when the ledger is already unknown or closed. It changes no counters or tickets;
later known completion cannot clear it. It is not a receipt, a new allowance or
permission to refund an unresolved transfer.

Missing grants are not zero traffic room. The service must park before invoking
`CommandJob.step` until its authenticated turn grant arrives. A zero grant can be
issued only when current uncommitted capacity is zero. `available_at_issue` is a
historical arithmetic fact, not permission to infer permanent global exhaustion:
another unsettled grant could later return unused capacity. The parent protocol
must decide whether a smaller/zero CLI grant is the final usable allowance after
mandatory RPC/terminal commitments, then bind it to the job. That protocol and its
byte-exact headroom are still unimplemented. The existing job must not interpret
ordinary network delay as budget exhaustion.

The original stream owner must define each directed transfer and charge it once
at its designated actual-observation point. A send and the matching receive can
reconcile the same transfer; a new relay/re-encoding is a new transfer. Header,
CLI, SDK, backend, producer and broker volume must all be covered under the actual
original contract. This generic ledger does not discount duplicates, invent
per-edge costs, or establish that mapping. A wire count alone does not settle an
unauthenticated or stale grant.

Counter metadata, interpreter/kernel overhead and all other process allocations
remain inside the original aggregate resource controls. Logical reserved totals
are not a measurement or universal physical-memory guarantee. No new resource
gate supersedes the original verified cgroup contract. Shared local `Root`
backings, remote allocation/alias ownership and actual peak coverage still need
binding. No default budget port or execution entrypoint silently enables them.

The tests use inert direct-owner objects, accounting identities and controlled
interruption. They check shared limits, spent-versus-reserved traffic, headroom,
counterfeit/stale tickets, pending-versus-zero grants, unknown commitments,
diagnostic isolation and required final-cleanup injection. No actual subject
process, socket, memory API, provider, native SDK or evaluation is exercised.
Additional pure checks use the actual original `WireCredit`/`ServiceLink` with
inert process owners, including data-first freeze and both credit orders. A
required one-shot trace at every actually executed line of a terminal prefix
update checks conserved commitment, retained prefixes and idempotent recovery;
these are source-accounting checks, not kernel/transport receipt authentication.
