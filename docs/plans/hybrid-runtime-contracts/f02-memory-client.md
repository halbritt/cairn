# F02-M1: authenticated memory client and checkpoint consumer

This component ports `Memory.call/search/checkpoint` from
`integrations/lifecycle/memory.py:848-920` into
`internal/lifecycle/memoryclient`. It is a callable source component of F-02,
with checkpoint search and pull as its concrete consumer. It does not register
a CLI or hook frontend. CAIRN-318 and full F-02 acceptance remain open.

The preservation baseline is `4eb9c41c017086e5012420747dcbb48158ea76fa`.
The implementation follows the F02-M1 assignment and the selected F-R1 criteria
corrections C1-C7. Those corrections reconcile the literal Python with the
existing CLI and shared Go search pipeline; they do not accept implementation
or runtime qualification.

## Caller contract

`New` borrows an existing authenticated `*localapi.Client`. The caller owns its
provisioned socket/profile selection and `Close`. Only a nil SDK is refused at
construction. The client is serial and belongs to one invocation, like the
Source `Memory` object. It copies context pins and the presentation command
prefix, and calls `WithDiagnostics` once. That SDK view preserves a supplied
trusted session view and obtains the actual transport build itself. Caller
scope and reported origin metadata never select an authenticated principal or
observation channel.

`Search` receives a resolved scope and optional context from construction.
Absent context becomes an explicit empty context object. Each search validates
the cumulative file-entity list while retaining its original order and values,
then uses `indexview.PrepareSearch`, a new `NewClientTransport`, and
`RetrieveCLI`/`Present`. Each prepared search probes capabilities once before
its index call. The shared pipeline preserves the semantic package, required
selections, conflict groups, source hashes/seal, discovery, budgets and paired
pull arguments. The command prefix is copied presentation data and is never
executed.

Room and timeout default to 8000 and five seconds only when their pointers are
nil. An explicit zero or negative value is retained. Nil and explicitly empty
request IDs remain distinct inputs at the public interface; both acquire a
fresh UUID through `PrepareSearch`. A non-empty request ID is passed verbatim.
Nil offset omits `page_offset`; explicit zero sends it. Successful search
statuses are `READY`, `SCOPE_EMPTY` and `DEGRADED_NO_EMBEDDINGS`, checked before
the Source's destination-name check for `hosted`.

`Pull` sends the entire `core.ExpandRequest` unchanged through SDK `expand`,
decoding into `*json.RawMessage`. The SDK therefore declares source-opening
support, and the complete reply, including unknown fields and numeric
literals, remains available to the consumer. There is no retry or replacement
request identity. Every call caps its caller's context with its own timeout;
capability negotiation and index share that context. An explicit nonpositive
timeout is already expired. Checkpoint's search and pulls retain the same
outer caller context.

## Accounting and failures

Accounting returns independent maps and a copied receipt slice. Only `search`
and `pull` are operation names; they start at zero. Initiated calls count even
when preparation, transport, response or consumer validation fails. The Source
exception is a pull with a nonempty receipt already known to have zero
credits: it returns `*core.Error{Code: "BUDGET_REFUSED"}` before I/O and before
either counter changes. An empty receipt still counts the pull operation but
creates no receipt counter or credit entry.

After a confirmed object pull reply, credits update before later checkpoint
validation. Starting from the previously observed value, or four if unknown,
an integer literal from zero through four gives `min(previous, remaining)`.
Other values give `max(0, previous-1)`. `-0` is an integer zero; fractional,
exponent, boolean, null, string and out-of-range values are not qualifying
integers. Raw-member decoding retains the last duplicate member, as in the
Source. Replayed replies cannot refund credits. Transport failures and
unconfirmed/nonobject replies do not change credits.

Search receipts are recorded only after `RetrieveCLI` returns a View and
before status or destination refusal. The list retains first-seen canonical
lowercase, non-nil UUIDs, deduplicated and capped at eight. A presentation
budget refusal retains no receipt.

Service refusals, the SDK's `API_CONNECTION_FAILED`, and context cancellation
or deadline causes remain inspectable. `errors.Is` identifies local Source
reasons through `ErrNotConfirmed`, `ErrSearchStatus`, `ErrNotHosted` and
`ErrCheckpointRecord`. Local response and transport diagnostics use fixed
messages while retaining underlying causes, without echoing bodies, tokens,
private text or paths. Missing successful response data is not confirmation.

## Checkpoint and verification boundary

Checkpoint searches with the literal double-quote + title + double-quote query,
room 16000 and the default five-second timeout. It visits index entries in
order, pulls only summaries beginning with title plus newline, and propagates
the first pull error. The returned selection must contain an object record
with a string body. A nonmatching body advances traversal; the first matching
record is returned as its exact raw bytes. Nil means no match. A malformed
record is an error, after any confirmed pull's credit update.

The four component tests use an in-process Unix HTTP fixture, a synthetic
owner-only token file and the real SDK. They exercise request and header
fidelity, search presentation/refusals, receipt and credit accounting, typed
failures, deadlines/cancellation, no retry, and concrete checkpoint traversal.
The permitted focused command is:

```sh
go test -count=1 -run '^(TestMemoryClientSearchContract|TestMemoryClientPullAccounting|TestMemoryClientFailuresAndDeadline|TestMemoryClientCheckpoint)$' ./internal/lifecycle/memoryclient
```

The separate static check is `make check`. These fixtures do not establish
server-side semantics, production contact, complete hook streams/exits/meters,
grant integration, frontend or executing-hook identity, runtime qualification,
kit acceptance or memory usefulness. Existing F04, RTERM, J04, original
1272/844 and O-C4 holds are unaffected. O-C4 remains immutable and unapplied,
with F1/F2/F3/A1 visible.
