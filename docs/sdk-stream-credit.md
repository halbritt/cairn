# Producer stream credit

`trial_sdk_stream_credit.StreamCredit` partitions one original producer DATA
stream ticket for actual stdin/stdout/stderr transfers and RPC records. It is a
source prerequisite with inert accounting tests. It allocates no raw backing,
launches no process, observes no EOF or wait, and authenticates no actor. The
actual builder must retain the genuine original `AttemptResources`, domain,
peer, transport and producer lifetime. Original 4-MiB traffic, 128-KiB raw queue,
combined 512-MiB allocation, callback deadlines and source gates remain in force.
No fresh pool, native admission, counterparty or physical-memory guarantee is
created here.

The trusted serialized parent creates `StreamCredit(resources, domain,
initial_maximum)` once for the WHOLE producer lifetime. The same domain cannot
also hold a separate DATA grant. `.maximum` is the effective committed ceiling;
`.resources` and `.ticket` are the held original ledger/ticket. Internal fixed
`WireCredit` and fixed CLI turn grants do not become growable. In particular,
growing a producer stream cannot restore a prior CLI final allowance or reset a
5-second deadline. Working metadata and caller-held object graphs still belong
inside the original aggregate process controls; logical totals do not measure
interpreter or kernel overhead.

## Funding before actual effects

`promise(n)` reserves a wire record of 32..544 bytes. `promise_stdio(n)` reserves
one raw action of 1..512 bytes without a fictional wire header. Both return an
opaque exact promise with `.maximum`; at most TWO promises can be active. They
grow only the necessary deficit from the same original global available bank
before returning. Known shortage raises `StreamCreditDenied` with the fixed code
`stream_capacity_denied` and no mutation, promise or unknown flag. It remains
possible to service already-funded work. `grow(cumulative_maximum)` explicitly
precommits and returns `False` on the same known shortage. Other errors use
`StreamCreditError` or the original `ResourceError`; exceptions after mutation or
uncertain bank return freeze, even if their class happens to be a denial class.

The current producer step can execute THREE actions (stdin512, stdout512 and
stderr512), not one512 action. Its runtime adapter must fund each action or a
source-derived whole-step envelope before effects. This class is not that
adapter. It does not force a smaller native workload by treating raw1 as wire32.

`known(promise, count)` accepts a trusted actually observed count, consumes that
exact promise once, and retains cumulative known traffic. Its unused pledge is
still inside the committed original stream and can fund another action; no
bytes have returned to the global bank. Known EAGAIN has zero actual bytes.
`unknown_prefix(promise, count)` keeps the actual observed prefix and unresolved
promise, freezes the original bank and all remaining ceiling, and never refunds.
No exception implies a fabricated count. `quarantine()` is the parent-only
terminal port even after another credit froze the ledger or a settlement was
consumed. It records this stream's cumulative known prefix when still held.

## Future RPC receive while producer stdio progresses

The parent encodes/holds its exact CREDIT frame but has not sent any byte. Its
live TX promise must cover the exact full frame size, e.g.34. It then calls
`protect_future_rx(transaction, peer, generation, maximum, trigger)` with the
actual held pending transaction object, actual held peer object, current positive
monotone bounded TX generation, expected RX maximum32..544, and exact live TX
promise. The returned claim is opaque. At most one future claim exists. These
source-held references bind bookkeeping, not a remote principal or actual kernel
provenance; the builder must prohibit IO until this reservation exists.

Before CREDIT is sent, its TX plus futureRX are both committed from genuine
capacity. The future claim holds no raw loan or third active promise. It is
protected in EVERY subsequent growth/spend calculation: known + active pledge
maxima + protectedRX <= stream ceiling on healthy paths. While TX is EAGAIN-held,
TX plus one stdio promise use the two slots; stdio grows extra capacity rather
than spending futureRX. The exact TX frame/promise and future claim stay held.

After `known(trigger, trigger.maximum)`, the parent calls
`promote_future_rx(claim, transaction, peer, generation)`. It requires the exact
binding and full matching trigger receipt, then returns the RX promise by one
state replacement of protected claim with active pledge. There is no healthy
gap, refund or reacquisition. Child reply bytes already queued before local
promotion were precommitted. RX plus one stdio promise again use two slots.
Future generation remains monotone after promotion and exhausts before another
reservation; completed/stale/foreign/duplicate triggers/claims cannot be reused.
The builder must prove that each CREDIT can trigger at most one bounded expected
record, and validate the actual response before issuing another trigger. The
numeric reservation alone does not prove this protocol obligation.

## Terminality and limits

Claim/promise updates publish one immutable state. A staged actual prefix remains
observable after a one-shot interruption and is not counted twice. Guards cover
mutation, bank-return handoff, final cleanup and return. Interrupted operations
retain unknown state and full surviving original commitment; no replay, retry,
positive effect, new promise or normal settlement is allowed. Unknown diagnostics
may retain a full unresolved pledge alongside a known subset; do not treat that
as free capacity or a healthy equality. Arbitrary repeated interruption, hostile
field mutation, foreign ownership and concurrent callbacks are outside the
trusted serialized source contract.

`finish()` refuses while ANY promise or future claim remains. Its caller must
also independently know the actual whole-producer lifetime and all relevant
loans ended. An RPC ending or an empty promise set does not establish EOF/wait or
release. Only then can known settlement return unused original commitment. No
timeout implies EOF. At global exhaustion a caller can use an actually funded
positive smaller action if its I/O contract permits it; otherwise stop with
honest missing EOF/release facts. Reserved room does not authorize uncredited
drain or a zero-read EOF claim. The existing exact-cap native output probe has
its own actual preconditions; no such probe is implemented by this accounting
module.

Tests exercise the real original ledger, two original fixed credits, inert
observed edges, protected RX/stdio scheduling, limits and exact one-shot trace
faults. They do not establish native execution, transport authentication,
producer scheduling, real peer/FD custody, memory API counts, physical fit or
whole-goal admission.
